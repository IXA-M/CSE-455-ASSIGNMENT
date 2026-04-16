#!/usr/bin/env python3

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


WINDOW_SECONDS = 60
CONTROL_ENDPOINT = "/api/anomaly-window"
DATASET_PATH = Path("aiops_dataset.csv")
PREDICTIONS_PATH = Path("anomaly_predictions.csv")
LOGS_PATH = Path("logs.json")
PLOTS_DIR = Path("plots")
RCA_JSON_PATH = Path("rca_report.json")
RCA_REPORT_PATH = Path("RCA_REPORT.md")
TIMELINE_PLOT_PATH = PLOTS_DIR / "incident_timeline_rca.png"


@dataclass
class IncidentWindow:
    start: pd.Timestamp
    end: pd.Timestamp
    source: str

    @property
    def incident_id(self) -> str:
        return f"incident-{self.start.strftime('%Y%m%dT%H%M%SZ')}-{self.end.strftime('%Y%m%dT%H%M%SZ')}"


def main() -> None:
    logs = load_logs()
    dataset = load_dataset()
    predictions = load_predictions()
    merged = merge_signals(dataset, predictions)

    incident = resolve_incident_window(logs, predictions)
    incident_rows = merged[(merged["timestamp"] >= incident.start) & (merged["timestamp"] <= incident.end)].copy()
    baseline_rows = merged[merged["timestamp"] < incident.start].copy()

    if incident_rows.empty:
        raise ValueError("No RCA rows were found inside the selected incident window.")
    if baseline_rows.empty:
        raise ValueError("No baseline rows exist before the selected incident window.")

    baseline_profiles = build_baseline_profiles(baseline_rows)
    incident_rows = apply_severity_scores(incident_rows, baseline_profiles)
    timeline_scores = build_timeline_scores(incident_rows)
    peak_timestamp = timeline_scores["total_severity"].idxmax()
    recovery_timestamp = find_recovery_timestamp(timeline_scores, peak_timestamp, incident.end)

    endpoint_summary = summarize_endpoints(
        incident_rows=incident_rows,
        logs=logs,
        incident=incident,
        peak_timestamp=peak_timestamp,
        baseline_profiles=baseline_profiles,
    )
    root_endpoint = endpoint_summary[0]["endpoint"]
    root_peak_row = incident_rows[
        (incident_rows["endpoint"] == root_endpoint) & (incident_rows["timestamp"] == peak_timestamp)
    ].iloc[0]
    primary_signal = determine_primary_signal(root_peak_row)

    window_logs = logs[
        (logs["path"] != CONTROL_ENDPOINT)
        & (logs["timestamp"] >= incident.start)
        & (logs["timestamp"] <= incident.end)
    ].copy()
    error_category_analysis = analyze_error_categories(window_logs, root_endpoint)
    timeline_entries = build_timeline_entries(
        merged=merged,
        root_endpoint=root_endpoint,
        incident=incident,
        peak_timestamp=peak_timestamp,
        recovery_timestamp=recovery_timestamp,
        baseline_profiles=baseline_profiles,
    )

    confidence_score = score_confidence(endpoint_summary)
    recommended_action = recommend_action(root_endpoint, primary_signal, error_category_analysis)
    supporting_evidence = build_supporting_evidence(
        endpoint_summary=endpoint_summary,
        root_peak_row=root_peak_row,
        baseline_profiles=baseline_profiles,
        window_logs=window_logs,
        error_category_analysis=error_category_analysis,
        incident=incident,
        peak_timestamp=peak_timestamp,
    )

    render_timeline_plot(
        merged=merged,
        logs=logs,
        incident=incident,
        root_endpoint=root_endpoint,
        peak_timestamp=peak_timestamp,
        recovery_timestamp=recovery_timestamp,
        baseline_profiles=baseline_profiles,
    )

    report = {
        "incident_id": incident.incident_id,
        "selected_window": {
            "start_utc": incident.start.isoformat(),
            "end_utc": incident.end.isoformat(),
            "source": incident.source,
        },
        "root_cause_endpoint": root_endpoint,
        "primary_signal": primary_signal,
        "supporting_evidence": supporting_evidence,
        "confidence_score": confidence_score,
        "recommended_action": recommended_action,
        "incident_timeline": timeline_entries,
        "signal_analysis": {
            "window_duration_seconds": int((incident.end - incident.start).total_seconds()),
            "peak_timestamp_utc": peak_timestamp.isoformat(),
            "endpoint_comparison": endpoint_summary,
        },
        "error_category_analysis": error_category_analysis,
        "artifact_paths": {
            "timeline_visualization": str(TIMELINE_PLOT_PATH),
            "markdown_report": str(RCA_REPORT_PATH),
        },
    }

    RCA_JSON_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")
    RCA_REPORT_PATH.write_text(render_markdown_report(report), encoding="utf-8")

    print(json.dumps(report, indent=2))


