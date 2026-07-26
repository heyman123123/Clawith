import { fetchJson } from './api';

export type RetryStrategy = 'fresh_context' | 'in_place';

export interface RetryRunResponse {
    run_id: string;
    thread_id: string;
    command_id: string;
    runtime_type: string;
    created: boolean;
    retry_of_run_id: string;
    strategy: string;
}

export function retryAgentRun(
    runId: string,
    strategy: RetryStrategy = 'fresh_context',
): Promise<RetryRunResponse> {
    return fetchJson<RetryRunResponse>(`/agent-runs/${runId}/retry`, {
        method: 'POST',
        body: JSON.stringify({ strategy }),
    });
}
