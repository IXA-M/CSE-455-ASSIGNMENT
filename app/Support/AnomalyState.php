<?php

namespace App\Support;

class AnomalyState
{
    private string $path;

    public function __construct()
    {
        $this->path = storage_path('app/anomaly_state.json');
    }

    public function setState(bool $active, ?string $type = null, ?string $startedAt = null, ?string $endsAt = null): void
    {
        $directory = dirname($this->path);

        if (! is_dir($directory)) {
            mkdir($directory, 0777, true);
        }

        file_put_contents($this->path, json_encode([
            'active' => $active,
            'type' => $type,
            'started_at' => $startedAt,
            'ends_at' => $endsAt,
        ], JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES), LOCK_EX);
    }

    public function getState(): array
    {
        if (! file_exists($this->path)) {
            return [
                'active' => false,
                'type' => null,
                'started_at' => null,
                'ends_at' => null,
            ];
        }

        $decoded = json_decode((string) file_get_contents($this->path), true);

        if (! is_array($decoded)) {
            return [
                'active' => false,
                'type' => null,
                'started_at' => null,
                'ends_at' => null,
            ];
        }

        return array_merge([
            'active' => false,
            'type' => null,
            'started_at' => null,
            'ends_at' => null,
        ], $decoded);
    }
}
