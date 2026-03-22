"""Tests for memory/github_store.py — mocks PyGithub entirely."""

from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from memory.github_store import GitHubStore, SHAConflictError


@pytest.fixture
def store():
    with patch("memory.github_store.Github") as MockGithub:
        mock_github = MockGithub.return_value
        mock_repo = MagicMock()
        mock_github.get_repo.return_value = mock_repo

        s = GitHubStore(token="fake", repo_owner="owner", repo_name="repo")
        s._mock_repo = mock_repo
        s._mock_github = mock_github
        return s


class TestGetFile:
    def test_returns_content_and_sha(self, store):
        contents = MagicMock()
        contents.decoded_content = b"hello world"
        contents.sha = "sha123"
        store._mock_repo.get_contents.return_value = contents

        content, sha = store.get_file("path/file.json")
        assert content == "hello world"
        assert sha == "sha123"

    def test_raises_file_not_found_on_404(self, store):
        from github import GithubException

        exc = GithubException(404, {"message": "Not Found"}, None)
        store._mock_repo.get_contents.side_effect = exc
        with pytest.raises(FileNotFoundError):
            store.get_file("missing.json")


class TestFileExists:
    def test_true(self, store):
        store._mock_repo.get_contents.return_value = MagicMock()
        assert store.file_exists("exists.json") is True

    def test_false_on_404(self, store):
        from github import GithubException

        store._mock_repo.get_contents.side_effect = GithubException(404, {}, None)
        assert store.file_exists("missing.json") is False


class TestListDirectory:
    def test_returns_paths(self, store):
        item1 = MagicMock()
        item1.path = "dir/a.json"
        item2 = MagicMock()
        item2.path = "dir/b.json"
        store._mock_repo.get_contents.return_value = [item1, item2]

        result = store.list_directory("dir")
        assert result == ["dir/a.json", "dir/b.json"]

    def test_empty_on_404(self, store):
        from github import GithubException

        store._mock_repo.get_contents.side_effect = GithubException(404, {}, None)
        assert store.list_directory("missing") == []


class TestCreateOrUpdateFile:
    def test_create_without_sha(self, store):
        result_mock = {"content": MagicMock(sha="new_sha")}
        store._mock_repo.create_file.return_value = result_mock

        sha = store.create_or_update_file("path.json", "content", "msg")
        assert sha == "new_sha"
        store._mock_repo.create_file.assert_called_once()

    def test_update_with_sha(self, store):
        result_mock = {"content": MagicMock(sha="updated_sha")}
        store._mock_repo.update_file.return_value = result_mock

        sha = store.create_or_update_file("path.json", "content", "msg", sha="old_sha")
        assert sha == "updated_sha"
        store._mock_repo.update_file.assert_called_once()

    def test_sha_conflict_on_409(self, store):
        from github import GithubException

        store._mock_repo.update_file.side_effect = GithubException(409, {"message": "conflict"}, None)
        with pytest.raises(SHAConflictError):
            store.create_or_update_file("path.json", "c", "m", sha="stale")

    def test_sha_conflict_on_422(self, store):
        from github import GithubException

        store._mock_repo.update_file.side_effect = GithubException(422, {"message": "conflict"}, None)
        with pytest.raises(SHAConflictError):
            store.create_or_update_file("path.json", "c", "m", sha="stale")


class TestAtomicCommit:
    def test_calls_git_data_api(self, store):
        repo = store._mock_repo
        ref = MagicMock()
        ref.object.sha = "parent_sha"
        repo.get_git_ref.return_value = ref

        parent_commit = MagicMock()
        parent_commit.tree = MagicMock()
        repo.get_git_commit.return_value = parent_commit

        new_tree = MagicMock()
        repo.create_git_tree.return_value = new_tree

        new_commit = MagicMock()
        new_commit.sha = "new_sha"
        repo.create_git_commit.return_value = new_commit

        result = store.atomic_commit({"a.json": "{}", "b.json": "{}"}, "test commit")
        assert result == "new_sha"
        repo.create_git_tree.assert_called_once()
        repo.create_git_commit.assert_called_once()
        ref.edit.assert_called_once_with("new_sha")


class TestEnsureRepo:
    def test_noop_when_exists(self, store):
        store._mock_github.get_repo.return_value = MagicMock()
        store.ensure_repo()
        store._mock_github.get_user.assert_not_called()

    def test_creates_on_404(self, store):
        from github import GithubException

        store._mock_github.get_repo.side_effect = GithubException(404, {}, None)
        mock_user = MagicMock()
        store._mock_github.get_user.return_value = mock_user

        store.ensure_repo()
        mock_user.create_repo.assert_called_once()
