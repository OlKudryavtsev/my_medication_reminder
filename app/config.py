from __future__ import annotations

from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    bot_token: str
    database_url: str = "sqlite+aiosqlite:///./medkid.db"
    app_base_url: str = ""
    parent_chat_ids: str = ""
    child_chat_id: str = ""
    timezone: str = "Europe/Berlin"
    reminder_interval_minutes: int = 7
    snooze_minutes: int = 30
    allow_dev_initdata: bool = False
    overdue_alert_minutes: int = 30
    low_stock_check_hour: int = 9
    default_breakfast_time: str = "08:00"
    default_lunch_time: str = "13:30"
    default_dinner_time: str = "19:30"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def normalized_app_base_url(self) -> str:
        url = (self.app_base_url or "").strip()
        if not url:
            return ""
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        if url.startswith("http://") and "localhost" not in url and "127.0.0.1" not in url:
            url = "https://" + url[len("http://"):]
        return url.rstrip("/")

    @property
    def app_url(self) -> str:
        base = self.normalized_app_base_url
        if not base:
            return ""
        if base.endswith("/app"):
            return base
        return base + "/app"

    @property
    def admin_url(self) -> str:
        app_url = self.app_url
        return app_url + "#admin" if app_url else ""

    @property
    def parents(self) -> list[int]:
        values: list[int] = []
        for item in self.parent_chat_ids.split(","):
            item = item.strip()
            if item:
                values.append(int(item))
        return values

    @property
    def child(self) -> int | None:
        return int(self.child_chat_id) if self.child_chat_id.strip() else None


@lru_cache
def get_settings() -> Settings:
    return Settings()
