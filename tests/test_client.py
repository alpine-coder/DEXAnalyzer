"""Unit tests for config, auth, and client export parsing."""

from __future__ import annotations

import gzip
from unittest.mock import MagicMock, patch

import pytest

from nql_analyzer.config import Settings, load_settings


class TestSettings:
    def test_load_settings_from_env(self, monkeypatch):
        monkeypatch.setenv("NEXTHINK_INSTANCE", "acme")
        monkeypatch.setenv("NEXTHINK_REGION", "us")
        monkeypatch.setenv("NEXTHINK_CLIENT_ID", "id123")
        monkeypatch.setenv("NEXTHINK_CLIENT_SECRET", "secret456")

        settings = load_settings()
        assert settings.instance == "acme"
        assert settings.region == "us"
        assert settings.client_id == "id123"

    def test_urls(self):
        s = Settings("acme", "us", "id", "secret")
        assert s.base_url == "https://acme.api.us.nexthink.cloud/api"
        assert s.token_url == "https://acme-login.us.nexthink.cloud/oauth2/default/v1/token"
        assert s.execute_url == "https://acme.api.us.nexthink.cloud/api/v2/nql/execute"
        assert s.export_url == "https://acme.api.us.nexthink.cloud/api/v1/nql/export"
        assert s.status_url("abc") == "https://acme.api.us.nexthink.cloud/api/v1/nql/status/abc"

    def test_missing_env_raises(self, monkeypatch, tmp_path):
        monkeypatch.delenv("NEXTHINK_INSTANCE", raising=False)
        monkeypatch.delenv("NEXTHINK_REGION", raising=False)
        monkeypatch.delenv("NEXTHINK_CLIENT_ID", raising=False)
        monkeypatch.delenv("NEXTHINK_CLIENT_SECRET", raising=False)

        # Point to an empty .env so dotenv doesn't load the real one
        empty_env = tmp_path / ".env"
        empty_env.write_text("")

        with pytest.raises(EnvironmentError, match="Missing required"):
            load_settings(env_path=empty_env)


class TestExportParsing:
    def _make_client(self):
        from nql_analyzer.client import NQLClient
        return NQLClient(Settings("acme", "us", "id", "secret"))

    def test_export_gzip_csv(self):
        """Export downloads GZIP-compressed CSV and decompresses it."""
        import requests
        client = self._make_client()

        csv_bytes = b"name,value\nPC1,10\nPC2,20\n"
        gzipped = gzip.compress(csv_bytes)

        # Mock the 3 requests: POST export, GET status, GET download
        export_resp = MagicMock()
        export_resp.json.return_value = {"exportId": "ex123"}
        export_resp.status_code = 200
        export_resp.raise_for_status = MagicMock()

        status_resp = MagicMock()
        status_resp.json.return_value = {"status": "COMPLETED", "resultsFileUrl": "https://s3/file.csv.gz"}
        status_resp.status_code = 200
        status_resp.raise_for_status = MagicMock()

        download_resp = MagicMock()
        download_resp.content = gzipped
        download_resp.status_code = 200
        download_resp.raise_for_status = MagicMock()

        with patch.object(client, "_request", side_effect=[export_resp, status_resp]) as mock_req, \
             patch.object(requests, "get", return_value=download_resp):
            df = client.export("#test")

        assert len(df) == 2
        assert list(df.columns) == ["name", "value"]

    def test_export_uncompressed_csv(self):
        """Export with NONE compression returns plain CSV."""
        import requests
        client = self._make_client()

        export_resp = MagicMock()
        export_resp.json.return_value = {"exportId": "ex456"}
        export_resp.status_code = 200
        export_resp.raise_for_status = MagicMock()

        status_resp = MagicMock()
        status_resp.json.return_value = {"status": "COMPLETED", "resultsFileUrl": "https://s3/file.csv"}
        status_resp.status_code = 200
        status_resp.raise_for_status = MagicMock()

        download_resp = MagicMock()
        download_resp.content = b"a,b\n1,2\n"
        download_resp.status_code = 200
        download_resp.raise_for_status = MagicMock()

        with patch.object(client, "_request", side_effect=[export_resp, status_resp]), \
             patch.object(requests, "get", return_value=download_resp):
            df = client.export("#test", compression="NONE")

        assert len(df) == 1
        assert list(df.columns) == ["a", "b"]
