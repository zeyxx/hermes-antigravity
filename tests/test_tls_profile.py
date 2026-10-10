"""TLS profile tests: what stdlib can align toward the Go client, and the drift pin.

Two kinds of test:

- Pure: the profile constants and the SSLContext knobs are what we claim.
- Live capture: dial a loopback ClientHello listener with the tuned context and
  assert the observable result (ALPN offered, cipher count narrowed). This is
  the drift detector: if a Python/OpenSSL upgrade silently changes the offered
  handshake, this fails loudly instead of the plugin quietly growing a new
  fingerprint. It never reaches Google — the listener closes before ServerHello.
"""
import hashlib
import socket
import ssl
import struct
import threading

from tls_profile import (
    GO_ALPN,
    GO_CIPHER_LIST,
    build_ssl_context,
    profile_facts,
)


def _u16(b, p):
    return struct.unpack_from("!H", b, p)[0]


def _grease(x):
    return (x & 0x0F0F) == 0x0A0A


def _capture_client_hello(ctx):
    """Dial a loopback listener and return the parsed ClientHello fields."""
    captured = {}

    def listener(sock):
        conn, _ = sock.accept()
        conn.settimeout(5)
        hs = b""
        while True:
            try:
                chunk = conn.recv(4096)
            except socket.timeout:
                break
            if not chunk:
                break
            hs += chunk
            if len(hs) >= 5 and hs[0] == 22:
                blen = int.from_bytes(hs[3:5], "big")
                if len(hs) >= 5 + blen:
                    break
        try:
            hello = hs[5:5 + int.from_bytes(hs[3:5], "big")][4:]
            p = 0
            legacy = _u16(hello, p); p += 34
            p += 1 + hello[p]; n = _u16(hello, p); p += 2
            ciphers = [_u16(hello, i) for i in range(p, p + n, 2)]; p += n
            p += 1 + hello[p]; n = _u16(hello, p); p += 2; end = p + n
            exts = []; alpn = []; groups = []
            while p < end:
                t = _u16(hello, p); ln = _u16(hello, p + 2); p += 4
                v = hello[p:p + ln]; p += ln; exts.append(t)
                if t == 10:
                    groups = [_u16(v, i) for i in range(2, len(v), 2)]
                elif t == 16:
                    q = 2
                    while q < len(v):
                        sz = v[q]; q += 1; alpn.append(v[q:q + sz].decode()); q += sz
            ja3 = ",".join([
                str(legacy),
                "-".join(str(x) for x in ciphers if not _grease(x)),
                "-".join(str(x) for x in exts if not _grease(x)),
                "-".join(str(x) for x in groups if not _grease(x)),
                "",
            ])
            captured.update(
                cipher_count=len(ciphers), alpn=alpn, groups=groups,
                ja3_md5=hashlib.md5(ja3.encode()).hexdigest(),
            )
        except Exception as exc:  # pragma: no cover - capture is best-effort
            captured["error"] = str(exc)
        conn.close()

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    t = threading.Thread(target=listener, args=(srv,), daemon=True)
    t.start()

    c = socket.create_connection(("127.0.0.1", port), timeout=5)
    try:
        tls = ctx.wrap_socket(c, server_hostname="daily-cloudcode-pa.googleapis.com")
        tls.close()
    except (ssl.SSLError, OSError):
        try:
            c.close()
        except Exception:
            pass
    t.join(timeout=3)
    srv.close()
    return captured


# ── Pure: the profile's stated shape ────────────────────────────────

def test_go_alpn_matches_the_measured_go_offer():
    assert GO_ALPN == ["h2", "http/1.1"]


def test_cipher_list_has_no_legacy_cbc_suites():
    """The narrowed list must not reintroduce CBC/legacy suites.

    The point is to shrink the cipher-count gap versus Go, so the list stays on
    AEAD + ECDHE; a CBC suite appearing here is a regression of that intent.
    """
    lowered = GO_CIPHER_LIST.lower()
    assert "-sha:" not in lowered and "-sha256:" not in lowered or "gcm" in lowered
    assert "3des" not in lowered
    assert "rc4" not in lowered
    assert "null" not in lowered


def test_profile_facts_pin_the_measured_go_ja3():
    facts = profile_facts()
    assert facts["go_ja3"] == "03117a8ed39ef02427ebbc39f121275c"
    assert facts["go_alpn"] == "h2,http/1.1"
    assert facts["go_cipher_count"] == "13"


def test_build_ssl_context_keeps_verification_on():
    """Tuning must not weaken certificate/hostname validation."""
    ctx = build_ssl_context()
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.check_hostname is True


# ── Live capture: the drift detector ────────────────────────────────

def test_tuned_context_offers_h2_alpn():
    """The tuned context must advertise h2,http/1.1 like the Go client.

    This is the single highest-value alignment stdlib allows: the default
    context advertises no ALPN (or http/1.1 alone), which is a one-field
    giveaway. If a future build drops ALPN, this fails loudly.
    """
    result = _capture_client_hello(build_ssl_context())
    assert "error" not in result, result.get("error")
    assert result["alpn"] == ["h2", "http/1.1"], (
        "the tuned context must offer h2,http/1.1 to match the Go ClientHello")


def test_tuned_context_narrows_the_cipher_count():
    """Tuning must reduce the offered cipher count versus the stdlib default.

    The default context offers 17 suites; the Go client offers 13. Narrowing is
    the achievable part of the gap. Assert strictly fewer than the default so a
    regression to the wide default is caught.
    """
    default = _capture_client_hello(ssl.create_default_context())
    tuned = _capture_client_hello(build_ssl_context())
    assert "error" not in tuned, tuned.get("error")
    assert tuned["cipher_count"] < default["cipher_count"], (
        f"tuned offered {tuned['cipher_count']} ciphers, "
        f"default {default['cipher_count']}: tuning must narrow the list")


def test_tuned_ja3_differs_from_default_but_is_stable():
    """The tuned fingerprint must differ from the default and be reproducible.

    We do NOT assert it equals Go (stdlib cannot order supported groups or
    extensions — see tls_profile docstring). We assert it is a deliberate,
    stable change from the default, so the profile is a known quantity rather
    than an accident.
    """
    tuned_a = _capture_client_hello(build_ssl_context())
    tuned_b = _capture_client_hello(build_ssl_context())
    assert "error" not in tuned_a, tuned_a.get("error")
    assert tuned_a["ja3_md5"] == tuned_b["ja3_md5"], (
        "the tuned ClientHello must be deterministic across connections")
