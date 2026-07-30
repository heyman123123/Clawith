import { fetchJson } from './api';
import type { GroupWorkflow, WorkflowDraft, WorkflowEventPage } from '../types/groupWorkflow';

const root = (groupId: string) => `/groups/${groupId}/workflow`;

export const groupWorkflowApi = {
    get: (groupId: string) => fetchJson<GroupWorkflow>(root(groupId)),
    events: (groupId: string, page = 1, pageSize = 20) =>
        fetchJson<WorkflowEventPage>(`${root(groupId)}/events?page=${page}&page_size=${pageSize}`),
    preset: (groupId: string, kind: 'default' | 'agile' | 'product_research') =>
        fetchJson<GroupWorkflow>(`${root(groupId)}/preset`, { method: 'POST', body: JSON.stringify({ kind }) }),
    createDraft: (groupId: string, request: string) =>
        fetchJson<WorkflowDraft>(`${root(groupId)}/drafts`, { method: 'POST', body: JSON.stringify({ request }) }),
    draft: (groupId: string, draftId: string) =>
        fetchJson<WorkflowDraft>(`${root(groupId)}/drafts/${draftId}`),
    confirmDraft: (groupId: string, draftId: string) =>
        fetchJson<GroupWorkflow>(`${root(groupId)}/drafts/${draftId}/confirm`, { method: 'POST' }),
    patchItem: (groupId: string, itemId: string, status: 'in_progress' | 'unblock', expectedVersion?: number) =>
        fetchJson<GroupWorkflow>(`${root(groupId)}/items/${itemId}`, {
            method: 'PATCH', body: JSON.stringify({ status, expected_version: expectedVersion }),
        }),
    evidence: (groupId: string, itemId: string, evidence: Record<string, unknown>, expectedVersion?: number) =>
        fetchJson<GroupWorkflow>(`${root(groupId)}/items/${itemId}/evidence`, {
            method: 'POST', body: JSON.stringify({ evidence, expected_version: expectedVersion }),
        }),
    block: (groupId: string, itemId: string, reason: string, expectedVersion?: number) =>
        fetchJson<GroupWorkflow>(`${root(groupId)}/items/${itemId}/block`, {
            method: 'POST', body: JSON.stringify({ reason, expected_version: expectedVersion }),
        }),
    confirmStage: (groupId: string, stageId: string) =>
        fetchJson<GroupWorkflow>(`${root(groupId)}/stages/${stageId}/confirm`, { method: 'POST' }),
    pause: (groupId: string) => fetchJson<GroupWorkflow>(`${root(groupId)}/pause`, { method: 'POST' }),
    resume: (groupId: string) => fetchJson<GroupWorkflow>(`${root(groupId)}/resume`, { method: 'POST' }),
};
