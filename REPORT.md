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
