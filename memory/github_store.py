"""Thin PyGithub wrapper for file CRUD on the memory repo."""

from __future__ import annotations

import logging
from typing import Any

from github import Github, GithubException, InputGitTreeElement

log = logging.getLogger(__name__)


class SHAConflictError(Exception):
    """Raised when an optimistic-locking SHA mismatch occurs."""


class GitHubStore:
    """Read/write files in a GitHub repo used as persistent memory storage."""

    def __init__(self, token: str, repo_owner: str, repo_name: str) -> None:
        self._github = Github(token)
        self._repo_full_name = f"{repo_owner}/{repo_name}"
        self._repo_cache = None

    @property
    def _repo(self):
        if self._repo_cache is None:
            self._repo_cache = self._github.get_repo(self._repo_full_name)
        return self._repo_cache

    # --- Read ---

    def get_file(self, path: str) -> tuple[str, str]:
        """Return (decoded_content, sha) for a file in the repo."""
        try:
            contents = self._repo.get_contents(path)
            return contents.decoded_content.decode(), contents.sha
        except GithubException as exc:
            if exc.status == 404:
                raise FileNotFoundError(f"{path} not found in {self._repo_full_name}") from exc
            raise

    def get_binary_file(self, path: str) -> tuple[bytes, str]:
        """Return (raw_bytes, sha) for a file in the repo."""
        try:
            contents = self._repo.get_contents(path)
            return contents.decoded_content, contents.sha
        except GithubException as exc:
            if exc.status == 404:
                raise FileNotFoundError(f"{path} not found in {self._repo_full_name}") from exc
            raise

    def file_exists(self, path: str) -> bool:
        try:
            self._repo.get_contents(path)
            return True
        except GithubException as exc:
            if exc.status == 404:
                return False
            raise

    def list_directory(self, path: str) -> list[str]:
        """Return file paths under *path*."""
        try:
            contents = self._repo.get_contents(path)
            if not isinstance(contents, list):
                return [contents.path]
            return [c.path for c in contents]
        except GithubException as exc:
            if exc.status == 404:
                return []
            raise

    # --- Write (single file) ---

    def create_or_update_file(
        self,
        path: str,
        content: str,
        message: str,
        sha: str | None = None,
    ) -> str:
        """Create or update a single file. Returns the new SHA.

        If *sha* is given the file is updated (optimistic lock).
        If *sha* is ``None`` the file is created.
        Raises ``SHAConflictError`` on 409/422 conflicts.
        """
        try:
            if sha is not None:
                result = self._repo.update_file(path, message, content, sha)
            else:
                result = self._repo.create_file(path, message, content)
            return result["content"].sha
        except GithubException as exc:
            if exc.status in (409, 422):
                raise SHAConflictError(
                    f"SHA conflict writing {path}: {exc.data}"
                ) from exc
            raise

    # --- Write (atomic multi-file commit) ---

    def atomic_commit(
        self,
        files: dict[str, str],
        message: str,
        delete_paths: list[str] | None = None,
    ) -> str:
        """Commit multiple files atomically using the Git Data API.

        *files* maps repo-relative paths to their string content.
        *delete_paths* is an optional list of repo-relative paths to remove.
        Returns the new commit SHA.
        """
        repo = self._repo
        ref = repo.get_git_ref("heads/main")
        parent_sha = ref.object.sha
        parent_commit = repo.get_git_commit(parent_sha)
        base_tree = parent_commit.tree

        tree_elements = [
            InputGitTreeElement(path=path, mode="100644", type="blob", content=content)
            for path, content in files.items()
        ]

        # To delete a file via the Git Data API, create a tree element with sha
        # set to None — this removes the entry from the tree.
        for path in delete_paths or []:
            tree_elements.append(
                InputGitTreeElement(path=path, mode="100644", type="blob", sha=None)
            )

        new_tree = repo.create_git_tree(tree_elements, base_tree=base_tree)
        new_commit = repo.create_git_commit(message, new_tree, [parent_commit])
        ref.edit(new_commit.sha)

        log.info("Atomic commit %s: %s (%d files)", new_commit.sha[:8], message, len(files))
        return new_commit.sha

    # --- Write (binary file) ---

    def store_binary_file(self, path: str, data: bytes, message: str) -> str:
        """Store binary content (e.g., images) using the Git Data API.

        Returns the new commit SHA.
        """
        import base64

        repo = self._repo
        b64_content = base64.b64encode(data).decode()

        blob = repo.create_git_blob(b64_content, "base64")
        ref = repo.get_git_ref("heads/main")
        parent_sha = ref.object.sha
        parent_commit = repo.get_git_commit(parent_sha)

        tree_element = InputGitTreeElement(
            path=path, mode="100644", type="blob", sha=blob.sha,
        )
        new_tree = repo.create_git_tree([tree_element], base_tree=parent_commit.tree)
        new_commit = repo.create_git_commit(message, new_tree, [parent_commit])
        ref.edit(new_commit.sha)

        log.info("Stored binary file %s (%d bytes)", path, len(data))
        return new_commit.sha

    # --- Repo creation (for init script) ---

    def ensure_repo(self, description: str = "Claude Agent Memory Store") -> None:
        """Create the repo if it doesn't exist."""
        try:
            self._github.get_repo(self._repo_full_name)
            log.info("Repo %s already exists", self._repo_full_name)
        except GithubException as exc:
            if exc.status == 404:
                user = self._github.get_user()
                repo_name = self._repo_full_name.split("/", 1)[1]
                user.create_repo(
                    repo_name,
                    private=True,
                    description=description,
                    auto_init=False,
                )
                self._repo_cache = None  # reset cache
                log.info("Created repo %s", self._repo_full_name)
            else:
                raise
