import { fetchJson } from './api';
import type {
    AIAgentStats,
    AIGroupStats,
    AIInteractionDetail,
    AIInteractionOverview,
    AIInteractionPage,
} from '../types/aiMonitoring';

export type AIMonitoringSortBy = 'failures' | 'tokens' | 'calls';
export type AIMonitoringRange = '24h';

function query(params: Record<string, string | number | undefined | null | boolean>): string {
    const search = new URLSearchParams();
    for (const [key, value] of Object.entries(params)) {
        if (value === undefined || value === null || value === '' || value === false) continue;
        search.set(key, String(value));
    }
    const text = search.toString();
    return text ? `?${text}` : '';
}

export const aiMonitoringApi = {
    overview: (page = 1, pageSize = 20, filters?: {
        agentId?: string | null;
        unassigned?: boolean;
        date?: string | null;
        range?: AIMonitoringRange | null;
    }) =>
        fetchJson<AIInteractionOverview>(`/ai-monitoring/overview${query({
            page,
            page_size: pageSize,
            agent_id: filters?.agentId,
            unassigned: filters?.unassigned ? 'true' : undefined,
            date: filters?.date,
            range: filters?.range,
        })}`),
    groupStats: (filters?: {
        date?: string | null;
        range?: AIMonitoringRange | null;
        sortBy?: AIMonitoringSortBy;
        order?: 'asc' | 'desc';
    }) =>
        fetchJson<AIGroupStats>(`/ai-monitoring/groups/stats${query({
            date: filters?.date,
            range: filters?.date ? undefined : (filters?.range ?? '24h'),
            sort_by: filters?.sortBy ?? 'failures',
            order: filters?.order ?? 'desc',
        })}`),
    agentStats: (filters?: {
        groupId?: string | null;
        date?: string | null;
        range?: AIMonitoringRange | null;
        sortBy?: AIMonitoringSortBy;
        order?: 'asc' | 'desc';
    }) =>
        fetchJson<AIAgentStats>(`/ai-monitoring/agents/stats${query({
            group_id: filters?.groupId,
            date: filters?.date,
            range: filters?.date ? undefined : (filters?.range ?? '24h'),
            sort_by: filters?.sortBy ?? 'failures',
            order: filters?.order ?? 'desc',
        })}`),
    groupInteractions: (groupId: string, page = 1, pageSize = 20, filters?: {
        agentId?: string | null;
        unassigned?: boolean;
        date?: string | null;
        range?: AIMonitoringRange | null;
    }) =>
        fetchJson<AIInteractionPage>(`/ai-monitoring/groups/${groupId}/interactions${query({
            page,
            page_size: pageSize,
            agent_id: filters?.agentId,
            unassigned: filters?.unassigned ? 'true' : undefined,
            date: filters?.date,
            range: filters?.range,
        })}`),
    detail: (interactionId: string) =>
        fetchJson<AIInteractionDetail>(`/ai-monitoring/interactions/${interactionId}`),
};
