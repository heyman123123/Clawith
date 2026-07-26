import { fetchJson } from './api';

export type AiOperationsData = {
    period: { days: number; from: string; to: string };
    overview: { total_runs: number; success: number; failed: number; cancelled: number; success_rate: number; failure_rate: number };
    daily: Array<{ date: string; success: number; failed: number; cancelled: number }>;
    agents: Array<{ agent_id: string | null; agent_name: string; success: number; failed: number; cancelled: number; total: number; success_rate: number }>;
    models: Array<{ model_id: string | null; model_name: string; provider: string; success: number; failed: number; cancelled: number; total: number; success_rate: number }>;
    failures: Array<{ run_id: string; agent_id: string | null; agent_name: string; model_name: string; error_code: string; error_message: string; goal: string; created_at: string }>;
    reports: Array<{ run_id: string; agent_id: string | null; agent_name: string; title: string; report_type: string; created_at: string }>;
};

export type AiOperationRunDetail = {
    run: {
        id: string;
        goal: string;
        source_type: string;
        created_at: string;
        agent_name: string;
        model_name: string;
        provider: string;
    };
    failure: { error_code: string; error_message: string; created_at: string } | null;
    input_context: string;
    return_content: string | null;
    timeline: Array<{
        created_at: string;
        event_type: string;
        summary: string;
        activity_type: string | null;
        content: string | null;
        tool_name: string | null;
        tool_arguments: unknown;
        tool_result: string | null;
        error_code: string | null;
    }>;
};

export const aiOperationsApi = {
    get: (days: 7 | 30 | 90) => fetchJson<AiOperationsData>(`/enterprise/ai-operations?days=${days}`),
    runDetail: (runId: string) => fetchJson<AiOperationRunDetail>(`/enterprise/ai-operations/runs/${runId}`),
};
