"""Manifest index management and keyword search."""

from __future__ import annotations

import json
import logging
import math
import re
import time
from datetime import datetime, timezone

from memory.github_store import GitHubStore, SHAConflictError
from memory.models import Manifest, ManifestEntry

log = logging.getLogger(__name__)

# Common English + Chinese stopwords
_STOPWORDS = frozenset(
    "a an the is are was were be been being have has had do does did "
    "will would shall should can could may might must "
    "i me my we our you your he she it they them "
    "in on at to for of with by from as into about "
    "and or but not no nor so yet if then else "
    "this that these those what which who whom how when where why "
    "的 了 在 是 我 他 她 它 们 你 也 就 都 而 及 與 或 但 "
    "一個 一 不 有 人 這 那 上 大 中 小".split()
)

_TYPE_WEIGHTS: dict[str, float] = {
    "fact": 1.5,
    "knowledge": 1.3,
    "conversation": 1.0,
    "feedback": 0.8,
}

_CACHE_TTL = 300  # 5 minutes


class MemoryIndexer:
    """Maintains a cached copy of the manifest and provides keyword search."""

    def __init__(self, store: GitHubStore) -> None:
        self._store = store
        self._manifest: Manifest | None = None
        self._manifest_sha: str | None = None
        self._last_refresh: float = 0.0

    def get_manifest(self, force_refresh: bool = False) -> Manifest:
        now = time.time()
        if (
            not force_refresh
            and self._manifest is not None
            and (now - self._last_refresh) < _CACHE_TTL
        ):
            return self._manifest

        try:
            raw, sha = self._store.get_file("_index/manifest.json")
            self._manifest = Manifest.model_validate_json(raw)
            self._manifest_sha = sha
        except FileNotFoundError:
            self._manifest = Manifest()
            self._manifest_sha = None
        self._last_refresh = now
        return self._manifest

    def search(
        self,
        query: str,
        top_k: int = 5,
        type_filter: str | None = None,
    ) -> list[ManifestEntry]:
        manifest = self.get_manifest()
        keywords = self._extract_keywords(query)
        if not keywords:
            return []

        entries = manifest.entries
        if type_filter:
            entries = [e for e in entries if e.type == type_filter]

        scored: list[tuple[float, ManifestEntry]] = []
        now = datetime.now(timezone.utc)

        for entry in entries:
            entry_words = self._extract_keywords(
                entry.summary + " " + " ".join(entry.tags)
            )
            if not entry_words:
                continue

            overlap = len(keywords & entry_words)
            if overlap == 0:
                continue
            keyword_score = overlap / len(keywords)

            days = max((now - entry.updated_at).total_seconds() / 86400, 0)
            recency = math.exp(-0.01 * days)

            type_w = _TYPE_WEIGHTS.get(entry.type, 1.0)
            score = keyword_score * recency * type_w

            if getattr(entry, "consolidated", False):
                score *= 0.3
            scored.append((score, entry))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [entry for _, entry in scored[:top_k]]

    def add_entry(self, entry: ManifestEntry) -> None:
        manifest = self.get_manifest(force_refresh=True)
        manifest.entries.append(entry)
        manifest.version += 1
        self._write_manifest(manifest)

    def _write_manifest(self, manifest: Manifest) -> None:
        content = manifest.model_dump_json(indent=2)
        try:
            if self._manifest_sha:
                new_sha = self._store.create_or_update_file(
                    "_index/manifest.json", content, "Update manifest", self._manifest_sha
                )
            else:
                new_sha = self._store.create_or_update_file(
                    "_index/manifest.json", content, "Create manifest"
                )
            self._manifest = manifest
            self._manifest_sha = new_sha
        except SHAConflictError:
            log.warning("SHA conflict on manifest, re-fetching and merging")
            remote_raw, remote_sha = self._store.get_file("_index/manifest.json")
            remote = Manifest.model_validate_json(remote_raw)
            existing_ids = {e.id for e in remote.entries}
            for e in manifest.entries:
                if e.id not in existing_ids:
                    remote.entries.append(e)
            remote.version += 1
            content = remote.model_dump_json(indent=2)
            new_sha = self._store.create_or_update_file(
                "_index/manifest.json", content, "Update manifest (merged)", remote_sha
            )
            self._manifest = remote
            self._manifest_sha = new_sha

    @staticmethod
    def _extract_keywords(text: str) -> set[str]:
        text = text.lower()
        # English/numeric words
        alpha_tokens = re.findall(r"[a-zA-Z0-9_]+", text)
        # CJK characters
        cjk_chars = re.findall(r"[\u4e00-\u9fff]", text)
        # Generate CJK bigrams for meaningful matching (e.g. "笑話", "記憶")
        cjk_bigrams = [cjk_chars[i] + cjk_chars[i + 1] for i in range(len(cjk_chars) - 1)]

        tokens = set(alpha_tokens) | set(cjk_chars) | set(cjk_bigrams)
        return {t for t in tokens if t not in _STOPWORDS}
