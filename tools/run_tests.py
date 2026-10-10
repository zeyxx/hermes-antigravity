#!/usr/bin/env python3
"""Run the plugin's test suite and fail if fewer tests ran than expected.

    python3 tools/run_tests.py
    python3 tools/run_tests.py --min-tests 50

Why not pytest
--------------
This plugin is a Hermes model-provider module: its modules import `providers`,
which only exists inside a Hermes install. pytest collects fixtures this
project does not define and cannot run these modules standalone, so the suite is
executed by importing the test modules and calling each test function. That is
also why a pytest segfault on the maintainer's host went unnoticed as a CI-shaped
problem — there was no CI.

Why --min-tests
---------------
A test module that fails to import drops its tests silently. The suite then
still reports green, just smaller. That is not hypothetical: a missing
`HERMES_ROOT` import took the run from 50 tests to 12 without a single failure,
which is how a stacked branch would have shipped broken.

So the expected count is collected first, then asserted after the run.
"""
from __future__ import annotations

import argparse
import importlib
import inspect
import os
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent

MODULES = (
    "tests.test_translator",
    "tests.test_models",
    "tests.test_quota",
    "tests.test_accounts",
    "tests.test_auth",
    "tests.test_client",
    "tests.test_provider_registration",
    "tests.test_tls_profile",
)


def _collect_callables(module_name: str, module: Any) -> list[tuple[str, Any]]:
    """Yield (label, callable) for module-level and class-level test functions.

    Class-based tests matter: `test_accounts.py` groups its registry tests in
    `TestAntigravityAccountRegistry` and `TestEdgeCases` (pytest `Test*`
    classes, not `test_*` globals). A runner that only walks module globals
    skips those whole classes without a word. That is silent coverage loss,
    which is the failure mode this runner exists to prevent.
    """
    found: list[tuple[str, Any]] = []
    for attr in sorted(dir(module)):
        obj = getattr(module, attr)
        if inspect.isclass(obj) and attr.startswith("Test") and obj.__module__ == module.__name__:
            instance = obj()
            for meth in sorted(dir(obj)):
                if not meth.startswith("test_"):
                    continue
                fn = getattr(instance, meth)
                if callable(fn):
                    found.append((f"{module_name}::{attr}::{meth}", fn))
        elif attr.startswith("test_") and callable(obj):
            found.append((f"{module_name}::{attr}", obj))
    return found


def _ensure_paths() -> None:
    # Auto-discover Hermes core install if available and not already in sys.path
    hermes_core = os.environ.get("HERMES_CORE")
    if hermes_core and Path(hermes_core).is_dir():
        if str(hermes_core) not in sys.path:
            sys.path.insert(0, str(hermes_core))
    else:
        default_core = Path.home() / ".hermes" / "hermes-agent"
        if default_core.is_dir() and str(default_core) not in sys.path:
            sys.path.insert(0, str(default_core))

    # Hermes' plugin loader re-execs this process through runpy, which REPLACES
    # sys.path and drops the plugin's own directory. Re-anchor it on every run so
    # `import accounts` keeps resolving after any reload.
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))


def collect() -> tuple[int, list[str]]:
    """Count test callables across importable modules, classes included."""
    _ensure_paths()
    total = 0
    broken: list[str] = []
    for name in MODULES:
        try:
            mod = importlib.import_module(name)
        except Exception as exc:
            broken.append(f"{name}: {type(exc).__name__}: {exc}")
            continue
        total += len(_collect_callables(name, mod))
    return total, broken


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument(
        "--min-tests",
        type=int,
        default=0,
        help="fail if fewer than this many tests ran",
    )
    ap.add_argument(
        "--collect-only",
        action="store_true",
        help="print the collectable test count and exit (single source for CI)",
    )
    args = ap.parse_args(argv)

    _ensure_paths()
    importlib.import_module("providers")  # establish the provider registry first

    if args.collect_only:
        total, broken = collect()
        for line in broken:
            print(f"unimportable: {line}", file=sys.stderr)
        print(total)
        return 1 if broken else 0

    def _reanchor() -> None:
        if str(REPO_ROOT) in sys.path:
            sys.path.remove(str(REPO_ROOT))
        sys.path.insert(0, str(REPO_ROOT))

    globals()["_reanchor"] = _reanchor

    passed = 0
    failures: list[tuple[str, str]] = []

    for module_name in MODULES:
        try:
            module = importlib.import_module(module_name)
        except Exception as exc:
            failures.append((module_name, f"IMPORT {type(exc).__name__}: {exc}"))
            continue
        for label, fn in _collect_callables(module_name, module):
            try:
                fn()
                passed += 1
            except Exception as exc:
                failures.append((label, f"{type(exc).__name__}: {exc}"))
        _reanchor()

    print(f"\npassed={passed} failed={len(failures)}")
    for label, reason in failures:
        print(f"  FAIL {label}\n       {reason}")

    if failures:
        return 1

    if args.min_tests and passed < args.min_tests:
        print(
            f"\nTOO FEW TESTS RAN: {passed} < {args.min_tests}. "
            "A module probably failed to import, which drops its tests silently.",
            file=sys.stderr,
        )
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
