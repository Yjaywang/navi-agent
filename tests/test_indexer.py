"""Tests for memory/indexer.py."""

import json
import time
from datetime import datetime, timedelta, timezone

from memory.indexer import MemoryIndexer, _CACHE_TTL
from memory.models import Manifest, ManifestEntry


def _make_entry(
    id: str,
    type: str = "fact",
    summary: str = "",
    tags: list[str] | None = None,
    days_ago: int = 0,
    consolidated: bool = False,
) -> ManifestEntry:
    dt = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return ManifestEntry(
        id=id,
        path=f"test/{id}.json",
        type=type,
        summary=summary,
        tags=tags or [],
        created_at=dt,
        updated_at=dt,
        consolidated=consolidated,
    )


class TestExtractKeywords:
    def test_english(self):
        kw = MemoryIndexer._extract_keywords("Hello World Python")
        assert "hello" in kw
        assert "world" in kw
        assert "python" in kw

    def test_stopwords_removed(self):
        kw = MemoryIndexer._extract_keywords("the is a an")
        assert kw == set()

    def test_cjk(self):
        kw = MemoryIndexer._extract_keywords("記憶系統")
        assert "記" in kw
        assert "憶" in kw
        # Bigrams
        assert "記憶" in kw
        assert "憶系" in kw

    def test_mixed(self):
        kw = MemoryIndexer._extract_keywords("Python 程式設計")
        assert "python" in kw
        assert "程式" in kw

    def test_empty(self):
        assert MemoryIndexer._extract_keywords("") == set()


class TestGetManifest:
    def test_caching(self, mock_store, indexer):
        m1 = indexer.get_manifest()
        m2 = indexer.get_manifest()
        assert m1 is m2
        assert mock_store.get_file.call_count == 1

    def test_force_refresh(self, mock_store, indexer):
        indexer.get_manifest()
        indexer.get_manifest(force_refresh=True)
        assert mock_store.get_file.call_count == 2

    def test_file_not_found_returns_empty(self, mock_store):
        mock_store.get_file.side_effect = FileNotFoundError
        idx = MemoryIndexer(mock_store)
        m = idx.get_manifest()
        assert m.entries == []


class TestSearch:
    def test_basic_ranking(self, mock_store):
        entries = [
            _make_entry("e1", type="fact", summary="python programming tutorial"),
            _make_entry("e2", type="conversation", summary="python basics"),
            _make_entry("e3", type="fact", summary="java enterprise"),
        ]
        manifest = Manifest(version=1, entries=entries)
        mock_store.get_file.side_effect = None
        mock_store.get_file.return_value = (manifest.model_dump_json(), "sha1")

        idx = MemoryIndexer(mock_store)
        results = idx.search("python programming")

        assert len(results) >= 1
        # e1 should rank first (fact type weight + more keyword overlap)
        assert results[0].id == "e1"
        # e3 has no overlap → should not appear
        assert all(r.id != "e3" for r in results)

    def test_type_filter(self, mock_store):
        entries = [
            _make_entry("e1", type="fact", summary="python"),
            _make_entry("e2", type="conversation", summary="python"),
        ]
        manifest = Manifest(version=1, entries=entries)
        mock_store.get_file.side_effect = None
        mock_store.get_file.return_value = (manifest.model_dump_json(), "sha1")

        idx = MemoryIndexer(mock_store)
        results = idx.search("python", type_filter="conversation")
        assert all(r.type == "conversation" for r in results)

    def test_consolidated_penalized(self, mock_store):
        entries = [
            _make_entry("e1", type="fact", summary="python guide", consolidated=True),
            _make_entry("e2", type="fact", summary="python guide", consolidated=False),
        ]
        manifest = Manifest(version=1, entries=entries)
        mock_store.get_file.side_effect = None
        mock_store.get_file.return_value = (manifest.model_dump_json(), "sha1")

        idx = MemoryIndexer(mock_store)
        results = idx.search("python guide")
        # Non-consolidated should rank higher
        assert results[0].id == "e2"

    def test_top_k(self, mock_store):
        entries = [_make_entry(f"e{i}", summary="python") for i in range(10)]
        manifest = Manifest(version=1, entries=entries)
        mock_store.get_file.side_effect = None
        mock_store.get_file.return_value = (manifest.model_dump_json(), "sha1")

        idx = MemoryIndexer(mock_store)
        results = idx.search("python", top_k=3)
        assert len(results) == 3

    def test_no_overlap_returns_empty(self, mock_store):
        entries = [_make_entry("e1", summary="java enterprise")]
        manifest = Manifest(version=1, entries=entries)
        mock_store.get_file.side_effect = None
        mock_store.get_file.return_value = (manifest.model_dump_json(), "sha1")

        idx = MemoryIndexer(mock_store)
        assert idx.search("python") == []

    def test_empty_query(self, mock_store):
        idx = MemoryIndexer(mock_store)
        assert idx.search("the is a") == []  # all stopwords
