"""OAuth 2.0 client-credentials token management for Nexthink API."""

from __future__ import annotations

import time

import requests

from .config import Settings


class TokenManager:
    """Handles OAuth 2.0 client credentials flow with caching."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._token: str | None = None
        self._expires_at: float = 0.0

    @property
    def is_expired(self) -> bool:
        return time.time() >= self._expires_at

    def get_token(self) -> str:
        """Return a valid access token, refreshing if needed."""
        if self._token and not self.is_expired:
            return self._token
        return self._refresh()

    def _refresh(self) -> str:
        resp = requests.post(
            self._settings.token_url,
            data={
                "grant_type": "client_credentials",
                "scope": "service:integration",
            },
            auth=(self._settings.client_id, self._settings.client_secret),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        self._token = data["access_token"]
        # Token expires in 900s (15 min); refresh 60s early
        self._expires_at = time.time() + data.get("expires_in", 900) - 60
        return self._token

    def auth_header(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.get_token()}"}
