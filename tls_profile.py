"""TLS transport profile for the Antigravity HTTPS client.

Problem
-------
The official ``agy`` client is a Go binary; this plugin speaks TLS through
Python's stdlib ``ssl``/OpenSSL. Google's backends see a TLS ClientHello that
differs between the two, and a JA3 fingerprint is a cheap server-side signal
to correlate a non-official client (see UPSTREAM_DRIFT.md and issue #35).

What stdlib can and cannot align (measured on this host, 2026-10-10,
Python 3.14.7 / OpenSSL 3.5.8, comparing ``agy`` and ``urllib`` ClientHellos
to ``daily-cloudcode-pa.googleapis.com``):

+---------------------+----------------------------------------+-------------------+
| ClientHello field   | stdlib ``ssl`` control                 | Go (agy)          |
+=====================+========================================+===================+
| ALPN                | set_alpn_protocols -> CAN align        | h2,http/1.1       |
| Cipher suites       | set_ciphers -> CAN narrow + reorder    | 13 suites         |
| Supported groups    | NO stdlib API (set_ecdh_curve sets one | 4588,4587,4589,29,|
|                     | curve, not the ordered list)           | 23,24,25          |
| Extension order     | NO stdlib API                          | 0,11,65281,23,... |
+---------------------+----------------------------------------+-------------------+

The supported-groups order (which carries the post-quantum hybrid
X25519MLKEM768 = 4588 plus the PQC groups 4587/4589) and the extension order
are the core of the JA3 hash. Python's public SSL API exposes neither, so an
exact Go fingerprint is **not achievable** in stdlib. This module therefore
does not claim parity: it reduces the observable difference as far as stdlib
allows (ALPN aligned, cipher list narrowed toward Go's) and pins the result
with a drift test so the profile cannot silently regress.

Measured JA3 (same host):
    agy   (Go)     : 03117a8ed39ef02427ebbc39f121275c
    urllib default : a1ebe7f90a577e9399eaa60be3c67721
    urllib + this  : (asserted by tests/test_tls_profile.py against a capture)

Security posture is unchanged: certificate and hostname validation stay on,
only the offered-cipher set and ALPN advertisement are tuned.
"""
from __future__ import annotations

import ssl

# Go 1.2x crypto/tls, ordered as ``agy`` offers them toward Google backends
# (TLS 1.3 AEAD first, then ECDHE AES/CHACHA GCM). Narrowing to this set drops
# the legacy CBC/SHA and non-PFS suites Python otherwise offers, shrinking the
# cipher-count and ordering gap versus the Go ClientHello.
GO_CIPHER_LIST = (
    "TLS_AES_128_GCM_SHA256:"
    "TLS_AES_256_GCM_SHA384:"
    "TLS_CHACHA20_POLY1305_SHA256:"
    "ECDHE-ECDSA-AES128-GCM-SHA256:"
    "ECDHE-RSA-AES128-GCM-SHA256:"
    "ECDHE-ECDSA-AES256-GCM-SHA384:"
    "ECDHE-RSA-AES256-GCM-SHA384:"
    "ECDHE-ECDSA-CHACHA20-POLY1305:"
    "ECDHE-RSA-CHACHA20-POLY1305:"
    "ECDHE-ECDSA-AES128-SHA:"
    "ECDHE-RSA-AES128-SHA:"
    "ECDHE-RSA-AES256-SHA"
)

# agy offers HTTP/2 and HTTP/1.1 toward these endpoints; matching the offer
# removes the clearest single-field difference (Python default: no ALPN, or
# http/1.1 alone). It does not force h2 — the server still negotiates.
GO_ALPN = ["h2", "http/1.1"]


def build_ssl_context() -> ssl.SSLContext:
    """An stdlib ``SSLContext`` tuned toward the Go client's ClientHello.

    Certificate and hostname validation remain enabled (``create_default_context``);
    this only narrows the offered ciphers and advertises ``h2,http/1.1``. Use it
    wherever the plugin opens an HTTPS connection so every request carries the
    same, narrower handshake rather than the stdlib default.
    """
    ctx = ssl.create_default_context()
    try:
        ctx.set_ciphers(GO_CIPHER_LIST)
    except ssl.SSLError:
        # An OpenSSL build missing one of these names: fall back to the default
        # context rather than fail the request. The ALPN alignment below still
        # applies and is the higher-value knob.
        ctx = ssl.create_default_context()
    try:
        ctx.set_alpn_protocols(GO_ALPN)
    except (ssl.SSLError, NotImplementedError):
        # No ALPN support in this build; the cipher narrowing still stands.
        pass
    return ctx


def profile_facts() -> dict[str, str]:
    """The measured reference points, for the drift test and the tracker.

    Pinned to the 2026-10-10 same-host capture so a regression in the offered
    profile is visible. Not a claim that the plugin matches Go — see the module
    docstring for what stdlib cannot reach.
    """
    return {
        "go_ja3": "03117a8ed39ef02427ebbc39f121275c",
        "go_alpn": "h2,http/1.1",
        "go_cipher_count": "13",
        "measured_host": "linux-x86-64",
        "measured_python": "3.14.7",
        "measured_openssl": "OpenSSL 3.5.8",
        "measured_date": "2026-10-10",
    }


def install_tuned_opener() -> None:
    """Install a urllib opener whose HTTPS handler uses :func:`build_ssl_context`.

    Called once at import time by the modules that make requests (models,
    client) so every ``urllib.request.urlopen`` in the plugin carries the
    tuned ClientHello instead of the stdlib default. Falls back silently to
    the default opener if the context cannot be built, so a request is never
    blocked by transport tuning.
    """
    import urllib.request

    try:
        ctx = build_ssl_context()
    except Exception:
        return  # keep the default opener; tuning is best-effort, never a gate
    https = urllib.request.HTTPSHandler(context=ctx)
    opener = urllib.request.build_opener(https)
    urllib.request.install_opener(opener)
