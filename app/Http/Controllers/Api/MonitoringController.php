<?php

namespace App\Http\Controllers\Api;

use App\Http\Controllers\Controller;
use App\Support\AnomalyState;
use App\Support\MetricsStore;
use Illuminate\Http\JsonResponse;
use Illuminate\Http\Request;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Validator;
use Symfony\Component\HttpFoundation\Response;

class MonitoringController extends Controller
{
    public function normal(): JsonResponse
    {
        return response()->json([
            'status' => 'ok',
            'message' => 'normal response',
        ]);
    }

    public function slow(Request $request): JsonResponse
    {
        if ($request->boolean('hard')) {
            sleep(random_int(5, 7));
        } else {
            sleep(5);
        }

        return response()->json([
            'status' => 'ok',
            'mode' => $request->boolean('hard') ? 'hard' : 'standard',
            'message' => 'slow response',
        ]);
    }

    public function error(): JsonResponse
    {
        abort(Response::HTTP_INTERNAL_SERVER_ERROR, 'Simulated system error');
    }

    public function random(): JsonResponse
    {
        $delay = random_int(1, 10);
        sleep($delay);

        return response()->json([
            'status' => 'ok',
            'delay_seconds' => $delay,
            'message' => 'random response',
        ]);
    }

    public function db(Request $request): JsonResponse
    {
        if ($request->boolean('fail')) {
            DB::table('non_existing_aiops_table')->count();
        }

        $total = DB::table('telemetry_samples')->count();

        return response()->json([
            'status' => 'ok',
            'rows' => $total,
            'message' => 'database query succeeded',
        ]);
    }

    public function validatePayload(Request $request): JsonResponse
    {
        Validator::make($request->all(), [
            'email' => ['required', 'email'],
            'age' => ['required', 'integer', 'between:18,60'],
        ])->validate();

        return response()->json([
            'status' => 'ok',
            'message' => 'payload is valid',
        ], Response::HTTP_CREATED);
    }

    public function metrics(MetricsStore $metricsStore): Response
    {
        return response($metricsStore->renderPrometheus(), Response::HTTP_OK, [
            'Content-Type' => 'text/plain; version=0.0.4; charset=utf-8',
        ]);
    }

    public function anomalyWindow(Request $request, AnomalyState $anomalyState): JsonResponse
    {
        $validated = Validator::make($request->all(), [
            'active' => ['required', 'boolean'],
            'type' => ['nullable', 'string'],
            'started_at' => ['nullable', 'date'],
            'ends_at' => ['nullable', 'date'],
        ])->validate();

        $anomalyState->setState(
            (bool) $validated['active'],
            $validated['type'] ?? null,
            $validated['started_at'] ?? null,
            $validated['ends_at'] ?? null,
        );

        return response()->json([
            'status' => 'ok',
            'active' => (bool) $validated['active'],
        ]);
    }
}
