// Draft Preview page - user reviews/edits the LLM-generated proposal.
// Per the design: user explicitly chooses template_visibility here.
import React, { useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { orchestratorApi, Draft } from '../services/orchestrator';

export function DraftPreview() {
  const { draftId } = useParams<{ draftId: string }>();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [visibility, setVisibility] = useState<'user_private' | 'tenant_private' | 'public'>('user_private');

  const draftQuery = useQuery<Draft>({
    queryKey: ['draft', draftId],
    queryFn: () => orchestratorApi.getDraft(draftId!),
    enabled: !!draftId,
  });

  const approve = useMutation<Draft, Error, void>({
    mutationFn: () => orchestratorApi.approveDraft(draftId!),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['draft', draftId] }),
  });

  const create = useMutation({
    mutationFn: () => orchestratorApi.createDraft(draftId!),
    onSuccess: (project) => {
      qc.invalidateQueries({ queryKey: ['projects'] });
      navigate(`/projects/${project.group_id}`);
    },
  });

  if (draftQuery.isLoading) return <div className="p-8">加载中...</div>;
  if (draftQuery.isError) return <div className="p-8 text-red-500">加载失败</div>;
  const draft = draftQuery.data!;

  return (
    <div className="p-8 max-w-4xl mx-auto">
      <h1 className="text-2xl font-bold mb-2">项目草稿</h1>
      <p className="text-sm text-gray-500 mb-4">
        类别: <code>{draft.summary.intent_category}</code>
        {' · '}状态: <code>{draft.status}</code>
      </p>

      <section className="mb-6 p-4 border rounded">
        <h2 className="font-medium mb-2">群组</h2>
        <p><strong>{draft.group.name}</strong></p>
        <p className="text-sm text-gray-500">{draft.group.description}</p>
      </section>

      <section className="mb-6 p-4 border rounded">
        <h2 className="font-medium mb-2">Agent 成员 ({draft.members.length})</h2>
        <ul className="space-y-2">
          {draft.members.map((m, i) => (
            <li key={i} className="flex justify-between items-center">
              <div>
                <strong>{m.name}</strong>
                <span className="text-sm text-gray-500 ml-2">({m.role})</span>
              </div>
              {m.is_new_template && (
                <span className="text-xs bg-purple-100 text-purple-700 px-2 py-1 rounded">
                  LLM 生成
                </span>
              )}
            </li>
          ))}
        </ul>
      </section>

      {draft.okr && (
        <section className="mb-6 p-4 border rounded">
          <h2 className="font-medium mb-2">OKR</h2>
          <p><strong>{draft.okr.objective_title}</strong></p>
          <ul className="mt-2 space-y-1 text-sm">
            {draft.okr.key_results.map((kr, i) => (
              <li key={i}>• {kr.title} (目标: {kr.target_value}{kr.unit || ''})</li>
            ))}
          </ul>
        </section>
      )}

      <section className="mb-6 p-4 border rounded">
        <h2 className="font-medium mb-2">任务 ({draft.tasks.length})</h2>
        <ul className="space-y-1 text-sm">
          {draft.tasks.map((t, i) => (
            <li key={i}>• {t.title} → {t.assignee_role}</li>
          ))}
        </ul>
      </section>

      <section className="mb-6 p-4 border rounded bg-gray-50">
        <h2 className="font-medium mb-2">新模板共享范围</h2>
        <p className="text-xs text-gray-500 mb-3">
          (只影响本次草稿中由 LLM 生成的新模板;已存在的 template 不变)
        </p>
        <div className="space-y-2">
          {(['user_private', 'tenant_private', 'public'] as const).map((v) => (
            <label key={v} className="flex items-center gap-2">
              <input
                type="radio"
                name="visibility"
                value={v}
                checked={visibility === v}
                onChange={() => setVisibility(v)}
              />
              <span>
                {v === 'user_private' && '仅我自己可见 (无需审核)'}
                {v === 'tenant_private' && '本租户共享 (需 admin 审核)'}
                {v === 'public' && '全租户共享 (需 platform admin 审核)'}
              </span>
            </label>
          ))}
        </div>
      </section>

      <div className="flex gap-2">
        {draft.status === 'pending' && (
          <button
            onClick={() => approve.mutate()}
            disabled={approve.isPending}
            className="px-4 py-2 bg-blue-600 text-white rounded disabled:opacity-50"
          >
            {approve.isPending ? '审核中...' : '审核草稿'}
          </button>
        )}
        {draft.status === 'approved' && (
          <button
            onClick={() => create.mutate()}
            disabled={create.isPending}
            className="px-4 py-2 bg-green-600 text-white rounded disabled:opacity-50"
          >
            {create.isPending ? '创建中...' : '批准并创建'}
          </button>
        )}
      </div>
    </div>
  );
}
