// Kanban Task Board component - drag-and-drop across columns.
// Columns: backlog / in_progress / blocked / review / done.
// Each move sends an optimistic request with expected_version (optimistic lock).
import React, { useState, DragEvent } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { taskBoardApi, TaskCard, TaskColumn } from '../../../services/taskBoard';
import { useAuthStore } from '../../../stores';

const COLUMNS: TaskColumn[] = ['backlog', 'in_progress', 'blocked', 'review', 'done'];
const COLUMN_LABEL: Record<TaskColumn, string> = {
  backlog: 'Backlog',
  in_progress: '进行中',
  blocked: '阻塞',
  review: '审核',
  done: '完成',
};

export function TaskBoard({ tenantId, groupId }: { tenantId: string; groupId: string }) {
  const qc = useQueryClient();
  const [draggedId, setDraggedId] = useState<string | null>(null);
  const userId = useAuthStore((s) => s.user?.id) ?? '';

  const cardsQuery = useQuery<TaskCard[]>({
    queryKey: ['task-board', groupId],
    queryFn: () => taskBoardApi.listCardsByGroup(tenantId, groupId),
  });

  const move = useMutation({
    mutationFn: ({ cardId, toColumn, expectedVersion }: { cardId: string; toColumn: TaskColumn; expectedVersion: number }) =>
      taskBoardApi.moveCard(cardId, {
        tenant_id: tenantId,
        actor_id: userId,
        actor_type: 'user',
        to_column: toColumn,
        expected_version: expectedVersion,
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['task-board', groupId] }),
  });

  const grouped: Record<TaskColumn, TaskCard[]> = COLUMNS.reduce(
    (acc, col) => ({ ...acc, [col]: (cardsQuery.data || []).filter((c) => c.column === col) }),
    {} as Record<TaskColumn, TaskCard[]>,
  );

  function onDragStart(e: DragEvent, cardId: string) {
    setDraggedId(cardId);
    e.dataTransfer.setData('text/card-id', cardId);
    e.dataTransfer.effectAllowed = 'move';
  }

  function onDrop(e: DragEvent, toCol: TaskColumn) {
    e.preventDefault();
    const cardId = e.dataTransfer.getData('text/card-id') || draggedId;
    if (!cardId) return;
    const card = (cardsQuery.data || []).find((c) => c.id === cardId);
    if (card && card.column !== toCol) {
      move.mutate({ cardId, toColumn: toCol, expectedVersion: card.version });
    }
    setDraggedId(null);
  }

  if (cardsQuery.isLoading) return <div>加载中...</div>;
  if (cardsQuery.isError) return <div className="text-red-500">加载失败</div>;

  return (
    <div className="grid grid-cols-5 gap-3 p-4 overflow-x-auto">
      {COLUMNS.map((col) => (
        <div
          key={col}
          onDragOver={(e) => e.preventDefault()}
          onDrop={(e) => onDrop(e, col)}
          className="bg-gray-50 dark:bg-gray-800 p-3 rounded min-h-[300px]"
          data-testid={`column-${col}`}
        >
          <h3 className="text-sm font-medium mb-3 flex justify-between items-center">
            {COLUMN_LABEL[col]}
            <span className="text-xs text-gray-500">{grouped[col].length}</span>
          </h3>
          <div className="space-y-2">
            {grouped[col].map((card) => (
              <div
                key={card.id}
                draggable
                onDragStart={(e) => onDragStart(e, card.id)}
                className="bg-white dark:bg-gray-700 p-2 rounded shadow-sm cursor-move text-sm"
                data-testid={`card-${card.id}`}
              >
                {card.title}
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}
