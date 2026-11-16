"""Regression checks for release publication gates."""

from pathlib import Path
import subprocess
import sys
import tempfile

from ruamel.yaml import YAML


ROOT = Path(__file__).resolve().parents[1]
CHECK = ROOT / "tools" / "check_release_version.py"


def _check(tag, manifest_version="1.8.0", package_version="1.8.0"):
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "plugin.yaml").write_text(f"version: {manifest_version}\n")
        (root / "pyproject.toml").write_text(f'[project]\nversion = "{package_version}"\n')
        return subprocess.run(
            [sys.executable, str(CHECK), tag, str(root)],
            capture_output=True, text=True, check=False,
        )


def test_release_version_accepts_matching_tag():
    result = _check("v1.8.0")
    assert result.returncode == 0, result.stderr


def test_release_version_rejects_invalid_tag():
    result = _check("v1.8.0-extra")
    assert result.returncode != 0
    assert "tag" in result.stderr.lower()


def test_release_version_rejects_leading_zero():
    result = _check("v01.8.0", manifest_version="01.8.0", package_version="01.8.0")
    assert result.returncode != 0


def test_release_version_rejects_invalid_yaml():
    result = _check("v1.8.0", manifest_version="'1.8.0\"")
    assert result.returncode != 0


def test_release_version_rejects_manifest_mismatch():
    result = _check("v1.8.0", manifest_version="1.7.0")
    assert result.returncode != 0
    assert "plugin.yaml" in result.stderr


def test_release_version_rejects_package_mismatch():
    result = _check("v1.8.0", package_version="1.7.0")
    assert result.returncode != 0
    assert "pyproject.toml" in result.stderr


def test_both_publication_workflows_require_common_gate():
    yaml = YAML(typ="safe")
    for name in ("release.yml", "pypi.yml"):
        workflow = yaml.load((ROOT / ".github" / "workflows" / name).read_text())
        assert "workflow_dispatch" not in workflow["on"]
        job = workflow["jobs"]["release" if name == "release.yml" else "build"]
        steps = job["steps"]
        gate = next(i for i, step in enumerate(steps)
                    if step.get("uses") == "./.github/actions/release-gate")
        assert steps[gate]["with"]["tag"] == "${{ github.ref_name }}"
        assert steps[0]["with"]["ref"] == "${{ github.sha }}"
        if name == "release.yml":
            publish = next(i for i, step in enumerate(steps) if step.get("name") == "Publish GitHub release")
        else:
            publish = next(i for i, step in enumerate(steps) if step.get("name") == "Build sdist and wheel")
            assert workflow["jobs"]["publish"]["needs"] == "build"
        assert gate < publish


def test_release_gate_requires_nonzero_test_count():
    action = YAML(typ="safe").load((ROOT / ".github/actions/release-gate/action.yml").read_text())
    test_step = next(step for step in action["runs"]["steps"] if step.get("name") == "Run test suite")
    assert 'test "$count" -gt 0' in test_step["run"]
