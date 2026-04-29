import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Settings:
    discord_bot_token: str
    clash_api_token: str
    clan_tag: str
    db_path: str
    discord_test_guild_id: Optional[int] = None


def load_dotenv(path=".env"):
    env_path = Path(path)
    if not env_path.exists():
        return

    for raw_line in env_path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key or key in os.environ:
            continue

        if (
            len(value) >= 2
            and value[0] == value[-1]
            and value[0] in ("'", '"')
        ):
            value = value[1:-1]

        os.environ[key] = value


def required_env(name):
    value = os.environ.get(name, "").strip()
    if not value:
        raise ConfigError(f"Missing required environment variable: {name}")
    return value


def optional_int_env(name):
    value = os.environ.get(name, "").strip()
    if not value:
        return None

    try:
        return int(value)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer.") from exc


def optional_env(name, default):
    value = os.environ.get(name, "").strip()
    return value if value else default


def load_settings(dotenv_path=".env"):
    load_dotenv(dotenv_path)

    return Settings(
        discord_bot_token=required_env("DISCORD_BOT_TOKEN"),
        clash_api_token=required_env("CLASH_API_TOKEN"),
        clan_tag=required_env("CLAN_TAG"),
        db_path=optional_env("CLASHCOMMAND_DB_PATH", "data/clashcommand.sqlite3"),
        discord_test_guild_id=optional_int_env("DISCORD_TEST_GUILD_ID"),
    )
