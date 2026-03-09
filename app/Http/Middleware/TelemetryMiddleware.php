<?php

namespace App\Http\Middleware;

use App\Exceptions\Handler;
use App\Support\ErrorCategory;
use App\Support\MetricsStore;
use Closure;
use Illuminate\Contracts\Debug\ExceptionHandler as ExceptionHandlerContract;
use Illuminate\Http\Request;
use Illuminate\Support\Facades\Log;
use Illuminate\Support\Str;
use Symfony\Component\HttpFoundation\Response;
use Throwable;

class TelemetryMiddleware
{
    public function __construct(private readonly MetricsStore $metricsStore)
    {
    }

    public function handle(Request $request, Closure $next): Response
    {
        if ($request->is('api/metrics')) {
            return $next($request);
        }

        $requestId = $request->headers->get('X-Request-Id') ?: (string) Str::uuid();
        $request->attributes->set('request_id', $requestId);

        $startedAt = microtime(true);

        try {
            $response = $next($request);
        } catch (Throwable $throwable) {
            $latencyMs = (int) round((microtime(true) - $startedAt) * 1000);
            $request->attributes->set('telemetry_latency_ms', $latencyMs);
            $request->attributes->set('error_category', Handler::categorize($throwable, $request, $latencyMs));
            $request->attributes->set('exception_class', $throwable::class);

            $exceptionHandler = app(ExceptionHandlerContract::class);
            $exceptionHandler->report($throwable);
            $response = $exceptionHandler->render($request, $throwable);
        }

        $latencyMs = (int) round((microtime(true) - $startedAt) * 1000);
        $request->attributes->set('telemetry_latency_ms', $latencyMs);

        $errorCategory = $request->attributes->get('error_category') ?: ErrorCategory::NONE;

        if ($errorCategory === ErrorCategory::NONE && Handler::categorize(new \RuntimeException('Latency threshold exceeded'), $request, $latencyMs) === ErrorCategory::TIMEOUT_ERROR) {
            $errorCategory = ErrorCategory::TIMEOUT_ERROR;
            $request->attributes->set('error_category', $errorCategory);
        }

        $severity = $this->resolveSeverity($response, $errorCategory);
        $response->headers->set('X-Request-Id', $requestId);

        $record = [
            'timestamp' => now()->toIso8601String(),
            'request_id' => $requestId,
            'method' => $request->getMethod(),
            'path' => '/'.$request->path(),
            'query' => $request->getQueryString(),
            'route_name' => optional($request->route())->getName() ?? 'unknown',
            'status_code' => $response->getStatusCode(),
            'latency_ms' => $latencyMs,
            'error_category' => $errorCategory,
            'severity' => $severity,
            'message' => $this->resolveMessage($response, $errorCategory),
            'client_ip' => $request->ip(),
            'user_agent' => $request->userAgent(),
            'payload_size_bytes' => strlen($request->getContent()),
            'response_size_bytes' => strlen((string) $response->getContent()),
            'build_version' => config('app.build_version'),
            'host' => gethostname() ?: php_uname('n'),
            'exception_class' => $request->attributes->get('exception_class'),
        ];

        Log::channel('aiops')->info(json_encode($record, JSON_UNESCAPED_SLASHES));

        $normalizedPath = $request->route()?->uri() ? '/'.$request->route()->uri() : '/'.$request->path();
        $this->metricsStore->recordRequest(
            method: $request->getMethod(),
            path: $normalizedPath,
            statusCode: $response->getStatusCode(),
            latencySeconds: $latencyMs / 1000,
            errorCategory: $record['error_category'],
        );

        return $response;
    }

    private function resolveSeverity(Response $response, ?string $errorCategory): string
    {
        if (($errorCategory && $errorCategory !== ErrorCategory::NONE) || $response->getStatusCode() >= 400) {
            return 'error';
        }

        return 'info';
    }

    private function resolveMessage(Response $response, ?string $errorCategory): string
    {
        if ($errorCategory === ErrorCategory::TIMEOUT_ERROR) {
            return 'Latency threshold exceeded';
        }

        if ($response->getStatusCode() >= 400) {
            return 'Request failed';
        }

        return 'Request completed';
    }
}
