<?php

namespace App\Console\Commands;

use App\Services\PrometheusClient;
use Illuminate\Console\Command;
use Illuminate\Support\Facades\File;
use Illuminate\Support\Str;

class AiopsDetectCommand extends Command
{
    protected $signature = 'aiops:detect {--interval=25 : Base interval in seconds (20-30)}';

    protected $description = 'Run the AIOps detection engine continuously.';

    private const ENDPOINTS = [
        '/api/normal',
        '/api/slow',
        '/api/db',
        '/api/error',
        '/api/validate',
    ];

    public function handle(PrometheusClient $prometheus): int
    {
        $this->info('[aiops] Detection engine started.');

        $interval = (int) ($this->option('interval') ?: 25);
        $minSleep = max(20, $interval - 5);
        $maxSleep = min(30, $interval + 5);

        while (true) {
            try {
                $metrics = $this->collectMetrics($prometheus);
                $this->logMetrics($metrics);

                $baselines = $this->updateBaselines($metrics);

                $incident = $this->detectIncident($metrics, $baselines);
                $this->processIncident($incident);
            } catch (\Throwable $e) {
                $this->error('[aiops] Detection cycle failed: '.$e->getMessage());
            }

            $sleepFor = random_int($minSleep, $maxSleep);
            $this->line(sprintf('[aiops] Sleeping %ds...', $sleepFor));
            sleep($sleepFor);
        }

        return self::SUCCESS;
    }

    private function collectMetrics(PrometheusClient $prometheus): array
    {
        $window = config('aiops.query_window', '2m');

        $requestRates = $prometheus->fetchRequestRatePerEndpoint($window);
        $errorRates = $prometheus->fetchErrorRatePerEndpoint($window);
        $avgLatencySeconds = $prometheus->fetchAverageLatencySecondsPerEndpoint($window);
        $percentiles = $prometheus->fetchLatencyPercentilesPerEndpoint($window, [0.5, 0.95, 0.99]);
        $errorCategories = $prometheus->fetchErrorCategoryRates($window);

        $metrics = [
            'timestamp' => now()->toIso8601String(),
            'request_rate' => [],
            'error_rate' => [],
            'avg_latency_ms' => [],
            'p50_latency_ms' => [],
            'p95_latency_ms' => [],
            'p99_latency_ms' => [],
            'error_categories' => [],
        ];

        foreach (self::ENDPOINTS as $endpoint) {
            $metrics['request_rate'][$endpoint] = $this->sanitizeMetric($requestRates[$endpoint] ?? 0.0);
            $metrics['error_rate'][$endpoint] = $this->sanitizeMetric($errorRates[$endpoint] ?? 0.0);
            $metrics['avg_latency_ms'][$endpoint] = $this->sanitizeMetric($avgLatencySeconds[$endpoint] ?? 0.0) * 1000;
            $metrics['p50_latency_ms'][$endpoint] = $this->sanitizeMetric($percentiles['0.5'][$endpoint] ?? 0.0) * 1000;
            $metrics['p95_latency_ms'][$endpoint] = $this->sanitizeMetric($percentiles['0.95'][$endpoint] ?? 0.0) * 1000;
            $metrics['p99_latency_ms'][$endpoint] = $this->sanitizeMetric($percentiles['0.99'][$endpoint] ?? 0.0) * 1000;
            $metrics['error_categories'][$endpoint] = $errorCategories[$endpoint] ?? [];
        }

        return $metrics;
    }

    private function logMetrics(array $metrics): void
    {
        $this->line('[aiops] Metrics snapshot '.$metrics['timestamp']);

        foreach (self::ENDPOINTS as $endpoint) {
            $this->line(sprintf(
                '[aiops] %s | rps=%.2f | err=%.2f%% | avg=%.1fms | p95=%.1fms | p99=%.1fms',
                $endpoint,
                $metrics['request_rate'][$endpoint],
                $metrics['error_rate'][$endpoint] * 100,
                $metrics['avg_latency_ms'][$endpoint],
                $metrics['p95_latency_ms'][$endpoint],
                $metrics['p99_latency_ms'][$endpoint],
            ));
        }
    }

