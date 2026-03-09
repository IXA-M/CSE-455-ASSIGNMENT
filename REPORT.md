# Engineering Report

## Overview

This project turns a Laravel API into an ML-ready telemetry source rather than a plain application log. Every API request emits a stable JSON record with request identity, latency, error class, client context, request and response sizes, host metadata, and build version. The telemetry is designed so it can feed anomaly detection, incident triage, and metric-based alerting without post-hoc schema cleanup.

The API includes deterministic and stochastic failure modes. `/api/error` creates a system failure, `/api/db?fail=1` creates a real `QueryException`, `/api/validate` raises `ValidationException` on bad payloads, and `/api/slow?hard=1` creates a latency-only anomaly that still returns HTTP 200. That last case matters because not all incidents present as status-code failures. The middleware and handler together tag those requests as `TIMEOUT_ERROR` when the latency exceeds 4000 ms.

## Log Schema Design

Each telemetry record uses one stable schema and always includes the same keys:

- `timestamp`: event time in ISO-8601 for correlation with Prometheus and Grafana.
- `request_id`: propagated from `X-Request-Id` or generated server-side for distributed tracing.
- `method`, `path`, `query`, `route_name`, `status_code`: request identity and routing context.
- `latency_ms`: core feature for anomaly detection, SLO tracking, and timeout classification.
- `error_category`: normalized label for supervised or weakly supervised incident analysis.
- `severity`: simplified signal for filtering logs into success vs failure streams.
- `client_ip`, `user_agent`: client fingerprinting and clustering of suspicious traffic.
- `payload_size_bytes`, `response_size_bytes`: size shifts are useful for abuse detection and regression analysis.
- `build_version`: lets operators correlate regressions with a deployment.
- `host`: required when the application is later scaled horizontally.
- `exception_class`: preserves coarse exception identity without leaking stack traces into the dataset.
- `message`: human-readable summary for triage dashboards.

The schema is intentionally flat and stable. Missing values are emitted as `null` rather than omitted keys, which avoids downstream feature engineering drift.

## Error Categorization

Central categorization lives in `app/Exceptions/Handler.php` and uses five classes:

- `VALIDATION_ERROR`
- `DATABASE_ERROR`
- `TIMEOUT_ERROR`
- `SYSTEM_ERROR`
- `UNKNOWN`

Validation and database failures are mapped from first-class Laravel exception types. HTTP aborts and other framework-level failures map to `SYSTEM_ERROR`. The timeout path is special because the endpoint can return HTTP 200. The middleware measures latency for every request, calls the centralized categorizer, and records `TIMEOUT_ERROR` when `/api/slow?hard=1` breaches the 4000 ms threshold. This yields log evidence where `status_code=200` but `error_category="TIMEOUT_ERROR"`, which is exactly the kind of hidden degradation an anomaly model should learn to detect.

## Metrics Design

Prometheus exposure follows RED-style metrics:

- `http_requests_total{method,path,status}`
- `http_errors_total{method,path,error_category}`
- `http_request_duration_seconds_bucket{method,path,le}`
- `http_request_duration_seconds_sum{method,path}`
- `http_request_duration_seconds_count{method,path}`
- `anomaly_window_active{type}`

Labels are constrained to method, normalized path, status, and error category. High-cardinality values such as `request_id`, raw query strings, payload fields, or client identifiers are deliberately excluded. Histogram buckets are tuned to the workload profile: `0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, +Inf`. These buckets clearly separate fast requests, normal slow requests, and pathological hard-slow requests.

The extra `anomaly_window_active` gauge is a ground-truth marker. It allows the dashboard to show the exact anomaly window without relying on manual notes, which is important when comparing observed behavior against the injected experiment design.

## Controlled Anomaly Design

The Python traffic generator produces a mixed workload for 8 to 12 minutes with at least 3000 requests. Baseline traffic follows the specified distribution, while the anomaly window lasts exactly two minutes and switches to a latency-spike profile where `/api/slow?hard=1` rises to 30 percent of traffic. That change is large enough to produce a visible shift in:

- P95 and P99 latency
- `TIMEOUT_ERROR` counts
- request completion times in logs
- the anomaly marker panel

Because the anomaly window is timestamped and exported to `ground_truth.json`, the dataset can be used for supervised evaluation, dashboard validation, or feature engineering experiments.

## Evidence To Capture After Running

After running the traffic generator and exporting logs, the required proof points are:

- `storage/logs/aiops.log` contains at least 1500 structured records.
- `logs.json` contains the same records as a JSON array.
- at least 100 records have `severity="error"`.
- some `/api/slow?hard=1` records show `status_code=200` with `error_category="TIMEOUT_ERROR"`.
- Grafana shows a clear two-minute spike in high-percentile latency and the anomaly marker aligns with it.
