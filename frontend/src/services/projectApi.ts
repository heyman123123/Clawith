import { fetchJson } from './api';
import type { ProjectWorkflow, ShareholderGroup, TeamPlan } from '../types/project';

export const projectApi = {
    buildTeamPlan: (data: { name: string; requirements: string }) =>
        fetchJson<TeamPlan>('/projects/team-plans', { method: 'POST', body: JSON.stringify(data) }),
    create: (data: { name: string; requirements: string; team_plan: TeamPlan }) =>
        fetchJson<ProjectWorkflow>('/projects', { method: 'POST', body: JSON.stringify(data) }),
    provision: (workflowId: string) =>
        fetchJson<ProjectWorkflow>(`/projects/${workflowId}/provision`, { method: 'POST' }),
    ensureDecisionGroup: (workflowId: string) =>
        fetchJson<ProjectWorkflow>(`/projects/${workflowId}/decision-group`, { method: 'POST' }),
    shareholderGroup: () => fetchJson<ShareholderGroup | null>('/projects/shareholder-group'),
    createShareholderGroup: () => fetchJson<ShareholderGroup>('/projects/shareholder-group', { method: 'POST' }),
    list: () => fetchJson<ProjectWorkflow[]>('/projects'),
};
