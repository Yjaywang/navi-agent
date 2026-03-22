"""Shared test fixtures."""

from __future__ import annotations

import json
from dataclasses import dataclass
from unittest.mock import MagicMock

import pytest

from memory.github_store import GitHubStore
from memory.indexer import MemoryIndexer
from memory.models import Manifest


# ---------------------------------------------------------------------------
# Lightweight Config stub (avoids importing the real one which reads .env)
# ---------------------------------------------------------------------------

@dataclass
class _StubConfig:
    discord_token: str = ""
    anthropic_api_key: str = ""
    model: str = "claude-sonnet-4-6"
    github_token: str = "fake-token"
    memory_repo_owner: str = "testuser"
    memory_repo_name: str = "test-memory"
    admin_role_ids: set[int] = None
    trusted_role_ids: set[int] = None
    rate_limit_everyone: int = 20
    rate_limit_trusted: int = 100
    session_ttl_minutes: int = 60
    max_turns: int = 20
    agent_query_timeout: int = 120
    log_level: str = "DEBUG"

    def __post_init__(self):
        if self.admin_role_ids is None:
            self.admin_role_ids = set()
        if self.trusted_role_ids is None:
            self.trusted_role_ids = set()


@pytest.fixture
def config():
    return _StubConfig()


# ---------------------------------------------------------------------------
# Mock GitHubStore
# ---------------------------------------------------------------------------

_EMPTY_MANIFEST = Manifest(version=1, entries=[])


@pytest.fixture
def mock_store():
    store = MagicMock(spec=GitHubStore)
    store.get_file.side_effect = _default_get_file
    store.atomic_commit.return_value = "abc123sha"
    store.create_or_update_file.return_value = "abc123sha"
    store.file_exists.return_value = False
    store.list_directory.return_value = []
    return store


def _default_get_file(path: str):
    if path == "_index/manifest.json":
        return _EMPTY_MANIFEST.model_dump_json(indent=2), "sha_manifest"
    raise FileNotFoundError(f"{path} not found")


@pytest.fixture
def indexer(mock_store):
    return MemoryIndexer(mock_store)
