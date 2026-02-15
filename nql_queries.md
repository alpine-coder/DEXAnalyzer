# NQL Queries

These queries must be created in the Nexthink web interface under **NQL API queries** before running the analysis tools. Each query is referenced by its `queryId` (e.g. `#call_analysis_calls`).

## Call CPU Analysis Tool

Used by: `python -m nql_analyzer.tools.call_cpu_analysis`

### `#call_analysis_calls`

Returns all collaboration calls (Teams and Zoom) from the past 7 days with timestamps and device info.

```nql
collaboration.sessions during past 168h
| list session.call.start_time, session.call.end_time, session.call.type, session.application.type, device.name
```

**Columns:**

| Column | Type | Description |
|--------|------|-------------|
| `collaboration.session.call.start_time` | datetime | Call start timestamp |
| `collaboration.session.call.end_time` | datetime | Call end timestamp |
| `collaboration.session.call.type` | string | `peer_to_peer`, `group_call`, or `unknown` |
| `collaboration.session.application.type` | string | `Teams` or `Zoom` |
| `device.name` | string | Device identifier (e.g. `NXT-ABC123`) |

### `#call_analysis_executions`

Returns 15-minute execution samples with CPU usage for Teams and Zoom binaries from the past 7 days.

```nql
execution.events during past 168h
| where binary.name in ["zoom.exe", "zoom.us", "msteams", "ms-teams.exe"]
| list execution.event.start_time, execution.event.end_time, execution.event.bucket_duration, execution.event.cpu_time, execution.event.real_memory, binary.name, binary.platform, device.name
| sort execution.event.start_time desc
```

**Columns:**

| Column | Type | Description |
|--------|------|-------------|
| `execution.event.start_time` | datetime | Sample bucket start (aligned to 15-min) |
| `execution.event.end_time` | datetime | Sample bucket end |
| `execution.event.bucket_duration` | int | Always `900` (seconds) |
| `execution.event.cpu_time` | float | CPU time consumed in this bucket (seconds) |
| `execution.event.real_memory` | float | Memory usage (bytes) |
| `binary.name` | string | `msteams`, `ms-teams.exe`, `zoom.us`, or `zoom.exe` |
| `binary.platform` | string | `macOS` or `Windows` |
| `device.name` | string | Device identifier |

## Notes

- Queries are executed via the **v1/export** API (unlimited rows, async)
- Results are cached locally as Parquet files in `.cache/` with a 1-hour TTL
- Both queries use a 168h (7 day) lookback window
