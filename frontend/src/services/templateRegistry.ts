// Template Registry Service - frontend wrapper for /api/template-registry/*
import { fetchJson as request } from './api';

export interface TemplateRecord {
  id: string;
  name: string;
  category: string;
  template_visibility: 'public' | 'tenant_private' | 'user_private';
  is_builtin: boolean;
  approval_state: 'draft' | 'approved' | 'rejected';
}

export const templateRegistryApi = {
  browse: (tenantId?: string) => {
    const q = tenantId ? `?tenant_id=${encodeURIComponent(tenantId)}` : '';
    return request<TemplateRecord[]>(`/template-registry/browse${q}`);
  },

  pendingApproval: (tenantId?: string) => {
    const q = tenantId ? `?tenant_id=${encodeURIComponent(tenantId)}` : '';
    return request<TemplateRecord[]>(`/template-registry/pending-approval${q}`);
  },

  approve: (templateId: string, approverId: string) =>
    request<{ id: string; approval_state: string }>(
      `/template-registry/${templateId}/approve`,
      { method: 'POST', body: JSON.stringify({ approver_id: approverId }) },
    ),

  reject: (templateId: string, reason: string) =>
    request<{ id: string; approval_state: string }>(
      `/template-registry/${templateId}/reject`,
      { method: 'POST', body: JSON.stringify({ reason }) },
    ),
};
