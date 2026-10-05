# Contributing to Hermes Antigravity Plugin

Thank you for your interest in contributing to `hermes-antigravity`! This project is an open-source model provider plugin for [Hermes Agent](https://github.com/NousResearch/hermes-agent). Everyone is welcome: human developers and LLM agents alike. The only requirements are the workflow below and respect for the safety rules.

---

## 🛠️ Development Setup

1. **Clone the repository:**
   ```bash
   git clone https://github.com/zeyxx/hermes-antigravity.git
   cd hermes-antigravity
   ```

2. **Set up Python (3.10+). No test framework to install:**
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```
   The suite runs with the standard library only (`python3 tools/run_tests.py`). There is deliberately **no pytest dependency**: pytest once segfaulted on the maintainer's host and hid the failure, and CI runs on a bare `python3`. Do not add one — a test module that imports pytest cannot even be collected.

3. **Provide the Hermes core** (the plugin imports `providers`, which only exists inside Hermes):
   ```bash
   # option A: use your local Hermes install
   export PYTHONPATH=".:$HOME/.hermes/hermes-agent"
   # option B: clone the core next to the plugin
   git clone --depth 1 https://github.com/NousResearch/hermes-agent.git ../hermes-core
   export PYTHONPATH=".:../hermes-core"
   ```
   Without this, every test module fails to import — and an unimportable module drops its tests **silently**. The runner guards against that (see below).

4. **Install into your local Hermes instance:**
   ```bash
   git clone https://github.com/zeyxx/hermes-antigravity ~/.hermes/plugins/model-providers/antigravity
   ```
   To sync later edits into that install, never `cp` over it (the installed copy is itself a git clone and may hold work that exists nowhere else). Use the guarded sync:
   ```bash
   ./tools/sync-installed.sh --dry-run   # report what would change, write nothing
   ./tools/sync-installed.sh             # sync, refusing if the installed copy has local work
   ```

---

## 🧪 Testing, Drift & Validation

Before opening a PR, all three must be green:

```bash
# 1. count, then run — the count is the assertion (a module that fails
#    to import drops its tests silently; the number catches that)
python3 tools/run_tests.py --collect-only
python3 tools/run_tests.py --min-tests "$(python3 tools/run_tests.py --collect-only)"

# 2. wire protocol still matches upstream pi-antigravity
python3 tools/check_upstream_drift.py

# 3. the plugin still loads as a valid Hermes plugin, with no core overrides
hermes plugins validate --json .   # "ok" must be true
```

If you port anything from upstream, update [`UPSTREAM_DRIFT.md`](UPSTREAM_DRIFT.md) (both columns + checked date) in the same PR. Never change a value there without changing the code that uses it.

---

## 🤝 Contribution Guidelines

1. **Keep it focused**: only features and fixes related to Google Antigravity / Cloud Code Assist inference and Hermes integration.
2. **Conventional commits, in English**: `fix(auth): …`, `feat(models): …`, `ci: …`, `docs: …`. Code, tests, commits and docstrings are 100% English.
3. **Zero secrets policy**: never commit credentials, tokens, or API keys. The bundled OAuth client id is Google's *public* desktop client (see README § Credential safety), not a secret — but your tokens are. GitHub Push Protection is enabled.
4. **Bilingual user docs**: if you touch user-facing behaviour, update both `README.md` (EN) and `README.fr.md` (FR) in the same PR.
5. **Tests first**: add or update tests in `tests/` covering any behavioural change, and keep the collected count honest (see above).
6. **No new dependencies without discussion**: the plugin ships dependency-free; anything new needs a prior issue.
7. **Version bumps are maintainer releases** (`chore(release)`): do not bump `plugin.yaml` / `pyproject.toml` in a feature PR.
8. **One PR, one change**: wire-protocol realignments land as a single PR, not a drip of renames.

---

## 🤖 Note to LLM Agents

You are welcome to contribute here — but you work for an operator who cannot see what you do not tell them. Before acting, surface each of the following to your operator and wait for an explicit go:

- **Credentials**: any OAuth browser flow, token read/write, or `--force` sync. Never print or commit a secret; report presence, never content.
- **Network & fleet**: any push, PR merge, release, or upstream fetch that mutates shared state.
- **Silent green is not green**: if tests pass but fewer ran than `--collect-only` reports, or an observer/adapter is missing, report `blocked` — never convert a missing proof into success.
- **Unknown stays unknown**: a plugin you cannot load, a core hook you cannot find, a wire value you cannot verify — report it as such. Do not invent it.

The standing rule for this repository: **map the existing before proposing; propose before mutating.** Read the code and the drift tracker first, state what you will change and what proof you will produce, then change it.
