#!/usr/bin/env python3

from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass
from pathlib import Path

import matplotlib
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.preprocessing import StandardScaler
from sklearn.svm import OneClassSVM

matplotlib.use("Agg")


WINDOW_SECONDS = 60
WINDOW_LABEL = f"{WINDOW_SECONDS}s"
DATASET_PATH = Path("aiops_dataset.csv")
PREDICTIONS_PATH = Path("anomaly_predictions.csv")
PLOTS_DIR = Path("plots")
SUMMARY_PATH = Path("ml_summary.json")
LOG_ARCHIVE_PATH = Path("Selected-assignment-submission.zip")
LOGS_PATH = Path("logs.json")
GROUND_TRUTH_PATH = Path("ground_truth.json")
CONTROL_ENDPOINT = "/api/anomaly-window"


@dataclass
class GroundTruthWindow:
    anomaly_start: pd.Timestamp
    anomaly_end: pd.Timestamp
    source: str


def main() -> None:
    logs = load_logs()
    full_logs = logs.copy()
    ground_truth = resolve_ground_truth(full_logs)

    app_logs = full_logs[full_logs["path"] != CONTROL_ENDPOINT].copy()
    dataset = build_dataset(app_logs, ground_truth)
    predictions, metrics = train_and_predict(dataset, ground_truth)

    DATASET_PATH.write_text(dataset.to_csv(index=False), encoding="utf-8")
    PREDICTIONS_PATH.write_text(predictions.to_csv(index=False), encoding="utf-8")
    save_plots(dataset, predictions, ground_truth)

    summary = {
        "dataset_rows": int(len(dataset)),
        "train_rows": int(metrics["train_rows"]),
        "prediction_rows": int(len(predictions)),
        "window_seconds": WINDOW_SECONDS,
        "ground_truth_source": ground_truth.source,
        "anomaly_start_utc": ground_truth.anomaly_start.isoformat(),
        "anomaly_end_utc": ground_truth.anomaly_end.isoformat(),
        "model": {
            "name": "One-Class SVM",
            "kernel": "rbf",
            "nu": 0.03,
            "gamma": 0.5,
        },
        "performance": {
            "slow_endpoint_recall_in_window": round(metrics["slow_recall"], 4),
            "slow_endpoint_precision_in_window": round(metrics["slow_precision"], 4),
            "normal_period_false_positive_rate": round(metrics["train_false_positive_rate"], 4),
            "detected_windows": int(metrics["detected_windows"]),
        },
        "artifacts": {
            "dataset": str(DATASET_PATH),
            "predictions": str(PREDICTIONS_PATH),
            "latency_plot": str(PLOTS_DIR / "latency_timeline.png"),
            "error_rate_plot": str(PLOTS_DIR / "error_rate_timeline.png"),
            "overview_plot": str(PLOTS_DIR / "anomaly_overview.png"),
        },
    }
    SUMMARY_PATH.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print(json.dumps(summary, indent=2))


def load_logs() -> pd.DataFrame:
    if LOGS_PATH.exists():
        raw = LOGS_PATH.read_text(encoding="utf-8")
    elif LOG_ARCHIVE_PATH.exists():
        with zipfile.ZipFile(LOG_ARCHIVE_PATH) as archive:
            raw = archive.read("logs.json").decode("utf-8")
        LOGS_PATH.write_text(raw, encoding="utf-8")
    else:
        raise FileNotFoundError("No logs source found. Expected `logs.json` or `Selected-assignment-submission.zip`.")

    logs = pd.DataFrame(json.loads(raw))
    logs["timestamp"] = pd.to_datetime(logs["timestamp"], utc=True)
    logs = logs.sort_values("timestamp").reset_index(drop=True)
    return logs


