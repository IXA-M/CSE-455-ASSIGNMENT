<?php

namespace App\Support;

class MetricsStore
{
    private const BUCKETS = [0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10];

    private string $path;

    public function __construct()
    {
        $this->path = storage_path('app/metrics_store.json');
    }

    public function recordRequest(string $method, string $path, int $statusCode, float $latencySeconds, string $errorCategory): void
    {
        $state = $this->load();

        $requestKey = $this->key([$method, $path, (string) $statusCode]);
        $state['http_requests_total'][$requestKey] = [
            'method' => $method,
            'path' => $path,
            'status' => (string) $statusCode,
            'value' => ($state['http_requests_total'][$requestKey]['value'] ?? 0) + 1,
        ];

        if ($errorCategory !== ErrorCategory::NONE) {
            $errorKey = $this->key([$method, $path, $errorCategory]);
            $state['http_errors_total'][$errorKey] = [
                'method' => $method,
                'path' => $path,
                'error_category' => $errorCategory,
                'value' => ($state['http_errors_total'][$errorKey]['value'] ?? 0) + 1,
            ];
        }

        foreach (self::BUCKETS as $bucket) {
            $bucketKey = $this->key([$method, $path, (string) $bucket]);
            $state['http_request_duration_seconds_bucket'][$bucketKey] = [
                'method' => $method,
                'path' => $path,
                'le' => (string) $bucket,
                'value' => ($state['http_request_duration_seconds_bucket'][$bucketKey]['value'] ?? 0) + ($latencySeconds <= $bucket ? 1 : 0),
            ];
        }

        $infKey = $this->key([$method, $path, '+Inf']);
        $state['http_request_duration_seconds_bucket'][$infKey] = [
            'method' => $method,
            'path' => $path,
            'le' => '+Inf',
            'value' => ($state['http_request_duration_seconds_bucket'][$infKey]['value'] ?? 0) + 1,
        ];

        $sumKey = $this->key([$method, $path]);
        $state['http_request_duration_seconds_sum'][$sumKey] = [
            'method' => $method,
            'path' => $path,
            'value' => ($state['http_request_duration_seconds_sum'][$sumKey]['value'] ?? 0) + $latencySeconds,
        ];
        $state['http_request_duration_seconds_count'][$sumKey] = [
            'method' => $method,
            'path' => $path,
            'value' => ($state['http_request_duration_seconds_count'][$sumKey]['value'] ?? 0) + 1,
        ];

        $anomalyState = app(AnomalyState::class)->getState();
        $state['anomaly_window_active'] = [
            'value' => $anomalyState['active'] ? 1 : 0,
            'type' => $anomalyState['type'],
        ];

        $this->store($state);
    }

    public function renderPrometheus(): string
    {
        $state = $this->load();
        $lines = [
            '# HELP http_requests_total Total HTTP requests.',
            '# TYPE http_requests_total counter',
        ];

        foreach ($state['http_requests_total'] as $sample) {
            $lines[] = sprintf(
                'http_requests_total{method="%s",path="%s",status="%s"} %d',
                $sample['method'],
                $sample['path'],
                $sample['status'],
                $sample['value'],
            );
        }

        $lines[] = '# HELP http_errors_total Total categorized HTTP errors.';
        $lines[] = '# TYPE http_errors_total counter';
        foreach ($state['http_errors_total'] as $sample) {
            $lines[] = sprintf(
                'http_errors_total{method="%s",path="%s",error_category="%s"} %d',
                $sample['method'],
                $sample['path'],
                $sample['error_category'],
                $sample['value'],
            );
        }

        $lines[] = '# HELP http_request_duration_seconds Request duration histogram.';
        $lines[] = '# TYPE http_request_duration_seconds histogram';
        foreach ($state['http_request_duration_seconds_bucket'] as $sample) {
            $lines[] = sprintf(
                'http_request_duration_seconds_bucket{method="%s",path="%s",le="%s"} %d',
                $sample['method'],
                $sample['path'],
                $sample['le'],
                $sample['value'],
            );
        }
        foreach ($state['http_request_duration_seconds_sum'] as $sample) {
            $lines[] = sprintf(
                'http_request_duration_seconds_sum{method="%s",path="%s"} %.6F',
                $sample['method'],
                $sample['path'],
                $sample['value'],
            );
        }
        foreach ($state['http_request_duration_seconds_count'] as $sample) {
            $lines[] = sprintf(
                'http_request_duration_seconds_count{method="%s",path="%s"} %d',
                $sample['method'],
                $sample['path'],
                $sample['value'],
            );
        }

        $lines[] = '# HELP anomaly_window_active Ground truth anomaly window marker.';
        $lines[] = '# TYPE anomaly_window_active gauge';
        $anomalyType = $state['anomaly_window_active']['type'] ?? 'none';
        $lines[] = sprintf(
            'anomaly_window_active{type="%s"} %d',
            $anomalyType ?: 'none',
            $state['anomaly_window_active']['value'] ?? 0,
        );

        return implode("\n", $lines)."\n";
    }

    private function load(): array
    {
        if (! file_exists($this->path)) {
            return [
                'http_requests_total' => [],
                'http_errors_total' => [],
                'http_request_duration_seconds_bucket' => [],
                'http_request_duration_seconds_sum' => [],
                'http_request_duration_seconds_count' => [],
                'anomaly_window_active' => ['value' => 0, 'type' => null],
            ];
        }

        $decoded = json_decode((string) file_get_contents($this->path), true);

        return is_array($decoded) ? $decoded : [
            'http_requests_total' => [],
            'http_errors_total' => [],
            'http_request_duration_seconds_bucket' => [],
            'http_request_duration_seconds_sum' => [],
            'http_request_duration_seconds_count' => [],
            'anomaly_window_active' => ['value' => 0, 'type' => null],
        ];
    }

    private function store(array $state): void
    {
        $directory = dirname($this->path);

        if (! is_dir($directory)) {
            mkdir($directory, 0777, true);
        }

        file_put_contents($this->path, json_encode($state, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES), LOCK_EX);
    }

    private function key(array $parts): string
    {
        return implode('|', $parts);
    }
}
