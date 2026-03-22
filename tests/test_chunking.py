"""Tests for utils/chunking.py."""

from utils.chunking import chunk_text


class TestChunkText:
    def test_short_text_single_chunk(self):
        assert chunk_text("hello") == ["hello"]

    def test_empty_string(self):
        assert chunk_text("") == [""]

    def test_exact_boundary(self):
        text = "a" * 1950
        assert chunk_text(text) == [text]

    def test_split_on_newline(self):
        line = "x" * 100
        text = (line + "\n") * 25  # 25 lines, 2525 chars total
        chunks = chunk_text(text)
        assert len(chunks) >= 2
        for c in chunks:
            assert len(c) <= 1950

    def test_split_on_space(self):
        # No newlines, only spaces
        word = "abcdefghij"  # 10 chars
        text = " ".join([word] * 250)  # ~2749 chars
        chunks = chunk_text(text)
        assert len(chunks) >= 2
        for c in chunks:
            assert len(c) <= 1950

    def test_hard_cut(self):
        # No newlines, no spaces
        text = "x" * 4000
        chunks = chunk_text(text)
        assert len(chunks) >= 2
        assert chunks[0] == "x" * 1950

    def test_custom_max_len(self):
        text = "hello world foo bar baz"
        chunks = chunk_text(text, max_len=10)
        assert len(chunks) >= 2
        for c in chunks:
            assert len(c) <= 10