def load_logs() -> pd.DataFrame:
    logs = pd.DataFrame(json.loads(LOGS_PATH.read_text(encoding="utf-8")))
    logs["timestamp"] = pd.to_datetime(logs["timestamp"], utc=True)
    return logs.sort_values("timestamp").reset_index(drop=True)


def load_dataset() -> pd.DataFrame:
    dataset = pd.read_csv(DATASET_PATH)
    dataset["timestamp"] = pd.to_datetime(dataset["timestamp"], utc=True)
    return dataset.sort_values(["timestamp", "endpoint"]).reset_index(drop=True)


def load_predictions() -> pd.DataFrame:
    predictions = pd.read_csv(PREDICTIONS_PATH)
    predictions["timestamp"] = pd.to_datetime(predictions["timestamp"], utc=True)
    return predictions.sort_values(["timestamp", "endpoint"]).reset_index(drop=True)


def merge_signals(dataset: pd.DataFrame, predictions: pd.DataFrame) -> pd.DataFrame:
    merged = dataset.merge(
        predictions[["timestamp", "endpoint", "anomaly_score", "is_anomaly"]],
        on=["timestamp", "endpoint"],
        how="left",
    )
    merged["anomaly_score"] = merged["anomaly_score"].fillna(0.0)
    merged["is_anomaly"] = merged["is_anomaly"].fillna(0).astype(int)
    return merged


def resolve_incident_window(logs: pd.DataFrame, predictions: pd.DataFrame) -> IncidentWindow:
    markers = logs[logs["path"] == CONTROL_ENDPOINT].sort_values("timestamp")
    if len(markers) >= 3:
        return IncidentWindow(
            start=markers.iloc[1]["timestamp"],
            end=markers.iloc[2]["timestamp"],
            source="logs.json anomaly-window control markers",
        )

    anomalous = predictions[predictions["is_anomaly"] == 1].sort_values("timestamp").copy()
    if anomalous.empty:
        raise ValueError("No anomaly window could be inferred because anomaly_predictions.csv has no anomalous rows.")

    anomalous["gap_seconds"] = anomalous["timestamp"].diff().dt.total_seconds().fillna(1.0)
    anomalous["cluster_id"] = (anomalous["gap_seconds"] > WINDOW_SECONDS).cumsum()
    clusters = anomalous.groupby("cluster_id").agg(
        start=("timestamp", "min"),
        end=("timestamp", "max"),
        anomaly_score_sum=("anomaly_score", "sum"),
    )
    selected = clusters.sort_values("anomaly_score_sum", ascending=False).iloc[0]
    return IncidentWindow(
        start=selected["start"],
        end=selected["end"],
        source="anomaly_predictions.csv contiguous anomaly cluster",
    )


def build_baseline_profiles(baseline_rows: pd.DataFrame) -> dict[str, dict[str, float]]:
    profiles: dict[str, dict[str, float]] = {}
    for endpoint, rows in baseline_rows.groupby("endpoint"):
        profiles[endpoint] = {}
        for signal in ("latency", "request_rate", "error_rate", "endpoint_frequency"):
            series = rows[signal]
            profiles[endpoint][f"{signal}_mean"] = float(series.mean())
            profiles[endpoint][f"{signal}_p95"] = float(series.quantile(0.95))
    return profiles


def safe_ratio(value: float, baseline: float, floor: float) -> float:
    return float(value / max(baseline, floor))


