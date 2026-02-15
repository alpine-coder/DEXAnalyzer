"""NQL API client supporting execute (v2) and export (v1) modes."""

from __future__ import annotations

import gzip
import io
import time
from typing import Any

import pandas as pd
import requests

from .auth import TokenManager
from .config import Settings, load_settings


class NQLClient:
    """Wraps the Nexthink NQL API (execute + export endpoints).

    Queries are pre-saved in the Nexthink web interface and referenced
    by a queryId like ``#my_query_name``.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or load_settings()
        self._token_mgr = TokenManager(self.settings)
        self._session = requests.Session()

    def _request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        """Make an authenticated request with 429 retry logic."""
        kwargs.setdefault("timeout", 60)
        for _ in range(5):
            kwargs["headers"] = {
                **kwargs.get("headers", {}),
                **self._token_mgr.auth_header(),
            }
            resp = self._session.request(method, url, **kwargs)
            if resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", "5"))
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp
        resp.raise_for_status()
        return resp

    # ── Execute (v2 — small, fast, high-frequency, max 1000 rows) ────

    def execute(
        self,
        query_id: str,
        parameters: dict[str, str] | None = None,
        fmt: str = "json",
    ) -> pd.DataFrame:
        """Run a saved query via v2/execute and return results as a DataFrame.

        Limited to 1000 rows. Use :meth:`export` for unlimited results.

        Args:
            query_id: Saved query identifier, e.g. ``#device_inventory``.
            parameters: Key-value pairs to substitute ``$param`` placeholders.
            fmt: Response format — ``json`` or ``csv``.
        """
        body: dict[str, Any] = {"queryId": query_id}
        if parameters:
            body["parameters"] = parameters

        headers = {"Accept": "application/json" if fmt == "json" else "text/csv"}
        resp = self._request(
            "POST", self.settings.execute_url, json=body, headers=headers,
        )

        if fmt == "json":
            data = resp.json()
            return pd.DataFrame(data.get("data", []))
        return pd.read_csv(io.StringIO(resp.text))

    # ── Export (v1 — unlimited, async) ───────────────────────────────

    def export(
        self,
        query_id: str,
        parameters: dict[str, str] | None = None,
        compression: str = "GZIP",
        poll_interval: int = 5,
    ) -> pd.DataFrame:
        """Run a saved query via v1/export, poll until ready, download CSV.

        No row limit. Async workflow: submit → poll status → download.

        Args:
            query_id: Saved query identifier, e.g. ``#device_inventory``.
            parameters: Key-value pairs to substitute ``$param`` placeholders.
            compression: Download compression — ``NONE``, ``GZIP``, or ``ZSTD``.
            poll_interval: Seconds between status polls.
        """
        body: dict[str, Any] = {
            "queryId": query_id,
            "compression": compression,
        }
        if parameters:
            body["parameters"] = parameters

        resp = self._request("POST", self.settings.export_url, json=body)
        export_id = resp.json()["exportId"]

        # Poll for completion
        while True:
            status_resp = self._request(
                "GET", self.settings.status_url(export_id),
            )
            status_data = status_resp.json()
            state = status_data["status"]

            if state == "COMPLETED":
                download_url = status_data["resultsFileUrl"]
                break
            if state == "ERROR":
                raise RuntimeError(
                    f"Export failed: {status_data.get('errorDescription', 'unknown')}"
                )
            # SUBMITTED or IN_PROGRESS — keep polling
            time.sleep(poll_interval)

        # Download the CSV result (pre-signed URL, valid 15 min)
        dl_resp = requests.get(download_url, timeout=120)
        dl_resp.raise_for_status()

        raw = dl_resp.content
        # Decompress if GZIP (magic bytes 1f 8b)
        if raw[:2] == b"\x1f\x8b":
            raw = gzip.decompress(raw)
        return pd.read_csv(io.BytesIO(raw))
