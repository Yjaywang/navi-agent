"""High-level memory orchestration: retrieve before query, store after query."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone

from config import Config
from memory.github_store import GitHubStore
from memory.indexer import MemoryIndexer
from memory.models import (
    ConversationMemory,
    FactMemory,
    ManifestEntry,
    UserProfile,
)

log = logging.getLogger(__name__)


class MemoryEngine:
    """Coordinates memory retrieval and storage against the GitHub repo."""

    def __init__(self, config: Config) -> None:
        self.store = GitHubStore(
            token=config.github_token,
            repo_owner=config.memory_repo_owner,
            repo_name=config.memory_repo_name,
        )
        self.indexer = MemoryIndexer(self.store)

    # --- Pre-query: build context string for system prompt ---

    def retrieve_context(self, user_message: str, user_id: str) -> str:
        sections: list[str] = []

        # 1. User profile
        profile = self.get_user_profile(user_id)
        if profile:
            lines = [f"- Display Name: {profile.display_name}"]
            if profile.preferred_language:
                lines.append(f"- Preferred Language: {profile.preferred_language}")
            if profile.notes:
                lines.append(f"- Notes: {'; '.join(profile.notes)}")
            sections.append("### User Profile\n" + "\n".join(lines))

        # 2. Relevant memories
        results = self.indexer.search(user_message, top_k=5)
        if results:
            items: list[str] = []
            for i, entry in enumerate(results, 1):
                try:
                    content, _ = self.store.get_file(entry.path)
                    snippet = content[:300].strip()
                except FileNotFoundError:
                    snippet = "(content unavailable)"
                date_str = entry.updated_at.strftime("%Y-%m-%d")
                items.append(f"{i}. [{entry.type}] {entry.summary} ({date_str})\n   {snippet}")
            sections.append("### Related Knowledge\n" + "\n".join(items))

        if not sections:
            return ""
        return "## Relevant Memory\n\n" + "\n\n".join(sections)

    # --- Post-query: store memories ---

    def store_conversation(self, memory: ConversationMemory) -> str:
        now = memory.created_at
        dir_path = f"conversations/{now.year}/{now.month:02d}/{now.day:02d}"
        file_path = f"{dir_path}/conv_{memory.id}.json"
        content = memory.model_dump_json(indent=2)

        manifest_entry = ManifestEntry(
            id=memory.id,
            path=file_path,
            type="conversation",
            summary=memory.summary,
            tags=memory.tags,
            created_at=memory.created_at,
            updated_at=memory.updated_at,
        )

        # Atomic: write memory file + update manifest
        manifest = self.indexer.get_manifest(force_refresh=True)
        manifest.entries.append(manifest_entry)
        manifest.version += 1

        self.store.atomic_commit(
            {
                file_path: content,
                "_index/manifest.json": manifest.model_dump_json(indent=2),
            },
            f"Store conversation {memory.id[:8]}",
        )

        # Update local cache
        self.indexer._manifest = manifest
        self.indexer._last_refresh = __import__("time").time()

        log.info("Stored conversation %s at %s", memory.id[:8], file_path)
        return file_path

    def store_fact(self, fact: FactMemory) -> str:
        file_path = f"knowledge/facts/fact_{fact.id}.json"
        content = fact.model_dump_json(indent=2)

        manifest_entry = ManifestEntry(
            id=fact.id,
            path=file_path,
            type="fact",
            summary=fact.summary,
            tags=fact.tags,
            created_at=fact.created_at,
            updated_at=fact.updated_at,
        )

        manifest = self.indexer.get_manifest(force_refresh=True)
        manifest.entries.append(manifest_entry)
        manifest.version += 1

        self.store.atomic_commit(
            {
                file_path: content,
                "_index/manifest.json": manifest.model_dump_json(indent=2),
            },
            f"Store fact {fact.id[:8]}",
        )

        self.indexer._manifest = manifest
        self.indexer._last_refresh = __import__("time").time()

        log.info("Stored fact %s at %s", fact.id[:8], file_path)
        return file_path

    # --- User profiles ---

    def get_user_profile(self, user_id: str) -> UserProfile | None:
        if not user_id:
            return None
        try:
            raw, _ = self.store.get_file(f"users/{user_id}/profile.json")
            return UserProfile.model_validate_json(raw)
        except FileNotFoundError:
            return None

    def update_user_profile(self, profile: UserProfile) -> None:
        path = f"users/{profile.user_id}/profile.json"
        content = profile.model_dump_json(indent=2)

        try:
            _, sha = self.store.get_file(path)
            self.store.create_or_update_file(path, content, f"Update profile {profile.user_id}", sha)
        except FileNotFoundError:
            self.store.create_or_update_file(path, content, f"Create profile {profile.user_id}")

        log.info("Updated profile for user %s", profile.user_id)