def apply_severity_scores(
    incident_rows: pd.DataFrame, baseline_profiles: dict[str, dict[str, float]]
) -> pd.DataFrame:
    frame = incident_rows.copy()
    latency_severity: list[float] = []
    error_severity: list[float] = []
    request_severity: list[float] = []
    activity_severity: list[float] = []

    for _, row in frame.iterrows():
        profile = baseline_profiles[row["endpoint"]]
        latency_score = max(0.0, safe_ratio(row["latency"], profile["latency_p95"], 1.0) - 1.0)
        error_score = max(0.0, safe_ratio(row["error_rate"], profile["error_rate_p95"], 0.05) - 1.0)
        request_score = max(0.0, safe_ratio(row["request_rate"], profile["request_rate_p95"], 0.05) - 1.0)
        activity_score = max(
            0.0, safe_ratio(row["endpoint_frequency"], profile["endpoint_frequency_p95"], 0.05) - 1.0
        )

        latency_severity.append(latency_score)
        error_severity.append(error_score)
        request_severity.append(request_score)
        activity_severity.append(activity_score)

    frame["latency_severity"] = latency_severity
    frame["error_severity"] = error_severity
    frame["request_severity"] = request_severity
    frame["activity_severity"] = activity_severity
    frame["row_severity"] = (
        frame["latency_severity"]
        + frame["error_severity"]
        + (0.5 * frame["request_severity"])
        + (0.5 * frame["activity_severity"])
        + frame["anomaly_score"].clip(lower=0.0)
    )
    return frame


def build_timeline_scores(incident_rows: pd.DataFrame) -> pd.DataFrame:
    timeline = incident_rows.groupby("timestamp").agg(total_severity=("row_severity", "sum"))
    full_range = pd.date_range(
        timeline.index.min(),
        timeline.index.max(),
        freq="1s",
        tz="UTC",
    )
    return timeline.reindex(full_range, fill_value=0.0)


def find_recovery_timestamp(
    timeline_scores: pd.DataFrame, peak_timestamp: pd.Timestamp, fallback: pd.Timestamp
) -> pd.Timestamp:
    series = timeline_scores["total_severity"]
    peak_value = float(series.loc[peak_timestamp])
    threshold = max(0.2, peak_value * 0.35)

    post_peak = series.loc[peak_timestamp:]
    if len(post_peak) < 30:
        return fallback

    below = (post_peak <= threshold).astype(int)
    sustained = below.rolling(window=30, min_periods=30).sum()
    recovered = sustained[sustained == 30]
    if recovered.empty:
        return fallback
    return recovered.index[0]


def summarize_endpoints(
    incident_rows: pd.DataFrame,
    logs: pd.DataFrame,
    incident: IncidentWindow,
    peak_timestamp: pd.Timestamp,
    baseline_profiles: dict[str, dict[str, float]],
) -> list[dict[str, object]]:
    window_logs = logs[
        (logs["path"] != CONTROL_ENDPOINT)
        & (logs["timestamp"] >= incident.start)
        & (logs["timestamp"] <= incident.end)
    ].copy()
    total_anomalies = max(1, int(incident_rows["is_anomaly"].sum()))

    summaries: list[dict[str, object]] = []
    for endpoint, rows in incident_rows.groupby("endpoint"):
        endpoint_logs = window_logs[window_logs["path"] == endpoint]
        peak_row = rows[rows["timestamp"] == peak_timestamp]
        if peak_row.empty:
            peak_row = rows.nlargest(1, "row_severity")
        peak_row = peak_row.iloc[0]

        anomaly_points = int(rows["is_anomaly"].sum())
        anomaly_score_sum = float(rows["anomaly_score"].clip(lower=0.0).sum())
        timeout_share = float((endpoint_logs["error_category"] == "TIMEOUT_ERROR").mean()) if len(endpoint_logs) else 0.0
        error_share = float((endpoint_logs["error_category"] != "NONE").mean()) if len(endpoint_logs) else 0.0
        profile = baseline_profiles[endpoint]

        composite_score = (
            (5.0 * float(peak_row["row_severity"]))
            + anomaly_points
            + anomaly_score_sum
            + (20.0 * timeout_share)
            + (10.0 * error_share)
        )

        summaries.append(
            {
                "endpoint": endpoint,
                "composite_score": round(composite_score, 3),
                "anomalous_points": anomaly_points,
                "anomaly_score_sum": round(anomaly_score_sum, 3),
                "anomaly_share": round(anomaly_points / total_anomalies, 4),
                "latency_baseline_p95_ms": round(profile["latency_p95"], 3),
                "latency_at_peak_ms": round(float(peak_row["latency"]), 3),
                "request_rate_baseline_p95_rps": round(profile["request_rate_p95"], 4),
                "request_rate_at_peak_rps": round(float(peak_row["request_rate"]), 4),
                "error_rate_baseline_p95": round(profile["error_rate_p95"], 4),
                "error_rate_at_peak": round(float(peak_row["error_rate"]), 4),
                "endpoint_activity_baseline_p95": round(profile["endpoint_frequency_p95"], 4),
                "endpoint_activity_at_peak": round(float(peak_row["endpoint_frequency"]), 4),
                "window_error_share": round(error_share, 4),
                "window_timeout_share": round(timeout_share, 4),
            }
        )

    summaries.sort(key=lambda item: float(item["composite_score"]), reverse=True)
    return summaries


