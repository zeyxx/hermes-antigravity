#!/usr/bin/env python3
"""Check the port's official Antigravity CLI fingerprint against measured constants.

This plugin tracks the official `agy` CLI fingerprint (version + build), which is
measured from the binary Google ships. It does not compare against
pi-antigravity anymore: that project was a historical reference and is no longer
the source of truth (see README / UPSTREAM_DRIFT.md).

Usage:
    python3 tools/check_upstream_drift.py            # report drift, exit 1 if any
    python3 tools/check_upstream_drift.py --json     # machine-readable, for CI
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent

# Official CLI fingerprint, measured from the `agy` binary. Bump in lockstep
# with models.py when the official CLI moves.
OFFICIAL_CLI_VERSION = "1.3.3"
OFFICIAL_CLI_BUILD = "996823801"

PORT_ONLY: dict[str, str] = {}


def read_port() -> dict[str, Any]:
    models_src = (REPO_ROOT / "models.py").read_text(encoding="utf-8")
    version = re.search(r'^CLI_VERSION = "([^"]+)"', models_src, re.M)
    build = re.search(r'^CLI_BUILD = "(\d+)"', models_src, re.M)
    if not version or not build:
        raise RuntimeError("CLI_VERSION / CLI_BUILD not found in models.py")
    return {"cli_version": version.group(1), "cli_build": build.group(1)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--refresh", action="store_true", help="(no-op, kept for compatibility)")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args(argv)

    port = read_port()
    findings: list[str] = []
    if port["cli_version"] != OFFICIAL_CLI_VERSION:
        findings.append(f"cli_version: port {port['cli_version']} != official {OFFICIAL_CLI_VERSION}")
    if port["cli_build"] != OFFICIAL_CLI_BUILD:
        findings.append(f"cli_build: port {port['cli_build']} != official {OFFICIAL_CLI_BUILD}")

    if args.json:
        print(json.dumps({
            "official_cli_version": OFFICIAL_CLI_VERSION,
            "official_cli_build": OFFICIAL_CLI_BUILD,
            "port": port,
            "findings": findings,
            "drift": bool(findings),
        }, indent=2))
    else:
        print(f"port      {REPO_ROOT.name}")
        print(f"  (fingerprint gated on official CLI {OFFICIAL_CLI_VERSION}/{OFFICIAL_CLI_BUILD})")
        for field, official in (("cli_version", OFFICIAL_CLI_VERSION), ("cli_build", OFFICIAL_CLI_BUILD)):
            pt = port[field]
            mark = "OK  " if pt == official else "DIFF"
            print(f"  {mark} {field}: {pt} (official {official})")
        if findings:
            print("\n  drift detected:")
            for f in findings:
                print(f"    ! {f}")

    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
