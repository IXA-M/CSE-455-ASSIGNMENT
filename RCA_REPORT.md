# Root Cause Analysis Report

## Executive Summary
The selected incident was `incident-20260309T164158Z-20260309T165022Z`. The automated RCA process analyzed the anomaly window from `2026-03-09T16:41:58+00:00` to `2026-03-09T16:50:22+00:00` and identified `/api/slow` as the most likely source of the incident. The primary signal was `error_rate`, with a confidence score of `0.76`.

The evidence points to a timeout-driven degradation centered on `/api/slow`. During the peak of the incident, that endpoint showed the strongest combined anomaly score, the highest concentration of anomalous windows, and the clearest deviation from its own pre-incident baseline. The recommended action is: Prioritize /api/slow: profile the slow execution path, add timeout protection and caching, and alert when TIMEOUT_ERROR exceeds the normal baseline.

## Incident Selection
This RCA used a detected anomaly window from the previous lab artifacts. The window was selected from `logs.json anomaly-window control markers` rather than hardcoding timestamps, so the analysis can be reproduced directly from the submission files.

The analysis inputs were:

- `aiops_dataset.csv` for rolling signal features.
- `anomaly_predictions.csv` for the Lab Work 3 anomaly output.
- `logs.json` for endpoint-level request, latency, and error-category evidence.

## Signal Analysis
The RCA compared all endpoints using latency, request rate, error rate, and endpoint activity. It computed a baseline for each endpoint from the normal period before the incident and then scored how strongly each endpoint deviated during the selected window.

Endpoint ranking:
- `/api/slow` score `512.069` with `243` anomalous windows, peak latency `6510.5` ms, peak error rate `1.0`.
- `/api/validate` score `265.326` with `238` anomalous windows, peak latency `14.667` ms, peak error rate `1.0`.
- `/api/error` score `171.498` with `129` anomalous windows, peak latency `2.0` ms, peak error rate `1.0`.
- `/api/normal` score `148.29` with `103` anomalous windows, peak latency `5.6` ms, peak error rate `0.0`.
- `/api/db` score `1.214` with `0` anomalous windows, peak latency `2.0` ms, peak error rate `0.0`.

Why `/api/slow` ranked first:
- /api/slow ranked first by RCA composite score (512.069) and produced 243 anomalous windows inside the selected incident.
- At the peak timestamp 2026-03-09T16:43:19+00:00, /api/slow latency was 6510.5 ms versus baseline P95 5648.5 ms.
- /api/slow error rate reached 1.00 at peak, compared with baseline P95 0.40.
- TIMEOUT_ERROR was the dominant error category on /api/slow, while the overall window distribution was led by SYSTEM_ERROR; /api/slow still contributed 31.0% of all non-NONE errors in the window.
- The analyzed incident window spans 2026-03-09T16:41:58+00:00 to 2026-03-09T16:50:22+00:00 and was selected from logs.json anomaly-window control markers.

## Error Category Analysis
The error-category distribution inside the selected anomaly window shows which failure modes were most active while the incident unfolded.

- `SYSTEM_ERROR`: 57.14% of incident error events
- `TIMEOUT_ERROR`: 30.95% of incident error events
- `VALIDATION_ERROR`: 11.90% of incident error events

For the attributed root endpoint specifically:
- `TIMEOUT_ERROR`: 100.00% of `/api/slow` error events

This distinction matters here because the service contains a dedicated `/api/error` endpoint that naturally emits `SYSTEM_ERROR`. The RCA therefore separates the global error mix from the root endpoint's own failure pattern before making the final attribution.

## Incident Timeline
- `normal_state` at `2026-03-09T16:40:58+00:00`: /api/slow was near its pre-incident baseline with latency 5098.5 ms and error rate 0.18.
- `anomaly_start` at `2026-03-09T16:41:58+00:00`: The selected anomaly window started at 2026-03-09T16:41:58+00:00 based on logs.json anomaly-window control markers.
- `peak_incident` at `2026-03-09T16:43:19+00:00`: Peak impact occurred when /api/slow reached 6510.5 ms, error rate 1.00, and activity share 0.20.
- `recovery` at `2026-03-09T16:44:05+00:00`: Severity fell back toward baseline by 2026-03-09T16:44:05+00:00, with /api/slow latency at 5020.3 ms versus baseline P95 5648.5 ms.

The generated timeline visualization is stored at `plots\incident_timeline_rca.png` and highlights the normal state, anomaly start, peak incident, and recovery markers on the same chart.

## Final Conclusion
The incident was primarily driven by `/api/slow`. The strongest evidence came from its peak-window severity, anomaly concentration, and alignment with the dominant incident error category. In practical terms, this means the service degradation was not a general platform-wide issue; it was localized to one endpoint whose behavior was severe enough to dominate the incident signature.

The RCA confidence is `0.76`. Based on the available metrics and logs, `/api/slow` is the most defensible and reproducible root-cause attribution for this anomaly window.
