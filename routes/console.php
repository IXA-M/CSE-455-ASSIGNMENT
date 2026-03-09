<?php

use Illuminate\Foundation\Inspiring;
use Illuminate\Support\Facades\Artisan;
use Illuminate\Support\Facades\File;

Artisan::command('inspire', function () {
    $this->comment(Inspiring::quote());
})->purpose('Display an inspiring quote');

Artisan::command('telemetry:export-logs', function () {
    $source = storage_path('logs/aiops.log');
    $target = base_path('logs.json');

    if (! File::exists($source)) {
        $this->error('No aiops.log file found.');
        return self::FAILURE;
    }

    $records = [];

    foreach (file($source, FILE_IGNORE_NEW_LINES | FILE_SKIP_EMPTY_LINES) as $line) {
        $decoded = json_decode($line, true);

        if (is_array($decoded)) {
            $records[] = $decoded;
        }
    }

    File::put($target, json_encode($records, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES));

    $this->info('Exported '.count($records).' records to '.$target);

    return self::SUCCESS;
})->purpose('Export telemetry records to logs.json');

Artisan::command('telemetry:reset', function () {
    File::ensureDirectoryExists(storage_path('logs'));
    File::put(storage_path('logs/aiops.log'), '');
    File::delete(storage_path('app/metrics_store.json'));
    File::delete(storage_path('app/anomaly_state.json'));
    File::delete(base_path('logs.json'));
    File::delete(base_path('ground_truth.json'));

    $this->info('Telemetry artifacts reset.');

    return self::SUCCESS;
})->purpose('Reset logs, exported datasets, and metric state');
