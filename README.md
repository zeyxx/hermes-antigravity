# Google Antigravity Provider Plugin for Hermes Agent


[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](CONTRIBUTING.md)

[🇬🇧 English Version](README.md) | [🇫🇷 Version Française](README.fr.md)

---

A native inference provider plugin integrating **Google Antigravity / Cloud Code Assist** into **Hermes Agent** (`nousresearch/hermes-agent`). It enables running state-of-the-art Gemini and Claude models with full streaming support, native tool calling (function calling), and reasoning/thinking tokens.

### 🎓 Built for Gemini Pro, Student & Google One AI Subscriptions
Google does **not** provide API keys for consumer or student subscriptions (Gemini Pro Student, Gemini Advanced, Google One AI Premium, Google Workspace for Education). Historically, Hermes Agent could only connect to Google via Google AI Studio API keys (`GOOGLE_API_KEY`) or Vertex AI service accounts, leaving subscription holders unable to use their quota.

This plugin solves that problem: it authenticates directly with your Google account via OAuth 2.0 PKCE, unlocking your subscription models in Hermes Agent without burning paid API tokens!
This plugin is a Python port of the proven `pi-antigravity` extension for the Pi Coding Agent — same wire protocol, same model routing, same session envelope, re-architected as a native Hermes `ProviderProfile`. See Acknowledgements & Provenance below.


## 🏗️ Architecture

```text
┌─────────────────────────────────────────────────────────────┐
│                 Hermes Agent (AIAgent Loop)                 │
└──────────────────────────────┬──────────────────────────────┘
                               │ client.chat.completions.create()
                               ▼
┌─────────────────────────────────────────────────────────────┐
│           Hermes Antigravity Plugin (ProviderProfile)        │
│                                                             │
│  ┌──────────────────────┐        ┌───────────────────────┐  │
│  │       auth.py        │        │     translator.py     │  │
│  │  - OAuth 2.0 PKCE    │        │  - OpenAI <-> Google  │  │
│  │  - Token Auto-refresh│        │  - SSE Chunk Parser   │  │
│  │  - loadCodeAssist    │        │  - Thinking & Tools   │  │
│  └──────────┬───────────┘        └───────────▲───────────┘  │
│             │ Bearer Token                   │ SSE Stream   │
│             ▼                                │              │
│  ┌───────────────────────────────────────────┴───────────┐  │
│  │                  client.py (Transport)                │  │
│  │  POST /v1internal:streamGenerateContent?alt=sse       │  │
│  │  Failover: daily-cloudcode-pa -> cloudcode-pa         │  │
│  └───────────────────────────┬───────────────────────────┘  │
└──────────────────────────────┼──────────────────────────────┘
                               │ HTTPS (User-Agent: antigravity/cli)
                               ▼
┌─────────────────────────────────────────────────────────────┐
│           Google Cloud Code Assist / Antigravity API        │
│    (Gemini 3.8 Flash, Gemini 3.7, Claude Sonnet/Opus)       │
└─────────────────────────────────────────────────────────────┘
```
---

## 🚀 Features

- **Live Model Discovery (30+ models)**:
  - `gemini-3.8-flash` (recommended for fast agentic coding; auto-routed to the `-low`/`-medium`/`-high` runtime IDs that carry your quota)
  - `gemini-3.7-flash` / `gemini-3.6-flash` / `gemini-3.5-flash`
  - `gemini-3.1-pro` / `gemini-2.5-pro`
  - `gemini-2.5-flash`
  - `claude-sonnet-4-6` / `claude-opus-4-6-thinking`
- **Model Routing**: public IDs map to the suffixed runtime IDs Google attributes quota on. Sending a bare public ID returns 429 even with quota remaining — the client routes automatically, per thinking effort.
- **Session Envelope**: stable conversation/trajectory IDs plus request labels on every call, so multi-turn usage is attributed to one session.
- **Per-Model Output Caps**: `max_tokens` is clamped to each family ceiling (Claude 64000, gpt-oss 32768) instead of failing with HTTP 400.
- **Strict Schema Sanitization**: tool schemas are stripped of keywords Claude rejects (`anyOf`, `oneOf`, `allOf`, …) before declaration.
- **Native SSE Streaming & Thinking**: Server-Sent Events (SSE) streaming with live reasoning display (`delta.reasoning_content`) in the Hermes TUI and Gateway.
- **Bidirectional Tool Calling**: Transparent mapping of OpenAI JSON schemas to Google Cloud Code Assist function declarations and function responses.
- **Hybrid Authentication**:
  - Auto-discovery of existing local sessions (`~/.gemini/antigravity-cli/antigravity-oauth-token`, `~/.pi/agent/auth.json`).
  - Interactive Google OAuth 2.0 PKCE flow with local callback listener (`http://localhost:51121/oauth-callback`) and headless fallback.
  - Transparent access token auto-refresh before expiration.
