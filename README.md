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
The architecture is directly inspired by and aligned with the proven `pi-antigravity` extension (`@earendil-works/pi`).


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
  - `gemini-3.8-flash-tiered` / `gemini-3.8-flash` (recommended for fast agentic coding)
  - `gemini-3.7-flash` / `gemini-3.7-flash-thinking`
  - `gemini-2.5-pro`
  - `gemini-2.5-flash`
  - `claude-3-7-sonnet`
  - `claude-3-5-sonnet`
- **Native SSE Streaming & Thinking**: Server-Sent Events (SSE) streaming with live reasoning display (`delta.reasoning_content`) in the Hermes TUI and Gateway.
- **Bidirectional Tool Calling**: Transparent mapping of OpenAI JSON schemas to Google Cloud Code Assist function declarations and function responses.
- **Hybrid Authentication**:
  - Auto-discovery of existing local sessions (`~/.gemini/antigravity-cli/antigravity-oauth-token`, `~/.pi/agent/auth.json`).
  - Interactive Google OAuth 2.0 PKCE flow with local callback listener (`http://localhost:51121/oauth-callback`) and headless fallback.
  - Transparent access token auto-refresh before expiration.
- **Native Hermes Integration (`hermes model` & `/model`)**:
  - Interactive provider and model selection with persistent configuration in `~/.hermes/config.yaml`.
- **Network Resilience**: Automatic failover across candidate endpoints (`daily-cloudcode-pa.googleapis.com`, `daily-cloudcode-pa.sandbox.googleapis.com`, `cloudcode-pa.googleapis.com`).

---

## 📦 Installation & Discovery

Clone this repository into your Hermes model providers plugin directory:
```bash
git clone https://github.com/zeyxx/hermes-antigravity ~/.hermes/plugins/model-providers/antigravity
```

Hermes Agent automatically discovers plugins placed in `$HERMES_HOME/plugins/model-providers/`. No changes to the core Hermes codebase are needed.

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
hermes -z "Explain this file in one concise sentence" --provider antigravity --model gemini-3.8-flash-tiered
```

### Persistent Configuration in `~/.hermes/config.yaml`
```yaml
model:
  provider: antigravity
  name: gemini-3.8-flash-tiered
  reasoning_effort: medium
```

---

## 🧪 Tests

Run the full suite of 15 unit and integration tests:
```bash
pytest tests/ -v
```

---

## 🙏 Acknowledgements & Provenance

This project stands on the shoulders of giants. In open-source software, provenance and proper attribution are fundamental to sustainable, ethical collaboration.

The reverse-engineered protocol wire format, Google Cloud Code Assist endpoint discovery (`daily-cloudcode-pa.googleapis.com`), and OAuth PKCE integration patterns were originally pioneered and researched in TypeScript by **Rahul Arya** ([@Rahularya01](https://github.com/Rahularya01)) for the Pi Coding Agent:

- **Upstream Project**: [`Rahularya01/pi-antigravity`](https://github.com/Rahularya01/pi-antigravity)
- **Author**: Rahul Arya
- **Harness**: Pi Coding Agent ([`@earendil-works/pi`](https://github.com/earendil-works/pi))
- **License**: MIT

We ported, adapted, and re-architected these concepts into a native Python `model-provider` plugin implementing `ProviderProfile` for **Hermes Agent** (`nousresearch/hermes-agent`). All gratitude and credit for the foundational Cloud Code Assist discovery go to Rahul Arya and the Pi open-source community.

---

## 📄 License

MIT License (c) 2026 zeyxx. Built for the Hermes Agent community.