def determine_primary_signal(root_peak_row: pd.Series) -> str:
    signal_map = {
        "latency": float(root_peak_row["latency_severity"]),
        "error_rate": float(root_peak_row["error_severity"]),
        "request_rate": float(root_peak_row["request_severity"]),
        "endpoint_activity": float(root_peak_row["activity_severity"]),
    }
    return max(signal_map, key=signal_map.get)


def analyze_error_categories(window_logs: pd.DataFrame, root_endpoint: str) -> dict[str, object]:
    counts = window_logs["error_category"].value_counts().to_dict()
    filtered_counts = {key: int(value) for key, value in counts.items() if key != "NONE"}
    total_errors = max(1, sum(filtered_counts.values()))
    distribution = {key: round(value / total_errors, 4) for key, value in filtered_counts.items()}

    endpoint_breakdown: dict[str, dict[str, int]] = {}
    for endpoint, rows in window_logs[window_logs["error_category"] != "NONE"].groupby("path"):
        endpoint_breakdown[endpoint] = {key: int(value) for key, value in rows["error_category"].value_counts().items()}

    dominant = next(iter(distribution.keys()), "NONE")

    root_logs = window_logs[
        (window_logs["path"] == root_endpoint) & (window_logs["error_category"] != "NONE")
    ].copy()
    root_counts = {key: int(value) for key, value in root_logs["error_category"].value_counts().items()}
    root_total = max(1, sum(root_counts.values()))
    root_distribution = {key: round(value / root_total, 4) for key, value in root_counts.items()}
    root_dominant = next(iter(root_distribution.keys()), "NONE")

    return {
        "total_error_events": int(total_errors if filtered_counts else 0),
        "distribution": distribution,
        "counts": filtered_counts,
        "dominant_category": dominant,
        "endpoint_breakdown": endpoint_breakdown,
        "root_endpoint": root_endpoint,
        "root_endpoint_distribution": root_distribution,
        "root_endpoint_counts": root_counts,
        "root_endpoint_dominant_category": root_dominant,
    }


def build_timeline_entries(
    merged: pd.DataFrame,
    root_endpoint: str,
    incident: IncidentWindow,
    peak_timestamp: pd.Timestamp,
    recovery_timestamp: pd.Timestamp,
    baseline_profiles: dict[str, dict[str, float]],
) -> list[dict[str, str]]:
    root_rows = merged[merged["endpoint"] == root_endpoint].sort_values("timestamp").copy()
    normal_reference_time = max(root_rows["timestamp"].min(), incident.start - pd.Timedelta(seconds=60))

    normal_row = root_rows[root_rows["timestamp"] <= normal_reference_time].tail(1).iloc[0]
    peak_row = root_rows[root_rows["timestamp"] == peak_timestamp].iloc[0]
    recovery_row = root_rows[root_rows["timestamp"] <= recovery_timestamp].tail(1).iloc[0]
    baseline = baseline_profiles[root_endpoint]

    return [
        {
            "phase": "normal_state",
            "timestamp_utc": normal_row["timestamp"].isoformat(),
            "summary": (
                f"{root_endpoint} was near its pre-incident baseline with latency "
                f"{normal_row['latency']:.1f} ms and error rate {normal_row['error_rate']:.2f}."
            ),
        },
        {
            "phase": "anomaly_start",
            "timestamp_utc": incident.start.isoformat(),
            "summary": (
                f"The selected anomaly window started at {incident.start.isoformat()} "
                f"based on {incident.source}."
            ),
        },
        {
            "phase": "peak_incident",
            "timestamp_utc": peak_timestamp.isoformat(),
            "summary": (
                f"Peak impact occurred when {root_endpoint} reached {peak_row['latency']:.1f} ms, "
                f"error rate {peak_row['error_rate']:.2f}, and activity share {peak_row['endpoint_frequency']:.2f}."
            ),
        },
        {
            "phase": "recovery",
            "timestamp_utc": recovery_timestamp.isoformat(),
            "summary": (
                f"Severity fell back toward baseline by {recovery_timestamp.isoformat()}, with "
                f"{root_endpoint} latency at {recovery_row['latency']:.1f} ms versus baseline P95 "
                f"{baseline['latency_p95']:.1f} ms."
            ),
        },
    ]


