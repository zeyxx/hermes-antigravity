import sys

# Ensure Hermes agent is in sys.path dynamically
from pathlib import Path

hermes_agent_dir = Path.home() / ".hermes" / "hermes-agent"
if hermes_agent_dir.is_dir() and str(hermes_agent_dir) not in sys.path:
    sys.path.insert(0, str(hermes_agent_dir))
from providers import get_provider_profile  # noqa: E402


def test_antigravity_profile_registered():
    profile = get_provider_profile("antigravity")
    assert profile is not None
    assert profile.name == "antigravity"
    assert "google-antigravity" in profile.aliases
    assert "agy" in profile.aliases
    assert profile.supports_vision is True
    assert profile.default_aux_model == "gemini-3.8-flash"


def test_antigravity_create_client_hook():
    # Seed a usable account so the hardened fallback path can build a client.
    from accounts import AntigravityAccountRegistry
    reg = AntigravityAccountRegistry()
    reg.add_account("test@example.com", {"access_token": "tok", "refresh_token": "ref", "expires_at": 9e9, "project_id": "p"})
    profile = get_provider_profile("antigravity")
    assert profile is not None
    client = profile.create_client()
    assert client is not None
    assert hasattr(client, "chat")
    assert hasattr(client.chat, "completions")
    assert hasattr(client.chat.completions, "create")

def test_antigravity_writes_no_core_tables():
    """Catalog rule 9: the plugin must not write core tables at import.

    No overlay in ``HERMES_OVERLAYS``, no model flow in the core's flow
    registry: inference and discovery go through the profile's own
    ``create_client`` / ``fetch_models`` (the public seam). A dedicated
    ``auth_type="oauth_external"`` hook belongs to the core issue tracker,
    not to a private-table write here.
    """
    import importlib.util
    from pathlib import Path
    init_path = Path(__file__).resolve().parent.parent / "__init__.py"
    spec = importlib.util.spec_from_file_location("_ag_init", init_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert getattr(mod, "_model_flow_antigravity", None) is None, (
        "custom model flow must stay out of the core's tables")
    try:
        from hermes_cli.providers import HERMES_OVERLAYS
    except ImportError:
        return
    assert "antigravity" not in HERMES_OVERLAYS, (
        "import must not write the core overlay table")


def test_antigravity_skips_models_health_probe():
    # Google has no REST /models on this base URL (catalog is
    # fetchAvailableModels) — the profile must opt out of the probe.
    profile = get_provider_profile("antigravity")
    assert profile is not None
    assert profile.supports_health_check is False


def test_declared_env_vars_match_what_the_code_reads():
    """Catalog rule 5: declared capabilities must match reality.

    Every env var on the profile must be read by auth.py (no dead
    declarations like the old ANTIGRAVITY_API_KEY), and every
    ANTIGRAVITY_*/GOOGLE_OAUTH_TOKEN name the code reads must be declared
    (no silent reads like the old missing REFRESH_TOKEN/PROJECT_ID pair).
    """
    import re
    from pathlib import Path

    profile = get_provider_profile("antigravity")
    assert profile is not None
    declared = set(profile.env_vars)

    source = (Path(__file__).resolve().parent.parent / "auth.py").read_text()
    read = set(re.findall(r'os\.environ\.get\("([A-Z_]+)"', source))

    assert declared == read, (
        f"declared={sorted(declared)} read={sorted(read)}")


def test_client_imports_as_package_submodule():
    """client.py must import as a package submodule (antigravity.client).

    Hermes loads the plugin as a package, so client.py's relative imports
    (from .auth, from .models, from .quota) must resolve. A regression that
    turns a relative import into a bare one (from quota) raises
    ModuleNotFoundError inside the package branch, falls into the except
    ImportError branch, and that one fails too — breaking plugin load while
    the repo-root test suite still passes (absolute fallbacks resolve there).

    This imports client.py as a submodule of the real plugin package from its
    parent directory, so relative imports must genuinely resolve.
    """
    import importlib
    from pathlib import Path

    repo = Path(__file__).resolve().parent.parent
    package_name = repo.name  # 'antigravity'
    parent = str(repo.parent)
    added = parent not in sys.path
    if added:
        sys.path.insert(0, parent)
    # Drop any cached copy so we exercise a fresh package import.
    for name in list(sys.modules):
        if name == package_name or name.startswith(f"{package_name}."):
            del sys.modules[name]
    try:
        mod = importlib.import_module(f"{package_name}.client")
    except ModuleNotFoundError as exc:
        raise AssertionError(
            f"{package_name}.client failed to import as a package "
            f"(relative import broken?): {exc}") from exc
    finally:
        if added:
            sys.path.remove(parent)
    # The package branch must have provided these, not the except fallback.
    assert hasattr(mod, "DEFAULT_OPENER")
    assert hasattr(mod, "AntigravityClient")


def test_auth_quota_status_has_no_bare_imports_under_package_load():
    """auth._print_quota_status must not fail with a bare-import error.

    Issue #39: auth.py imported `from models import fetch_account_quota` and
    `import quota as _quota_mod` inside _print_quota_status WITHOUT the
    try/except ImportError guard the module-level imports use. Under Hermes'
    package load, those bare imports raise ModuleNotFoundError and the quota
    line degrades to "quota: unavailable (No module named 'models')".

    The repo-root suite never caught it: run from the repo root, the bare
    names resolve as top-level modules (and once `models` is in sys.modules,
    even a stripped sys.path resolves it). Reproduce Hermes' real load in a
    subprocess: a tempdir holds `antigravity` as a symlink to this repo, that
    tempdir (NOT the repo root) is on PYTHONPATH, and the quota path is called
    there.
    """
    import os
    import subprocess
    import sys as _sys
    import tempfile
    import textwrap
    from pathlib import Path

    repo = Path(__file__).resolve().parent.parent
    # Mirror run_tests.py's resolution: HERMES_CORE env wins, then the local
    # default. On CI the core is cloned to $HERMES_CORE, not ~/.hermes.
    core_env = os.environ.get("HERMES_CORE")
    hermes_core = Path(core_env) if core_env else Path.home() / ".hermes" / "hermes-agent"

    child = textwrap.dedent(
        """
        import sys, os
        sys.path.insert(0, os.environ["TEST_TMPDIR"])
        from unittest.mock import patch
        import antigravity.auth as auth_mod

        class _Account:
            account_id = "regression"
            email = "regression@example.com"

        with patch.object(auth_mod, "AntigravityAuthManager") as mgr:
            mgr.return_value.get_credentials.return_value = ("tok", "proj")
            auth_mod._print_quota_status(object(), _Account())
        """
    )

    with tempfile.TemporaryDirectory() as tmp:
        pkg = Path(tmp) / "antigravity"
        try:
            pkg.symlink_to(repo, target_is_directory=True)
        except OSError:
            # Symlinks unavailable (e.g. Windows without privilege): skip.
            return
        env = dict(os.environ)
        # The tempdir is the package parent; the repo root is deliberately
        # absent, as it is when Hermes loads the plugin.
        paths = [tmp]
        if hermes_core.is_dir():
            paths.append(str(hermes_core))
        env["PYTHONPATH"] = os.pathsep.join(paths)
        env["TEST_TMPDIR"] = tmp
        proc = subprocess.run(
            [os.environ.get("HERMES_PYTHON") or _sys.executable, "-c", child],
            capture_output=True, text=True, env=env, timeout=60,
        )

    combined = proc.stdout + proc.stderr
    assert "No module named" not in combined, (
        "auth.py quota path used a bare import under package load "
        f"(issue #39 regression):\\n{combined}")
