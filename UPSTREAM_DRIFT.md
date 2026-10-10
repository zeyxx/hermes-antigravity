# Upstream drift tracker

This plugin is a **port**. Its wire protocol, envelope and credentials are owned by
[`Rahularya01/pi-antigravity`](https://github.com/Rahularya01/pi-antigravity) (MIT), which
reverse-engineered them from the official Antigravity desktop CLI.

A port has no compiler to tell it when upstream moves. Every field below was copied at
some point and is now only as correct as the day it was copied. Three separate incidents
in this repository were **silent**: the relay degraded, or 404'd, without raising.

This file is the defence. One row per wire field, with the value observed on each side and
the date it was checked. **A test in `tests/test_models.py` asserts the port side**, so an
upstream bump shows up as a red test rather than a silent degradation.

## How to use this

1. `git fetch` pi-antigravity, note its version and commit date.
2. Compare each row below against its upstream source.
3. On any mismatch: port the change, update **both** columns and the `checked` date, and
   open a PR. Never change a value here without changing the code that uses it.
4. Rows marked **port only** are deliberately absent upstream. Do not "fix" them by
   copying upstream — they exist because Hermes passes something Pi does not.

### Finding *when* a value moved

The `since` column is not a guess. To date a change, bisect upstream rather than reading
its changelog:

```bash
git log --oneline -S '<exact old value or symbol>' -- src/   # commits that touched it
git log --oneline --reverse -S '<new symbol>' -- src/ | head -1   # when it appeared
git show <commit>^:<file> | grep '<symbol>'                  # confirm it was absent before
```

This matters: a changelog entry credits a *release*, but the change lands in a specific
commit. Reading the changelog dated the envelope realignment to 0.9.0; `git log -S` put
it at **0.5.0** — six releases earlier. A changelog-only check reports "we're slightly
behind"; a commit-level check reports "we never saw the hub move".

Always confirm with `git show <commit>^` that the value was genuinely absent before, not
merely renamed in the same diff.

## Status

| Field | pi-antigravity | hermes-antigravity | Since | Checked |
|---|---|---|---|---|
| CLI version | `1.2.4` | `1.2.4` | 0.9.0 / #10 | 2026-10-05 |
| CLI build (`cl=`) | `982146307` | `982146307` | 0.9.0 / #10 | 2026-10-05 |
| User-Agent shape | `antigravity/cli/<v> (aidev_client; os_type=; arch=; cl=; auth_method=consumer)` | same, host-derived `os_type`/`arch` | **0.5.0** | 2026-10-05 |
| OAuth scopes | 6 scopes | same 6, same order | 0.1.0 | 2026-10-05 |
| OAuth callback | `http://localhost:51121/oauth-callback` | same | 0.1.0 | 2026-10-05 |
| OAuth client id | public desktop client | byte-identical | 0.1.0 | 2026-10-05 |
| Endpoint order | `daily-cloudcode-pa`, `…sandbox…`, `cloudcode-pa` | same | 0.1.0 | 2026-10-05 |
| `requestId` | `agent/<conv>/<ms>/<traj>/<step>` | `<traj>-<reqIdx>-<step>` | **0.5.0** | 2026-10-05 |
| `sessionId` | random int64, **per request** | stable `conversationId` | **0.5.0** | 2026-10-05 |
| Label `cli-version` | absent from labels (version is in the User-Agent) | `antigravity/cli-version` | — | 2026-10-05 |
| Label `last_step_index` | present | `antigravity/last-step-index` | **0.5.0** | 2026-10-05 |
| Label `request_id` | `<traj>-<requestIndex>` | absent | **0.5.0** | 2026-10-05 |
| Label `trajectory_id` | present | absent | **0.5.0** | 2026-10-05 |
| Label `model_enum` | present (via `getModelEnum`) | `antigravity/model` | **0.5.0** | 2026-10-05 |
| Label `used_claude` | `"true"`/`"false"` always | `antigravity/provider` only when Claude | **0.5.0** | 2026-10-05 |
| Label `used_claude_conservative` | `"true"`/`"false"` always | absent | **0.5.0** | 2026-10-05 |
| Label `used_non_gemini_model` | `"true"`/`"false"` always | absent | **0.5.0** | 2026-10-05 |
| Label `last_execution_id` | present when `step > 1`, stable per step | absent | **0.9.0** | 2026-10-05 |
| Trajectory seed | `session:<id>` authoritative | `session:<id>` when forwarded, else first message | 0.8.1 / #12 | 2026-10-05 |
| Text sanitising | `sanitizeText`, unpaired surrogates only | same | 0.9.0 / #9 | 2026-10-05 |
| `$ref` schema resolution | local pointers, cyclic-safe | **port only** — see below | — | 2026-10-05 |
| Quota failover | `failoverToNextAccount` | `next_untried_account` | 0.8.0 / #13 | 2026-10-05 |
| Per-model quota (`quotaInfo`) | `remainingFraction`, `resetTime` | parsed in `quota.py`, measured via `fetch_account_quota` | 0.10.0 | 2026-10-10 |
| Aggregate quota (`retrieveUserQuotaSummary`) | groups/buckets, 403 on free-tier | `quota.parse_quota_groups`, best-effort | 0.10.0 | 2026-10-10 |
| Plan tier (`paidTier` over `currentTier`) | `planLabel` | `quota.plan_label` | 0.10.0 | 2026-10-10 |

## Known divergences (read before porting)

**The envelope was realigned upstream in 0.5.0 (`ac1a393`) and this port never caught
up.** Every field marked **0.5.0** above is stale. Upstream moved from prefixed keys
(`antigravity/step`) to unprefixed ones (`last_step_index`, `trajectory_id`), changed
`requestId` to a path-shaped `agent/…` form, made `sessionId` a fresh random int64 per
request rather than a stable conversation id, and added `last_execution_id` on later turns
(0.8.1). The commit message was "align generate requests with the current Antigravity hub
client" — i.e. the hub changed, and the client followed.

This is **six releases old**, not one: the gap was invisible because nothing compared the
two shapes. That is the failure mode this file was created for.

That is a single coherent change and it should land as **one PR**, not a drip of label
renames. Until it does, this port is sending an envelope that current upstream no longer
sends. Tests still pass because they assert our shape, not agreement with upstream — which
is the whole reason this file exists.

## Deliberately port-only

| Behaviour | Why |
|---|---|
| `$ref` JSON Schema resolution | Pi resolves local schema pointers before serialising. Hermes passes tool schemas straight through, so the plugin sanitises in place instead. Do not port Pi's resolver without first checking whether Hermes ever emits `$ref`. |
| Stable `sessionId` | Pi mints a fresh id per request. Hermes threads a session through the whole call, so a stable id gives better grouping. Revisit if per-request is ever shown to be required. |
| Host-derived `os_type`/`arch` | Pi hardcodes `linux`/`amd64`; this port reports the real host. Strictly more correct. |

## Verification

```bash
# the port side is asserted; a red test means upstream moved or someone edited a value here
export PYTHONPATH=".:$HOME/.hermes/hermes-agent"
python3 tools/run_tests.py --min-tests "$(python3 tools/run_tests.py --collect-only)"
```

Tested against **pi-antigravity 0.9.0** (`a3d8cab`, 2026-09-30).
