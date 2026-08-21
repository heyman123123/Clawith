// Task Board Service - frontend wrapper for /api/task-board/*
import { fetchJson as request } from './api';

export type TaskColumn = 'backlog' | 'in_progress' | 'blocked' | 'review' | 'done';

export interface TaskCard {
  id: string;
  title: string;
  column: TaskColumn;
  position: number;
  version: number;
  assignee_agent_id?: string | null;
  artifact_paths?: string[];
}

export const taskBoardApi = {
  createCard: (payload: Record<string, unknown>) =>
    request<TaskCard>('/task-board/cards', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  listCardsByGroup: (tenantId: string, groupId: string) =>
    request<TaskCard[]>(
      `/task-board/cards?tenant_id=${encodeURIComponent(tenantId)}&group_id=${encodeURIComponent(groupId)}`,
    ),

  listCardsByAssignee: (tenantId: string, agentId: string) =>
    request<TaskCard[]>(
      `/task-board/cards?tenant_id=${encodeURIComponent(tenantId)}&agent_id=${encodeURIComponent(agentId)}`,
    ),

  moveCard: (cardId: string, payload: Record<string, unknown>) =>
    request<TaskCard>(`/task-board/cards/${cardId}/move`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),

  assignCard: (cardId: string, payload: Record<string, unknown>) =>
    request<TaskCard>(`/task-board/cards/${cardId}/assign`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),

  markDone: (cardId: string, payload: Record<string, unknown>) =>
    request<TaskCard>(`/task-board/cards/${cardId}/done`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
};
