#!/usr/bin/env python3
"""Best-effort probe of the official `agy` binary for its CLI fingerprint.

This does not connect to Google. It only inspects the local binary strings.
If the fingerprint is not embedded in plain text (newer builds), use a
local MITM capture instead and update OFFICIAL_CLI_VERSION / OFFICIAL_CLI_BUILD
in models.py and tools/check_upstream_drift.py manually.
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
from pathlib import Path

DEFAULT_BINARY = shutil.which("agy") or "/home/user/.local/bin/agy"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--binary", default=DEFAULT_BINARY)
    args = ap.parse_args()

    binary = Path(args.binary)
    if not binary.is_file():
        print(f"binary not found: {binary}", file=__import__("sys").stderr)
        return 2

    try:
        out = subprocess.run(
            ["strings", str(binary)], capture_output=True, text=True, timeout=120
        ).stdout
    except Exception as exc:
        print(f"strings failed: {exc}", file=__import__("sys").stderr)
        return 2

    versions = sorted(set(re.findall(r"\b1\.\d+\.\d+\b", out)))
    builds = sorted(set(re.findall(r"\b\d{9}\b", out)))
    ua = [line for line in out.splitlines() if "antigravity/cli/" in line]

    print(f"binary: {binary}")
    print(f"candidate versions: {versions[-5:]}")
    print(f"candidate 9-digit builds: {builds[-10:]}")
    if ua:
        print("UA-like strings:")
        for u in ua[:5]:
            print(f"  {u}")
    else:
        print("no plain-text 'antigravity/cli/' UA found (probe via MITM required)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
