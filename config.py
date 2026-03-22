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
    admin_role_ids: set[int]
    trusted_role_ids: set[int]
    rate_limit_everyone: int
    rate_limit_trusted: int
    max_turns: int
    log_level: str


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

    raw_admin = os.environ.get("DISCORD_ADMIN_ROLE_IDS", "")
    admin_role_ids = {int(x.strip()) for x in raw_admin.split(",") if x.strip()}
    raw_trusted = os.environ.get("DISCORD_TRUSTED_ROLE_IDS", "")
    trusted_role_ids = {int(x.strip()) for x in raw_trusted.split(",") if x.strip()}

    rate_limit_everyone = int(os.environ.get("RATE_LIMIT_EVERYONE", "20"))
    rate_limit_trusted = int(os.environ.get("RATE_LIMIT_TRUSTED", "100"))
    max_turns = int(os.environ.get("MAX_TURNS", "20"))
    log_level = os.environ.get("LOG_LEVEL", "INFO")

    return Config(
        discord_token=discord_token,
        anthropic_api_key=anthropic_api_key,
        model=model,
        github_token=github_token,
        memory_repo_owner=memory_repo_owner,
        memory_repo_name=memory_repo_name,
        admin_role_ids=admin_role_ids,
        trusted_role_ids=trusted_role_ids,
        rate_limit_everyone=rate_limit_everyone,
        rate_limit_trusted=rate_limit_trusted,
        max_turns=max_turns,
        log_level=log_level,
    )