- **Native Hermes Integration (`hermes model` & `/model`)**:
  - Interactive provider and model selection with persistent configuration in `~/.hermes/config.yaml`.
  - Native auth commands: `hermes auth add antigravity`, `hermes auth list`, `hermes auth status antigravity`, `hermes auth remove antigravity`.
  - Multi-account registry with legacy migration.
- **Network Resilience**: Automatic failover across candidate endpoints (`daily-cloudcode-pa.googleapis.com`, `daily-cloudcode-pa.sandbox.googleapis.com`, `cloudcode-pa.googleapis.com`) plus retry with backoff on transient HTTP 429.

---

## 📦 Installation & Discovery

### Requirements

**Hermes Agent v0.20.0 or newer.** This plugin delivers inference through
`ProviderProfile.create_client()`. On older cores that hook does not exist, so the
provider still loads and still appears in `hermes model`, but every request fails with
**HTTP 404** — the core quietly builds its own OpenAI-shaped client and POSTs it to the
Antigravity API, which does not speak that shape.

If requests 404 on a provider that looks correctly configured, check your core version
before anything else. The plugin logs an explicit error at load time when the hook is
missing.

Clone this repository into your Hermes model providers plugin directory:

**Linux & macOS:**
```bash
git clone https://github.com/zeyxx/hermes-antigravity ~/.hermes/plugins/model-providers/antigravity
```

**Windows (PowerShell):**
```powershell
git clone https://github.com/zeyxx/hermes-antigravity "$env:USERPROFILE\.hermes\plugins\model-providers\antigravity"
```

Verify detection:
```bash
python3 -c "from providers import get_provider_profile; print(get_provider_profile('antigravity'))"
```

---

## 🔑 Authentication

### 1. Interactive via `hermes model` (Recommended)
Run:
```bash
hermes model
```
Select **Google Antigravity**. If not already authenticated, Hermes launches the interactive Google OAuth 2.0 PKCE flow directly in your terminal.

### 2. Standalone Authentication CLI
Authenticate or inspect status at any time:
```bash
# Check status
python3 ~/.hermes/plugins/model-providers/antigravity/auth.py status

# Run interactive OAuth login
python3 ~/.hermes/plugins/model-providers/antigravity/auth.py
```

### 3. Optional Environment Variables
- `ANTIGRAVITY_ACCESS_TOKEN`: Google Bearer access token.
- `ANTIGRAVITY_REFRESH_TOKEN`: Google OAuth refresh token.
- `ANTIGRAVITY_PROJECT_ID`: Google Cloud Code Assist project ID.
- `ANTIGRAVITY_BASE_URL`: Optional inference endpoint override.
- `ANTIGRAVITY_CLIENT_ID` / `ANTIGRAVITY_CLIENT_SECRET`: use your own Google OAuth client instead of the bundled default.

### 4. Credential safety

**The default OAuth client is Google's *public* Antigravity desktop client, not a private app secret.**

