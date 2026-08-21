// Chief Runtime Service - frontend wrapper for /api/chief/*
// Note: chief_chat is a chat_sessions row with chief_run_id set.
import { fetchJson as request } from './api';

export interface ChiefRun {
  id: string;
  tenant_id: string;
  group_id: string;
  chief_agent_id: string;
  status: 'active' | 'paused' | 'degraded' | 'stopped';
  failure_count: number;
  last_failure_reason?: string | null;
}

export const chiefApi = {
  getMessages: (chiefRunId: string) =>
    request<Array<{ id: string; from: 'user' | 'chief'; content: string; created_at: string }>>(
      `/chief/${chiefRunId}/messages`,
    ),

  postMessage: (chiefRunId: string, content: string) =>
    request<{ id: string; content: string }>(`/chief/${chiefRunId}/messages`, {
      method: 'POST',
      body: JSON.stringify({ content }),
    }),

  getStatus: (chiefRunId: string) =>
    request<ChiefRun>(`/chief/${chiefRunId}/status`),
};
