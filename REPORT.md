# AIOps Detection Engine Engineering Report

## Overview
The detection engine runs as a long-lived Laravel command (`php artisan aiops:detect`). It polls Prometheus every 20-30 seconds, builds per-endpoint baselines from real observed data, detects multi-signal anomalies, correlates them into a single incident, writes structured incidents to disk, and emits alerts with suppression for duplicates.

## Prometheus Queries
The `App\Services\PrometheusClient` issues the required PromQL queries against `http://localhost:9090/api/v1/query`:

- Request rate per endpoint:
  `sum by (path) (rate(http_requests_total[2m]))`
- Error rate per endpoint:
  `sum by (path) (rate(http_errors_total[2m])) / sum by (path) (rate(http_requests_total[2m]))`
- Latency percentiles (P50/P95/P99):
  `histogram_quantile(0.95, sum by (le, path) (rate(http_request_duration_seconds_bucket[2m])))`
- Error category counters:
  `sum by (path, error_category) (rate(http_errors_total[2m]))`
- Average latency per endpoint (for baselines):
  `sum by (path) (rate(http_request_duration_seconds_sum[2m])) / sum by (path) (rate(http_request_duration_seconds_count[2m]))`

Window length is configurable via `AIOPS_QUERY_WINDOW`.

## Baseline Modeling
Baselines are computed per endpoint for:

- Average latency (ms)
- Request rate (rps)
- Error rate (ratio)

Each metric uses a capped running mean with a configurable sample cap (`AIOPS_BASELINE_SAMPLE_CAP`) and a minimum sample count (`AIOPS_BASELINE_MIN_SAMPLES`) before anomaly checks are activated. Baselines are persisted in `storage/aiops/baselines.json` and are derived entirely from observed Prometheus data (no hardcoding).

## Anomaly Detection Rules
Per endpoint, the detector evaluates:

- Latency anomaly: `observed_latency > 3x baseline_latency`
- Error rate anomaly: `observed_error_rate > max(10%, 3x baseline_error_rate)`
- Traffic anomaly: `observed_rps > 2x baseline_rps`

These signals satisfy the required multi-signal detection and ensure the Lab Work 1 anomaly window is caught once baselines are established.

## Correlation Logic
Signals are merged into a single incident per cycle. Incident type and severity are chosen via signal distribution:

- `ERROR_STORM` (critical): errors across multiple endpoints
- `SERVICE_DEGRADATION` (high): latency across multiple endpoints
- `TRAFFIC_SURGE` (medium): traffic across multiple endpoints or single-endpoint surge
- `LATENCY_SPIKE` (medium): single endpoint latency only
- `LOCALIZED_ENDPOINT_FAILURE` (high): single endpoint error (or mixed error+latency)

## Incident Schema + Storage
Incidents are appended to `storage/aiops/incidents.json` with the stable schema:

- `incident_id`
- `incident_type`
- `severity`
- `status`
- `detected_at`
- `affected_service`
- `affected_endpoints`
- `triggering_signals`
- `baseline_values`
- `observed_values`
- `summary`

An open incident is tracked in `storage/aiops/incident_state.json`. When anomalies clear, the incident is marked `resolved` in `incidents.json`.

## Alerting + Suppression
Alerts are emitted to the console as JSON and include:

- `incident_id`
- `incident_type`
- `severity`
- `timestamp`
- `summary`

Repeated alerts for the same incident are suppressed by comparing a stable incident key derived from type, endpoints, and signals.

## Example Alert
```json
{
  "incident_id": "b0dd7c0e-0b31-4b89-82a1-5e205f3b8f0a",
  "incident_type": "LATENCY_SPIKE",
  "severity": "medium",
  "timestamp": "2026-03-21T12:40:15+02:00",
  "summary": "LATENCY SPIKE detected affecting /api/slow. Signals: latency@/api/slow"
}
```

