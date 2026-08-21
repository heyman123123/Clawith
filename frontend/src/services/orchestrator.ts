// Orchestrator Service - frontend wrapper for /api/orchestrator/*
// Uses the unified request wrapper; never imports axios directly (C4).
import { fetchJson as request } from './api';

export interface DraftSummary {
  intent_category: string;
  scope_summary: string;
  key_constraints: string[];
}

export interface AgentProposal {
  role: string;
  name: string;
  system_prompt?: string;
  template_id?: string | null;
  is_new_template: boolean;
  suggested_visibility: 'user_private' | 'tenant_private' | 'public';
}

export interface GroupProposal {
  name: string;
  description: string;
}

export interface TaskProposal {
  title: string;
  description: string;
  assignee_role?: string;
  estimated_artifacts?: string[];
}

export interface OKRProposal {
  objective_title: string;
  objective_description: string;
  key_results: Array<{
    title: string;
    target_value: number;
    unit?: string;
    acceptance_artifact_paths: string[];
  }>;
}

export interface Draft {
  id: string;
  tenant_id: string;
  user_id: string;
  user_message: string;
  summary: DraftSummary;
  group: GroupProposal;
  members: AgentProposal[];
  okr?: OKRProposal | null;
  tasks: TaskProposal[];
  template_visibility: 'user_private' | 'tenant_private' | 'public';
  status: 'pending' | 'approved' | 'rejected' | 'expired' | 'error' | 'consumed';
  created_at: string;
  updated_at: string;
}

export interface ProjectCreated {
  draft_id: string;
  group_id: string;
  chief_agent_id: string;
  chief_run_id: string;
  task_card_ids: string[];
  okr_objective_id?: string | null;
}

export const orchestratorApi = {
  proposeDraft: (userMessage: string) =>
    request<Draft>('/orchestrator/drafts', {
      method: 'POST',
      body: JSON.stringify({ user_message: userMessage }),
    }),

  getDraft: (id: string) => request<Draft>(`/orchestrator/drafts/${id}`),

  listDrafts: () => request<Draft[]>('/orchestrator/drafts'),

  approveDraft: (id: string) =>
    request<Draft>(`/orchestrator/drafts/${id}/approve`, { method: 'POST' }),

  createDraft: (id: string) =>
    request<ProjectCreated>(`/orchestrator/drafts/${id}/create`, { method: 'POST' }),

  setProactiveEnabled: (enabled: boolean) =>
    request<{ enabled: boolean }>('/orchestrator/settings/proactive', {
      method: 'POST',
      body: JSON.stringify({ enabled }),
    }),
};
