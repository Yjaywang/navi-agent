"""High-level memory orchestration: retrieve before query, store after query."""

from __future__ import annotations

import json
import logging
import math
import uuid
from datetime import datetime, timedelta, timezone

from config import Config
from memory.github_store import GitHubStore
from memory.indexer import MemoryIndexer
from memory.models import (
    ConversationMemory,
    FactMemory,
    FeedbackMemory,
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

        # 3. Negative feedback for similar queries
        feedbacks = self._get_feedback_for_query(user_message)
        if feedbacks:
            fb_items: list[str] = []
            for fb in feedbacks[:3]:
                fb_items.append(
                    f"- Query: {fb['original_query'][:100]}\n"
                    f"  Issue: The user indicated this response was unhelpful.\n"
                    f"  Correction: {fb.get('correction') or 'No correction provided'}"
                )
            sections.append("### Previous Feedback\n" + "\n".join(fb_items))

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

    # --- Feedback ---

    def store_feedback(self, feedback: FeedbackMemory) -> str:
        now = feedback.created_at
        dir_path = f"feedback/{now.year}/{now.month:02d}/{now.day:02d}"
        file_path = f"{dir_path}/fb_{feedback.id}.json"
        content = feedback.model_dump_json(indent=2)

        manifest_entry = ManifestEntry(
            id=feedback.id,
            path=file_path,
            type="feedback",
            summary=feedback.summary,
            tags=feedback.tags,
            created_at=feedback.created_at,
            updated_at=feedback.updated_at,
        )

        manifest = self.indexer.get_manifest(force_refresh=True)
        manifest.entries.append(manifest_entry)
        manifest.version += 1

        self.store.atomic_commit(
            {
                file_path: content,
                "_index/manifest.json": manifest.model_dump_json(indent=2),
            },
            f"Store feedback {feedback.id[:8]}",
        )

        self.indexer._manifest = manifest
        self.indexer._last_refresh = __import__("time").time()

        log.info("Stored feedback %s at %s", feedback.id[:8], file_path)
        return file_path

    def _get_feedback_for_query(self, query: str) -> list[dict]:
        """Find negative feedback entries relevant to the given query."""
        manifest = self.indexer.get_manifest()
        feedback_entries = [e for e in manifest.entries if e.type == "feedback"]
        if not feedback_entries:
            return []

        query_keywords = self.indexer._extract_keywords(query)
        if not query_keywords:
            return []

        results: list[tuple[float, dict]] = []
        for entry in feedback_entries:
            entry_keywords = self.indexer._extract_keywords(
                entry.summary + " " + " ".join(entry.tags)
            )
            overlap = len(query_keywords & entry_keywords)
            if overlap == 0:
                continue

            try:
                raw, _ = self.store.get_file(entry.path)
                data = json.loads(raw)
            except (FileNotFoundError, json.JSONDecodeError):
                continue

            if data.get("feedback_type") != "negative":
                continue

            score = overlap / len(query_keywords)
            results.append((score, data))

        results.sort(key=lambda x: x[0], reverse=True)
        return [d for _, d in results]

    # --- Knowledge consolidation ---

    def consolidate_knowledge(
        self, topic: str | None, date_range_days: int
    ) -> dict:
        """Load recent facts, group by topic tag. Return groups with 3+ facts."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=date_range_days)
        manifest = self.indexer.get_manifest(force_refresh=True)

        candidates = [
            e
            for e in manifest.entries
            if e.type == "fact"
            and e.created_at >= cutoff
            and not getattr(e, "consolidated", False)
        ]

        if topic:
            candidates = [e for e in candidates if topic in e.tags]

        # Load full fact data
        facts_by_topic: dict[str, list[dict]] = {}
        all_ids: list[str] = []
        for entry in candidates:
            try:
                raw, _ = self.store.get_file(entry.path)
                data = json.loads(raw)
            except (FileNotFoundError, json.JSONDecodeError):
                continue

            all_ids.append(entry.id)
            topic_key = entry.tags[0] if entry.tags else "general"
            facts_by_topic.setdefault(topic_key, []).append(data)

        # Keep only groups with 3+ facts
        groups = {k: v for k, v in facts_by_topic.items() if len(v) >= 3}

        return {"groups": groups, "entry_ids": all_ids}

    def mark_consolidated(self, entry_ids: list[str]) -> None:
        """Mark facts as consolidated in both manifest and individual files."""
        manifest = self.indexer.get_manifest(force_refresh=True)
        id_set = set(entry_ids)

        files_to_commit: dict[str, str] = {}
        for entry in manifest.entries:
            if entry.id in id_set:
                entry.consolidated = True
                # Also update the individual fact file
                try:
                    raw, _ = self.store.get_file(entry.path)
                    data = json.loads(raw)
                    data["consolidated"] = True
                    files_to_commit[entry.path] = json.dumps(data, indent=2, ensure_ascii=False)
                except (FileNotFoundError, json.JSONDecodeError):
                    continue

        manifest.version += 1
        files_to_commit["_index/manifest.json"] = manifest.model_dump_json(indent=2)

        if files_to_commit:
            self.store.atomic_commit(files_to_commit, "Mark facts as consolidated")
            self.indexer._manifest = manifest
            self.indexer._last_refresh = __import__("time").time()

        log.info("Marked %d entries as consolidated", len(id_set))

    def archive_stale_entries(self) -> int:
        """Archive entries with relevance score below threshold."""
        manifest = self.indexer.get_manifest(force_refresh=True)
        now = datetime.now(timezone.utc)
        threshold = 0.1

        to_archive: list[ManifestEntry] = []
        remaining: list[ManifestEntry] = []

        for entry in manifest.entries:
            days = max((now - entry.updated_at).total_seconds() / 86400, 0)
            score = math.exp(-0.01 * days)
            if score < threshold:
                to_archive.append(entry)
            else:
                remaining.append(entry)

        if not to_archive:
            return 0

        files_to_commit: dict[str, str] = {}
        for entry in to_archive:
            try:
                raw, _ = self.store.get_file(entry.path)
                archive_path = f"_archive/{entry.path}"
                files_to_commit[archive_path] = raw
            except FileNotFoundError:
                continue

        manifest.entries = remaining
        manifest.version += 1
        files_to_commit["_index/manifest.json"] = manifest.model_dump_json(indent=2)

        self.store.atomic_commit(files_to_commit, f"Archive {len(to_archive)} stale entries")
        self.indexer._manifest = manifest
        self.indexer._last_refresh = __import__("time").time()

        log.info("Archived %d stale entries", len(to_archive))
        return len(to_archive)
