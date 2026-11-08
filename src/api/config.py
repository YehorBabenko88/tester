import json
import os
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TESTER_", extra="forbid")
    database_url: SecretStr | None = None
    data_dir: Path = Path("runtime")
    host: Literal["127.0.0.1", "::1"] = "127.0.0.1"
    port: int = Field(default=8765, ge=1024, le=65535)
    min_free_bytes: int = Field(default=1024**3, ge=0)
    storage_quota_bytes: int = Field(default=100 * 1024**3, gt=0)
    lease_seconds: int = Field(default=60, ge=10, le=3600)
    max_pending: int = Field(default=256, ge=1, le=10000)
    max_running: int = Field(default=2, ge=1, le=32)
    provider_factory: str | None = None
    handler_factory: str | None = None
    frontend_dir: Path = Path("frontend")
    market_catalog_enabled: bool = False
    market_catalog_database_url: SecretStr | None = None

    @model_validator(mode="after")
    def validate_paths(self):
        self.data_dir = self.data_dir.resolve()
        if self.market_catalog_enabled and not self.market_catalog_database_url:
            raise ValueError("Enabled catalog requires explicit market_catalog_database_url")
        if self.market_catalog_database_url:
            from .catalog_store import validate_catalog_dsn

            validate_catalog_dsn(self.market_catalog_database_url.get_secret_value())
        if self.database_url:
            from psycopg.conninfo import conninfo_to_dict

            values = conninfo_to_dict(self.database_url.get_secret_value())
            if not values.get("dbname") or values["dbname"] in {
                "postgres",
                "template0",
                "template1",
            }:
                raise ValueError("A dedicated application database is required")
        return self

    @property
    def dsn(self) -> str | None:
        return self.database_url.get_secret_value() if self.database_url else None

    @property
    def market_catalog_dsn(self) -> str | None:
        return (
            self.market_catalog_database_url.get_secret_value()
            if self.market_catalog_database_url
            else None
        )


def load_settings() -> Settings:
    path = os.environ.get("TESTER_CONFIG_FILE")
    if path:
        return Settings(**json.loads(Path(path).read_text(encoding="utf-8")))
    return Settings()
