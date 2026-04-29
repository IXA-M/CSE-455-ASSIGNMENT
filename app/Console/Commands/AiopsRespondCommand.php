<?php

namespace App\Console\Commands;

use Illuminate\Console\Command;
use Illuminate\Support\Facades\File;

class AiopsRespondCommand extends Command
{
    protected $signature = 'aiops:respond {--watch : Continue monitoring incident records} {--interval=10 : Watch interval in seconds}';

    protected $description = 'Run the AIOps automation engine against detected incidents.';

    public function handle(): int
    {
        $this->info('[aiops] Automation engine started.');

        do {
            try {
                $processed = $this->processOpenIncidents();
                $this->info(sprintf('[aiops] Processed %d open incident(s).', $processed));
            } catch (\Throwable $e) {
                $this->error('[aiops] Automation cycle failed: '.$e->getMessage());

                return self::FAILURE;
            }

            if (! $this->option('watch')) {
                break;
            }

            $sleepFor = max(1, (int) $this->option('interval'));
            $this->line(sprintf('[aiops] Sleeping %ds...', $sleepFor));
            sleep($sleepFor);
        } while (true);

        return self::SUCCESS;
    }

    private function processOpenIncidents(): int
    {
        $incidents = $this->loadJson(storage_path('aiops/incidents.json'), []);
        $responses = $this->loadJson(storage_path('aiops/responses.json'), []);
        $processed = 0;

        foreach ($incidents as $incident) {
            if (($incident['status'] ?? null) !== 'open') {
                continue;
            }

            $processed++;
            $incidentId = (string) ($incident['incident_id'] ?? '');

            if ($incidentId === '') {
                continue;
            }

            $incidentResponses = $this->responsesForIncident($responses, $incidentId);

            if ($this->alreadyEscalated($incidentResponses)) {
                $this->line(sprintf('[aiops] Incident %s is already escalated.', $incidentId));
                continue;
            }

            if ($incidentResponses !== []) {
                $responses[] = $this->buildEscalationResponse(
                    $incident,
                    'Anomaly persisted after automated response; escalating to critical alert.'
                );
                $this->warn(sprintf('[aiops] Escalated persistent incident %s.', $incidentId));
                continue;
            }

            $response = $this->executePolicy($incident);
            $responses[] = $response;

            if (($response['result'] ?? null) === 'failed') {
                $responses[] = $this->buildEscalationResponse(
                    $incident,
                    'Automated action failed; escalating to critical alert.'
                );
                $this->warn(sprintf('[aiops] Escalated failed response for incident %s.', $incidentId));
            } else {
                $this->line(sprintf(
                    '[aiops] Incident %s handled with %s.',
                    $incidentId,
                    $response['action_taken']
                ));
            }
        }

        $this->storeJson(storage_path('aiops/responses.json'), $responses);

        return $processed;
    }

    private function executePolicy(array $incident): array
    {
        $type = (string) ($incident['incident_type'] ?? 'default');
        $policy = config("aiops.response_policies.$type", config('aiops.response_policies.default'));
        $success = (bool) ($policy['simulate_success'] ?? true);
        $action = (string) ($policy['action'] ?? 'incident_escalation');
        $summary = (string) ($incident['summary'] ?? 'No incident summary available.');
        $endpoints = implode(', ', $incident['affected_endpoints'] ?? []);

        return [
            'incident_id' => (string) ($incident['incident_id'] ?? ''),
            'action_taken' => $action,
            'timestamp' => now()->toIso8601String(),
            'result' => $success ? 'success' : 'failed',
            'notes' => trim(sprintf(
                '%s Incident type: %s. Affected endpoints: %s. %s',
                (string) ($policy['notes'] ?? 'Simulated automated response.'),
                $type,
                $endpoints !== '' ? $endpoints : 'none',
                $summary
            )),
        ];
    }

    private function buildEscalationResponse(array $incident, string $reason): array
    {
        return [
            'incident_id' => (string) ($incident['incident_id'] ?? ''),
            'action_taken' => (string) config('aiops.response_escalation_action', 'CRITICAL_ALERT'),
            'timestamp' => now()->toIso8601String(),
            'result' => 'escalated',
            'notes' => $reason,
        ];
    }

    private function responsesForIncident(array $responses, string $incidentId): array
    {
        return array_values(array_filter(
            $responses,
            fn (array $response): bool => ($response['incident_id'] ?? null) === $incidentId
        ));
    }

    private function alreadyEscalated(array $responses): bool
    {
        $escalationAction = (string) config('aiops.response_escalation_action', 'CRITICAL_ALERT');

        foreach ($responses as $response) {
            if (($response['action_taken'] ?? null) === $escalationAction) {
                return true;
            }
        }

        return false;
    }

    private function loadJson(string $path, array $default): array
    {
        if (! File::exists($path)) {
            return $default;
        }

        $decoded = json_decode((string) File::get($path), true);

        return is_array($decoded) ? $decoded : $default;
    }

    private function storeJson(string $path, array $payload): void
    {
        File::ensureDirectoryExists(dirname($path));
        File::put($path, json_encode($payload, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES));
    }
}