    private function updateBaselines(array $metrics): array
    {
        $path = storage_path('aiops/baselines.json');
        $baselineState = $this->loadJson($path, [
            'updated_at' => null,
            'endpoints' => [],
        ]);

        $sampleCap = (int) config('aiops.baseline_sample_cap', 50);

        foreach (self::ENDPOINTS as $endpoint) {
            $baselineState['endpoints'][$endpoint] = $baselineState['endpoints'][$endpoint] ?? [];

            $this->updateBaselineMetric($baselineState['endpoints'][$endpoint], 'avg_latency_ms', $metrics['avg_latency_ms'][$endpoint], $sampleCap);
            $this->updateBaselineMetric($baselineState['endpoints'][$endpoint], 'request_rate', $metrics['request_rate'][$endpoint], $sampleCap);
            $this->updateBaselineMetric($baselineState['endpoints'][$endpoint], 'error_rate', $metrics['error_rate'][$endpoint], $sampleCap);
        }

        $baselineState['updated_at'] = now()->toIso8601String();
        $this->storeJson($path, $baselineState);

        $baselineValues = [];
        foreach (self::ENDPOINTS as $endpoint) {
            $baselineValues[$endpoint] = [
                'avg_latency_ms' => (float) ($baselineState['endpoints'][$endpoint]['avg_latency_ms']['value'] ?? 0.0),
                'request_rate' => (float) ($baselineState['endpoints'][$endpoint]['request_rate']['value'] ?? 0.0),
                'error_rate' => (float) ($baselineState['endpoints'][$endpoint]['error_rate']['value'] ?? 0.0),
                'counts' => [
                    'avg_latency_ms' => (int) ($baselineState['endpoints'][$endpoint]['avg_latency_ms']['count'] ?? 0),
                    'request_rate' => (int) ($baselineState['endpoints'][$endpoint]['request_rate']['count'] ?? 0),
                    'error_rate' => (int) ($baselineState['endpoints'][$endpoint]['error_rate']['count'] ?? 0),
                ],
            ];
        }

        return $baselineValues;
    }

    private function updateBaselineMetric(array &$endpointBaseline, string $metric, float $value, int $sampleCap): void
    {
        $current = $endpointBaseline[$metric] ?? ['value' => 0.0, 'count' => 0];
        $count = (int) ($current['count'] ?? 0);
        $avg = (float) ($current['value'] ?? 0.0);

        $effectiveCount = min($count, $sampleCap);
        $newAvg = ($avg * $effectiveCount + $value) / max(1, $effectiveCount + 1);

        $endpointBaseline[$metric] = [
            'value' => $newAvg,
            'count' => min($count + 1, $sampleCap),
        ];
    }

    private function detectIncident(array $metrics, array $baselines): ?array
    {
        $minSamples = (int) config('aiops.baseline_min_samples', 5);
        $signals = [];

        foreach (self::ENDPOINTS as $endpoint) {
            $baseline = $baselines[$endpoint] ?? null;

            $latencyBaseline = (float) ($baseline['avg_latency_ms'] ?? 0.0);
            $latencyCount = (int) ($baseline['counts']['avg_latency_ms'] ?? 0);
            $observedLatency = (float) $metrics['avg_latency_ms'][$endpoint];

            if ($latencyCount >= $minSamples && $latencyBaseline > 0 && $observedLatency > $latencyBaseline * 3) {
                $signals[] = $this->buildSignal('latency', $endpoint, $observedLatency, $latencyBaseline, $latencyBaseline * 3);
            }

            $errorBaseline = (float) ($baseline['error_rate'] ?? 0.0);
            $errorCount = (int) ($baseline['counts']['error_rate'] ?? 0);
            $observedError = (float) $metrics['error_rate'][$endpoint];
            $errorThreshold = $errorCount >= $minSamples
                ? max(0.10, $errorBaseline * 3)
                : 0.10;

            if ($observedError > $errorThreshold) {
                $signals[] = $this->buildSignal('error_rate', $endpoint, $observedError, $errorBaseline, $errorThreshold);
            }

            $trafficBaseline = (float) ($baseline['request_rate'] ?? 0.0);
            $trafficCount = (int) ($baseline['counts']['request_rate'] ?? 0);
            $observedTraffic = (float) $metrics['request_rate'][$endpoint];

            if ($trafficCount >= $minSamples && $trafficBaseline > 0 && $observedTraffic > $trafficBaseline * 2) {
                $signals[] = $this->buildSignal('traffic', $endpoint, $observedTraffic, $trafficBaseline, $trafficBaseline * 2);
            }
        }

        if ($signals === []) {
            return null;
        }

        return $this->correlateIncident($signals, $metrics, $baselines);
    }

