<?php

return [
    'prometheus_url' => env('PROMETHEUS_URL', 'http://localhost:9090'),
    'query_window' => env('AIOPS_QUERY_WINDOW', '2m'),
    'baseline_sample_cap' => (int) env('AIOPS_BASELINE_SAMPLE_CAP', 50),
    'baseline_min_samples' => (int) env('AIOPS_BASELINE_MIN_SAMPLES', 5),
];
