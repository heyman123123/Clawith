import { fetchJson } from './api';
import type { AIInteractionDetail, AIInteractionOverview, AIInteractionPage } from '../types/aiMonitoring';

export const aiMonitoringApi = {
    overview: (page = 1, pageSize = 20) =>
        fetchJson<AIInteractionOverview>(`/ai-monitoring/overview?page=${page}&page_size=${pageSize}`),
    groupInteractions: (groupId: string, page = 1, pageSize = 20) =>
        fetchJson<AIInteractionPage>(`/ai-monitoring/groups/${groupId}/interactions?page=${page}&page_size=${pageSize}`),
    detail: (interactionId: string) =>
        fetchJson<AIInteractionDetail>(`/ai-monitoring/interactions/${interactionId}`),
};