def resolve_ground_truth(logs: pd.DataFrame) -> GroundTruthWindow:
    log_start = logs["timestamp"].min()
    log_end = logs["timestamp"].max()

    if GROUND_TRUTH_PATH.exists():
        raw_ground_truth = json.loads(GROUND_TRUTH_PATH.read_text(encoding="utf-8"))
        anomaly_start = pd.to_datetime(raw_ground_truth["anomaly_start_iso"], utc=True)
        anomaly_end = pd.to_datetime(raw_ground_truth["anomaly_end_iso"], utc=True)
        if log_start <= anomaly_start <= log_end and log_start <= anomaly_end <= log_end:
            return GroundTruthWindow(anomaly_start, anomaly_end, "ground_truth.json")

    markers = logs[logs["path"] == CONTROL_ENDPOINT].sort_values("timestamp")
    if len(markers) < 3:
        raise ValueError("Unable to infer the anomaly window. Expected at least three control events on /api/anomaly-window.")

    return GroundTruthWindow(
        anomaly_start=markers.iloc[1]["timestamp"],
        anomaly_end=markers.iloc[2]["timestamp"],
        source="logs.json control markers",
    )


def build_dataset(logs: pd.DataFrame, ground_truth: GroundTruthWindow) -> pd.DataFrame:
    logs = logs.copy()
    logs["error_flag"] = (logs["error_category"] != "NONE").astype(int)
    logs["latency_sq"] = logs["latency_ms"] ** 2

    endpoints = sorted(logs["path"].unique())
    error_categories = sorted(category for category in logs["error_category"].unique() if category != "NONE")

    timeline = pd.date_range(
        logs["timestamp"].min().floor("s"),
        logs["timestamp"].max().ceil("s"),
        freq="1s",
        tz="UTC",
    )

    all_request_counts = (
        logs.set_index("timestamp")
        .assign(request_count=1)["request_count"]
        .resample("1s")
        .sum()
        .reindex(timeline, fill_value=0)
    )
    all_window_counts = all_request_counts.rolling(WINDOW_LABEL, min_periods=1).sum()

    endpoint_frames: list[pd.DataFrame] = []
    for endpoint in endpoints:
        endpoint_logs = logs[logs["path"] == endpoint].copy()

        second_level = (
            endpoint_logs.set_index("timestamp")
            .resample("1s")
            .agg(
                request_count=("path", "size"),
                latency_sum=("latency_ms", "sum"),
                latency_sq_sum=("latency_sq", "sum"),
                second_max_latency=("latency_ms", "max"),
                error_count=("error_flag", "sum"),
            )
            .reindex(timeline, fill_value=0)
        )

        second_level["window_requests"] = second_level["request_count"].rolling(WINDOW_LABEL, min_periods=1).sum()
        second_level["window_latency_sum"] = second_level["latency_sum"].rolling(WINDOW_LABEL, min_periods=1).sum()
        second_level["window_latency_sq_sum"] = second_level["latency_sq_sum"].rolling(WINDOW_LABEL, min_periods=1).sum()
        second_level["window_errors"] = second_level["error_count"].rolling(WINDOW_LABEL, min_periods=1).sum()

        second_level["avg_latency"] = np.where(
            second_level["window_requests"] > 0,
            second_level["window_latency_sum"] / second_level["window_requests"],
            0.0,
        )
        second_level["max_latency"] = second_level["second_max_latency"].rolling(WINDOW_LABEL, min_periods=1).max()
        second_level["request_rate"] = second_level["window_requests"] / WINDOW_SECONDS
        second_level["error_rate"] = np.where(
            second_level["window_requests"] > 0,
            second_level["window_errors"] / second_level["window_requests"],
            0.0,
        )

        variance = np.where(
            second_level["window_requests"] > 0,
            (second_level["window_latency_sq_sum"] / second_level["window_requests"]) - (second_level["avg_latency"] ** 2),
            0.0,
        )
        second_level["latency_std"] = np.sqrt(np.maximum(variance, 0.0))
        second_level["errors_per_window"] = second_level["window_errors"]
        second_level["endpoint_frequency"] = np.where(
            all_window_counts.values > 0,
            second_level["window_requests"] / all_window_counts.values,
            0.0,
        )
        second_level["latency"] = second_level["avg_latency"]

        category_window_columns: list[str] = []
        for category in error_categories:
            safe_name = category.lower()
            category_counts = (
                endpoint_logs.assign(match=(endpoint_logs["error_category"] == category).astype(int))
                .set_index("timestamp")["match"]
                .resample("1s")
                .sum()
                .reindex(timeline, fill_value=0)
            )
            window_column = f"{safe_name}_per_window"
            rate_column = f"{safe_name}_rate"
            second_level[window_column] = category_counts.rolling(WINDOW_LABEL, min_periods=1).sum()
            second_level[rate_column] = np.where(
                second_level["window_requests"] > 0,
                second_level[window_column] / second_level["window_requests"],
                0.0,
            )
            category_window_columns.append(window_column)

        if category_window_columns:
            category_maxima = second_level[category_window_columns].max(axis=1)
            dominant_category = second_level[category_window_columns].idxmax(axis=1).str.replace("_per_window", "", regex=False).str.upper()
            second_level["error_category"] = np.where(category_maxima > 0, dominant_category, "NONE")
        else:
            second_level["error_category"] = "NONE"

        second_level = second_level[second_level["window_requests"] > 0].copy()
        second_level["timestamp"] = second_level.index
        second_level["endpoint"] = endpoint
        endpoint_frames.append(second_level)

    dataset = pd.concat(endpoint_frames, ignore_index=True)
    dataset["is_ground_truth_anomaly"] = (
        (dataset["timestamp"] >= ground_truth.anomaly_start)
        & (dataset["timestamp"] <= ground_truth.anomaly_end)
        & (dataset["endpoint"] == "/api/slow")
    ).astype(int)

    dataset = dataset[
        [
            "timestamp",
            "endpoint",
            "latency",
            "error_rate",
            "request_rate",
            "error_category",
            "avg_latency",
            "max_latency",
            "latency_std",
            "errors_per_window",
            "endpoint_frequency",
            "timeout_error_rate",
            "system_error_rate",
            "validation_error_rate",
            "is_ground_truth_anomaly",
        ]
    ].sort_values(["timestamp", "endpoint"]).reset_index(drop=True)
    dataset["timestamp"] = dataset["timestamp"].dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    return dataset


