<?php

use App\Http\Controllers\Api\MonitoringController;
use Illuminate\Support\Facades\Route;

Route::get('/normal', [MonitoringController::class, 'normal'])->name('api.normal');
Route::get('/slow', [MonitoringController::class, 'slow'])->name('api.slow');
Route::get('/error', [MonitoringController::class, 'error'])->name('api.error');
Route::get('/random', [MonitoringController::class, 'random'])->name('api.random');
Route::get('/db', [MonitoringController::class, 'db'])->name('api.db');
Route::post('/validate', [MonitoringController::class, 'validatePayload'])->name('api.validate');
Route::get('/metrics', [MonitoringController::class, 'metrics'])->name('api.metrics');
Route::post('/anomaly-window', [MonitoringController::class, 'anomalyWindow'])->name('api.anomaly-window');
