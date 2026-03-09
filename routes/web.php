<?php

use App\Http\Controllers\Api\MonitoringController;
use Illuminate\Support\Facades\Route;

Route::get('/', function () {
    return view('welcome');
});

Route::get('/metrics', [MonitoringController::class, 'metrics'])->name('metrics');