## Example Incident Record
```json
 {
        "incident_id": "6dfe450f-6e84-4d99-9368-3356b4926235",
        "incident_type": "SERVICE_DEGRADATION",
        "severity": "high",
        "status": "resolved",
        "detected_at": "2026-03-21T16:01:44+00:00",
        "affected_service": "Laravel",
        "affected_endpoints": [
            "/api/db",
            "/api/validate"
        ],
        "triggering_signals": [
            {
                "signal": "latency",
                "endpoint": "/api/db",
                "observed": 4.500000000000001,
                "baseline": 1.4999999999999998,
                "threshold": 4.499999999999999
            },
            {
                "signal": "latency",
                "endpoint": "/api/validate",
                "observed": 12.999999999999998,
                "baseline": 3.0000000000000004,
                "threshold": 9.000000000000002
            }
        ],
        "baseline_values": {
            "/api/db": {
                "avg_latency_ms": 1.4999999999999998,
                "request_rate": 0.014964577574704263,
                "error_rate": 0
            },
            "/api/validate": {
                "avg_latency_ms": 3.0000000000000004,
                "request_rate": 0.005555424981679103,
                "error_rate": 0.3076923076923077
            }
        },
        "observed_values": {
            "/api/db": {
                "avg_latency_ms": 4.500000000000001,
                "p95_latency_ms": 47.5,
                "p99_latency_ms": 49.5,
                "request_rate": 0.022221975311385426,
                "error_rate": 0,
                "error_categories": []
            },
            "/api/validate": {
                "avg_latency_ms": 12.999999999999998,
                "p95_latency_ms": 47.5,
                "p99_latency_ms": 49.5,
                "request_rate": 0,
                "error_rate": 0,
                "error_categories": {
                    "VALIDATION_ERROR": 0
                }
            }
        },
        "summary": "SERVICE DEGRADATION detected affecting /api/db, /api/validate. Signals: latency@/api/db, latency@/api/validate"
    },
```

## How To Run
1. Start Laravel + Prometheus:
   - `php artisan serve`
   - `docker compose up -d`
2. Generate traffic (optional):
   - `py traffic_generator.py --base-url http://127.0.0.1:8000 --target-rps 6`
3. Run the detector:
   - `php artisan aiops:detect`

The command runs continuously and evaluates every 20-30 seconds.

## Lab Work 3 - ML Anomaly Detection

### Dataset Construction
The ML pipeline is implemented in `ml_anomaly_detection.py`. It loads `logs.json` from the workspace; when that file is missing, it extracts the same `logs.json` from `Selected-assignment-submission.zip` so the experiment remains reproducible from the submission bundle.

The available root `ground_truth.json` is dated `2026-03-21`, but the telemetry used for ML spans `2026-03-09T16:36:26Z` to `2026-03-09T16:53:03Z`. To keep training and evaluation aligned to the same run, the anomaly window is derived from the `/api/anomaly-window` control events inside `logs.json`:


- Anomaly start: `2026-03-09T16:41:58Z`
- Anomaly end: `2026-03-09T16:50:22Z`

The final dataset is stored in `aiops_dataset.csv` and contains `3995` observations, which satisfies the minimum requirement of `>= 1500` observations.

Each observation is an endpoint-level rolling window sampled every second using a `60` second window. The dataset includes the required fields:

- `timestamp`
- `endpoint`
- `latency`
- `error_rate`
- `request_rate`
- `error_category`

Operational metrics are reconstructed from the request log using the same Prometheus-style counters and latency statistics emitted by `app/Support/MetricsStore.php`.

### Chosen Features
The model uses the required engineered features:

- `avg_latency`
- `max_latency`
- `request_rate`
- `error_rate`
- `latency_std`
- `errors_per_window`
- `endpoint_frequency`

To strengthen detection of the injected `/api/slow` degradation, I also added per-window error-category rates:

- `timeout_error_rate`
- `system_error_rate`
- `validation_error_rate`

These extra features are still derived strictly from the Lab Work 1 telemetry and help the model distinguish the latency spike from normal endpoint-specific traffic variation.

### Model Selection
I chose a `One-Class SVM` with an RBF kernel:

- `kernel = rbf`
- `nu = 0.03`
- `gamma = 0.5`

The assignment requires training on normal behavior only, so the model is fit only on windows before `2026-03-09T16:41:58Z`. This gives `1507` normal-training windows.

I selected One-Class SVM over the other allowed models because it produced the cleanest separation between pre-anomaly and anomaly-period windows in this telemetry run while keeping the normal-period false-positive rate low.

### Prediction Output
Predictions are written to `anomaly_predictions.csv` with:

- `timestamp`
- `anomaly_score`
- `is_anomaly`

The file also includes `endpoint` and `is_ground_truth_anomaly` so the anomaly points can be traced back to the affected service path and evaluated against the injected window.

