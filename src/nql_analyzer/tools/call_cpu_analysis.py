"""Analysis tool: CPU efficiency comparison between Teams and Zoom calls.

Downloads call data and execution samples, then computes CPU time per
second of call for each application by matching 15-minute execution
buckets to calls on the same device.
"""

from __future__ import annotations

import logging
import os

import numpy as np
import pandas as pd

logging.basicConfig(
    level=os.environ.get("NQL_LOG_LEVEL", "WARNING").upper(),
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)

from ..cache import QueryCache

# Map call application names → execution binary names
APP_TO_BINARIES: dict[str, list[str]] = {
    "Teams": ["msteams", "ms-teams.exe"],
    "Zoom": ["zoom.us", "zoom.exe"],
}

SAMPLE_DURATION = 900  # 15 minutes in seconds


def _load_data(cache: QueryCache) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Fetch and prepare calls and executions DataFrames."""
    df_calls = cache.execute("#call_analysis_calls")
    df_exec = cache.execute("#call_analysis_executions")

    # Parse timestamps
    df_calls["start"] = pd.to_datetime(df_calls["collaboration.session.call.start_time"])
    df_calls["end"] = pd.to_datetime(df_calls["collaboration.session.call.end_time"])
    df_calls["app"] = df_calls["collaboration.session.application.type"]
    df_calls["device"] = df_calls["device.name"]

    df_exec["sample_start"] = pd.to_datetime(df_exec["execution.event.start_time"])
    df_exec["sample_end"] = pd.to_datetime(df_exec["execution.event.end_time"])
    df_exec["cpu_time"] = df_exec["execution.event.cpu_time"]
    df_exec["binary"] = df_exec["binary.name"]
    df_exec["platform"] = df_exec["binary.platform"]
    df_exec["device"] = df_exec["device.name"]

    return df_calls, df_exec


def _build_binary_col(df_calls: pd.DataFrame) -> pd.DataFrame:
    """Expand each call into rows per matching binary name."""
    rows = []
    for app, binaries in APP_TO_BINARIES.items():
        mask = df_calls["app"] == app
        for binary in binaries:
            subset = df_calls[mask].copy()
            subset["binary"] = binary
            rows.append(subset)
    return pd.concat(rows, ignore_index=True)


def run(cache: QueryCache | None = None) -> pd.DataFrame:
    """Run the full CPU efficiency analysis.

    Returns a DataFrame with one row per call containing:
    - app, device, call start/end
    - n_samples: number of execution samples matched
    - sample_cpu_total: sum of cpu_time across matched samples
    - sample_duration_s: n_samples x 900s
    - avg_cpu_per_sec: sample_cpu_total / sample_duration_s
    - call_duration_s: total call length in seconds
    - estimated_call_cpu: avg_cpu_per_sec x call_duration_s
    """
    if cache is None:
        cache = QueryCache()

    df_calls, df_exec = _load_data(cache)

    # Drop calls with no device (can't match executions)
    df_calls = df_calls.dropna(subset=["device"]).copy()
    df_calls["call_duration_s"] = (df_calls["end"] - df_calls["start"]).dt.total_seconds()
    df_calls["call_id"] = np.arange(len(df_calls))

    # Expand calls: one row per (call, binary) so we can merge with executions
    df_calls_exp = _build_binary_col(df_calls)

    # Merge calls with executions on (device, binary)
    merged = df_calls_exp.merge(df_exec, on=["device", "binary"], how="inner")

    # Keep only samples fully contained within the call
    merged = merged[
        (merged["sample_start"] >= merged["start"])
        & (merged["sample_end"] <= merged["end"])
    ]

    if merged.empty:
        return pd.DataFrame()

    # Aggregate: sum cpu_time and count samples per (call, binary, platform)
    agg = (
        merged.groupby(["call_id", "binary", "platform"])
        .agg(
            app=("app", "first"),
            device=("device", "first"),
            call_start=("start", "first"),
            call_end=("end", "first"),
            call_duration_s=("call_duration_s", "first"),
            n_samples=("cpu_time", "size"),
            sample_cpu_total=("cpu_time", "sum"),
        )
    )

    agg["sample_duration_s"] = agg["n_samples"] * SAMPLE_DURATION
    agg["avg_cpu_per_sec"] = agg["sample_cpu_total"] / agg["sample_duration_s"]
    agg["estimated_call_cpu"] = agg["avg_cpu_per_sec"] * agg["call_duration_s"]

    return agg.reset_index()


def summary(df_results: pd.DataFrame) -> pd.DataFrame:
    """Group results by app + platform and compute CPU % stats per call."""
    # Aggregate per call first (sum across binaries within the same call+platform)
    per_call = (
        df_results.groupby(["call_id", "app", "platform"])
        .agg(
            call_duration_s=("call_duration_s", "first"),
            avg_cpu_per_sec=("avg_cpu_per_sec", "sum"),
        )
        .reset_index()
    )
    return (
        per_call.groupby(["app", "platform"])
        .agg(
            total_calls=("app", "size"),
            avg_cpu_pct=("avg_cpu_per_sec", lambda x: x.mean() * 100),
            median_cpu_pct=("avg_cpu_per_sec", lambda x: x.median() * 100),
            avg_call_duration_s=("call_duration_s", "mean"),
        )
        .sort_values("avg_cpu_pct", ascending=True)
    )


def summary_by_binary(df_results: pd.DataFrame) -> pd.DataFrame:
    """Group results by binary + platform and compute CPU % percentiles."""
    return (
        df_results.groupby(["binary", "platform"])
        .agg(
            total_calls=("binary", "size"),
            avg_cpu_pct=("avg_cpu_per_sec", lambda x: x.mean() * 100),
            p25_cpu_pct=("avg_cpu_per_sec", lambda x: x.quantile(0.25) * 100),
            p50_cpu_pct=("avg_cpu_per_sec", lambda x: x.median() * 100),
            p75_cpu_pct=("avg_cpu_per_sec", lambda x: x.quantile(0.75) * 100),
            avg_call_duration_s=("call_duration_s", "mean"),
        )
        .sort_values("avg_cpu_pct", ascending=True)
    )


def main() -> None:
    """Run analysis and print results to terminal."""
    from rich.console import Console
    from rich.table import Table

    console = Console()

    console.print("[bold]CPU Efficiency: Teams vs Zoom[/bold]\n")
    console.print("Loading data...")

    df = run()

    if df.empty:
        console.print("[red]No calls with matching execution samples found.[/red]")
        return

    n_calls = df["call_id"].nunique()
    console.print(f"Matched [green]{n_calls}[/green] calls with execution data.\n")

    # ── Section 1: Summary ──────────────────────────────────────────
    s = summary(df)

    table = Table(title="Summary — CPU Usage per Call (% of 1 core)", show_lines=True)
    table.add_column("Application")
    table.add_column("Platform")
    table.add_column("Calls", justify="right")
    table.add_column("Avg CPU %", justify="right")
    table.add_column("Median CPU %", justify="right")
    table.add_column("Avg Call Duration", justify="right")

    for (app, platform), row in s.iterrows():
        table.add_row(
            str(app),
            str(platform),
            str(row["total_calls"]),
            f"{row['avg_cpu_pct']:.1f}%",
            f"{row['median_cpu_pct']:.1f}%",
            f"{row['avg_call_duration_s']:.0f}s",
        )

    console.print(table)

    # AI summary
    dist_lines = []
    for (app, platform), row in s.iterrows():
        dist_lines.append(
            f"  {app} ({platform}): avg={row['avg_cpu_pct']:.1f}%  median={row['median_cpu_pct']:.1f}%"
        )

    ai_text = _ai_summary(s, dist_lines)
    if ai_text:
        console.print(f"\n[bold]AI Summary:[/bold]\n{ai_text}")

    # ── Section 2: Details by binary ────────────────────────────────
    console.print()
    sb = summary_by_binary(df)

    detail = Table(title="Details — CPU Usage by Binary (% of 1 core)", show_lines=True)
    detail.add_column("Binary")
    detail.add_column("Platform")
    detail.add_column("Calls", justify="right")
    detail.add_column("Avg CPU %", justify="right")
    detail.add_column("p25", justify="right")
    detail.add_column("p50", justify="right")
    detail.add_column("p75", justify="right")
    detail.add_column("Avg Call Duration", justify="right")

    for (binary, platform), row in sb.iterrows():
        detail.add_row(
            str(binary),
            str(platform),
            str(row["total_calls"]),
            f"{row['avg_cpu_pct']:.1f}%",
            f"{row['p25_cpu_pct']:.1f}%",
            f"{row['p50_cpu_pct']:.1f}%",
            f"{row['p75_cpu_pct']:.1f}%",
            f"{row['avg_call_duration_s']:.0f}s",
        )

    console.print(detail)


def _format_results_for_llm(s: pd.DataFrame, dist_lines: list[str]) -> str:
    """Format the analysis results as plain text for the LLM prompt."""
    lines = ["CPU Efficiency Analysis: Teams vs Zoom by platform (% of 1 CPU core)", ""]
    for (app, platform), row in s.iterrows():
        lines.append(
            f"{app} ({platform}): {int(row['total_calls'])} calls, "
            f"avg CPU={row['avg_cpu_pct']:.1f}%, "
            f"median CPU={row['median_cpu_pct']:.1f}%, "
            f"avg call duration={row['avg_call_duration_s']:.0f}s"
        )
    lines.append("")
    lines.extend(dist_lines)
    return "\n".join(lines)


def _ai_summary(s: pd.DataFrame, dist_lines: list[str]) -> str | None:
    """Generate an AI summary using Claude. Returns None if unavailable."""
    import os

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None

    try:
        import anthropic
    except ImportError:
        return None

    data_text = _format_results_for_llm(s, dist_lines)
    client = anthropic.Anthropic(api_key=api_key)
    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=300,
        messages=[{
            "role": "user",
            "content": (
                "You are a Digital Employee Experience (DEX) analyst. "
                "Based on the following CPU efficiency analysis of video "
                "conferencing applications, write a concise summary (3-4 "
                "sentences) highlighting the key findings. "
                "The number of calls is only relevant for understanding "
                "the statistical relevance of the data.\n\n"
                f"{data_text}"
            ),
        }],
    )
    return message.content[0].text


if __name__ == "__main__":
    main()
