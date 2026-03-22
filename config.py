import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass
class Config:
    discord_token: str
    anthropic_api_key: str
    model: str
    github_token: str
    memory_repo_owner: str
    memory_repo_name: str


def load_config() -> Config:
    load_dotenv()

    discord_token = os.environ.get("DISCORD_TOKEN", "")
    if not discord_token:
        raise ValueError("DISCORD_TOKEN environment variable is required")

    anthropic_api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not anthropic_api_key:
        raise ValueError("ANTHROPIC_API_KEY environment variable is required")

    github_token = os.environ.get("GITHUB_TOKEN", "")
    if not github_token:
        raise ValueError("GITHUB_TOKEN environment variable is required")

    model = os.environ.get("MODEL", "claude-sonnet-4-6")
    memory_repo_owner = os.environ.get("MEMORY_REPO_OWNER", "")
    memory_repo_name = os.environ.get("MEMORY_REPO_NAME", "claude-agent-memory")

    return Config(
        discord_token=discord_token,
        anthropic_api_key=anthropic_api_key,
        model=model,
        github_token=github_token,
        memory_repo_owner=memory_repo_owner,
        memory_repo_name=memory_repo_name,
    )
