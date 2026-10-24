# Upstream drift tracker

This plugin speaks Google's Cloud Code Assist API directly. Its references are
the **official Antigravity CLI** (wire fingerprint, measured live) and the
**live relay responses** (model catalog, quota, retirement tags).
[`Rahularya01/pi-antigravity`](https://github.com/Rahularya01/pi-antigravity) (MIT)
pioneered the reverse-engineering this plugin originated from; it is kept as a
secondary cross-check in `tools/check_upstream_drift.py`, not as the tracked
reference — the plugin no longer follows pi-antigravity's advances.

Nothing compiles against Google to tell it when the relay moves. Every field below
was measured at some point and is now only as correct as the day it was measured. Three separate incidents
in this repository were **silent**: the relay degraded, or 404'd, without raising.

This file is the defence. One row per wire field, with the value observed on each side and
the date it was checked. **A test in `tests/test_models.py` asserts the port side**, so an
upstream bump shows up as a red test rather than a silent degradation.

## How to use this

Two references, checked separately:

1. **Official CLI + live relay** (primary): measure the live User-Agent of the
   `agy` binary (see "How the official CLI was measured" below) and note the
   version + build; read the model catalog, quota and retirement tags from live
   `fetchAvailableModels` responses. These drive `CLI_VERSION` / `CLI_BUILD`
   and discovery.
2. **pi-antigravity** (secondary cross-check): `git fetch` it and compare each
   protocol row below (scopes, redirect, model enums) against its TypeScript
   source. A mismatch here alone does not move the plugin — the official CLI
   and the live relay decide.
3. On any mismatch against the primary reference: port the change, update the relevant columns and the
   `checked` date, and open a PR. Never change a value here without changing the
   code that uses it.
4. Rows marked **port only** are deliberately absent upstream. Do not "fix" them
   by copying upstream — they exist because Hermes passes something Pi does not.

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

Two references, not one. **Official CLI + live relay** are the source of truth
the plugin tracks: the binary Google ships, measured by capturing its live
User-Agent on the operator's host (see "How the official CLI was measured" below),
plus the live `fetchAvailableModels` responses. **pi-antigravity** is the
historical implementation this plugin originated from; its enum table and
envelope notes remain a useful cross-check, which is why the table keeps its
column and the checker keeps comparing against it.

| Field | Official CLI | pi-antigravity | hermes-antigravity | Since | Checked |
|---|---|---|---|---|---|
| CLI version | `1.3.3` | `1.2.4` | `1.3.3` | **measured** (2026-10-10) | 2026-10-10 |
| CLI build (`cl=`) | `996823801` | `982146307` | `996823801` | **measured** (2026-10-10) | 2026-10-10 |
| User-Agent shape | `antigravity/cli/<v> (aidev_client; os_type=; arch=; cl=; auth_method=consumer)` | `antigravity/cli/<v> (aidev_client; os_type=; arch=; cl=; auth_method=consumer)` | same, host-derived `os_type`/`arch` | **0.5.0** | 2026-10-10 |
| OAuth scopes | 6 scopes | 6 scopes | same 6, same order | 0.1.0 | 2026-10-10 |
| OAuth callback | `http://localhost:51121/oauth-callback` | `http://localhost:51121/oauth-callback` | same | 0.1.0 | 2026-10-10 |
| OAuth client id | public desktop client | public desktop client | byte-identical | 0.1.0 | 2026-10-10 |
| Endpoint order | `daily-cloudcode-pa`, `…sandbox…`, `cloudcode-pa` | `daily-cloudcode-pa`, `…sandbox…`, `cloudcode-pa` | same | 0.1.0 | 2026-10-10 |
| `requestId` | `agent/<conv>/<ms>/<traj>/<step>` | `agent/<conv>/<ms>/<traj>/<step>` | `<traj>-<reqIdx>-<step>` | **0.5.0** | 2026-10-10 |
| `sessionId` | random int64, **per request** | random int64, **per request** | stable `conversationId` | **0.5.0** | 2026-10-10 |
| Label `cli-version` | absent from labels | absent from labels | `antigravity/cli-version` | — | 2026-10-10 |
| Label `last_step_index` | present | present | `antigravity/last-step-index` | **0.5.0** | 2026-10-10 |
| Label `request_id` | `<traj>-<requestIndex>` | `<traj>-<requestIndex>` | absent | **0.5.0** | 2026-10-10 |
| Label `trajectory_id` | present | present | absent | **0.5.0** | 2026-10-10 |
| Label `model_enum` | present | present | `antigravity/model` | **0.5.0** | 2026-10-10 |
| Label `used_claude` | `"true"`/`"false"` always | `"true"`/`"false"` always | `antigravity/provider` only when Claude | **0.5.0** | 2026-10-10 |
| Label `used_claude_conservative` | `"true"`/`"false"` always | `"true"`/`"false"` always | absent | **0.5.0** | 2026-10-10 |
| Label `used_non_gemini_model` | `"true"`/`"false"` always | `"true"`/`"false"` always | absent | **0.5.0** | 2026-10-10 |
| Label `last_execution_id` | present when `step > 1` | present when `step > 1` | absent | **0.9.0** | 2026-10-10 |
| Trajectory seed | `session:<id>` authoritative | `session:<id>` authoritative | `session:<id>` when forwarded, else first message | 0.8.1 / #12 | 2026-10-10 |
| Text sanitising | `sanitizeText` | `sanitizeText` | same | 0.9.0 / #9 | 2026-10-10 |
| `$ref` schema resolution | local pointers | local pointers | **port only** — see below | — | 2026-10-10 |
| Quota failover | `failoverToNextAccount` | `failoverToNextAccount` | `next_untried_account` | 0.8.0 / #13 | 2026-10-10 |
| Per-model quota (`quotaInfo`) | `remainingFraction`, `resetTime` | `remainingFraction`, `resetTime` | parsed in `quota.py`, measured via `fetch_account_quota` | 0.10.0 | 2026-10-10 |
| Aggregate quota (`retrieveUserQuotaSummary`) | groups/buckets, 403 on free-tier | groups/buckets, 403 on free-tier | `quota.parse_quota_groups`, best-effort | 0.10.0 | 2026-10-10 |
| Plan tier (`paidTier` over `currentTier`) | `planLabel` | `planLabel` | `quota.plan_label` | 0.10.0 | 2026-10-10 |
| Model retirement tag (`tagTitle`) | `Leaving Soon` | `Leaving Soon` | filtered in `fetch_available_models` | 1.5.0 | 2026-10-10 |
| Account/tier-unavailable tag (`tagTitle`) | `Unavailable` (e.g. `gemini-4-argon-disabled`, gated behind paid AI Pro) | not observed | filtered in `fetch_available_models` (added 2026-10-23) | 1.5.0 | 2026-10-23 |

## How the official CLI was measured

The official CLI fingerprint was captured on 2026-10-10 on the operator's
Linux host (x86-64) by running `agy models` through a local MITM proxy that
terminates TLS with a self-signed CA injected via `SSL_CERT_FILE`. The proxy
logs only the request line and headers (Authorization value withheld, kind
recorded) and returns a stub response; nothing is forwarded to Google and no
inference request is completed.

Captured User-Agent (10 requests, all identical):

```text
antigravity/cli/1.3.3 (aidev_client; os_type=linux; arch=amd64; cl=996823801; auth_method=consumer)
```

Endpoints observed: `v1internal:loadCodeAssist`, `v1internal:listExperiments`
on `daily-cloudcode-pa.googleapis.com`. No proprietary headers beyond the
User-Agent were observed (`X-Goog-Api-Client` absent).

The binary at `/home/user/.local/bin/agy` (214 MB, stripped Go ELF) also
contains the string `996823801` repeated in build metadata
(`googlefile:/google_src/files/996823801/`, `changelist 996823801`), and
reports internal version `1.3.2.1` while `--version` prints `1.3.3`.

## TLS transport divergence (issue #35) — tuning removed

The wire constants above are only half the identity: the transport differs too.
`agy` is a Go binary; this port speaks TLS through Python's stdlib
`ssl`/OpenSSL with a **default handshake**. Their ClientHello (hence JA3)
differs, and a JA3 is a cheap server-side signal to correlate a non-official
client. That gap is now **accepted, not narrowed**: probed 2026-10-14, the
relay answers 200 (`PONG` on `streamGenerateContent`) over a default stdlib
handshake, so `tls_profile.py` was deleted and the tuning tests replaced by
neutral-transport guards (`tests/test_tls_profile.py` pins the absence of
tuning — any reintroduced knob fails loudly).

Same-host capture, 2026-10-10, Linux x86-64, Python 3.14.7 / OpenSSL 3.5.8,
both dialing `daily-cloudcode-pa.googleapis.com` (kept as reference):

| Field | agy (Go) | urllib default (= production since 2026-10-14) |
|---|---|---|
| Cipher count | 13 | 17 |
| ALPN | (none) | `http/1.1` |
| Supported groups | `4588,4587,4589,29,23,24,25` | `4588,29,23,30,24,25,256,257` |
| JA3 MD5 | `9b7dcdf3f997f1fb7b4409c94cb7ef36` | `a1ebe7f90a577e9399eaa60be3c67721` |

**Critical correction (dhaern review):** agy sends **no ALPN** toward
`daily-cloudcode-pa.googleapis.com`. The earlier measurement of `h2,http/1.1` was
from agy talking to `antigravity-unleash.goog` and `play.googleapis.com`, not the
inference host.

The removed `tls_profile.py` used to narrow the cipher list toward Go's
(13 suites, Go order) and drop extensions agy does not send. It **could not**
reach parity anyway: Python's public SSL API exposes neither the ordered
supported-groups list (Go carries the PQC hybrids `4587`/`4589`; Python's
carries P-256/P-384 instead) nor the extension order. Those two fields
dominate the JA3, so an exact Go fingerprint was never achievable in stdlib —
one more reason the tuning was not worth its mimicry cost. The remaining gap
does not change the ToS position (see README disclaimer — the OAuth use
itself is the violation).

`tests/test_tls_profile.py` now pins the neutral transport (no tuning knob
may exist), and `tools/transport_check.py` measures the production default
handshake against the contract below.

## Transport parity contract

`transport_contract.json` defines 7 TLS axes (JA3, ALPN, cipher count/order,
supported groups, extension order, EC point formats), each with an expected
value measured from `agy`, a tolerance, and a weight. `tools/transport_check.py`
measures the plugin's active transport and compares against the contract.

Two modes:
- **permissive** — log divergence, never block (default). Always operational.
- **strict** — fail-closed. Exit 1 on any axis beyond tolerance.

Current status (stdlib backend):
- 1/7 axes matched (ALPN only)
- 6/7 diverged — all reachable by a Go sidecar with `utls` replay

`utls` (refraction-networking/utls) can replay a captured `agy` ClientHello
byte-for-byte, achieving 100% JA3 parity. A Go sidecar using `utls` is the
path to full transport parity; stdlib cannot reach it.

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
