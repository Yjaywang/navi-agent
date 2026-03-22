#!/usr/bin/env python3
"""One-time script to initialise the GitHub memory repo structure.

Usage:
    python init_memory.py
"""

import json
import logging
import sys

from config import load_config
from memory.github_store import GitHubStore
from skills.loader import install_builtins

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)


def main() -> None:
    config = load_config()

    if not config.memory_repo_owner:
        print("ERROR: MEMORY_REPO_OWNER is not set. Add it to .env")
        sys.exit(1)

    store = GitHubStore(
        token=config.github_token,
        repo_owner=config.memory_repo_owner,
        repo_name=config.memory_repo_name,
    )

    # Create repo if it doesn't exist
    store.ensure_repo()

    # Check if already initialized
    if store.file_exists("_index/manifest.json"):
        log.info("Memory repo already initialized — nothing to do.")
        return

    # Commit initial structure
    # For empty repos, atomic_commit fails because there's no branch yet.
    # Create the first file via create_file (which bootstraps the default branch),
    # then use atomic_commit for the rest.
    files = {
        "_index/manifest.json": json.dumps({"version": 1, "entries": []}, indent=2),
        "_index/topics.json": json.dumps({}, indent=2),
        "_index/users.json": json.dumps({}, indent=2),
        "README.md": (
            "# Claude Agent Memory\n\n"
            "This repository stores persistent memory for the Discord Claude Agent.\n\n"
            "**Do not edit files manually** — they are managed by the agent.\n"
        ),
    }

    # Bootstrap: create README first to initialize the branch
    store.create_or_update_file("README.md", files.pop("README.md"), "Initialize memory repo")

    # Now the branch exists — commit remaining files atomically
    store.atomic_commit(files, "Add initial memory structure")

    # Initialize skills registry
    skills_registry = json.dumps({"version": 0, "skills": []}, indent=2)
    store.create_or_update_file(
        "skills/registry.json", skills_registry, "Add skills registry",
    )

    # Install builtin skills (summarize, translate)
    installed = install_builtins(store)
    if installed:
        log.info(
            "Installed %d builtin skill(s): %s",
            len(installed),
            ", ".join(m.name for m, _ in installed),
        )

    log.info("Memory repo initialized successfully!")


if __name__ == "__main__":
    main()
