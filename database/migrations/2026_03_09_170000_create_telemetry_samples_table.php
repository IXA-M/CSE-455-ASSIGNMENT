<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Schema;

return new class extends Migration
{
    public function up(): void
    {
        Schema::create('telemetry_samples', function (Blueprint $table) {
            $table->id();
            $table->string('label');
            $table->timestamps();
        });

        DB::table('telemetry_samples')->insert([
            ['label' => 'seed-a', 'created_at' => now(), 'updated_at' => now()],
            ['label' => 'seed-b', 'created_at' => now(), 'updated_at' => now()],
            ['label' => 'seed-c', 'created_at' => now(), 'updated_at' => now()],
        ]);
    }

    public function down(): void
    {
        Schema::dropIfExists('telemetry_samples');
    }
};
