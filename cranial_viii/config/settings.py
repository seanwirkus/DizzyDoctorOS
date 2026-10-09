"""
CranialVIII Configuration Settings
Supports environment variables from .env with fallbacks for local and clinical deployment.
"""

from pathlib import Path
from typing import List
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    # Database
    POSTGRES_HOST: str = "127.0.0.1"
    POSTGRES_PORT: int = 5432
    POSTGRES_DB: str = "dizzy"
    POSTGRES_USER: str = "dizzy"
    POSTGRES_PASSWORD: str = ""
    DATABASE_URL: str = "postgresql://dizzy@127.0.0.1:5432/dizzy"
    PGSUPERUSER: str = "postgres"
    PGSUPERPASS: str = ""

    # Source & Filesystem
    SOURCE_ROOT: str = "./data/raw"
    CRAWL_LIMIT: int = 0

    # Key Encryption & Sessions
    KEK_PATH: str = "./keys/master.kek"
    KEK_ID: str = "kek-2026-09"
    SESSION_SECRET: str = "dev-secret-key-change-in-production"
    SESSION_TTL_MINUTES: int = 30

    # Microsoft Graph
    GRAPH_TENANT_ID: str = ""
    GRAPH_CLIENT_ID: str = ""
    GRAPH_CLIENT_SECRET: str = ""
    GRAPH_MAILBOXES: str = "staff1@example.com,staff2@example.com"

    # PaperEpley Fax Slayer
    PAPER_EPLEY_WATCH_DIR: str = "./data/faxes/inbound"
    PAPER_EPLEY_PROCESSED_DIR: str = "./data/records/filed"
    PAPER_EPLEY_CONFLICT_DIR: str = "./data/records/conflicts"
    PAPER_EPLEY_POLL_INTERVAL_SEC: int = 30

    # Offline Sovereign AI
    OFFLINE_AI_BASE_URL: str = "http://127.0.0.1:11434/v1"
    OFFLINE_AI_API_KEY: str = "ollama"
    OFFLINE_AI_MODEL: str = "qwen2.5:14b-instruct-q4_K_M"
    OFFLINE_EMBED_MODEL: str = "nomic-embed-text:latest"

    # Web Dashboard
    WEB_HOST: str = "127.0.0.1"
    WEB_PORT: int = 8008
    DEBUG: bool = False

    @property
    def mailbox_list(self) -> List[str]:
        if not self.GRAPH_MAILBOXES:
            return []
        return [m.strip() for m in self.GRAPH_MAILBOXES.split(",") if m.strip()]


settings = Settings()
