#!/usr/bin/env python3

import argparse
import concurrent.futures
import json
import random
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone


BASE_WEIGHTS = [
    ("/api/normal", 70),
    ("/api/slow", 15),
    ("/api/slow?hard=1", 5),
    ("/api/error", 5),
    ("/api/db", 3),
    ("/api/validate", 2),
]

ANOMALY_WEIGHTS = [
    ("/api/normal", 45),
    ("/api/slow", 15),
    ("/api/slow?hard=1", 30),
    ("/api/error", 5),
    ("/api/db", 3),
    ("/api/validate", 2),
]


def choose_endpoint(in_anomaly: bool) -> str:
    population = ANOMALY_WEIGHTS if in_anomaly else BASE_WEIGHTS
    routes = [route for route, _ in population]
    weights = [weight for _, weight in population]
    return random.choices(routes, weights=weights, k=1)[0]


def post_json(url: str, payload: dict) -> None:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-Request-Id": str(uuid.uuid4())},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            response.read()
    except urllib.error.HTTPError as exc:
        exc.read()
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Unable to reach {url}: {exc.reason}") from exc
    except TimeoutError as exc:
        raise RuntimeError(f"Timed out calling {url}") from exc


def assert_server_ready(base_url: str) -> None:
    request = urllib.request.Request(
        base_url + "/api/normal",
        headers={"Accept": "application/json", "X-Request-Id": str(uuid.uuid4())},
        method="GET",
    )

    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            response.read()
    except urllib.error.URLError as exc:
        raise SystemExit(
            f"Laravel is not reachable at {base_url}. Start the app first with `php artisan serve` and retry. Root cause: {exc.reason}"
        ) from exc


def safe_post_json(url: str, payload: dict) -> None:
    try:
        post_json(url, payload)
    except RuntimeError as exc:
        print(f"[warn] {exc}")


def send_request(base_url: str, endpoint: str) -> dict:
    request_id = str(uuid.uuid4())
    headers = {
        "Accept": "application/json",
        "User-Agent": "aiops-traffic-generator/1.0",
        "X-Request-Id": request_id,
    }

    if endpoint == "/api/validate":
        is_valid = random.random() >= 0.5
        payload = {
            "email": "valid@example.com" if is_valid else "broken-email",
            "age": random.randint(18, 60) if is_valid else random.choice([0, 17, 61, "old"]),
        }
        request = urllib.request.Request(
            base_url + endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={**headers, "Content-Type": "application/json"},
            method="POST",
        )
    else:
        request = urllib.request.Request(base_url + endpoint, headers=headers, method="GET")

    started = time.time()
    status = 0

    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            response.read()
            status = response.getcode()
    except urllib.error.HTTPError as exc:
        exc.read()
        status = exc.code
    except Exception:
        status = 0

    return {
        "request_id": request_id,
        "endpoint": endpoint,
        "status": status,
        "latency_ms": round((time.time() - started) * 1000, 2),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Controlled traffic generator for Laravel AIOps telemetry.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="Laravel base URL.")
    parser.add_argument("--duration-minutes", type=int, default=10, help="Total run time in minutes.")
    parser.add_argument("--target-rps", type=float, default=6.0, help="Average request rate.")
    parser.add_argument("--workers", type=int, default=40, help="Thread pool size.")
    args = parser.parse_args()
    assert_server_ready(args.base_url)

    total_duration = args.duration_minutes * 60
    anomaly_duration = 120
    start_ts = time.time()
    anomaly_start = start_ts + max(60, (total_duration - anomaly_duration) / 2)
    anomaly_end = anomaly_start + anomaly_duration

    ground_truth = {
        "anomaly_start_iso": datetime.fromtimestamp(anomaly_start, timezone.utc).isoformat(),
        "anomaly_end_iso": datetime.fromtimestamp(anomaly_end, timezone.utc).isoformat(),
        "anomaly_type": "LATENCY_SPIKE",
        "expected_behavior": "P95/P99 latency and TIMEOUT_ERROR counts increase because /api/slow?hard=1 rises to 30% for exactly two minutes.",
    }

    with open("ground_truth.json", "w", encoding="utf-8") as handle:
        json.dump(ground_truth, handle, indent=2)

    safe_post_json(
        args.base_url + "/api/anomaly-window",
        {
            "active": False,
            "type": ground_truth["anomaly_type"],
            "started_at": ground_truth["anomaly_start_iso"],
            "ends_at": ground_truth["anomaly_end_iso"],
        },
    )

    submitted = 0
    completed = 0
    futures = []
    marker_started = False
    marker_ended = False
    lock = threading.Lock()

    def _consume(future: concurrent.futures.Future) -> None:
        nonlocal completed
        try:
            future.result()
        finally:
            with lock:
                completed += 1

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
        while True:
            now = time.time()
            elapsed = now - start_ts

            if elapsed >= total_duration:
                break

            in_anomaly = anomaly_start <= now < anomaly_end

            if in_anomaly and not marker_started:
                safe_post_json(
                    args.base_url + "/api/anomaly-window",
                    {
                        "active": True,
                        "type": ground_truth["anomaly_type"],
                        "started_at": ground_truth["anomaly_start_iso"],
                        "ends_at": ground_truth["anomaly_end_iso"],
                    },
                )
                marker_started = True

            if now >= anomaly_end and marker_started and not marker_ended:
                safe_post_json(
                    args.base_url + "/api/anomaly-window",
                    {
                        "active": False,
                        "type": ground_truth["anomaly_type"],
                        "started_at": ground_truth["anomaly_start_iso"],
                        "ends_at": ground_truth["anomaly_end_iso"],
                    },
                )
                marker_ended = True

            endpoint = choose_endpoint(in_anomaly)
            future = executor.submit(send_request, args.base_url, endpoint)
            future.add_done_callback(_consume)
            futures.append(future)
            submitted += 1

            next_dispatch = start_ts + (submitted / args.target_rps)
            time.sleep(max(0.0, next_dispatch - time.time()))

        for future in futures:
            future.result()

    if marker_started and not marker_ended:
        safe_post_json(
            args.base_url + "/api/anomaly-window",
            {
                "active": False,
                "type": ground_truth["anomaly_type"],
                "started_at": ground_truth["anomaly_start_iso"],
                "ends_at": ground_truth["anomaly_end_iso"],
            },
        )

    summary = {
        "submitted_requests": submitted,
        "completed_requests": completed,
        "run_started_iso": datetime.fromtimestamp(start_ts, timezone.utc).isoformat(),
        "run_finished_iso": datetime.now(timezone.utc).isoformat(),
        "duration_seconds": total_duration,
    }

    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
