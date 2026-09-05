# Plugin Google Antigravity pour Hermes Agent


[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](CONTRIBUTING.md)

[🇬🇧 English Version](README.md) | [🇫🇷 Version Française](README.fr.md)

---

Ce plugin intègre l'infrastructure d'inférence **Google Antigravity / Cloud Code Assist** dans **Hermes Agent** (`nousresearch/hermes-agent`). Il permet d'utiliser les modèles de pointe Gemini et Claude avec un support complet du streaming, du tool calling (function calling) et des tokens de réflexion (thinking/reasoning).

### 🎓 Conçu pour les abonnements Gemini Pro, Étudiants & Google One AI
Google ne fournit **pas** de clés d'API pour les abonnements grand public ou étudiants (Gemini Pro Étudiant, Gemini Advanced, Google One AI Premium, Google Workspace for Education). Jusqu'à présent, Hermes Agent ne pouvait se connecter à Google que via des clés d'API Google AI Studio (`GOOGLE_API_KEY`) ou Vertex AI, empêchant les titulaires d'abonnements d'utiliser leurs quotas.

Ce plugin résout ce problème : il s'authentifie directement avec votre compte Google via OAuth 2.0 PKCE, débloquant l'ensemble des modèles de votre abonnement dans Hermes Agent sans consommer de tokens payants !
L'architecture est directement inspirée et alignée sur le plugin éprouvé `pi-antigravity` (`@earendil-works/pi`).


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

## 🚀 Fonctionnalités

- **Modèles découverts dynamiquement (30+ modèles)** :
  - `gemini-3.8-flash-tiered` / `gemini-3.8-flash` (recommandé pour le coding agentique rapide)
  - `gemini-3.7-flash` / `gemini-3.7-flash-thinking`
  - `gemini-2.5-pro`
  - `gemini-2.5-flash`
  - `claude-3-7-sonnet`
  - `claude-3-5-sonnet`
- **Streaming natif & Thinking** : support du flux Server-Sent Events (SSE) avec affichage en direct des réflexions (`reasoning_content`) dans la TUI et la Gateway Hermes.
- **Tool Calling bidirectionnel** : conversion transparente des déclarations d'outils et des appels de fonction.
- **Authentification Hybride** :
  - Détection automatique des sessions locales existantes (`~/.gemini/antigravity-cli/antigravity-oauth-token`, `~/.pi/agent/auth.json`).
  - Flux interactif Google OAuth 2.0 PKCE avec serveur de callback local (`http://localhost:51121/oauth-callback`) ou saisie manuelle en mode headless.
  - Rafraîchissement automatique des jetons d'accès avant expiration.
- **Intégration native dans `hermes model` & `/model`** :
  - Sélection interactive du provider et du modèle avec configuration persistante dans `~/.hermes/config.yaml`.
- **Résilience Réseau** : bascule automatique entre les endpoints candidats (`daily-cloudcode-pa.googleapis.com`, `daily-cloudcode-pa.sandbox.googleapis.com`, `cloudcode-pa.googleapis.com`).

---

## 📦 Installation & Découverte

Clonez simplement le dépôt dans votre répertoire de plugins Hermes :
```bash
git clone https://github.com/zeyxx/hermes-antigravity ~/.hermes/plugins/model-providers/antigravity
```

Hermes Agent découvre automatiquement les plugins placés dans `$HERMES_HOME/plugins/model-providers/`. Aucune modification du cœur de Hermes n'est nécessaire.

Pour vérifier la détection par Hermes :
```bash
python3 -c "from providers import get_provider_profile; print(get_provider_profile('antigravity'))"
```

---

## 🔑 Authentification

### 1. Déclenchement automatique via `hermes model` (Recommandé)
Lancez simplement :
```bash
hermes model
```
Choisissez **Google Antigravity**. Si vous n'êtes pas encore connecté, Hermes lance automatiquement le flux interactif Google OAuth 2.0 PKCE dans votre terminal.

### 2. Commande autonome de connexion
Vous pouvez également vous authentifier ou vérifier le statut à tout moment :
```bash
# Vérifier le statut
python3 ~/.hermes/plugins/model-providers/antigravity/auth.py status

# Connexion interactive
python3 ~/.hermes/plugins/model-providers/antigravity/auth.py
```

### 3. Variables d'environnement optionnelles
- `ANTIGRAVITY_ACCESS_TOKEN` : Jeton d'accès Bearer Google.
- `ANTIGRAVITY_REFRESH_TOKEN` : Jeton de rafraîchissement OAuth.
- `ANTIGRAVITY_PROJECT_ID` : ID de projet Google Cloud Code Assist.
- `ANTIGRAVITY_BASE_URL` : Surcharge optionnelle de l'endpoint d'inférence.

---

## 🛠️ Utilisation avec Hermes Agent

### Sélection interactive
```bash
hermes model
# ou dans le chat TUI :
/model
```

### Lancement direct en ligne de commande
```bash
hermes -z "Explique ce code en une phrase" --provider antigravity --model gemini-3.8-flash-tiered
```

### Configuration persistante dans `~/.hermes/config.yaml`
```yaml
model:
  provider: antigravity
  name: gemini-3.8-flash-tiered
  reasoning_effort: medium
```

---

## 🧪 Tests

Pour exécuter l'ensemble des 15 tests unitaires et d'intégration :
```bash
pytest tests/ -v
```

---

## 🙏 Remerciements & Provenance Open Source

Ce projet s'appuie sur le travail précieux de la communauté open source. L'attribution et la traçabilité du code sont indispensables à la pérennité et à l'éthique de notre écosystème.

Le reverse-engineering des endpoints Google Cloud Code Assist (`daily-cloudcode-pa.googleapis.com`), le flux OAuth PKCE et les conventions d'appel de l'API Antigravity ont été initialement pionniérisés en TypeScript par **Rahul Arya** ([@Rahularya01](https://github.com/Rahularya01)) pour le harness Pi :

- **Dépôt d'origine** : [`Rahularya01/pi-antigravity`](https://github.com/Rahularya01/pi-antigravity)
- **Auteur original** : Rahul Arya
- **Harness d'origine** : Pi Coding Agent ([`@earendil-works/pi`](https://github.com/earendil-works/pi))
- **Licence d'origine** : Licence MIT

Nous avons adapté, réécrit et intégré ces principes d'architecture dans un plugin Python natif `model-provider` pour **Hermes Agent** (`nousresearch/hermes-agent`). Tout le mérite et notre gratitude pour les recherches initiales sur l'API Cloud Code Assist reviennent à Rahul Arya et aux contributeurs de l'écosystème Pi.

---

## 📄 Licence

Licence MIT (c) 2026 zeyxx. Développé pour la communauté Hermes Agent.
