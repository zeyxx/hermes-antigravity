# Capturing the official Antigravity CLI fingerprint (optional, network)

The plugin tracks `CLI_VERSION` / `CLI_BUILD` from the official `agy` binary.
To refresh them you need the full HTTP User-Agent, which is end-to-end TLS.

1. Generate a local CA and server cert for the target host.
2. Run a terminating proxy (e.g. mitmproxy) with that CA.
3. Point `agy` at the proxy via `HTTPS_PROXY` and trust the CA (`SSL_CERT_FILE`).
4. Capture the `streamGenerateContent` request headers; extract `antigravity/cli/<ver> ... cl=<build>`.

Note: `agy` may pin certificates or reject unknown CAs, so this may fail.
The non-network fallback is `tools/probe_agy_fingerprint.py` (strings only).

## Self-signed CA (concrete)

```sh
mkdir -p /tmp/mitm-ca && cd /tmp/mitm-ca
openssl genrsa -out ca.key 2048
openssl req -x509 -new -nodes -key ca.key -sha256 -days 1 -out ca.crt -subj "/CN=hermes-mitm"
openssl genrsa -out server.key 2048
openssl req -new -key server.key -out server.csr -subj "/CN=daily-cloudcode-pa.googleapis.com"
openssl x509 -req -in server.csr -CA ca.crt -CAkey ca.key -CAcreateserial -out server.crt -days 1 -sha256
```

Then run a terminating proxy (mitmproxy or a small Python ssl server) presenting
`server.crt`, and launch `agy` with `HTTPS_PROXY=http://127.0.0.1:PORT` and
`SSL_CERT_FILE=/tmp/mitm-ca/ca.crt`. Capture request headers; expect failure if
`agy` pins certificates.
