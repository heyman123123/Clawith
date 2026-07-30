export interface AIInteractionSummary {
    id: string;
    agent_id: string | null;
    agent_name: string | null;
    llm_model_id: string | null;
    model_label: string | null;
    provider: string;
    model_name: string;
    source: string;
    invocation_kind: string;
    status: 'success' | 'error';
    token_source: 'provider' | 'estimated' | 'unavailable';
    input_tokens: number;
    output_tokens: number;
    cache_read_tokens: number;
    cache_creation_tokens: number;
    total_tokens: number;
    estimated_tokens: number;
    duration_ms: number | null;
    started_at: string;
    finished_at: string;
    created_at: string;
}

export interface AIInteractionDetail extends AIInteractionSummary {
    session_id: string | null;
    run_id: string | null;
    request_context: Record<string, unknown>;
    response_content: string | null;
    error: Record<string, unknown> | null;
}

export interface AIInteractionOverview {
    calls_24h: number;
    errors_24h: number;
    total_tokens_24h: number;
    page: number;
    page_size: number;
    total: number;
    interactions: AIInteractionSummary[];
}

export interface AIInteractionPage {
    page: number;
    page_size: number;
    total: number;
    interactions: AIInteractionSummary[];
}

export interface AIAgentStatsRow {
    agent_id: string | null;
    agent_name: string | null;
    calls: number;
    successes: number;
    failures: number;
    total_tokens: number;
}

export interface AIAgentStats {
    range: string;
    date: string | null;
    since: string;
    until: string;
    sort_by: 'failures' | 'tokens' | 'calls';
    order: 'asc' | 'desc';
    group_id?: string | null;
    calls: number;
    successes: number;
    failures: number;
    total_tokens: number;
    agents: AIAgentStatsRow[];
}

export interface AIGroupStatsRow {
    group_id: string | null;
    group_name: string | null;
    calls: number;
    successes: number;
    failures: number;
    total_tokens: number;
}

export interface AIGroupStats {
    range: string;
    date: string | null;
    since: string;
    until: string;
    sort_by: 'failures' | 'tokens' | 'calls';
    order: 'asc' | 'desc';
    calls: number;
    successes: number;
    failures: number;
    total_tokens: number;
    groups: AIGroupStatsRow[];
}
