// Projects page - main entry for Intent-Driven Orchestrator.
// User describes a goal in natural language; Orchestrator returns a Draft.
import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useMutation } from '@tanstack/react-query';
import { orchestratorApi, Draft } from '../services/orchestrator';

export function Projects() {
  const [message, setMessage] = useState('');
  const navigate = useNavigate();

  const propose = useMutation<Draft, Error, string>({
    mutationFn: (userMessage: string) => orchestratorApi.proposeDraft(userMessage),
    onSuccess: (draft) => {
      navigate(`/projects/draft/${draft.id}`);
    },
  });

  return (
    <div className="p-8 max-w-3xl mx-auto">
      <h1 className="text-2xl font-bold mb-2">新建项目</h1>
      <p className="text-sm text-gray-500 mb-6">
        描述你的目标,系统会自动组建 Agent 团队并搭建群聊。
      </p>
      <textarea
        value={message}
        onChange={(e) => setMessage(e.target.value)}
        placeholder="例如:帮我做一个出海 SaaS 获客方案"
        rows={6}
        className="w-full p-3 border rounded resize-none"
        disabled={propose.isPending}
      />
      <div className="mt-4 flex gap-2">
        <button
          onClick={() => propose.mutate(message)}
          disabled={!message.trim() || propose.isPending}
          className="px-4 py-2 bg-blue-600 text-white rounded disabled:opacity-50"
        >
          {propose.isPending ? '生成草稿中...' : '生成草稿'}
        </button>
        {propose.isError && (
          <span className="text-red-500 text-sm self-center">
            {propose.error?.message || '生成失败'}
          </span>
        )}
      </div>
    </div>
  );
}
