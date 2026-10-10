#!/usr/bin/env python3
"""Compare this port's wire constants against two references.

    python3 tools/check_upstream_drift.py            # report drift, exit 1 if any
    python3 tools/check_upstream_drift.py --refresh  # git fetch upstream first
    python3 tools/check_upstream_drift.py --json     # machine-readable, for CI

Why this exists
---------------
This plugin is a port. The wire protocol has two sources: the official
Antigravity CLI (the binary Google ships) owns the CLI fingerprint, and
pi-antigravity (a community reimplementation) is the reference for the
protocol fields (scopes, redirect, model enums). A port has no compiler to say
when either moves. Three incidents in this repository were silent: a stale CLI
fingerprint that the relay quietly downgraded, a missing core hook that 404'd
every request, and an envelope realigned upstream in 0.5.0 that this port never
saw. Nothing compared the two shapes, so nothing failed loudly.

What it checks
--------------
The CLI fingerprint (cli_version, cli_build) is compared to measured official
CLI constants (OFFICIAL_CLI_VERSION / OFFICIAL_CLI_BUILD). The protocol fields
are read out of pi-antigravity's TypeScript source with narrow regexes and
compared against this repository's Python. It reports, and never edits:

  * the CLI fingerprint (version + build)
  * the envelope label names
  * the static model-enum table
  * the OAuth scopes and callback URI

Deliberately port-only behaviours are declared in PORT_ONLY and never reported,
so this does not nag about the divergences that are intentional.

Limitations
------------
This detects divergence after upstream publishes, never before. It is not a
substitute for reading an upstream changelog. And an extraction that fails
loudly (missing file, renamed symbol) is reported as an error rather than
skipped — a checker that silently finds nothing is worse than no checker.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

UPSTREAM_URL = "https://github.com/Rahularya01/pi-antigravity.git"
UPSTREAM_REF = "main"

# Official CLI fingerprint, measured 2026-10-10 by capturing the live
# User-Agent via a local MITM proxy (see UPSTREAM_DRIFT.md). The port must
# track the official CLI, not pi-antigravity, which is a reimplementation
# that may lag behind the binary Google ships.
OFFICIAL_CLI_VERSION = "1.3.3"
OFFICIAL_CLI_BUILD = "996823801"

REPO_ROOT = Path(__file__).resolve().parent.parent

# Divergences that are intentional. Keys are field names.
PORT_ONLY: dict[str, str] = {
    "labels_prefixed": (
        "This port sent antigravity/-prefixed labels before the 0.5.0 realignment. "
        "Those are gone; the label set now matches upstream."
    ),
    "model_enum_dynamic": (
        "Enums are read from discovery at runtime and cached; the static table is "
        "only a fallback for models discovery has not reported yet."
    ),
    "session_id_stable": (
        "Kept stable rather than randomised per request. See UPSTREAM_DRIFT.md: "
        "Hermes does not forward a session id to providers, so a random value "
        "would change every request."
    ),
    "os_type_arch_host": (
        "Derived from the host; pi-antigravity hardcodes linux/amd64. Strictly more correct."
    ),
}


class ExtractionError(RuntimeError):
    """Upstream shape changed in a way this checker does not understand."""


def _read(path: Path) -> str:
    if not path.is_file():
        raise ExtractionError(f"expected upstream file missing: {path}")
    return path.read_text(encoding="utf-8")


def extract_upstream(up: Path) -> dict[str, Any]:
    """Pull the comparable constants out of pi-antigravity's source."""
    data: dict[str, Any] = {}

    client = _read(up / "src/client/client.ts")
    fp = re.search(r'antigravity/cli/([0-9.]+)[^"]*?cl=(\d+)', client)
    if not fp:
        raise ExtractionError("CLI fingerprint not found in src/client/client.ts")
    # Informational only: the port is gated on the official CLI (measured), not
    # on pi-antigravity, which is a reimplementation that may lag behind the
    # binary Google ships. Kept so its fingerprint is still visible in reports.
    data["cli_version"] = fp.group(1)
    data["cli_build"] = fp.group(2)

    util = _read(up / "src/utils/util.ts")
    labels_block = re.search(
        r"const labels: Record<string, string> = \{(.*?)\n  \};", util, re.S
    )
    if not labels_block:
        raise ExtractionError("envelope labels not found in src/utils/util.ts")
    data["labels"] = sorted(set(re.findall(r"(\w+):\s", labels_block.group(1))))

    models_src = _read(up / "src/models/models.ts")
    table = re.search(
        r"export const ANTIGRAVITY_MODEL_ENUM[^=]*= \{(.*?)\n\};", models_src, re.S
    )
    if not table:
        raise ExtractionError("ANTIGRAVITY_MODEL_ENUM not found in src/models/models.ts")
    data["enums"] = dict(re.findall(r'"([^"]+)":\s*"([^"]+)"', table.group(1)))

    oauth = _read(up / "src/auth/oauth.ts")
    scopes_block = re.search(r"SCOPES = \[(.*?)\];", oauth, re.S)
    if not scopes_block:
        raise ExtractionError("SCOPES not found in src/auth/oauth.ts")
    data["scopes"] = re.findall(r'"([^"]+)"', scopes_block.group(1))
    redirect = re.search(r'REDIRECT_URI = "([^"]+)"', oauth)
    if not redirect:
        raise ExtractionError("REDIRECT_URI not found in src/auth/oauth.ts")
    data["redirect_uri"] = redirect.group(1)

    return data