### Detection Performance
Using the derived ground-truth window on `/api/slow`, the ML detector produced:

- Slow-endpoint recall inside the anomaly window: `53.29%`
- Slow-endpoint precision for predicted `/api/slow` anomalies: `69.63%`
- False-positive rate during the normal-only training period: `2.12%`
- Total detected anomalous windows: `969`

This means the model does detect the injected anomaly window and highlights a sustained cluster of anomalous `/api/slow` windows during the degraded period.

### Visualizations
The required plots are generated in `plots/`:

- `plots/latency_timeline.png`
- `plots/error_rate_timeline.png`
- `plots/anomaly_overview.png`

The latency and error-rate timelines both highlight the predicted anomaly points and shade the injected anomaly window for inspection.

### Deliverables
- Training script: `ml_anomaly_detection.py`
- Dataset: `aiops_dataset.csv`
- Predictions: `anomaly_predictions.csv`
- Plots: `plots/latency_timeline.png`, `plots/error_rate_timeline.png`, `plots/anomaly_overview.png`
- Summary file: `ml_summary.json`


### Reproducibility
Run the ML

```bash
python ml_anomaly_detection.py
```

## Lab Work 4 - Automated Root Cause Analysis

### RCA Scope
Lab Work 4 uses one detected anomaly window from the previous labs and attributes the most likely cause by combining metrics and logs. The implementation is in `root_cause_analysis.py`, which consumes:

- `aiops_dataset.csv`
- `anomaly_predictions.csv`
- `logs.json`

The selected incident window is reconstructed automatically from the `/api/anomaly-window` control markers in `logs.json`:

- Incident start: `2026-03-09T16:41:58+00:00`
- Incident end: `2026-03-09T16:50:22+00:00`

### Signal Analysis
The RCA evaluates the required signals for every endpoint inside the selected anomaly window:

- latency
- request rate
- error rate
- endpoint activity
- error categories

For each endpoint, the script builds a normal-period baseline from windows before the incident and compares the incident-period behavior against that baseline using:

- latency P95 deviation
- error-rate deviation
- request-rate deviation
- endpoint-frequency deviation
- Lab Work 3 anomaly scores and anomalous window counts

### Root Cause Attribution
The endpoint ranking produced by the RCA identified `/api/slow` as the root-cause endpoint.

Structured RCA result:

- `incident_id`: `incident-20260309T164158Z-20260309T165022Z`
- `root_cause_endpoint`: `/api/slow`
- `primary_signal`: `error_rate`
- `confidence_score`: `0.76`

This attribution is supported by three consistent signals:

- `/api/slow` produced the highest RCA composite score (`512.069`)
- `/api/slow` contributed the largest number of anomalous windows (`243`)
- `/api/slow` was the only root endpoint whose own failures were entirely `TIMEOUT_ERROR`

### Error Category Analysis
Across the full anomaly window, the non-normal error distribution was:

- `SYSTEM_ERROR`: `24` events (`57.14%`)
- `TIMEOUT_ERROR`: `13` events (`30.95%`)
- `VALIDATION_ERROR`: `5` events (`11.90%`)

Because `/api/error` naturally emits `SYSTEM_ERROR`, the RCA separates global error mix from the selected root endpoint's own failures. For `/api/slow`, the endpoint-specific distribution was:

- `TIMEOUT_ERROR`: `13` events (`100%` of `/api/slow` errors)

That endpoint-level view is what makes `/api/slow` the strongest root-cause candidate instead of `/api/error`.

### Incident Timeline
The RCA generated the required four-phase incident timeline:

- Normal state: `2026-03-09T16:40:58+00:00`
- Anomaly start: `2026-03-09T16:41:58+00:00`
- Peak incident: `2026-03-09T16:43:19+00:00`
- Recovery: `2026-03-09T16:44:05+00:00`

The corresponding visualization is saved as `plots/incident_timeline_rca.png`.

### Deliverables
Lab Work 4 deliverables are present in the repository:

- RCA script: `root_cause_analysis.py`
- Timeline visualization: `plots/incident_timeline_rca.png`
- Structured report: `rca_report.json`
- Two-page RCA report: `RCA_REPORT.md`

### Reproducibility
Run the RCA workflow with:

```bash
python root_cause_analysis.py
```