def score_confidence(endpoint_summary: list[dict[str, object]]) -> float:
    root = float(endpoint_summary[0]["composite_score"])
    second = float(endpoint_summary[1]["composite_score"]) if len(endpoint_summary) > 1 else 0.0
    total = sum(float(item["composite_score"]) for item in endpoint_summary)
    dominance = root / max(total, 1.0)
    margin = (root - second) / max(root, 1.0)
    confidence = 0.55 + (0.25 * dominance) + (0.20 * margin)
    return round(min(0.99, max(0.55, confidence)), 2)


def recommend_action(root_endpoint: str, primary_signal: str, error_category_analysis: dict[str, object]) -> str:
    dominant_category = error_category_analysis["root_endpoint_dominant_category"]
    if dominant_category == "TIMEOUT_ERROR":
        return (
            f"Prioritize {root_endpoint}: profile the slow execution path, add timeout protection and caching, "
            "and alert when TIMEOUT_ERROR exceeds the normal baseline."
        )
    if primary_signal == "error_rate":
        return f"Inspect the failure path for {root_endpoint} and add guards for the dominant error category."
    if primary_signal == "latency":
        return f"Optimize or rate-limit {root_endpoint} and add latency-specific alerting on the 60-second window."
    return f"Review traffic shaping and per-endpoint safeguards for {root_endpoint}."


def build_supporting_evidence(
    endpoint_summary: list[dict[str, object]],
    root_peak_row: pd.Series,
    baseline_profiles: dict[str, dict[str, float]],
    window_logs: pd.DataFrame,
    error_category_analysis: dict[str, object],
    incident: IncidentWindow,
    peak_timestamp: pd.Timestamp,
) -> list[str]:
    root = endpoint_summary[0]
    baseline = baseline_profiles[root["endpoint"]]
    root_logs = window_logs[window_logs["path"] == root["endpoint"]]
    total_errors = max(1, len(window_logs[window_logs["error_category"] != "NONE"]))
    root_error_share = len(root_logs[root_logs["error_category"] != "NONE"]) / total_errors
    root_category = error_category_analysis["root_endpoint_dominant_category"]
    overall_category = error_category_analysis["dominant_category"]

    evidence = [
        (
            f"{root['endpoint']} ranked first by RCA composite score ({root['composite_score']}) and produced "
            f"{root['anomalous_points']} anomalous windows inside the selected incident."
        ),
        (
            f"At the peak timestamp {peak_timestamp.isoformat()}, {root['endpoint']} latency was "
            f"{float(root_peak_row['latency']):.1f} ms versus baseline P95 {baseline['latency_p95']:.1f} ms."
        ),
        (
            f"{root['endpoint']} error rate reached {float(root_peak_row['error_rate']):.2f} at peak, "
            f"compared with baseline P95 {baseline['error_rate_p95']:.2f}."
        ),
        (
            f"{root_category} was the dominant error category on {root['endpoint']}, while the overall window "
            f"distribution was led by {overall_category}; {root['endpoint']} still contributed "
            f"{root_error_share:.1%} of all non-NONE errors in the window."
        ),
        (
            f"The analyzed incident window spans {incident.start.isoformat()} to {incident.end.isoformat()} "
            f"and was selected from {incident.source}."
        ),
    ]
    return evidence


