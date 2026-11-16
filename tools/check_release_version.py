#!/usr/bin/env python3
"""Check that a release tag matches both version manifests."""

import re
import sys
from pathlib import Path

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError


def check(tag: str, root: Path) -> str:
    number = r"(?:0|[1-9][0-9]*)"
    match = re.fullmatch(rf"v({number}\.{number}\.{number})", tag)
    if not match:
        raise ValueError(f"Invalid release tag: {tag!r}")
    version = match.group(1)
    # Safe mode constructs only plain data, never arbitrary Python objects.
    manifest = YAML(typ="safe").load((root / "plugin.yaml").read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("version") != version:
        raise ValueError(f"plugin.yaml version does not match {tag}")
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
    project = re.search(r"(?ms)^\[project\]\s*$\n(.*?)(?=^\[|\Z)", pyproject)
    versions = re.findall(r'^version\s*=\s*["\']([0-9]+\.[0-9]+\.[0-9]+)["\']\s*$',
                          project.group(1), re.M) if project else []
    if versions != [version]:
        raise ValueError(f"pyproject.toml version does not match {tag}")
    return version


def main() -> int:
    if len(sys.argv) not in (2, 3):
        print("Usage: check_release_version.py TAG [ROOT]", file=sys.stderr)
        return 2
    try:
        version = check(sys.argv[1], Path(sys.argv[2]) if len(sys.argv) == 3 else Path.cwd())
    except (ValueError, OSError, YAMLError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"Release version {version} confirmed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
