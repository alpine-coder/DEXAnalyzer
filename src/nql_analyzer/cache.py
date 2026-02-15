"""File-based query cache using Parquet for persistent NQL result storage."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any

import pandas as pd

from .client import NQLClient

log = logging.getLogger(__name__)

DEFAULT_CACHE_DIR = Path(".cache")
DEFAULT_TTL = 3600  # 1 hour


def _cache_key(query_id: str, parameters: dict[str, str] | None) -> str:
    """Build a deterministic cache key from query ID and parameters."""
    # Strip leading # for the filename portion
    name = query_id.lstrip("#")
    param_str = json.dumps(parameters or {}, sort_keys=True)
    param_hash = hashlib.sha256(param_str.encode()).hexdigest()[:8]
    return f"{name}_{param_hash}"


class QueryCache:
    """Caches NQL query results as Parquet files with TTL expiry.

    Usage::

        cache = QueryCache(client)
        df = cache.execute("#call_analysis_calls")
        # Second call hits disk, not the API
        df = cache.execute("#call_analysis_calls")
    """

    def __init__(
        self,
        client: NQLClient | None = None,
        cache_dir: Path | str = DEFAULT_CACHE_DIR,
        ttl: int = DEFAULT_TTL,
    ) -> None:
        self._client = client or NQLClient()
        self._cache_dir = Path(cache_dir)
        self._ttl = ttl
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    def _parquet_path(self, key: str) -> Path:
        return self._cache_dir / f"{key}.parquet"

    def _meta_path(self, key: str) -> Path:
        return self._cache_dir / f"{key}.meta.json"

    def _read_meta(self, key: str) -> dict[str, Any] | None:
        meta_path = self._meta_path(key)
        if not meta_path.exists():
            return None
        return json.loads(meta_path.read_text())

    def _write(self, key: str, df: pd.DataFrame, query_id: str, parameters: dict[str, str] | None) -> None:
        df.to_parquet(self._parquet_path(key))
        meta = {
            "query_id": query_id,
            "parameters": parameters or {},
            "fetched_at": time.time(),
            "rows": len(df),
        }
        self._meta_path(key).write_text(json.dumps(meta, indent=2))

    def _is_valid(self, key: str) -> bool:
        meta = self._read_meta(key)
        if meta is None:
            return False
        if not self._parquet_path(key).exists():
            return False
        age = time.time() - meta["fetched_at"]
        return age < self._ttl

    def execute(
        self,
        query_id: str,
        parameters: dict[str, str] | None = None,
        force_refresh: bool = False,
    ) -> pd.DataFrame:
        """Export a query (unlimited rows), returning cached results when available.

        Uses the v1/export async endpoint which has no row limit,
        unlike v2/execute which caps at 1000 rows.

        Args:
            query_id: Saved query identifier, e.g. ``#call_analysis_calls``.
            parameters: Query parameter substitutions.
            force_refresh: Bypass cache and fetch fresh data.
        """
        key = _cache_key(query_id, parameters)

        if not force_refresh and self._is_valid(key):
            meta = self._read_meta(key)
            age = int(time.time() - meta["fetched_at"])
            log.info(
                "%s — cache hit (%d rows, cached %ds ago)",
                query_id, meta["rows"], age,
            )
            return pd.read_parquet(self._parquet_path(key))

        log.info("%s — querying API (export)...", query_id)
        t0 = time.time()
        df = self._client.export(query_id, parameters=parameters)
        elapsed = time.time() - t0
        log.info(
            "%s — received %d rows in %.1fs, writing to cache",
            query_id, len(df), elapsed,
        )
        self._write(key, df, query_id, parameters)
        return df

    def info(self, query_id: str, parameters: dict[str, str] | None = None) -> dict[str, Any] | None:
        """Return cache metadata for a query, or None if not cached."""
        key = _cache_key(query_id, parameters)
        meta = self._read_meta(key)
        if meta is None:
            return None
        age = time.time() - meta["fetched_at"]
        return {**meta, "age_seconds": round(age), "valid": age < self._ttl}

    def clear(self, query_id: str | None = None, parameters: dict[str, str] | None = None) -> int:
        """Remove cached entries. If query_id is None, clear everything.

        Returns the number of entries removed.
        """
        if query_id is not None:
            key = _cache_key(query_id, parameters)
            removed = 0
            for path in [self._parquet_path(key), self._meta_path(key)]:
                if path.exists():
                    path.unlink()
                    removed += 1
            return min(removed, 1)

        count = 0
        for path in self._cache_dir.glob("*.parquet"):
            path.unlink()
            meta = path.with_suffix(".meta.json")
            if meta.exists():
                meta.unlink()
            count += 1
        return count