def train_and_predict(dataset: pd.DataFrame, ground_truth: GroundTruthWindow) -> tuple[pd.DataFrame, dict[str, float]]:
    working = dataset.copy()
    working["timestamp"] = pd.to_datetime(working["timestamp"], utc=True)

    feature_columns = [
        "latency",
        "request_rate",
        "error_rate",
        "avg_latency",
        "max_latency",
        "latency_std",
        "errors_per_window",
        "endpoint_frequency",
        "timeout_error_rate",
        "system_error_rate",
        "validation_error_rate",
    ]

    encoded_endpoints = pd.get_dummies(working["endpoint"], prefix="endpoint")
    feature_matrix = pd.concat([working[feature_columns], encoded_endpoints], axis=1)

    train_mask = working["timestamp"] < ground_truth.anomaly_start
    scaler = StandardScaler()
    train_features = scaler.fit_transform(feature_matrix[train_mask])
    all_features = scaler.transform(feature_matrix)

    model = OneClassSVM(kernel="rbf", gamma=0.5, nu=0.03)
    model.fit(train_features)

    raw_scores = -model.score_samples(all_features)
    threshold = float(np.quantile(raw_scores[train_mask], 0.98))
    normalized_scores = raw_scores - threshold
    is_anomaly = (normalized_scores >= 0).astype(int)

    predictions = working[["timestamp", "endpoint"]].copy()
    predictions["anomaly_score"] = normalized_scores
    predictions["is_anomaly"] = is_anomaly
    predictions["is_ground_truth_anomaly"] = working["is_ground_truth_anomaly"]
    predictions["timestamp"] = predictions["timestamp"].dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    slow_window = working["is_ground_truth_anomaly"] == 1
    slow_predictions = predictions[slow_window]
    slow_endpoint_predictions = predictions[predictions["endpoint"] == "/api/slow"]
    detected_window_points = int(slow_predictions["is_anomaly"].sum())
    slow_predicted_positive = slow_endpoint_predictions[slow_endpoint_predictions["is_anomaly"] == 1]
    slow_true_positive = int(slow_predicted_positive["is_ground_truth_anomaly"].sum())

    metrics = {
        "train_rows": int(train_mask.sum()),
        "slow_recall": float(slow_predictions["is_anomaly"].mean()),
        "slow_precision": float(slow_true_positive / max(1, len(slow_predicted_positive))),
        "train_false_positive_rate": float(predictions[train_mask]["is_anomaly"].mean()),
        "detected_windows": int(predictions["is_anomaly"].sum()),
    }

    return predictions, metrics


