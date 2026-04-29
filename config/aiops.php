<?php

return [
    'prometheus_url' => env('PROMETHEUS_URL', 'http://localhost:9090'),
    'query_window' => env('AIOPS_QUERY_WINDOW', '2m'),
    'baseline_sample_cap' => (int) env('AIOPS_BASELINE_SAMPLE_CAP', 50),
    'baseline_min_samples' => (int) env('AIOPS_BASELINE_MIN_SAMPLES', 5),
    'response_policies' => [
        'LATENCY_SPIKE' => [
            'action' => 'restart_service',
            'notes' => 'Simulated service restart to clear latency pressure.',
            'simulate_success' => true,
        ],
        'ERROR_STORM' => [
            'action' => 'send_alert',
            'notes' => 'Simulated high-priority alert to the operations channel.',
            'simulate_success' => true,
        ],
        'TRAFFIC_SURGE' => [
            'action' => 'scale_service',
            'notes' => 'Simulated horizontal scaling for elevated request volume.',
            'simulate_success' => true,
        ],
        'SERVICE_DEGRADATION' => [
            'action' => 'restart_service',
            'notes' => 'Simulated service restart for broad degradation.',
            'simulate_success' => true,
        ],
        'LOCALIZED_ENDPOINT_FAILURE' => [
            'action' => 'traffic_throttling',
            'notes' => 'Simulated throttling around the failing endpoint.',
            'simulate_success' => true,
        ],
        'default' => [
            'action' => 'incident_escalation',
            'notes' => 'No specific policy matched; routing to manual response.',
            'simulate_success' => true,
        ],
    ],
    'response_escalation_action' => 'CRITICAL_ALERT',
];
