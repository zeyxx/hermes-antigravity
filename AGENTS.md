# AGENTS.md — hermes-antigravity

Google Antigravity / Cloud Code Assist inference provider for Hermes Agent.
A dependency-free Python `model-provider` plugin: profile in `__init__.py`,
pure translators in `translator.py`/`models.py`, side effects behind explicit
seams in `auth.py`/`client.py`/`accounts.py`.

## Commands (canonical)

```bash
export PYTHONPATH=".:$HOME/.hermes/hermes-agent"   # `providers` lives in the Hermes core
python3 tools/run_tests.py --collect-only
python3 tools/run_tests.py --min-tests "$(python3 tools/run_tests.py --collect-only)"
python3 tools/check_upstream_drift.py               # wire protocol vs pi-antigravity
hermes plugins validate --json .                    # "ok" must be true
```

No pytest, no third-party test deps — by design (see CONTRIBUTING). Never add one.

## Structure

- `__init__.py` — provider profile: `create_client`, `auth_handler` (owns `add`),
  `refresh_credential` (pool-side rotation). Public Hermes seams only.
- `auth.py` — Google OAuth PKCE flow, token cache, account registry wiring.
- `accounts.py` — multi-account registry, profile-scoped via `HERMES_HOME`.
- `client.py` — chat-completions transport, quota failover, 403 surfacing.
- `models.py` — catalog discovery, model-enum wire labels, core-version gate.
- `translator.py` — pure Hermes ↔ Antigravity payload translation.
- `tests/` — one module per source file plus provider registration.
- `tools/` — `run_tests.py` (the suite), `check_upstream_drift.py`, `sync-installed.sh`.
- `UPSTREAM_DRIFT.md` — every wire field, both sides, checked dates.

## Authority (when sources disagree)

1. observed behaviour (run it, read the output)
2. `hermes plugins validate` + the Hermes core on disk (`~/.hermes/hermes-agent`)
3. `UPSTREAM_DRIFT.md` for wire values, `plugin.yaml` for version/floor metadata
4. this file and `CONTRIBUTING.md`
5. prose anywhere else

## Git and releases

- `main` is protected: strict `tests` + `drift` checks, linear history, no force
  pushes, no direct pushes. Everything lands via PR with green CI.
- Conventional commits, English: `fix(auth): …`, `feat(models): …`, `ci: …`, `docs: …`.
- Version bumps are maintainer `chore(release)` PRs (`plugin.yaml` + `pyproject.toml`
  in lockstep). Pushing an annotated tag `vX.Y.Z` triggers `.github/workflows/release.yml`,
  which verifies tag == both version files + suite + drift, then publishes the release.
- Never `git add -A`; stage explicit paths. Never commit secrets.

## Boundaries

- Always: public Hermes surfaces (`ProviderProfile` hooks, `register_provider`).
  Never: writes into core private tables, runtime rebinding (`sys.modules[...]`,
  `setattr` on core objects), competing with another plugin's seam.
- Ask first: new dependency, new capability, anything touching credentials, network,
  releases, or another contributor's scope.
- User docs stay bilingual: behaviour changes update `README.md` (EN) **and**
  `README.fr.md` (FR) in the same PR. Code, tests, commits: 100% English.
- Porting upstream: update `UPSTREAM_DRIFT.md` (both columns + checked date) in the
  same PR. A red drift test means upstream moved — say so, don't silence it.
- Unknown is data: report `blocked` or `unknown`. A missing proof is never a pass.
- LLM agents: the operator checkpoints in `CONTRIBUTING.md` ("Note to LLM Agents")
  apply to every change — credentials, network and release actions go to the
  operator first, with an explicit go before acting.