def save_plots(dataset: pd.DataFrame, predictions: pd.DataFrame, ground_truth: GroundTruthWindow) -> None:
    PLOTS_DIR.mkdir(parents=True, exist_ok=True)

    merged = dataset.merge(predictions, on=["timestamp", "endpoint", "is_ground_truth_anomaly"], how="left")
    merged["timestamp"] = pd.to_datetime(merged["timestamp"], utc=True)
    slow = merged[merged["endpoint"] == "/api/slow"].copy()
    anomaly_points = slow[slow["is_anomaly"] == 1]

    fig, ax = plt.subplots(figsize=(14, 5))
    ax.plot(slow["timestamp"], slow["avg_latency"], color="#1f77b4", linewidth=1.5, label="Avg latency (ms)")
    ax.scatter(anomaly_points["timestamp"], anomaly_points["avg_latency"], color="#d62728", s=24, label="Predicted anomaly")
    ax.axvspan(ground_truth.anomaly_start, ground_truth.anomaly_end, color="#ff7f0e", alpha=0.15, label="Ground truth window")
    ax.set_title("/api/slow latency timeline with anomaly points")
    ax.set_ylabel("Latency (ms)")
    ax.legend()
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M:%S", tz=mdates.UTC))
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(PLOTS_DIR / "latency_timeline.png", dpi=150)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(14, 5))
    ax.plot(slow["timestamp"], slow["error_rate"], color="#2ca02c", linewidth=1.5, label="Error rate")
    ax.scatter(anomaly_points["timestamp"], anomaly_points["error_rate"], color="#d62728", s=24, label="Predicted anomaly")
    ax.axvspan(ground_truth.anomaly_start, ground_truth.anomaly_end, color="#ff7f0e", alpha=0.15, label="Ground truth window")
    ax.set_title("/api/slow error-rate timeline with anomaly points")
    ax.set_ylabel("Error rate")
    ax.legend()
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M:%S", tz=mdates.UTC))
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(PLOTS_DIR / "error_rate_timeline.png", dpi=150)
    plt.close(fig)

    fig, axes = plt.subplots(2, 1, figsize=(14, 8), sharex=True)
    axes[0].plot(slow["timestamp"], slow["avg_latency"], color="#1f77b4", linewidth=1.3)
    axes[0].scatter(anomaly_points["timestamp"], anomaly_points["avg_latency"], color="#d62728", s=22)
    axes[0].axvspan(ground_truth.anomaly_start, ground_truth.anomaly_end, color="#ff7f0e", alpha=0.15)
    axes[0].set_ylabel("Latency (ms)")
    axes[0].set_title("ML anomaly detection on /api/slow")

    axes[1].plot(slow["timestamp"], slow["anomaly_score"], color="#9467bd", linewidth=1.3)
    axes[1].scatter(anomaly_points["timestamp"], anomaly_points["anomaly_score"], color="#d62728", s=22)
    axes[1].axvspan(ground_truth.anomaly_start, ground_truth.anomaly_end, color="#ff7f0e", alpha=0.15)
    axes[1].set_ylabel("Anomaly score")
    axes[1].set_xlabel("UTC time")
    axes[1].xaxis.set_major_formatter(mdates.DateFormatter("%H:%M:%S", tz=mdates.UTC))

    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(PLOTS_DIR / "anomaly_overview.png", dpi=150)
    plt.close(fig)


if __name__ == "__main__":
    main()
