// ChiefChat - 1:1 PM chat with the Group's Chief Agent.
// Wired to chat_sessions.chief_run_id; reuses the standard chat substrate.
import React, { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { chiefApi } from '../../../services/chief';

export function ChiefChat({ chiefRunId }: { chiefRunId: string }) {
  const qc = useQueryClient();
  const [input, setInput] = useState('');

  const messagesQuery = useQuery({
    queryKey: ['chief-chat', chiefRunId],
    queryFn: () => chiefApi.getMessages(chiefRunId),
    refetchInterval: 5000,
  });

  const send = useMutation({
    mutationFn: (content: string) => chiefApi.postMessage(chiefRunId, content),
    onSuccess: () => {
      setInput('');
      qc.invalidateQueries({ queryKey: ['chief-chat', chiefRunId] });
    },
  });

  const messages = messagesQuery.data || [];

  return (
    <div className="border rounded flex flex-col h-full">
      <div className="px-3 py-2 border-b bg-gray-50 dark:bg-gray-800 text-sm font-medium">
        与 Chief 对话
      </div>
      <div className="flex-1 overflow-y-auto p-3 space-y-2 min-h-[300px]">
        {messagesQuery.isLoading && <div className="text-sm text-gray-500">加载中...</div>}
        {messages.map((m) => (
          <div
            key={m.id}
            className={`p-2 rounded text-sm max-w-[80%] ${
              m.from === 'chief'
                ? 'bg-blue-100 dark:bg-blue-900 ml-auto'
                : 'bg-gray-100 dark:bg-gray-700'
            }`}
          >
            <div className="text-xs text-gray-500 mb-1">{m.from}</div>
            <div>{m.content}</div>
          </div>
        ))}
      </div>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (input.trim()) send.mutate(input.trim());
        }}
        className="border-t p-2 flex gap-2"
      >
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="给 Chief 发指令..."
          className="flex-1 px-2 py-1 border rounded text-sm"
          disabled={send.isPending}
        />
        <button
          type="submit"
          disabled={!input.trim() || send.isPending}
          className="px-3 py-1 bg-blue-600 text-white rounded text-sm disabled:opacity-50"
        >
          发送
        </button>
      </form>
    </div>
  );
}
