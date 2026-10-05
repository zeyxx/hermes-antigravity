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
**Projet de base & provenance :** ce plugin est une adaptation Python de [`pi-antigravity`](https://github.com/Rahularya01/pi-antigravity) par Rahul Arya ([@Rahularya01](https://github.com/Rahularya01)), ré-architecturée en `ProviderProfile` natif Hermes. **Ce n'est pas un fork du dépôt Pi** — il réimplémente le même protocole wire, le même routage de modèles, la même enveloppe de session, le nettoyage des schémas d'outils, la gestion des thought-signatures et le streaming SSE. Chaque constante wire est suivie dans [`UPSTREAM_DRIFT.md`](UPSTREAM_DRIFT.md), afin qu'une divergence avec l'implémentation de référence reste visible au lieu d'être silencieuse. Voir [Remerciements & Provenance](#-remerciements--provenance-open-source).


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
  - `gemini-3.8-flash` (recommandé pour le coding agentique rapide ; routé automatiquement vers les IDs runtime `-low`/`-medium`/`-high` qui portent votre quota)
  - `gemini-3.7-flash` / `gemini-3.6-flash` / `gemini-3.5-flash`
  - `gemini-3.1-pro` / `gemini-2.5-pro`
  - `gemini-2.5-flash`
  - `claude-sonnet-4-6` / `claude-opus-4-6-thinking`
- **Routage de modèles** : les IDs publics sont convertis vers les IDs runtime suffixés sur lesquels Google impute le quota. Envoyer un ID public nu retourne 429 même avec du quota restant — le client route automatiquement, selon l'effort de réflexion.
- **Enveloppe de session** : IDs de conversation/trajectoire stables plus labels de requête à chaque appel, pour une attribution multi-tours correcte.
- **Plafonds de sortie par modèle** : `max_tokens` est borné au plafond de chaque famille (Claude 64000, gpt-oss 32768) au lieu d'échouer en HTTP 400.
- **Assainissement strict des schémas** : les schémas d'outils sont purgés des mots-clés que Claude rejette (`anyOf`, `oneOf`, `allOf`, …) avant déclaration.
- **Streaming natif & Thinking** : support du flux Server-Sent Events (SSE) avec affichage en direct des réflexions (`reasoning_content`) dans la TUI et la Gateway Hermes.
- **Tool Calling bidirectionnel** : conversion transparente des déclarations d'outils et des appels de fonction.
- **Authentification Hybride** :
  - Détection automatique des sessions locales existantes (`~/.gemini/antigravity-cli/antigravity-oauth-token`, `~/.pi/agent/auth.json`).
  - Flux interactif Google OAuth 2.0 PKCE avec serveur de callback local (`http://localhost:51121/oauth-callback`) ou saisie manuelle en mode headless.
  - Rafraîchissement automatique des jetons d'accès avant expiration.
- **Intégration native dans `hermes model` & `/model`** :
  - Sélection interactive du provider et du modèle avec configuration persistante dans `~/.hermes/config.yaml`.
  - Commandes auth natives : `hermes auth add antigravity`, `hermes auth list`, `hermes auth status antigravity`, `hermes auth remove antigravity`.
  - Registre multi-comptes avec migration automatique.
- **Résilience Réseau** : bascule automatique entre les endpoints candidats (`daily-cloudcode-pa.googleapis.com`, `daily-cloudcode-pa.sandbox.googleapis.com`, `cloudcode-pa.googleapis.com`) plus relance avec backoff sur les HTTP 429 transitoires.

---

## 📦 Installation & Découverte

### Prérequis

**Hermes Agent v0.20.0 ou plus récent.** Ce plugin fournit l'inférence via
`ProviderProfile.create_client()`. Sur les cores plus anciens, ce hook n'existe pas : le
provider se charge quand même et apparaît dans `hermes model`, mais chaque requête échoue
en **HTTP 404** — le core construit discrètement son propre client de forme OpenAI et
l'envoie à l'API Antigravity, qui ne parle pas cette forme.

Si des requêtes répondent 404 sur un provider qui semble correctement configuré, vérifiez
d'abord la version de votre core. Le plugin journalise une erreur explicite au chargement
lorsque le hook est absent.

**Mettre à jour une installation existante :** ne faites pas de `cp` par-dessus. Le plugin
installé est lui-même un clone git et peut contenir des commits ou des modifications qui
n'existent nulle part ailleurs ; une simple copie les détruit sans diff ni erreur. Utilisez
la synchronisation protégée à la place :

```bash
./tools/sync-installed.sh --dry-run   # rapporte les changements, n'écrit rien
./tools/sync-installed.sh             # synchronise, en refusant si la copie locale a du travail
```

Elle refuse quand l'arborescence installée est sale, quand elle contient des commits
absents de son propre amont, ou quand elle est sur une révision inattendue — un travail qui
n'existe que sur cette machine devient un échec bruyant plutôt qu'une perte silencieuse.
`--force` passe outre, et le message indique ce que vous perdriez.

Clonez simplement le dépôt dans votre répertoire de plugins Hermes :

**Linux & macOS :**
```bash
git clone https://github.com/zeyxx/hermes-antigravity ~/.hermes/plugins/model-providers/antigravity
```

**Windows (PowerShell) :**
```powershell
git clone https://github.com/zeyxx/hermes-antigravity "$env:USERPROFILE\.hermes\plugins\model-providers\antigravity"
```

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
- `ANTIGRAVITY_CLIENT_ID` / `ANTIGRAVITY_CLIENT_SECRET` : utiliser votre propre client OAuth
  Google au lieu du client par défaut.

### 4. Sécurité des identifiants

**Le client OAuth par défaut est le client public desktop d'Antigravity de Google, pas un
secret d'application privé.**

Un client OAuth « installed app » ne peut pas garder de secret : le binaire l'embarque, la
valeur est donc publique par nature et sa seule protection est que Google la considère comme
non confidentielle. Ce plugin embarque le même identifiant client public que l'application
Antigravity Desktop officielle, à l'octet près identique à
[`pi-antigravity`](https://github.com/Rahularya01/pi-antigravity) (MIT), qui l'a
rétro-ingénieré depuis le CLI officiel.

C'est différent d'un secret d'API, et c'est pourquoi un scanner de secrets peut le signaler :

- **La valeur embarquée est publique par conception.** Elle ne donne aucun accès à elle
  seule — un échange de jeton exige toujours une connexion Google interactive par le
  propriétaire du compte.
- **Les identifiants réellement sensibles sont les jetons, et ils ne sont jamais dans ce
  dépôt.** `ANTIGRAVITY_REFRESH_TOKEN` est un identifiant de compte vivant : gardez-le hors
  du contrôle de version, des issues et des logs de discussion.
- **Ne committez pas de vrais jetons.** Les comptes stockés vivent dans
  `~/.hermes/antigravity-accounts.json`, qui doit rester accessible au seul propriétaire
  (`chmod 600`).
- **Préférez votre propre client pour tout ce qui est sensible.** Définissez
  `ANTIGRAVITY_CLIENT_ID` / `ANTIGRAVITY_CLIENT_SECRET` si vous voulez votre propre quota,
  votre propre piste d'audit et votre propre surface de révocation.

Si un scanner bloque un push ici, lisez cette section avant de la passer outre : vérifiez que
la valeur signalée est bien le *client desktop public* embarqué, et non un jeton
utilisateur. Un push bloqué mérite d'être compris plutôt que forcé.

Scopes OAuth demandés à la connexion :

<!-- prettier-ignore -->
| Scope | Raison |
| --- | --- |
| `aicode` | Accès au catalogue et aux endpoints Cloud Code Assist / Antigravity |
| `cloud-platform` | Accès général à l'API Cloud Code Assist |
| `userinfo.email`, `userinfo.profile` | Identifier le compte Google connecté |
| `cclog` | Endpoints de logging/télémétrie Cloud Code Assist utilisés par l'API |
| `experimentsandconfigs` | Flags d'expérimentation et de configuration côté serveur |

Examinez ces permissions avant d'approuver l'accès. Si des identifiants expirent ou sont
révoqués, relancez `hermes model` et resélectionnez Google Antigravity.

### 5. Plusieurs profils Hermes

La découverte des plugins est par profil. Si vous utilisez plusieurs profils Hermes
(`hermes -p <profile> ...`, ou une surcharge de `HERMES_HOME`), liez le plugin dans chacun :

```bash
for profile in work personal; do
  mkdir -p ~/.hermes/profiles/$profile/plugins/model-providers
  ln -s ~/.hermes/plugins/model-providers/antigravity \
          ~/.hermes/profiles/$profile/plugins/model-providers/antigravity
done
```

**Chaque profil conserve ses propres comptes.** Le registre de comptes est résolu via le
`HERMES_HOME` du profil, donc les identifiants ne fuient jamais entre profils. Connectez-vous
une fois par profil :

```bash
hermes -p work model     # puis sélectionnez Google Antigravity
hermes -p personal model # puis sélectionnez Google Antigravity
```

Si vous *voulez* un seul jeu de comptes Google entre profils, liez plutôt le registre — c'est
un unique fichier JSON, `chmod 600` :

```bash
ln -s ~/.hermes/antigravity-accounts.json ~/.hermes/profiles/work/antigravity-accounts.json
```

> Avant la v1.1.2, tous les profils partageaient `~/.hermes/antigravity-accounts.json` parce
> que le chemin était codé en dur au lieu d'être résolu via `HERMES_HOME`. Cela ressemblait
> à un partage fonctionnel et aurait cassé dans l'autre sens dès que le core aurait honoré la
> variable. Les profils sont isolés par défaut désormais.

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
hermes -z "Explique ce code en une phrase" --provider antigravity --model gemini-3.8-flash
```

### Configuration persistante dans `~/.hermes/config.yaml`
```yaml
model:
  provider: antigravity
  default: gemini-3.8-flash
  reasoning_effort: medium
```

---

## 🧪 Tests

Pour exécuter l'ensemble des tests (96 actuellement — vérifiez avec
`--collect-only`, un module qui ne s'importe pas perd ses tests en silence) :
```bash
export PYTHONPATH=".:$HOME/.hermes/hermes-agent"   # le plugin importe `providers` depuis le cœur Hermes
python3 tools/run_tests.py --collect-only
python3 tools/run_tests.py --min-tests "$(python3 tools/run_tests.py --collect-only)"
```

Ni pytest, ni dépendances : le lanceur n'utilise que la bibliothèque standard
(voir [`CONTRIBUTING.md`](CONTRIBUTING.md) pour le parcours contributeur complet).

---

## ⚠️ Avertissement

Ce plugin utilise les endpoints Google Cloud Code Assist (`cloudcode-pa.googleapis.com`) en dehors du client officiel Antigravity CLI. Il ne s'agit **pas** d'une intégration officiellement supportée par Google. L'utilisation de ces endpoints via un client tiers peut ne pas être conforme aux Conditions d'Utilisation de Google et pourrait entraîner des restrictions ou la suspension de votre compte Google. Ces endpoints peuvent également être modifiés ou bloqués par Google à tout moment et sans préavis.

**Utilisation à vos propres risques.** Les auteurs de ce plugin ne sont pas responsables des conséquences liées à son utilisation.

---

## 🔄 Dérive vis-à-vis de l'amont

Ce plugin est un portage de [`pi-antigravity`](https://github.com/Rahularya01/pi-antigravity),
qui possède le protocole wire que ce plugin réimplémente. L'amont avance ; un portage n'a
aucun compilateur pour s'en apercevoir.

**[`UPSTREAM_DRIFT.md`](UPSTREAM_DRIFT.md)** liste chaque champ wire, sa valeur de chaque
côté, et la date de la dernière vérification. Les tests asserteraient le côté portage, donc
une mise à jour amont se traduit par un test rouge plutôt que par une dégradation silencieuse
— le mode de défaillance derrière chaque incident réel dans l'historique de ce plugin (une
empreinte de CLI périmée, un hook de core manquant).

Si vous portez quelque chose depuis l'amont, mettez à jour ce fichier dans la même PR.

**Vérification automatisée :**

```bash
python3 tools/check_upstream_drift.py --refresh
```

Le script compare les constantes extraites de l'amont à celles du code, signale les écarts,
et peut être utilisé en local comme dans la CI.

## 🙏 Remerciements & Provenance Open Source

Ce projet s'appuie sur le travail précieux de la communauté open source. L'attribution et la traçabilité du code sont indispensables à la pérennité et à l'éthique de notre écosystème.

Le reverse-engineering des endpoints Google Cloud Code Assist (`daily-cloudcode-pa.googleapis.com`), le flux OAuth PKCE et les conventions d'appel de l'API Antigravity ont été initialement pionniérisés en TypeScript par **Rahul Arya** ([@Rahularya01](https://github.com/Rahularya01)) pour le harness Pi :

- **Dépôt d'origine** : [`Rahularya01/pi-antigravity`](https://github.com/Rahularya01/pi-antigravity)
- **Auteur original** : Rahul Arya
- **Harness d'origine** : Pi Coding Agent ([`@earendil-works/pi`](https://github.com/earendil-works/pi))
- **Licence d'origine** : Licence MIT

Nous avons adapté, réécrit et intégré ces principes d'architecture dans un plugin Python natif `model-provider` pour **Hermes Agent** (`nousresearch/hermes-agent`). Portés à l'identique depuis le source TypeScript : la table de routage public-vers-runtime, l'enveloppe de session/trajectoire, le nettoyage des schémas d'outils, la gestion des thought-signatures pour Gemini 3+, et le protocole de streaming SSE. Tout le mérite et notre gratitude pour les recherches initiales sur l'API Cloud Code Assist reviennent à Rahul Arya et aux contributeurs de l'écosystème Pi.

---

## 📄 Licence

Licence MIT (c) 2026 zeyxx. Développé pour la communauté Hermes Agent.
