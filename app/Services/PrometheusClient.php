<?php

namespace App\Services;

use Illuminate\Support\Facades\Http;
use RuntimeException;

class PrometheusClient
{
    private string $baseUrl;

    public function __construct(?string $baseUrl = null)
    {
        $this->baseUrl = rtrim($baseUrl ?: config('aiops.prometheus_url', 'http://localhost:9090'), '/');
    }

    public function fetchRequestRatePerEndpoint(string $window = '2m'): array
    {
        $result = $this->query(sprintf('sum by (path) (rate(http_requests_total[%s]))', $window));

        return $this->vectorToKeyedSeries($result, 'path');
    }

    public function fetchErrorRatePerEndpoint(string $window = '2m'): array
    {
        $result = $this->query(sprintf(
            'sum by (path) (rate(http_errors_total[%s])) / sum by (path) (rate(http_requests_total[%s]))',
            $window,
            $window,
        ));

        return $this->vectorToKeyedSeries($result, 'path');
    }

    public function fetchAverageLatencySecondsPerEndpoint(string $window = '2m'): array
    {
        $result = $this->query(sprintf(
            'sum by (path) (rate(http_request_duration_seconds_sum[%s])) / sum by (path) (rate(http_request_duration_seconds_count[%s]))',
            $window,
            $window,
        ));

        return $this->vectorToKeyedSeries($result, 'path');
    }

    public function fetchLatencyPercentilesPerEndpoint(string $window = '2m', array $percentiles = [0.5, 0.95, 0.99]): array
    {
        $data = [];

        foreach ($percentiles as $percentile) {
            $query = sprintf(
                'histogram_quantile(%.2F, sum by (le, path) (rate(http_request_duration_seconds_bucket[%s])))',
                $percentile,
                $window,
            );

            $data[(string) $percentile] = $this->vectorToKeyedSeries($this->query($query), 'path');
        }

        return $data;
    }

    public function fetchErrorCategoryRates(string $window = '2m'): array
    {
        $result = $this->query(sprintf('sum by (path, error_category) (rate(http_errors_total[%s]))', $window));
        $grouped = [];

        foreach ($result as $sample) {
            $metric = $sample['metric'] ?? [];
            $path = $metric['path'] ?? 'unknown';
            $category = $metric['error_category'] ?? 'unknown';
            $value = isset($sample['value'][1]) ? (float) $sample['value'][1] : 0.0;

            $grouped[$path][$category] = $value;
        }

        return $grouped;
    }

    public function query(string $promql): array
    {
        $response = Http::timeout(6)->get($this->baseUrl.'/api/v1/query', [
            'query' => $promql,
        ]);

        if (! $response->ok()) {
            throw new RuntimeException('Prometheus query failed: HTTP '.$response->status());
        }

        $payload = $response->json();

        if (! is_array($payload) || ($payload['status'] ?? null) !== 'success') {
            throw new RuntimeException('Prometheus query failed: invalid response');
        }

        return $payload['data']['result'] ?? [];
    }

    private function vectorToKeyedSeries(array $vector, string $label): array
    {
        $series = [];

        foreach ($vector as $sample) {
            $metric = $sample['metric'] ?? [];
            $key = $metric[$label] ?? 'unknown';
            $series[$key] = isset($sample['value'][1]) ? (float) $sample['value'][1] : 0.0;
        }

        return $series;
    }
}