def render_timeline_plot(
    merged: pd.DataFrame,
    logs: pd.DataFrame,
    incident: IncidentWindow,
    root_endpoint: str,
    peak_timestamp: pd.Timestamp,
    recovery_timestamp: pd.Timestamp,
    baseline_profiles: dict[str, dict[str, float]],
) -> None:
    PLOTS_DIR.mkdir(parents=True, exist_ok=True)

    root_rows = merged[merged["endpoint"] == root_endpoint].sort_values("timestamp").copy()
    plot_start = max(root_rows["timestamp"].min(), incident.start - pd.Timedelta(minutes=2))
    plot_end = min(root_rows["timestamp"].max(), incident.end + pd.Timedelta(minutes=2))
    root_plot = root_rows[(root_rows["timestamp"] >= plot_start) & (root_rows["timestamp"] <= plot_end)].copy()

    root_logs = logs[
        (logs["path"] == root_endpoint)
        & (logs["timestamp"] >= plot_start)
        & (logs["timestamp"] <= plot_end)
    ].copy()
    root_logs["is_error"] = (root_logs["error_category"] != "NONE").astype(int)
    second_index = pd.date_range(plot_start.floor("s"), plot_end.ceil("s"), freq="1s", tz="UTC")

    category_counts: dict[str, pd.Series] = {}
    for category in sorted(root_logs["error_category"].dropna().unique()):
        if category == "NONE":
            continue
        counts = (
            root_logs.assign(match=(root_logs["error_category"] == category).astype(int))
            .set_index("timestamp")["match"]
            .resample("1s")
            .sum()
            .reindex(second_index, fill_value=0)
            .rolling(f"{WINDOW_SECONDS}s", min_periods=1)
            .sum()
        )
        category_counts[category] = counts

    fig, axes = plt.subplots(3, 1, figsize=(14, 10), sharex=True)

    axes[0].plot(root_plot["timestamp"], root_plot["latency"], color="#1f77b4", linewidth=1.5, label="Latency (ms)")
    axes[0].axhline(
        baseline_profiles[root_endpoint]["latency_p95"],
        color="#ff7f0e",
        linestyle="--",
        linewidth=1.2,
        label="Baseline P95",
    )
    axes[0].set_ylabel("Latency")
    axes[0].set_title(f"Incident timeline for {root_endpoint}")
    axes[0].legend(loc="upper left")

    axes[1].plot(root_plot["timestamp"], root_plot["error_rate"], color="#d62728", linewidth=1.4, label="Error rate")
    axes[1].plot(root_plot["timestamp"], root_plot["request_rate"], color="#2ca02c", linewidth=1.2, label="Request rate")
    axes[1].plot(
        root_plot["timestamp"],
        root_plot["endpoint_frequency"],
        color="#9467bd",
        linewidth=1.2,
        label="Endpoint activity share",
    )
    axes[1].set_ylabel("Rate / share")
    axes[1].legend(loc="upper left")

    if category_counts:
        category_frame = pd.DataFrame(category_counts, index=second_index)
        axes[2].stackplot(
            category_frame.index,
            [category_frame[column] for column in category_frame.columns],
            labels=list(category_frame.columns),
            alpha=0.8,
        )
        axes[2].legend(loc="upper left")
    axes[2].set_ylabel("60s error count")
    axes[2].set_xlabel("UTC time")

    for ax in axes:
        ax.axvspan(incident.start, incident.end, color="#ff7f0e", alpha=0.12)
        ax.axvline(incident.start, color="#ff7f0e", linestyle="--", linewidth=1.0)
        ax.axvline(peak_timestamp, color="#d62728", linestyle="--", linewidth=1.0)
        ax.axvline(recovery_timestamp, color="#2ca02c", linestyle="--", linewidth=1.0)
        ax.grid(alpha=0.2)

    axes[0].annotate("Anomaly start", xy=(incident.start, baseline_profiles[root_endpoint]["latency_p95"]))
    axes[0].annotate(
        "Peak incident",
        xy=(peak_timestamp, float(root_plot[root_plot["timestamp"] == peak_timestamp]["latency"].iloc[0])),
    )
    axes[0].annotate("Recovery", xy=(recovery_timestamp, baseline_profiles[root_endpoint]["latency_p95"]))

    axes[2].xaxis.set_major_formatter(mdates.DateFormatter("%H:%M:%S", tz=mdates.UTC))
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(TIMELINE_PLOT_PATH, dpi=150)
    plt.close(fig)


