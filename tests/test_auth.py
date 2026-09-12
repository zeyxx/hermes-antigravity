import os
import tempfile
from unittest.mock import patch
from auth import (
    generate_pkce,
    load_local_token,
    save_token_cache,
    extract_project_id,
    AntigravityAuthManager,
)


def test_generate_pkce():
    verifier, challenge = generate_pkce()
    assert len(verifier) >= 43
    assert len(challenge) >= 43
    assert verifier != challenge


def test_extract_project_id():
    assert extract_project_id({"projectId": "proj-123"}) == "proj-123"
    assert extract_project_id({"cloudaicompanionProject": "proj-456"}) == "proj-456"
    assert extract_project_id({"antigravityProjectId": "proj-789"}) == "proj-789"
    assert extract_project_id({"nested": {"id": "p1"}}) is None


def test_save_and_load_token_cache():
    with patch.dict(os.environ, {}, clear=True):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = os.path.join(tmpdir, "antigravity-auth.json")
            data = {
                "access_token": "mock-access-token-123",
                "refresh_token": "mock-refresh-token-456",
                "expires_at": 9999999999.0,
                "project_id": "test-project-001",
            }
            save_token_cache(data, cache_path=cache_path)

            loaded = load_local_token(cache_paths=[cache_path])
            assert loaded["access_token"] == "mock-access-token-123"
            assert loaded["project_id"] == "test-project-001"


def test_auth_manager_get_valid_token_cached():
    manager = AntigravityAuthManager(
        credentials={
            "access_token": "mock-valid-token-789",
            "refresh_token": "mock-refresh-token-456",
            "expires_at": 9999999999.0,
            "project_id": "proj-abc",
        }
    )
    token, proj = manager.get_credentials()
    assert token == "mock-valid-token-789"


def test_register_removal_step_returns_bool():
    from auth import _register_removal_step
    # Must never raise: True when the core registry accepts the step,
    # False when the core removed the shim (add/list/status unaffected).
    assert _register_removal_step() in (True, False)
