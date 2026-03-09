# Engineering Report

## Executive Summary

This project instruments a Laravel API as an AIOps-style telemetry source rather than a simple application logger. The design goal was to emit machine-usable signals for anomaly detection and incident triage: stable structured logs, correlation IDs, centralized error categories, RED metrics for Prometheus, and a Grafana dashboard that makes a controlled anomaly window visibly obvious.

The experiment intentionally includes both hard failures and silent degradation. `/api/error` simulates system faults, `/api/db?fail=1` triggers a real query exception, `/api/validate` produces validation failures, and `/api/slow?hard=1` creates the most interesting case: requests that still return HTTP 200 but are operationally unhealthy because they exceed the latency threshold. That distinction matters in production systems because not all incidents show up as 5xx spikes.

## Incident and Experiment Design

The API surface was built to create a mixed telemetry distribution rather than a synthetic single-pattern dataset. Normal traffic comes from `/api/normal`, variable but healthy latency comes from `/api/random`, deterministic slow calls come from `/api/slow`, and controlled fault modes come from `/api/error`, `/api/db`, and `/api/validate`.

The traffic generator creates a base workload and then injects a clearly bounded anomaly window. The selected anomaly type is a latency spike: during the anomaly window, the proportion of `/api/slow?hard=1` traffic rises sharply. This is a useful experiment design because it shifts percentiles, increases timeout-category records, and keeps status codes partially healthy, which mirrors real-world degradations where customers feel the outage before the error budget fully reflects it.

Ground truth is exported to `ground_truth.json` with explicit start and end timestamps, anomaly type, and expected behavior. That makes the dataset usable for dashboard validation, post-run analysis, and future anomaly model benchmarking.

## Log Schema Design

Every log line follows one stable flat JSON schema. Keys are never conditionally removed; when data is unavailable, the field remains present with a null value. This prevents downstream schema drift and makes the dataset easier to use in pandas, Spark, SIEM tooling, or model feature pipelines.

Field rationale:

- `timestamp`: aligns logs with Prometheus time series and the anomaly window.
- `request_id`: correlation key propagated from `X-Request-Id` or generated server-side.
- `method`, `path`, `query`, `route_name`: request identity for aggregation and debugging.
- `status_code`: keeps transport success and failure visible.
- `latency_ms`: the most important operational feature for anomaly detection.
- `error_category`: normalized label for weak supervision and grouped triage.
- `severity`: lightweight separation of nominal versus problematic events.
- `client_ip`, `user_agent`: useful for clustering and isolating traffic patterns.
- `payload_size_bytes`, `response_size_bytes`: captures request and response size drift.
- `build_version`: supports release correlation when behavior changes after deployment.
- `host`: future-proofs the schema for multi-instance deployments.
- `exception_class`: preserves coarse exception identity without full stack traces.
- `message`: human-readable summary for analysts and screenshots.

The central operational property of the schema is that it captures both transport-level outcomes and application-level health. This is what allows a request to be logged as `status_code=200` while still being labeled `TIMEOUT_ERROR`.

## Error Categorization and Timeout Logic

Centralized classification lives in the exception handling layer and uses five categories:

- `VALIDATION_ERROR`
- `DATABASE_ERROR`
- `TIMEOUT_ERROR`
- `SYSTEM_ERROR`
- `UNKNOWN`

Validation and database categories map directly from Laravel exception types. `SYSTEM_ERROR` covers framework-level or explicit fault paths such as `/api/error`. `TIMEOUT_ERROR` is intentionally orthogonal to HTTP status. The middleware measures latency for all requests and applies the timeout classification when `/api/slow?hard=1` exceeds 4000 ms, even if the response remains 200.

This is the strongest evidence that the telemetry is incident-oriented instead of response-code-oriented. It captures brownout behavior, not only crashes.

## Metrics Design

Prometheus exposure follows RED metrics:

- `http_requests_total{method,path,status}`
- `http_errors_total{method,path,error_category}`
- `http_request_duration_seconds_bucket{method,path,le}`
- `http_request_duration_seconds_sum{method,path}`
- `http_request_duration_seconds_count{method,path}`
- `anomaly_window_active{type}`

The label design intentionally avoids explosion. There is no `request_id`, no raw query string, no client IP, and no payload-derived label. Labels are limited to dimensions that are operationally meaningful and aggregation-safe.

The histogram buckets are tuned to the workload profile rather than copied from a default template:

- `0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, +Inf`

These boundaries separate the workload into intuitive bands:

- sub-100 ms healthy responses
- sub-second normal API behavior
- 1 to 2.5 second moderate slowness
- 5 second deterministic slow calls
- 5 to 10 second hard-slow anomaly calls

Because the buckets match the actual endpoint behavior, P50, P95, and P99 remain useful in Grafana rather than collapsing into a single saturated bucket.

## Grafana Evidence and Screenshot Areas

Insert screenshots into the final PDF in the following locations.

### Screenshot A: Request Rate and Error Rate

Place a Grafana screenshot showing:

- Request Rate Per Endpoint
- Error Rate Per Endpoint

[Insert Screenshot A Here]

### Screenshot B: Latency Spike

Place a Grafana screenshot showing:

- P50 / P95 / P99 Latency Per Endpoint
- visible spike during the anomaly window

[Insert Screenshot B Here]

### Screenshot C: Category Breakdown and Marker

Place a Grafana screenshot showing:

- Error Category Breakdown
- Anomaly Window Marker

[Insert Screenshot C Here]

These screenshots should prove two things: first, the anomaly is visible in the metrics; second, the category breakdown distinguishes validation, system, and timeout behavior instead of collapsing all failures together.

## Conclusion

From an engineering perspective, the system now produces telemetry that is realistic enough for anomaly analysis. Logs are structured and stable, metrics are bounded and aggregatable, and the anomaly window is both controlled and externally documented. The most important design decision was treating latency-only degradation as a first-class error condition. That makes the dataset much more useful for incident-oriented analysis than a simple success/failure log stream.
