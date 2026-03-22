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
    session_ttl_minutes: int
    max_turns: int
    log_level: str


def _parse_int(env_var: str, default: str) -> int:
    raw = os.environ.get(env_var, default)
    try:
        return int(raw)
    except ValueError:
        raise ValueError(f"{env_var} must be an integer, got: {raw!r}") from None


def _parse_role_ids(env_var: str) -> set[int]:
    raw = os.environ.get(env_var, "")
    try:
        return {int(x.strip()) for x in raw.split(",") if x.strip()}
    except ValueError:
        raise ValueError(
            f"{env_var} must be comma-separated integer role IDs, got: {raw!r}"
        ) from None


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
        admin_role_ids=_parse_role_ids("DISCORD_ADMIN_ROLE_IDS"),
        trusted_role_ids=_parse_role_ids("DISCORD_TRUSTED_ROLE_IDS"),
        session_ttl_minutes=_parse_int("SESSION_TTL_MINUTES", "60"),
        rate_limit_everyone=_parse_int("RATE_LIMIT_EVERYONE", "20"),
        rate_limit_trusted=_parse_int("RATE_LIMIT_TRUSTED", "100"),
        max_turns=_parse_int("MAX_TURNS", "20"),
        log_level=os.environ.get("LOG_LEVEL", "INFO"),
    )