A desktop "installed app" OAuth client cannot keep a secret: the binary ships with it, so the value is inherently public and its only protection is that Google treats it as non-confidential. This plugin embeds the same public client identifier used by the official Antigravity Desktop app, byte-identical to [`pi-antigravity`](https://github.com/Rahularya01/pi-antigravity) (MIT), which reverse-engineered it from the official CLI.

That is different from an API secret, and it is why secret scanners may flag it:

- **The embedded value is public by design.** It grants no access on its own — a token exchange still requires an interactive Google sign-in by the account owner.
- **The credentials that *are* sensitive are the tokens, and they are never in this repository.** `ANTIGRAVITY_REFRESH_TOKEN` is a live account credential: keep it out of source control, issues and chat logs.
- **Do not commit real tokens.** Stored accounts live in `~/.hermes/antigravity-accounts.json`, which must stay owner-only (`chmod 600`).
- **Prefer your own client for anything sensitive.** Set `ANTIGRAVITY_CLIENT_ID` / `ANTIGRAVITY_CLIENT_SECRET` if you want your own quota, audit trail and revocation surface.

If a scanner blocks a push here, read this section before dismissing it: confirm the flagged value is the embedded *public desktop client*, not a user token. A blocked push is worth understanding rather than force-pushing past.

Signing in requests these Google OAuth scopes:

<!-- prettier-ignore -->
| Scope | Why it's needed |
| --- | --- |
| `aicode` | Access to the Cloud Code Assist / Antigravity model catalog and endpoints |
| `cloud-platform` | General Cloud Code Assist API access |
| `userinfo.email`, `userinfo.profile` | Identify the signed-in Google account |
| `cclog` | Cloud Code Assist logging/telemetry endpoints used by the API |
| `experimentsandconfigs` | Server-side experiment and config flags for the API |

Review these permissions before approving access. If credentials expire or are revoked, run `hermes model` again and re-select Google Antigravity.

---

## 🛠️ Usage with Hermes Agent

### Interactive Model Selection
```bash
hermes model
# or inside the interactive chat / TUI:
/model
```

### Direct CLI Invocation (One-Shot)
```bash
hermes -z "Explain this file in one concise sentence" --provider antigravity --model gemini-3.8-flash
```

### Persistent Configuration in `~/.hermes/config.yaml`
```yaml
model:
  provider: antigravity
  default: gemini-3.8-flash
  reasoning_effort: medium
```

---

## 🧪 Tests

Run the full suite (65 unit and integration tests):
```bash
pytest tests/ -v
```

---

## ⚠️ Disclaimer

This plugin uses Google's Cloud Code Assist endpoints (`cloudcode-pa.googleapis.com`) outside of the official Antigravity CLI. This is **not** an officially supported integration by Google. Using these endpoints through a third-party client may not comply with Google's Terms of Service and could carry a risk of account restrictions or suspension. The endpoints may also change or be blocked by Google at any time without notice.

**Use at your own discretion and risk.** The authors of this plugin are not responsible for any consequences arising from its use.

---

## 🔄 Upstream drift

This plugin is a port of [`pi-antigravity`](https://github.com/Rahularya01/pi-antigravity),
which owns the wire protocol this plugin reimplements. Upstream moves; a port has no
compiler to notice.

**[`UPSTREAM_DRIFT.md`](UPSTREAM_DRIFT.md)** lists every wire field, its value on each side,
and when it was last checked. Tests assert the port side, so an upstream bump shows up as a
red test rather than a silent degradation — the failure mode behind every real incident in
this plugin's history (a stale CLI fingerprint, a missing core hook).

If you port something from upstream, update that file in the same PR.

## 🙏 Acknowledgements & Provenance

This project stands on the shoulders of giants. In open-source software, provenance and proper attribution are fundamental to sustainable, ethical collaboration.

The reverse-engineered protocol wire format, Google Cloud Code Assist endpoint discovery (`daily-cloudcode-pa.googleapis.com`), and OAuth PKCE integration patterns were originally pioneered and researched in TypeScript by **Rahul Arya** ([@Rahularya01](https://github.com/Rahularya01)) for the Pi Coding Agent:

- **Upstream Project**: [`Rahularya01/pi-antigravity`](https://github.com/Rahularya01/pi-antigravity)
- **Author**: Rahul Arya
- **Harness**: Pi Coding Agent ([`@earendil-works/pi`](https://github.com/earendil-works/pi))
- **License**: MIT

We ported, adapted, and re-architected these concepts into a native Python `model-provider` plugin implementing `ProviderProfile` for **Hermes Agent** (`nousresearch/hermes-agent`). Ported 1:1 from the TypeScript source: the public-to-runtime model routing table, the session/trajectory request envelope, the tool-schema stripping, the thought-signature handling for Gemini 3+, and the SSE streaming protocol. All gratitude and credit for the foundational Cloud Code Assist discovery go to Rahul Arya and the Pi open-source community.

---

## 📄 License

MIT License (c) 2026 zeyxx. Built for the Hermes Agent community.