    private function buildSignal(string $type, string $endpoint, float $observed, float $baseline, float $threshold): array
    {
        return [
            'signal' => $type,
            'endpoint' => $endpoint,
            'observed' => $observed,
            'baseline' => $baseline,
            'threshold' => $threshold,
        ];
    }

    private function sanitizeMetric(float $value): float
    {
        if (! is_finite($value)) {
            return 0.0;
        }

        return $value;
    }

    private function correlateIncident(array $signals, array $metrics, array $baselines): array
    {
        $latencyEndpoints = $this->extractEndpoints($signals, 'latency');
        $errorEndpoints = $this->extractEndpoints($signals, 'error_rate');
        $trafficEndpoints = $this->extractEndpoints($signals, 'traffic');
        $affectedEndpoints = array_values(array_unique(array_merge($latencyEndpoints, $errorEndpoints, $trafficEndpoints)));

        $type = 'SERVICE_DEGRADATION';
        $severity = 'high';

        if (count($errorEndpoints) >= 2) {
            $type = 'ERROR_STORM';
            $severity = 'critical';
        } elseif (count($latencyEndpoints) >= 2) {
            $type = 'SERVICE_DEGRADATION';
            $severity = 'high';
        } elseif (count($trafficEndpoints) >= 2) {
            $type = 'TRAFFIC_SURGE';
            $severity = 'medium';
        } elseif (count($affectedEndpoints) === 1) {
            if (count($errorEndpoints) === 1) {
                $type = 'LOCALIZED_ENDPOINT_FAILURE';
                $severity = 'high';
            } elseif (count($latencyEndpoints) === 1) {
                $type = 'LATENCY_SPIKE';
                $severity = 'medium';
            } elseif (count($trafficEndpoints) === 1) {
                $type = 'TRAFFIC_SURGE';
                $severity = 'medium';
            }
        } elseif (count($errorEndpoints) >= 1 && count($latencyEndpoints) >= 1) {
            $type = 'SERVICE_DEGRADATION';
            $severity = 'high';
        }

        $baselineValues = [];
        $observedValues = [];

        foreach ($affectedEndpoints as $endpoint) {
            $baselineValues[$endpoint] = [
                'avg_latency_ms' => (float) ($baselines[$endpoint]['avg_latency_ms'] ?? 0.0),
                'request_rate' => (float) ($baselines[$endpoint]['request_rate'] ?? 0.0),
                'error_rate' => (float) ($baselines[$endpoint]['error_rate'] ?? 0.0),
            ];
            $observedValues[$endpoint] = [
                'avg_latency_ms' => (float) $metrics['avg_latency_ms'][$endpoint],
                'p95_latency_ms' => (float) $metrics['p95_latency_ms'][$endpoint],
                'p99_latency_ms' => (float) $metrics['p99_latency_ms'][$endpoint],
                'request_rate' => (float) $metrics['request_rate'][$endpoint],
                'error_rate' => (float) $metrics['error_rate'][$endpoint],
                'error_categories' => $metrics['error_categories'][$endpoint],
            ];
        }

        $summary = sprintf(
            '%s detected affecting %s. Signals: %s',
            str_replace('_', ' ', $type),
            implode(', ', $affectedEndpoints),
            implode(', ', array_map(fn ($signal) => $signal['signal'].'@'.$signal['endpoint'], $signals)),
        );

        $incidentKey = implode('|', [
            $type,
            implode(',', $affectedEndpoints),
            implode(',', array_map(fn ($signal) => $signal['signal'].'@'.$signal['endpoint'], $signals)),
        ]);

        return [
            'incident_key' => $incidentKey,
            'incident_type' => $type,
            'severity' => $severity,
            'affected_endpoints' => $affectedEndpoints,
            'triggering_signals' => $signals,
            'baseline_values' => $baselineValues,
            'observed_values' => $observedValues,
            'summary' => $summary,
        ];
    }

