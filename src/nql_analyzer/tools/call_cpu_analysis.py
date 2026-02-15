"""Analysis tool: CPU efficiency comparison between Teams and Zoom calls.

Downloads call data and execution samples, then computes CPU time per
second of call for each application by matching 15-minute execution
buckets to calls on the same device.
"""

from __future__ import annotations

import logging
import numpy as np
import pandas as pd

logging.basicConfig(
    level=logging.INFO,
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

    # Aggregate: sum cpu_time and count samples per call
    agg = (
        merged.groupby("call_id")
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

    return agg.reset_index(drop=True)


def summary(df_results: pd.DataFrame) -> pd.DataFrame:
    """Group results by app and compute average CPU time per second of call."""
    return (
        df_results.groupby("app")
        .agg(
            total_calls=("app", "size"),
            avg_cpu_per_sec_of_call=("avg_cpu_per_sec", "mean"),
            median_cpu_per_sec_of_call=("avg_cpu_per_sec", "median"),
            total_estimated_cpu=("estimated_call_cpu", "sum"),
            avg_call_duration_s=("call_duration_s", "mean"),
            avg_samples_per_call=("n_samples", "mean"),
        )
        .sort_values("avg_cpu_per_sec_of_call", ascending=True)
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

    console.print(f"Matched [green]{len(df)}[/green] calls with execution data.\n")

    # Summary table
    s = summary(df)

    table = Table(title="Average CPU Time per Second of Call", show_lines=True)
    table.add_column("Application")
    table.add_column("Calls", justify="right")
    table.add_column("Avg CPU/sec", justify="right")
    table.add_column("Median CPU/sec", justify="right")
    table.add_column("Avg Call Duration", justify="right")
    table.add_column("Avg Samples/Call", justify="right")

    for app, row in s.iterrows():
        table.add_row(
            str(app),
            str(row["total_calls"]),
            f"{row['avg_cpu_per_sec_of_call']:.4f}",
            f"{row['median_cpu_per_sec_of_call']:.4f}",
            f"{row['avg_call_duration_s']:.0f}s",
            f"{row['avg_samples_per_call']:.1f}",
        )

    console.print(table)

    # Per-app distribution
    dist_lines = []
    console.print("\n[bold]Distribution of CPU/sec per call:[/bold]")
    for app in df["app"].unique():
        subset = df[df["app"] == app]["avg_cpu_per_sec"]
        line = (
            f"  {app}: min={subset.min():.4f}  p25={subset.quantile(0.25):.4f}  "
            f"p50={subset.median():.4f}  p75={subset.quantile(0.75):.4f}  "
            f"max={subset.max():.4f}"
        )
        console.print(line)
        dist_lines.append(line)

    # AI summary (optional)
    ai_text = _ai_summary(s, dist_lines)
    if ai_text:
        console.print(f"\n[bold]AI Summary:[/bold]\n{ai_text}")


def _format_results_for_llm(s: pd.DataFrame, dist_lines: list[str]) -> str:
    """Format the analysis results as plain text for the LLM prompt."""
    lines = ["CPU Efficiency Analysis: Teams vs Zoom", ""]
    for app, row in s.iterrows():
        lines.append(
            f"{app}: {int(row['total_calls'])} calls, "
            f"avg CPU/sec={row['avg_cpu_per_sec_of_call']:.4f}, "
            f"median CPU/sec={row['median_cpu_per_sec_of_call']:.4f}, "
            f"avg call duration={row['avg_call_duration_s']:.0f}s, "
            f"avg samples/call={row['avg_samples_per_call']:.1f}"
        )
    lines.append("")
    lines.append("Distribution of CPU/sec per call:")
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