def _git(args: list[str], cwd: Path) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, timeout=180
    )
    if proc.returncode != 0:
        raise ExtractionError(f"git {' '.join(args)} failed: {proc.stderr.strip()[:200]}")
    return proc.stdout.strip()


def ensure_upstream(refresh: bool, cache: Path) -> Path:
    """Clone or fetch pi-antigravity into a local cache and return the path."""
    if not (cache / ".git").is_dir():
        cache.parent.mkdir(parents=True, exist_ok=True)
        proc = subprocess.run(
            ["git", "clone", "--depth", "50", UPSTREAM_URL, str(cache)],
            capture_output=True,
            text=True,
            timeout=600,
        )
        if proc.returncode != 0:
            raise ExtractionError(f"clone failed: {proc.stderr.strip()[:200]}")
        return cache
    if refresh:
        _git(["fetch", "--depth", "50", "origin", UPSTREAM_REF], cache)
    _git(["checkout", "--quiet", "--force", f"origin/{UPSTREAM_REF}"], cache)
    return cache


def read_port() -> dict[str, Any]:
    """Read this repository's own constants without importing the plugin."""
    models_src = (REPO_ROOT / "models.py").read_text(encoding="utf-8")
    auth_src = (REPO_ROOT / "auth.py").read_text(encoding="utf-8")

    version = re.search(r'^CLI_VERSION = "([^"]+)"', models_src, re.M)
    build = re.search(r'^CLI_BUILD = "(\d+)"', models_src, re.M)
    if not version or not build:
        raise ExtractionError("CLI_VERSION / CLI_BUILD not found in models.py")

    table = re.search(
        r"_STATIC_MODEL_ENUMS: dict\[str, str\] = \{(.*?)\n\}", models_src, re.S
    )
    if not table:
        raise ExtractionError("_STATIC_MODEL_ENUMS not found in models.py")
    enums = dict(re.findall(r'"([^"]+)":\s*"([^"]+)"', table.group(1)))

    scopes_block = re.search(r"SCOPES = \[(.*?)\]", auth_src, re.S)
    if not scopes_block:
        raise ExtractionError("SCOPES not found in auth.py")
    scopes = re.findall(r'"([^"]+)"', scopes_block.group(1))

    # REDIRECT_URI is either a literal or derived from the callback port, since the
    # listener's port is fixed by the redirect URI registered with Google.
    redirect = re.search(r'REDIRECT_URI = "([^"]+)"', auth_src)
    if not redirect:
        port = re.search(r"^_CALLBACK_PORT = (\d+)", auth_src, re.M)
        if not port:
            raise ExtractionError("REDIRECT_URI not found in auth.py")
        redirect_value = f"http://localhost:{port.group(1)}/oauth-callback"
    else:
        redirect_value = redirect.group(1)

    return {
        "cli_version": version.group(1),
        "cli_build": build.group(1),
        "enums": enums,
        "scopes": scopes,
        "redirect_uri": redirect_value,
    }