    private function processIncident(?array $incident): void
    {
        $statePath = storage_path('aiops/incident_state.json');
        $state = $this->loadJson($statePath, ['current' => null]);
        $current = $state['current'] ?? null;

        if ($incident === null) {
            if ($current) {
                $this->resolveIncident($current['incident_id']);
                $state['current'] = null;
                $this->storeJson($statePath, $state);
            }

            return;
        }

        $incidentKey = $incident['incident_key'];

        if ($current && ($current['incident_key'] ?? null) === $incidentKey) {
            $state['current']['last_seen_at'] = now()->toIso8601String();
            $this->storeJson($statePath, $state);

            return;
        }

        if ($current) {
            $this->resolveIncident($current['incident_id']);
        }

        $incidentId = (string) Str::uuid();
        $detectedAt = now()->toIso8601String();

        $record = [
            'incident_id' => $incidentId,
            'incident_type' => $incident['incident_type'],
            'severity' => $incident['severity'],
            'status' => 'open',
            'detected_at' => $detectedAt,
            'affected_service' => config('app.name', 'laravel-aiops'),
            'affected_endpoints' => $incident['affected_endpoints'],
            'triggering_signals' => $incident['triggering_signals'],
            'baseline_values' => $incident['baseline_values'],
            'observed_values' => $incident['observed_values'],
            'summary' => $incident['summary'],
        ];

        $this->appendIncident($record);
        $this->emitAlert($record);

        $state['current'] = [
            'incident_key' => $incidentKey,
            'incident_id' => $incidentId,
            'opened_at' => $detectedAt,
            'last_seen_at' => $detectedAt,
        ];

        $this->storeJson($statePath, $state);
    }

    private function emitAlert(array $incident): void
    {
        $alert = [
            'incident_id' => $incident['incident_id'],
            'incident_type' => $incident['incident_type'],
            'severity' => $incident['severity'],
            'timestamp' => now()->toIso8601String(),
            'summary' => $incident['summary'],
        ];

        $this->warn('[aiops][alert] '.json_encode($alert, JSON_UNESCAPED_SLASHES));
    }

    private function appendIncident(array $incident): void
    {
        $path = storage_path('aiops/incidents.json');
        $incidents = $this->loadJson($path, []);
        $incidents[] = $incident;

        $this->storeJson($path, $incidents);
    }

    private function resolveIncident(string $incidentId): void
    {
        $path = storage_path('aiops/incidents.json');
        $incidents = $this->loadJson($path, []);
        $changed = false;

        foreach ($incidents as &$incident) {
            if (($incident['incident_id'] ?? null) === $incidentId) {
                $incident['status'] = 'resolved';
                $changed = true;
                break;
            }
        }
        unset($incident);

        if ($changed) {
            $this->storeJson($path, $incidents);
        }
    }

    private function extractEndpoints(array $signals, string $type): array
    {
        $endpoints = [];

        foreach ($signals as $signal) {
            if (($signal['signal'] ?? null) === $type) {
                $endpoints[] = $signal['endpoint'];
            }
        }

        return array_values(array_unique($endpoints));
    }

    private function loadJson(string $path, array $default): array
    {
        if (! File::exists($path)) {
            return $default;
        }

        $decoded = json_decode((string) File::get($path), true);

        return is_array($decoded) ? $decoded : $default;
    }

    private function storeJson(string $path, array $payload): void
    {
        File::ensureDirectoryExists(dirname($path));
        File::put($path, json_encode($payload, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES));
    }
}
