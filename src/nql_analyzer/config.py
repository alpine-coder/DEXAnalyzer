"""Settings loaded from environment variables / .env file."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    instance: str
    region: str
    client_id: str
    client_secret: str

    @property
    def base_url(self) -> str:
        return f"https://{self.instance}.api.{self.region}.nexthink.cloud/api"

    @property
    def token_url(self) -> str:
        return f"https://{self.instance}-login.{self.region}.nexthink.cloud/oauth2/default/v1/token"

    @property
    def execute_url(self) -> str:
        return f"{self.base_url}/v2/nql/execute"

    @property
    def export_url(self) -> str:
        return f"{self.base_url}/v1/nql/export"

    def status_url(self, export_id: str) -> str:
        return f"{self.base_url}/v1/nql/status/{export_id}"


def load_settings(env_path: str | Path | None = None) -> Settings:
    """Load settings from .env file and environment variables."""
    load_dotenv(env_path or ".env")

    instance = os.environ.get("NEXTHINK_INSTANCE", "")
    region = os.environ.get("NEXTHINK_REGION", "")
    client_id = os.environ.get("NEXTHINK_CLIENT_ID", "")
    client_secret = os.environ.get("NEXTHINK_CLIENT_SECRET", "")

    missing = []
    if not instance:
        missing.append("NEXTHINK_INSTANCE")
    if not region:
        missing.append("NEXTHINK_REGION")
    if not client_id:
        missing.append("NEXTHINK_CLIENT_ID")
    if not client_secret:
        missing.append("NEXTHINK_CLIENT_SECRET")

    if missing:
        raise EnvironmentError(
            f"Missing required environment variables: {', '.join(missing)}"
        )

    return Settings(
        instance=instance,
        region=region,
        client_id=client_id,
        client_secret=client_secret,
    )
