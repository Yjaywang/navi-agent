"""Tests for memory/models.py — Pydantic model creation and serialization."""

from datetime import datetime, timezone

from memory.models import (
    ConversationMemory,
    FactMemory,
    FeedbackMemory,
    Manifest,
    ManifestEntry,
    MemoryEntry,
    UserProfile,
)


class TestMemoryEntry:
    def test_defaults(self):
        entry = MemoryEntry(id="abc123", type="fact")
        assert entry.id == "abc123"
        assert entry.type == "fact"
        assert entry.tags == []
        assert entry.source_guild is None
        assert entry.relevance_score == 1.0
        assert entry.created_at.tzinfo == timezone.utc

    def test_roundtrip_json(self):
        entry = MemoryEntry(id="x", type="conversation", tags=["a", "b"])
        raw = entry.model_dump_json()
        restored = MemoryEntry.model_validate_json(raw)
        assert restored.id == entry.id
        assert restored.tags == ["a", "b"]


class TestConversationMemory:
    def test_defaults(self):
        conv = ConversationMemory(id="c1")
        assert conv.type == "conversation"
        assert conv.turns == []
        assert conv.outcome == ""

    def test_roundtrip(self):
        conv = ConversationMemory(
            id="c2",
            turns=[{"role": "user", "content": "hi"}],
            outcome="greeted",
            topics=["greet"],
        )
        restored = ConversationMemory.model_validate_json(conv.model_dump_json())
        assert restored.turns == [{"role": "user", "content": "hi"}]
        assert restored.topics == ["greet"]


class TestFactMemory:
    def test_fields(self):
        fact = FactMemory(
            id="f1",
            confidence=0.9,
            source_conversation="c1",
            contradicts=["f0"],
            consolidated=True,
        )
        assert fact.type == "fact"
        assert fact.confidence == 0.9
        assert fact.consolidated is True


class TestFeedbackMemory:
    def test_each_feedback_type(self):
        for ft in ("positive", "negative", "bookmark"):
            fb = FeedbackMemory(id=f"fb_{ft}", feedback_type=ft)
            assert fb.feedback_type == ft
            assert fb.type == "feedback"


class TestManifestEntry:
    def test_creation(self):
        entry = ManifestEntry(id="m1", path="a/b.json", type="fact", summary="test")
        assert entry.consolidated is False


class TestManifest:
    def test_empty(self):
        m = Manifest()
        assert m.version == 1
        assert m.entries == []

    def test_with_entries(self):
        entry = ManifestEntry(id="m1", path="x", type="fact", summary="s")
        m = Manifest(version=3, entries=[entry])
        assert len(m.entries) == 1


class TestUserProfile:
    def test_defaults(self):
        p = UserProfile(user_id="u1")
        assert p.display_name == ""
        assert p.preferred_language is None
        assert p.notes == []
        assert p.first_seen.tzinfo == timezone.utc

    def test_roundtrip(self):
        p = UserProfile(user_id="u1", display_name="Alice", notes=["likes tea"])
        restored = UserProfile.model_validate_json(p.model_dump_json())
        assert restored.display_name == "Alice"
        assert restored.notes == ["likes tea"]