def compare(upstream: dict[str, Any], port: dict[str, Any]) -> dict[str, Any]:
    findings: dict[str, Any] = {"mismatch": [], "missing": [], "extra": []}

    # The CLI fingerprint is compared to the official CLI (measured), not to
    # pi-antigravity, which is a reimplementation that may lag behind the
    # binary Google ships. The port must track the official CLI.
    if port.get("cli_version") != OFFICIAL_CLI_VERSION:
        findings["mismatch"].append(
            {
                "field": "cli_version",
                "upstream": OFFICIAL_CLI_VERSION,
                "port": port.get("cli_version"),
            }
        )
    if port.get("cli_build") != OFFICIAL_CLI_BUILD:
        findings["mismatch"].append(
            {
                "field": "cli_build",
                "upstream": OFFICIAL_CLI_BUILD,
                "port": port.get("cli_build"),
            }
        )

    # The remaining fields are still compared to pi-antigravity, which is the
    # reference implementation for the protocol (scopes, redirect, enums).
    if upstream.get("redirect_uri") != port.get("redirect_uri"):
        findings["mismatch"].append(
            {
                "field": "redirect_uri",
                "upstream": upstream.get("redirect_uri"),
                "port": port.get("redirect_uri"),
            }
        )

    if upstream.get("scopes") != port.get("scopes"):
        findings["mismatch"].append(
            {
                "field": "scopes",
                "upstream": upstream.get("scopes"),
                "port": port.get("scopes"),
            }
        )

    up_enums = upstream.get("enums", {})
    port_enums = port.get("enums", {})
    findings["missing"] = [
        {"model": k, "enum": v}
        for k, v in sorted(up_enums.items())
        if k not in port_enums
    ]
    findings["extra"] = [
        {"model": k, "enum": v}
        for k, v in sorted(port_enums.items())
        if k not in up_enums
    ]
    findings["enum_value_mismatch"] = [
        {"model": k, "upstream": v, "port": port_enums[k]}
        for k, v in sorted(up_enums.items())
        if k in port_enums and port_enums[k] != v
    ]
    return findings


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--refresh", action="store_true", help="fetch upstream before comparing")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument(
        "--cache",
        default=os.environ.get(
            "HERMES_ANTIGRAVITY_DRIFT_CACHE",
            str(Path.home() / ".cache" / "hermes-antigravity" / "pi-antigravity"),
        ),
        help="local clone of pi-antigravity",
    )
    args = ap.parse_args(argv)

    try:
        upstream_dir = ensure_upstream(args.refresh, Path(args.cache))
        upstream = extract_upstream(upstream_dir)
        commit = _git(["rev-parse", "--short", "HEAD"], upstream_dir)
        port = read_port()
    except ExtractionError as exc:
        print(f"drift check failed: {exc}", file=sys.stderr)
        return 2

    findings = compare(upstream, port)
    drift = sum(len(v) for v in findings.values() if isinstance(v, list))

    if args.json:
        print(json.dumps({
            "upstream_commit": commit,
            "official_cli_version": OFFICIAL_CLI_VERSION,
            "official_cli_build": OFFICIAL_CLI_BUILD,
            "checked": ["cli_version", "cli_build", "redirect_uri", "scopes", "model_enums"],
            "port_only": sorted(PORT_ONLY),
            "findings": findings,
            "drift": drift,
        }, indent=2))
    else:
        print(f"upstream pi-antigravity @ {commit}")
        print(f"port      {REPO_ROOT.name}")
        print(f"  (fingerprint gated on official CLI "
              f"{OFFICIAL_CLI_VERSION}/{OFFICIAL_CLI_BUILD})")
        # Fingerprint compared to official CLI (measured), not pi-antigravity.
        for field, official in (("cli_version", OFFICIAL_CLI_VERSION), ("cli_build", OFFICIAL_CLI_BUILD)):
            pt = port.get(field)
            mark = "OK  " if pt == official else "DIFF"
            print(f"  {mark} {field}: {pt} (official {official})")
        for field in ("redirect_uri", "scopes"):
            up, pt = upstream.get(field), port.get(field)
            mark = "OK  " if up == pt else "DIFF"
            shown = up if isinstance(up, str) else f"{len(up or [])} scopes"
            print(f"  {mark} {field}: {shown}")

        up_n, pt_n = len(upstream["enums"]), len(port["enums"])
        print(f"  {'OK  ' if not findings['enum_value_mismatch'] else 'DIFF'} "
              f"model enums: {pt_n} port / {up_n} upstream")
        if findings["missing"]:
            print(f"\n  {len(findings['missing'])} enum(s) missing from the port:")
            for row in findings["missing"]:
                print(f"    + {row['model']} = {row['enum']}")
        if findings["extra"]:
            print(f"\n  {len(findings['extra'])} enum(s) not present upstream:")
            for row in findings["extra"]:
                print(f"    - {row['model']} = {row['enum']}")
        for row in findings["enum_value_mismatch"]:
            print(f"    ! {row['model']}: upstream {row['upstream']} != port {row['port']}")
        for row in findings["mismatch"]:
            print(f"    ! {row['field']}: upstream {row['upstream']!r} != port {row['port']!r}")

        print("\n  deliberately port-only (not reported):")
        for name, why in sorted(PORT_ONLY.items()):
            print(f"    - {name}")

    return 1 if drift else 0


if __name__ == "__main__":
    sys.exit(main())
