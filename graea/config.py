"""Settings. Env vars with prefix GRAEA_, or a .env file in cwd."""
from __future__ import annotations

from pathlib import Path
from typing import Literal, Optional

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # env_ignore_empty: an empty `GRAEA_API_ID=` / `GRAEA_PHONE=` line in .env
    # (exactly what a fresh install writes before the human fills it in)
    # must fall back to the field default (None) instead of pydantic trying
    # to parse "" as an int and crashing every command, doctor included.
    model_config = SettingsConfigDict(env_prefix="GRAEA_", env_file=".env",
                                      env_file_encoding="utf-8", extra="ignore",
                                      env_ignore_empty=True)

    # Telegram test-user account (my.telegram.org)
    api_id: Optional[int] = None
    api_hash: Optional[str] = None
    phone: Optional[str] = None
    session: Path = Path("./data/graea.session")
    session_string: Optional[str] = None  # StringSession, takes priority over `session` file

    # Target
    bot: Optional[str] = None  # "@username" or "username"

    # Storage
    db: Path = Path("./data/graea.duckdb")
    shots: Path = Path("./data/shots")

    # Visual eye
    web_profile: Path = Path("./data/web-profile")
    web_headless: bool = True
    web_url: str = "https://web.telegram.org/k/"
    web_viewport_width: int = 1100
    web_viewport_height: int = 900
    web_settle_ms: int = 800
    web_login_settle_s: int = 20  # how long is_logged_in() waits for a positive signal after launch
    two_fa_password: Optional[str] = Field(None, validation_alias=AliasChoices("GRAEA_2FA_PASSWORD", "GRAEA_TWO_FA_PASSWORD"))
    selectors_file: Optional[Path] = None  # JSON {role: [css, ...]} tried before the built-in SELECTORS  # wait after action before screenshot

    # Timing
    reply_timeout_ms: int = 8000
    quiet_ms: int = 1200  # after first reply, keep collecting until this long with no new events
    connect_timeout_s: int = 20  # cap on the first MTProto connect (can be slow on first run)

    # Reader
    vision_provider: Literal["caller", "openai_compatible", "anthropic", "ocr", "none"] = "caller"
    vision_base_url: str = "http://localhost:11434/v1"
    vision_model: str = "llama3.2-vision"
    vision_api_key: str = "ollama"
    vision_timeout_s: int = 120
    ocr: Literal["auto", "on", "off"] = "auto"

    # HTTP interface
    http_host: str = "127.0.0.1"
    http_port: int = 8765

    # Update check (graea/version.py)
    check_updates: bool = True

    def bot_username(self) -> Optional[str]:
        if not self.bot:
            return None
        return self.bot if self.bot.startswith("@") else f"@{self.bot}"

    def ensure_dirs(self) -> None:
        self.db.parent.mkdir(parents=True, exist_ok=True)
        self.shots.mkdir(parents=True, exist_ok=True)
        self.session.parent.mkdir(parents=True, exist_ok=True)
        self.web_profile.mkdir(parents=True, exist_ok=True)


def get_settings(**overrides) -> Settings:
    return Settings(**overrides)
