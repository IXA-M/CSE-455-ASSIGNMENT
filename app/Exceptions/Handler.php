<?php

namespace App\Exceptions;

use App\Support\ErrorCategory;
use Illuminate\Database\QueryException;
use Illuminate\Foundation\Configuration\Exceptions;
use Illuminate\Http\Request;
use Illuminate\Validation\ValidationException;
use Symfony\Component\HttpFoundation\Response;
use Throwable;

class Handler
{
    public static function register(Exceptions $exceptions): void
    {
        $exceptions->report(function (Throwable $throwable) {
            $request = request();

            if (! $request instanceof Request) {
                return;
            }

            $category = self::categorize($throwable, $request, null);

            $request->attributes->set('error_category', $category);
            $request->attributes->set('exception_class', $throwable::class);
        });

        $exceptions->render(function (Throwable $throwable, Request $request) {
            $latencyMs = $request->attributes->get('telemetry_latency_ms');
            $category = self::categorize($throwable, $request, $latencyMs);

            $request->attributes->set('error_category', $category);
            $request->attributes->set('exception_class', $throwable::class);

            if ($throwable instanceof ValidationException) {
                return response()->json([
                    'message' => 'Validation failed',
                    'error_category' => $category,
                    'errors' => $throwable->errors(),
                ], Response::HTTP_UNPROCESSABLE_ENTITY);
            }

            if ($throwable instanceof QueryException) {
                return response()->json([
                    'message' => 'Database query failed',
                    'error_category' => $category,
                ], Response::HTTP_INTERNAL_SERVER_ERROR);
            }

            if ($category === ErrorCategory::SYSTEM_ERROR || $category === ErrorCategory::UNKNOWN) {
                return response()->json([
                    'message' => $throwable->getMessage() ?: 'System failure',
                    'error_category' => $category,
                ], Response::HTTP_INTERNAL_SERVER_ERROR);
            }

            return null;
        });
    }

    public static function categorize(Throwable $throwable, Request $request, ?int $latencyMs): string
    {
        if ($throwable instanceof ValidationException) {
            return ErrorCategory::VALIDATION_ERROR;
        }

        if ($throwable instanceof QueryException) {
            return ErrorCategory::DATABASE_ERROR;
        }

        if (
            $latencyMs !== null &&
            $latencyMs > ErrorCategory::TIMEOUT_THRESHOLD_MS &&
            $request->routeIs('api.slow') &&
            $request->boolean('hard')
        ) {
            return ErrorCategory::TIMEOUT_ERROR;
        }

        if ($throwable instanceof \Symfony\Component\HttpKernel\Exception\HttpExceptionInterface) {
            return ErrorCategory::SYSTEM_ERROR;
        }

        return ErrorCategory::UNKNOWN;
    }
}
