"""Pydantic models for the memory system."""

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class MemoryEntry(BaseModel):
    id: str
    type: Literal["conversation", "fact", "knowledge", "feedback"]
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
    tags: list[str] = []
    source_guild: str | None = None
    source_user: str = ""
    content: str = ""
    summary: str = ""
    relevance_score: float = 1.0


class ConversationMemory(MemoryEntry):
    type: Literal["conversation"] = "conversation"
    turns: list[dict] = []
    outcome: str = ""
    topics: list[str] = []


class FactMemory(MemoryEntry):
    type: Literal["fact"] = "fact"
    confidence: float = 1.0
    source_conversation: str = ""
    contradicts: list[str] = []
    consolidated: bool = False


class FeedbackMemory(MemoryEntry):
    type: Literal["feedback"] = "feedback"
    feedback_type: Literal["positive", "negative", "bookmark"]
    original_query: str = ""
    original_response: str = ""
    correction: str = ""
    conversation_id: str = ""
    turn_index: int = -1
    message_id: str = ""


class ManifestEntry(BaseModel):
    id: str
    path: str
    type: str
    summary: str
    tags: list[str] = []
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
    consolidated: bool = False


class Manifest(BaseModel):
    version: int = 1
    entries: list[ManifestEntry] = []


class UserProfile(BaseModel):
    user_id: str
    display_name: str = ""
    preferred_language: str | None = None
    notes: list[str] = []
    first_seen: datetime = Field(default_factory=_utcnow)
    last_seen: datetime = Field(default_factory=_utcnow)
