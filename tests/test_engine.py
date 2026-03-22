"""Tests for memory/engine.py — uses mocked GitHubStore."""

import json
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from memory.engine import MemoryEngine
from memory.github_store import GitHubStore
from memory.indexer import MemoryIndexer
from memory.models import (
    ConversationMemory,
    FactMemory,
    FeedbackMemory,
    Manifest,
    ManifestEntry,
    UserProfile,
)


@pytest.fixture
def engine(mock_store):
    """Create a MemoryEngine with a mocked store (bypass __init__)."""
    with patch.object(MemoryEngine, "__init__", lambda self, *a, **kw: None):
        eng = MemoryEngine.__new__(MemoryEngine)
        eng.store = mock_store
        eng.indexer = MemoryIndexer(mock_store)
    return eng


class TestStoreFact:
    def test_stores_and_returns_path(self, engine, mock_store):
        fact = FactMemory(id="f001", summary="Python is great", tags=["python"])
        path = engine.store_fact(fact)

        assert path == "knowledge/facts/fact_f001.json"
        mock_store.atomic_commit.assert_called_once()
        files = mock_store.atomic_commit.call_args[0][0]
        assert "knowledge/facts/fact_f001.json" in files
        assert "_index/manifest.json" in files


class TestStoreConversation:
    def test_date_based_path(self, engine, mock_store):
        now = datetime(2026, 3, 22, tzinfo=timezone.utc)
        conv = ConversationMemory(id="c001", summary="test", created_at=now)
        path = engine.store_conversation(conv)

        assert path == "conversations/2026/03/22/conv_c001.json"


class TestStoreFeedback:
    def test_stores_feedback(self, engine, mock_store):
        fb = FeedbackMemory(
            id="fb01", feedback_type="negative", summary="bad response",
        )
        path = engine.store_feedback(fb)
        assert "fb_fb01.json" in path
        mock_store.atomic_commit.assert_called_once()


class TestGetUserProfile:
    def test_returns_none_when_not_found(self, engine, mock_store):
        mock_store.get_file.side_effect = FileNotFoundError
        assert engine.get_user_profile("u123") is None

    def test_returns_none_for_empty_id(self, engine):
        assert engine.get_user_profile("") is None

    def test_returns_profile(self, engine, mock_store):
        profile = UserProfile(user_id="u1", display_name="Alice")
        mock_store.get_file.side_effect = None
        mock_store.get_file.return_value = (profile.model_dump_json(), "sha")
        result = engine.get_user_profile("u1")
        assert result is not None
        assert result.display_name == "Alice"


class TestUpdateUserProfile:
    def test_creates_new(self, engine, mock_store):
        mock_store.get_file.side_effect = FileNotFoundError
        profile = UserProfile(user_id="u1", display_name="Bob")
        engine.update_user_profile(profile)
        mock_store.create_or_update_file.assert_called_once()
        # sha should be None (create, not update)
        call_args = mock_store.create_or_update_file.call_args
        assert call_args[0][0] == "users/u1/profile.json"

    def test_updates_existing(self, engine, mock_store):
        existing = UserProfile(user_id="u1", display_name="Old")
        mock_store.get_file.side_effect = None
        mock_store.get_file.return_value = (existing.model_dump_json(), "sha_old")
        profile = UserProfile(user_id="u1", display_name="New")
        engine.update_user_profile(profile)
        call_args = mock_store.create_or_update_file.call_args
        assert "sha_old" in call_args[0]


class TestForgetTopic:
    def test_nothing_to_forget(self, engine, mock_store):
        # indexer.search returns [] → nothing to do
        count = engine.forget_topic("nonexistent")
        assert count == 0
        mock_store.atomic_commit.assert_not_called()

    def test_deletes_matching_entries(self, engine, mock_store):
        entries = [
            ManifestEntry(id="e1", path="a.json", type="fact", summary="python tutorial"),
            ManifestEntry(id="e2", path="b.json", type="fact", summary="python guide"),
        ]
        manifest = Manifest(version=1, entries=entries)
        mock_store.get_file.side_effect = None
        mock_store.get_file.return_value = (manifest.model_dump_json(), "sha1")

        count = engine.forget_topic("python")
        assert count == 2
        mock_store.atomic_commit.assert_called_once()


class TestConsolidateKnowledge:
    def test_groups_by_topic(self, engine, mock_store):
        entries = [
            ManifestEntry(id=f"f{i}", path=f"facts/f{i}.json", type="fact",
                          summary="python tip", tags=["python"])
            for i in range(4)
        ]
        manifest = Manifest(version=1, entries=entries)
        mock_store.get_file.side_effect = lambda path: (
            (manifest.model_dump_json(), "sha1")
            if path == "_index/manifest.json"
            else (json.dumps({"id": path, "summary": "tip", "tags": ["python"]}), "sha2")
        )

        result = engine.consolidate_knowledge(topic=None, date_range_days=30)
        # 4 facts with tag "python" → group "python" should appear (3+ threshold)
        assert "python" in result["groups"]
        assert len(result["entry_ids"]) == 4

    def test_filters_by_topic(self, engine, mock_store):
        entries = [
            ManifestEntry(id="f1", path="f1.json", type="fact", summary="py", tags=["python"]),
            ManifestEntry(id="f2", path="f2.json", type="fact", summary="js", tags=["javascript"]),
        ]
        manifest = Manifest(version=1, entries=entries)
        mock_store.get_file.side_effect = lambda path: (
            (manifest.model_dump_json(), "sha1")
            if path == "_index/manifest.json"
            else (json.dumps({"summary": "data"}), "sha2")
        )

        result = engine.consolidate_knowledge(topic="python", date_range_days=30)
        assert "f2" not in result["entry_ids"]


class TestRetrieveContext:
    def test_empty_when_no_data(self, engine, mock_store):
        mock_store.get_file.side_effect = FileNotFoundError
        ctx = engine.retrieve_context("hello", "u1")
        assert ctx == ""

    def test_includes_profile(self, engine, mock_store):
        profile = UserProfile(user_id="u1", display_name="Alice", preferred_language="zh-TW")
        entries = []
        manifest = Manifest(version=1, entries=entries)

        def side_effect(path):
            if path == "_index/manifest.json":
                return manifest.model_dump_json(), "sha1"
            if path == "users/u1/profile.json":
                return profile.model_dump_json(), "sha2"
            raise FileNotFoundError

        mock_store.get_file.side_effect = side_effect
        ctx = engine.retrieve_context("hello", "u1")
        assert "Alice" in ctx
        assert "zh-TW" in ctx
