export interface RuntimeErrorContext {
    message: string;
    code?: string;
    traceId?: string;
    runId?: string;
    agentId?: string;
    stage?: string;
    retryable?: boolean;
}

type RuntimePacket = Record<string, unknown>;

const text = (value: unknown): string | undefined =>
    typeof value === 'string' && value.trim() ? value.trim() : undefined;

const RETRYABLE_CODES = new Set([
    'model_tool_protocol_violation',
    'finish_protocol_violation',
    'invalid_tool_call_protocol_violation',
    'llm_timeout',
    'llm_unavailable',
    'llm_rate_limit',
    'model_provider_unavailable',
]);

export function normalizeRuntimeError(packet: RuntimePacket): RuntimeErrorContext {
    const canonical = packet.error && typeof packet.error === 'object'
        ? packet.error as RuntimePacket
        : {};
    const retryableRaw = canonical.retryable ?? packet.retryable;
    return {
        message: text(canonical.message)
            || text(packet.message)
            || text(packet.content)
            || text(packet.detail)
            || 'Request denied',
        code: text(canonical.code) || text(packet.code) || text(packet.delivery_error),
        traceId: text(canonical.trace_id) || text(packet.trace_id),
        runId: text(canonical.run_id) || text(packet.run_id),
        agentId: text(canonical.agent_id) || text(packet.agent_id),
        stage: text(canonical.stage) || text(packet.stage),
        retryable: typeof retryableRaw === 'boolean' ? retryableRaw : undefined,
    };
}

export function formatRuntimeErrorDiagnostics(error: RuntimeErrorContext): string {
    return [
        error.code && `Code: ${error.code}`,
        error.traceId && `Trace: ${error.traceId}`,
        error.runId && `Run: ${error.runId}`,
    ].filter(Boolean).join(' · ');
}

export function runtimeErrorRetryable(error: RuntimeErrorContext): boolean {
    if (typeof error.retryable === 'boolean') return error.retryable;
    if (!error.code) return false;
    if (error.code.endsWith('_protocol_violation')) return true;
    return RETRYABLE_CODES.has(error.code);
}

export const runtimeErrorDisablesReconnect = (error: RuntimeErrorContext): boolean =>
    error.code === 'model_unavailable'
    || error.code === 'agent_expired'
    || error.code === 'setup_failed';

export const runtimeErrorMarksAgentExpired = (error: RuntimeErrorContext): boolean =>
    error.code === 'agent_expired';
