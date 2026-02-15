# DEXAnalyzer

Nexthink NQL API toolkit for querying, caching, and analyzing Digital Employee Experience data.

## Features

- **NQL API client** — OAuth 2.0 auth, v2/execute (fast, 1k limit) and v1/export (unlimited, async) endpoints
- **Persistent query cache** — Parquet-based file cache with configurable TTL to avoid repeated API calls
- **Analysis tools** — One file per tool, operating on cached DataFrames
- **AI summaries** — Optional Claude-powered natural language insights (requires Anthropic API key)

## Setup

```bash
pip install -e .
```

Copy `.env.example` to `.env` and fill in your credentials:

```
NEXTHINK_INSTANCE=yourcompany
NEXTHINK_REGION=eu
NEXTHINK_CLIENT_ID=...
NEXTHINK_CLIENT_SECRET=...
```

Optional settings:

```
ANTHROPIC_API_KEY=sk-ant-...    # Enables AI summaries
NQL_LOG_LEVEL=INFO              # DEBUG, INFO, WARNING (default)
```

## Analysis Tools

### Call CPU Analysis

Compares CPU efficiency of Teams vs Zoom during calls by matching 15-minute execution samples to call time windows on the same device.

```bash
python -m nql_analyzer.tools.call_cpu_analysis
```

**How it works:**

1. Downloads call data (`#call_analysis_calls`) and execution samples (`#call_analysis_executions`) via the export API
2. Maps call apps to binaries (Teams → `msteams`/`ms-teams.exe`, Zoom → `zoom.us`/`zoom.exe`)
3. For each call, finds execution samples fully contained within the call window on the same device
4. Computes average CPU % per second from matched samples, extrapolates to full call duration
5. Reports summary by app+platform and detail by binary+platform with percentiles

## Project Structure

```
src/nql_analyzer/
├── config.py       # Settings from .env (instance, region, credentials)
├── auth.py         # OAuth 2.0 token management with auto-refresh
├── client.py       # NQL API client (execute + export)
├── cache.py        # Parquet file cache with TTL
└── tools/
    └── call_cpu_analysis.py   # Teams vs Zoom CPU efficiency
```

## NQL Queries

Queries are pre-saved in the Nexthink web interface and referenced by ID (e.g. `#call_analysis_calls`). The export API is used by default for unlimited results.

## License

MIT — see [LICENSE](LICENSE).
