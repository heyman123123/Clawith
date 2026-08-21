// Kanban Task Board with HTML5 drag-and-drop.
// Columns: backlog / in_progress / blocked / review / done.
// Each move sends an optimistic request with expected_version (optimistic lock).
//
// UX:
//   - Sticky column header with add-card button
//   - Cards show priority + assignee + artifact count
//   - Drop targets get a visual highlight
//   - Optimistic update with rollback on conflict
//   - Inline error toast for failed moves
//   - Empty columns prompt "drop a card here"
import React, { useState, DragEvent, useRef, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { IconPlus, IconUser, IconFile, IconAlertTriangle, IconGripVertical, IconCircleCheck } from '@tabler/icons-react';
import { taskBoardApi, TaskCard, TaskColumn } from '../../../services/taskBoard';
import { useAuthStore } from '../../../stores';
import { useToast } from '../../../components/Toast/ToastProvider';
import {
    Card, SectionLabel, Spinner, EmptyState, AgentAvatar,
    CenteredLoader, ErrorState, ensureOrchestratorAnimations,
} from '../../../components/Orchestrator/OrchestratorUI';

const COLUMNS: TaskColumn[] = ['backlog', 'in_progress', 'blocked', 'review', 'done'];

const COLUMN_META: Record<TaskColumn, { label: string; hint: string; dot: string }> = {
    backlog:     { label: '待开始', hint: '尚未开始',          dot: 'var(--text-tertiary)' },
    in_progress: { label: '进行中', hint: '正在被 Agent 执行', dot: 'var(--info)' },
    blocked:     { label: '阻塞',   hint: '需要人工介入',       dot: 'var(--warning)' },
    review:      { label: '审核',   hint: 'Chief 等待 review',  dot: 'var(--accent-primary)' },
    done:        { label: '完成',   hint: '已交付',             dot: 'var(--success)' },
};

export function TaskBoard({ tenantId, groupId }: { tenantId: string; groupId: string }) {
    ensureOrchestratorAnimations();
    const { t } = useTranslation();
    const qc = useQueryClient();
    const toast = useToast();
    const [draggedId, setDraggedId] = useState<string | null>(null);
    const [dragOverCol, setDragOverCol] = useState<TaskColumn | null>(null);
    const userId = useAuthStore((s) => s.user?.id) ?? '';
    const userName = useAuthStore((s) => s.user?.display_name) || t('taskBoard.you', '你');

    const cardsQuery = useQuery<TaskCard[]>({
        queryKey: ['task-board', groupId],
        queryFn: () => taskBoardApi.listCardsByGroup(tenantId, groupId),
        enabled: !!tenantId && !!groupId,
        refetchInterval: 30_000,
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
        onSuccess: (_d, vars) => {
            qc.invalidateQueries({ queryKey: ['task-board', groupId] });
            qc.setQueryData<TaskCard[]>(['task-board', groupId], (old) => {
                if (!old) return old;
                return old.map(c => c.id === vars.cardId ? { ...c, column: vars.toColumn, version: c.version + 1 } : c);
            });
        },
        onError: (err: any, vars) => {
            // Rollback optimistic + toast
            qc.invalidateQueries({ queryKey: ['task-board', groupId] });
            toast?.error?.(err?.message || t('taskBoard.versionConflict', '卡片已被其他人修改,请刷新'), { details: t('taskBoard.moveFailedTitle', '移动失败') });
        },
    });

    const grouped: Record<TaskColumn, TaskCard[]> = COLUMNS.reduce(
        (acc, col) => ({ ...acc, [col]: (cardsQuery.data || []).filter(c => c.column === col) }),
        {} as Record<TaskColumn, TaskCard[]>,
    );

    function onDragStart(e: DragEvent, cardId: string) {
        setDraggedId(cardId);
        e.dataTransfer.setData('text/card-id', cardId);
        e.dataTransfer.effectAllowed = 'move';
    }

    function onDragOver(e: DragEvent, col: TaskColumn) {
        e.preventDefault();
        e.dataTransfer.dropEffect = 'move';
        if (dragOverCol !== col) setDragOverCol(col);
    }

    function onDragLeave(col: TaskColumn) {
        if (dragOverCol === col) setDragOverCol(null);
    }

    function onDrop(e: DragEvent, toCol: TaskColumn) {
        e.preventDefault();
        const cardId = e.dataTransfer.getData('text/card-id') || draggedId;
        setDraggedId(null);
        setDragOverCol(null);
        if (!cardId) return;
        const card = (cardsQuery.data || []).find(c => c.id === cardId);
        if (card && card.column !== toCol) {
            move.mutate({ cardId, toColumn: toCol, expectedVersion: card.version });
        }
    }

    if (cardsQuery.isLoading) {
        return <CenteredLoader label={t('taskBoard.loading', '加载看板…')}/>;
    }
    if (cardsQuery.isError) {
        return <ErrorState
            title={t('taskBoard.loadErrorTitle', '加载失败')}
            message={(cardsQuery.error as any)?.message}
            onRetry={() => cardsQuery.refetch()}
        />;
    }

    return (
        <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
            <div style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                padding: 'var(--space-4) var(--space-5)',
                borderBottom: '1px solid var(--border-subtle)',
            }}>
                <div>
                    <div style={{ fontSize: 'var(--text-sm)', fontWeight: 600, color: 'var(--text-primary)' }}>
                        {t('taskBoard.title', '任务看板')}
                    </div>
                    <div style={{ fontSize: 'var(--text-xs)', color: 'var(--text-tertiary)', marginTop: 2 }}>
                        {t('taskBoard.totalTasks', '共 {{n}} 个任务', { n: cardsQuery.data?.length || 0 })}
                    </div>
                </div>
                <button
                    className="btn btn-secondary"
                    onClick={() => cardsQuery.refetch()}
                    disabled={cardsQuery.isFetching}
                    style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}
                >
                    {cardsQuery.isFetching ? <Spinner size={12}/> : null}
                    <span>{t('common.refresh', '刷新')}</span>
                </button>
            </div>

            <div style={{
                display: 'grid',
                gridTemplateColumns: 'repeat(5, 1fr)',
                gap: 'var(--space-3)',
                padding: 'var(--space-4) var(--space-5)',
                flex: 1,
                overflowX: 'auto',
                minHeight: 0,
            }}>
                {COLUMNS.map(col => {
                    const cards = grouped[col];
                    const meta = COLUMN_META[col];
                    const isOver = dragOverCol === col;
                    return (
                        <div
                            key={col}
                            data-testid={`column-${col}`}
                            onDragOver={(e) => onDragOver(e, col)}
                            onDragLeave={() => onDragLeave(col)}
                            onDrop={(e) => onDrop(e, col)}
                            style={{
                                display: 'flex',
                                flexDirection: 'column',
                                gap: 'var(--space-2)',
                                background: isOver ? 'var(--accent-subtle)' : 'var(--bg-secondary)',
                                border: `1px ${isOver ? 'solid' : 'dashed'} ${isOver ? 'var(--accent-primary)' : 'var(--border-subtle)'}`,
                                borderRadius: 'var(--radius-lg)',
                                padding: 'var(--space-3)',
                                minHeight: 0,
                                transition: 'all var(--transition-fast)',
                            }}
                        >
                            {/* Column header */}
                            <div style={{
                                display: 'flex',
                                alignItems: 'center',
                                gap: 'var(--space-2)',
                                marginBottom: 'var(--space-2)',
                            }}>
                                <span style={{ width: 8, height: 8, borderRadius: '50%', background: meta.dot, flexShrink: 0 }}/>
                                <span style={{ fontSize: 'var(--text-sm)', fontWeight: 600, color: 'var(--text-primary)' }}>{meta.label}</span>
                                <span style={{
                                    fontSize: 'var(--text-xs)',
                                    color: 'var(--text-tertiary)',
                                    background: 'var(--bg-tertiary)',
                                    padding: '1px 7px',
                                    borderRadius: 'var(--radius-full)',
                                    fontWeight: 500,
                                }}>{cards.length}</span>
                                <div style={{ flex: 1 }} />
                                <button
                                    title={t('taskBoard.addCard', '添加卡片')}
                                    style={{
                                        background: 'transparent',
                                        border: 'none',
                                        color: 'var(--text-tertiary)',
                                        cursor: 'pointer',
                                        padding: 4,
                                        borderRadius: 'var(--radius-sm)',
                                        display: 'inline-flex',
                                    }}
                                    onClick={() => toast?.info?.(t('taskBoard.addCardHint', '通过 Orchestrator 草稿创建任务;手动添加任务面板即将推出'))}
                                >
                                    <IconPlus size={14}/>
                                </button>
                            </div>

                            {/* Cards or empty state */}
                            {cards.length === 0 ? (
                                <div style={{
                                    flex: 1,
                                    minHeight: 80,
                                    display: 'flex',
                                    alignItems: 'center',
                                    justifyContent: 'center',
                                    color: 'var(--text-tertiary)',
                                    fontSize: 'var(--text-xs)',
                                    border: '1px dashed var(--border-subtle)',
                                    borderRadius: 'var(--radius-md)',
                                    padding: 'var(--space-3)',
                                    textAlign: 'center',
                                }}>
                                    {col === 'done'
                                        ? t('taskBoard.emptyDone', '完成后移到这里')
                                        : t('taskBoard.emptyCol', '拖动卡片到这里')}
                                </div>
                            ) : (
                                <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-2)' }}>
                                    {cards.map(card => (
                                        <Card key={card.id} padding="sm" style={{ cursor: 'grab' }}>
                                            <div style={{ display: 'flex', alignItems: 'flex-start', gap: 6 }}>
                                                <IconGripVertical
                                                    size={14}
                                                    style={{ color: 'var(--text-tertiary)', marginTop: 2, cursor: 'grab', flexShrink: 0 }}
                                                />
                                                <div
                                                    draggable
                                                    onDragStart={(e) => onDragStart(e, card.id)}
                                                    style={{ flex: 1, minWidth: 0 }}
                                                >
                                                    <div style={{
                                                        fontSize: 'var(--text-sm)',
                                                        color: 'var(--text-primary)',
                                                        fontWeight: 500,
                                                        lineHeight: 1.4,
                                                    }}>{card.title}</div>
                                                    <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)', marginTop: 6 }}>
                                                        {card.assignee_agent_id && (
                                                            <AgentAvatar
                                                                name={card.assignee_agent_name || card.assignee_agent_id.slice(0, 8)}
                                                                size={16}
                                                            />
                                                        )}
                                                        {(card.artifact_paths?.length ?? 0) > 0 && (
                                                            <span style={{
                                                                display: 'inline-flex', alignItems: 'center', gap: 4,
                                                                fontSize: 10, color: 'var(--text-tertiary)',
                                                            }}>
                                                                <IconFile size={10}/> {card.artifact_paths!.length}
                                                            </span>
                                                        )}
                                                    </div>
                                                </div>
                                            </div>
                                        </Card>
                                    ))}
                                </div>
                            )}
                        </div>
                    );
                })}
            </div>
        </div>
    );
}
