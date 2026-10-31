# Capturing the official Antigravity CLI fingerprint (optional, network)

The plugin tracks `CLI_VERSION` / `CLI_BUILD` from the official `agy` binary.
To refresh them you need the full HTTP User-Agent, which is end-to-end TLS.

1. Generate a local CA and server cert for the target host.
2. Run a terminating proxy (e.g. mitmproxy) with that CA.
3. Point `agy` at the proxy via `HTTPS_PROXY` and trust the CA (`SSL_CERT_FILE`).
4. Capture the `streamGenerateContent` request headers; extract `antigravity/cli/<ver> ... cl=<build>`.

Note: `agy` may pin certificates or reject unknown CAs, so this may fail.
The non-network fallback is `tools/probe_agy_fingerprint.py` (strings only).
