"""Shared test fixtures."""

from __future__ import annotations

import json
from dataclasses import dataclass
from unittest.mock import MagicMock

import discord

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
    health_check_port: int = 8080
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


# ---------------------------------------------------------------------------
# Discord mock helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_bot_user():
    user = MagicMock(spec=discord.User)
    user.id = 12345
    user.bot = False
    return user


def make_mock_message(
    *,
    author=None,
    channel=None,
    content="hello",
    mentions=None,
    bot=False,
):
    """Factory for creating mock discord.Message objects."""
    msg = MagicMock(spec=discord.Message)
    if author is None:
        author = MagicMock(spec=discord.User)
        author.bot = bot
    msg.author = author
    msg.channel = channel or MagicMock(spec=discord.TextChannel)
    msg.content = content
    msg.mentions = mentions or []
    return msg


class AsyncIteratorMock:
    """Helper that wraps a list into an async iterator (for channel.history())."""

    def __init__(self, items):
        self._items = list(items)
        self._index = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._index >= len(self._items):
            raise StopAsyncIteration
        item = self._items[self._index]
        self._index += 1
        return item


# ---------------------------------------------------------------------------
# Agent singleton reset
# ---------------------------------------------------------------------------

@pytest.fixture
def reset_agent_singletons():
    """Reset agent module singletons before and after each test."""
    import agent as _agent_mod
    _agent_mod._engine = None
    _agent_mod._skill_registry = None
    yield
    _agent_mod._engine = None
    _agent_mod._skill_registry = None
