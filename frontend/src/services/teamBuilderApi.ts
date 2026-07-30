import { fetchJson } from './api';
import type { TeamBuildDraft, TeamBuildHistoryItem, TeamPlan, TeamProvisionJob, TeamWorkflowPreset } from '../types/teamBuilder';

const root = '/team-build-drafts';

export const teamBuilderApi = {
    listHistory: (limit = 30) => fetchJson<TeamBuildHistoryItem[]>(`${root}?limit=${limit}`),

    createDraft: (data: {
        requirement: string;
        group_name?: string;
        constraints?: Record<string, unknown>;
        workflow_preset?: Exclude<TeamWorkflowPreset, 'custom'>;
    }) => fetchJson<TeamBuildDraft>(root, { method: 'POST', body: JSON.stringify(data) }),

    getDraft: (draftId: string) => fetchJson<TeamBuildDraft>(`${root}/${draftId}`),

    updateDraft: (draftId: string, reviewedPlan: TeamPlan) => fetchJson<TeamBuildDraft>(
        `${root}/${draftId}`,
        { method: 'PATCH', body: JSON.stringify({ reviewed_plan: reviewedPlan }) },
    ),

    reviseDraft: (draftId: string, feedback: string, scope: 'members' | 'workflow' | 'both' = 'both') =>
        fetchJson<TeamBuildDraft>(`${root}/${draftId}/revise`, {
            method: 'POST',
            body: JSON.stringify({ feedback, scope }),
        }),

    applyWorkflowPreset: (draftId: string, preset: Exclude<TeamWorkflowPreset, 'custom'>) =>
        fetchJson<TeamBuildDraft>(`${root}/${draftId}/workflow-preset`, {
            method: 'POST',
            body: JSON.stringify({ preset }),
        }),

    confirmDraft: (draftId: string, planVersion: number, idempotencyKey: string) =>
        fetchJson<TeamProvisionJob>(`${root}/${draftId}/confirm`, {
            method: 'POST',
            body: JSON.stringify({ plan_version: planVersion, idempotency_key: idempotencyKey }),
        }),

    getJob: (jobId: string) => fetchJson<TeamProvisionJob>(`${root}/jobs/${jobId}`),
};