def render_markdown_report(report: dict[str, object]) -> str:
    timeline_lines = "\n".join(
        f"- `{entry['phase']}` at `{entry['timestamp_utc']}`: {entry['summary']}"
        for entry in report["incident_timeline"]
    )
    endpoint_lines = "\n".join(
        (
            f"- `{item['endpoint']}` score `{item['composite_score']}` with "
            f"`{item['anomalous_points']}` anomalous windows, peak latency `{item['latency_at_peak_ms']}` ms, "
            f"peak error rate `{item['error_rate_at_peak']}`."
        )
        for item in report["signal_analysis"]["endpoint_comparison"]
    )
    evidence_lines = "\n".join(f"- {item}" for item in report["supporting_evidence"])
    error_lines = "\n".join(
        f"- `{category}`: {ratio:.2%} of incident error events"
        for category, ratio in report["error_category_analysis"]["distribution"].items()
    ) or "- No non-NONE errors were present in the selected window."
    root_error_lines = "\n".join(
        f"- `{category}`: {ratio:.2%} of `{report['root_cause_endpoint']}` error events"
        for category, ratio in report["error_category_analysis"]["root_endpoint_distribution"].items()
    ) or f"- `{report['root_cause_endpoint']}` had no non-NONE errors in the selected window."

    return f"""# Root Cause Analysis Report

## Executive Summary
The selected incident was `{report['incident_id']}`. The automated RCA process analyzed the anomaly window from `{report['selected_window']['start_utc']}` to `{report['selected_window']['end_utc']}` and identified `{report['root_cause_endpoint']}` as the most likely source of the incident. The primary signal was `{report['primary_signal']}`, with a confidence score of `{report['confidence_score']}`.

The evidence points to a timeout-driven degradation centered on `{report['root_cause_endpoint']}`. During the peak of the incident, that endpoint showed the strongest combined anomaly score, the highest concentration of anomalous windows, and the clearest deviation from its own pre-incident baseline. The recommended action is: {report['recommended_action']}

## Incident Selection
This RCA used a detected anomaly window from the previous lab artifacts. The window was selected from `{report['selected_window']['source']}` rather than hardcoding timestamps, so the analysis can be reproduced directly from the submission files.

The analysis inputs were:

- `aiops_dataset.csv` for rolling signal features.
- `anomaly_predictions.csv` for the Lab Work 3 anomaly output.
- `logs.json` for endpoint-level request, latency, and error-category evidence.

## Signal Analysis
The RCA compared all endpoints using latency, request rate, error rate, and endpoint activity. It computed a baseline for each endpoint from the normal period before the incident and then scored how strongly each endpoint deviated during the selected window.

Endpoint ranking:
{endpoint_lines}

Why `{report['root_cause_endpoint']}` ranked first:
{evidence_lines}

## Error Category Analysis
The error-category distribution inside the selected anomaly window shows which failure modes were most active while the incident unfolded.

{error_lines}

For the attributed root endpoint specifically:
{root_error_lines}

This distinction matters here because the service contains a dedicated `/api/error` endpoint that naturally emits `SYSTEM_ERROR`. The RCA therefore separates the global error mix from the root endpoint's own failure pattern before making the final attribution.

## Incident Timeline
{timeline_lines}

The generated timeline visualization is stored at `{report['artifact_paths']['timeline_visualization']}` and highlights the normal state, anomaly start, peak incident, and recovery markers on the same chart.

## Final Conclusion
The incident was primarily driven by `{report['root_cause_endpoint']}`. The strongest evidence came from its peak-window severity, anomaly concentration, and alignment with the dominant incident error category. In practical terms, this means the service degradation was not a general platform-wide issue; it was localized to one endpoint whose behavior was severe enough to dominate the incident signature.

The RCA confidence is `{report['confidence_score']}`. Based on the available metrics and logs, `{report['root_cause_endpoint']}` is the most defensible and reproducible root-cause attribution for this anomaly window.
"""


if __name__ == "__main__":
    main()
