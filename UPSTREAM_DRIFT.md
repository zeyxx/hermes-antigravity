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

Two references, checked separately:

1. **Official CLI** (the fingerprint source): measure the live User-Agent of the
   `agy` binary (see "How the official CLI was measured" below) and note the
   version + build. This drives `CLI_VERSION` / `CLI_BUILD`.
2. **pi-antigravity** (the protocol reference): `git fetch` it and compare each
   protocol row below (scopes, redirect, model enums) against its TypeScript
   source.
3. On any mismatch: port the change, update the relevant columns and the
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

Three references, not two. **pi-antigravity** is the reference
*implementation* this port reimplements, but it is not the wire source of truth.
**Official CLI** is the binary Google ships, measured by capturing its live
User-Agent on the operator's host (see "How the official CLI was measured" below).
The port must track the official CLI; pi-antigravity is kept because its enum
table and envelope notes remain useful.

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

## TLS transport divergence (issue #35)

The wire constants above are only half the identity: the transport differs too.
`agy` is a Go binary; this port speaks TLS through Python's stdlib
`ssl`/OpenSSL. Their ClientHello (hence JA3) differs, and a JA3 is a cheap
server-side signal to correlate a non-official client.

Same-host capture, 2026-10-10, Linux x86-64, Python 3.14.7 / OpenSSL 3.5.8,
both dialing `daily-cloudcode-pa.googleapis.com`:

| Field | agy (Go) | urllib default | urllib + `tls_profile` |
|---|---|---|---|
| Cipher count | 13 | 17 | 12 |
| ALPN | `h2,http/1.1` | `http/1.1` | `h2,http/1.1` |
| Supported groups | `4588,4587,4589,29,23,24,25` | `4588,29,23,30,24,25,256,257` | same as default |
| JA3 MD5 | `03117a8ed39ef02427ebbc39f121275c` | `a1ebe7f90a577e9399eaa60be3c67721` | `e1c38f8d660e92ed4c7d7dfe75c91a1c` |

`tls_profile.py` aligns ALPN (`h2,http/1.1`) and narrows the cipher list toward
Go's, which is what stdlib permits. It **cannot** reach parity: Python's public
SSL API exposes neither the ordered supported-groups list (the Go list carries
the PQC hybrids `4587`/`4589`; Python's carries P-256/P-384 instead) nor the
extension order. Those two fields dominate the JA3, so an exact Go fingerprint
is not achievable in stdlib. The profile reduces the observable difference; it
does not erase it, and it does not change the ToS position (see README
disclaimer — the OAuth use itself is the violation).

`tests/test_tls_profile.py` pins the tuned profile with a live loopback capture,
so a Python/OpenSSL upgrade that silently changes the offered handshake fails
loudly instead of growing a new fingerprint unnoticed.

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
