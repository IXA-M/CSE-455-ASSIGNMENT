# Laravel AIOps Telemetry Assignment

This repository contains a Laravel 12 API instrumented for ML-ready telemetry, Prometheus metrics, Grafana dashboards, and a controlled anomaly generator.

## Implemented API Surface

- `GET /api/normal`
- `GET /api/slow`
- `GET /api/slow?hard=1`
- `GET /api/error`
- `GET /api/random`
- `GET /api/db`
- `GET /api/db?fail=1`
- `POST /api/validate`
- `GET /metrics`
- `POST /api/anomaly-window`

## Telemetry Features

- Correlation ID propagation via `X-Request-Id`
- Stable JSON log schema in `storage/logs/aiops.log`
- Latency capture for success and failure paths
- Central error categorization in `app/Exceptions/Handler.php`
- Prometheus RED metrics with bounded labels
- Ground-truth anomaly marker metric via `anomaly_window_active`

## Local Run

1. Install dependencies if needed:

```powershell
composer install
```

2. Ensure environment values exist in `.env`:

```env
APP_URL=http://127.0.0.1:8000
BUILD_VERSION=1.0.0
DB_CONNECTION=sqlite
DB_DATABASE=database/database.sqlite
```

3. Run migrations:

```powershell
php artisan migrate
```

4. Start Laravel:

```powershell
php artisan serve
```

## Monitoring Stack

Start Prometheus and Grafana:

```powershell
docker compose up -d
```

Services:

- Prometheus: `http://localhost:9090`
- Grafana: `http://localhost:3000`
- Grafana login: `admin` / `admin`

Prometheus scrapes the native Laravel server at `http://host.docker.internal:8000/metrics`.

## Traffic Generation

Reset previous telemetry before a fresh run:

```powershell
php artisan telemetry:reset
```

Run the controlled workload:

```powershell
py traffic_generator.py --base-url http://127.0.0.1:8000 --duration-minutes 10 --target-rps 3 --max-inflight 8
```

This produces `ground_truth.json` with:

- `anomaly_start_iso`
- `anomaly_end_iso`
- `anomaly_type`
- `expected_behavior`

## Dataset Export

After traffic completes, export logs:

```powershell
php artisan telemetry:export-logs
```

Artifacts:

- `storage/logs/aiops.log`
- `logs.json`
- `ground_truth.json`

## Grafana Panels

The provisioned dashboard includes:

- Request rate per endpoint
- Error rate percentage per endpoint
- P50, P95, and P99 latency per endpoint
- Error category breakdown
- Anomaly window marker

Dashboard JSON export is stored at `monitoring/grafana/provisioning/dashboards/aiops-dashboard.json`.

## Useful Checks

Timeout anomaly proof in logs:

```powershell
Select-String -Path storage\logs\aiops.log -Pattern '"error_category":"TIMEOUT_ERROR"'
```

Validation failures:

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/validate -ContentType 'application/json' -Body '{"email":"bad","age":0}'
```

Database failures:

```powershell
Invoke-RestMethod -Uri http://127.0.0.1:8000/api/db?fail=1
```

## Files Of Interest

- `app/Http/Middleware/TelemetryMiddleware.php`
- `app/Exceptions/Handler.php`
- `app/Support/MetricsStore.php`
- `routes/api.php`
- `traffic_generator.py`
- `prometheus.yml`
- `docker-compose.yml`
- `REPORT.md`
