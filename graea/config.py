"""Settings. Env vars with prefix GRAEA_, or a .env file in cwd."""
from __future__ import annotations

from pathlib import Path
from typing import Literal, Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GRAEA_", env_file=".env",
                                      env_file_encoding="utf-8", extra="ignore")

    # Telegram test-user account (my.telegram.org)
    api_id: Optional[int] = None
    api_hash: Optional[str] = None
    phone: Optional[str] = None
    session: Path = Path("./data/graea.session")

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
    web_settle_ms: int = 800  # wait after action before screenshot

    # Timing
    reply_timeout_ms: int = 8000
    quiet_ms: int = 1200  # after first reply, keep collecting until this long with no new events

    # Reader
    vision_provider: Literal["openai_compatible", "anthropic", "ocr", "none"] = "openai_compatible"
    vision_base_url: str = "http://localhost:11434/v1"
    vision_model: str = "llama3.2-vision"
    vision_api_key: str = "ollama"
    vision_timeout_s: int = 120
    ocr: Literal["auto", "on", "off"] = "auto"

    # HTTP interface
    http_host: str = "127.0.0.1"
    http_port: int = 8765

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
