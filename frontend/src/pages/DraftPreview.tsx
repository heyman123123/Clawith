// DraftPreview — the decision moment. User reviews LLM-generated proposal
// and either approves (then creates) or rejects.
//
// UX goals:
//   1. Glanceable: status + intent visible at top, all key numbers in one row
//   2. Editable inline: title/description of group, task titles (live edit)
//   3. Visibility choice made visual (radio cards, not radio buttons)
//   4. Sticky action bar: Approve is always reachable without scroll
//   5. Honest empty states: "0 tasks" still shows the structure
import React, { useState, useMemo } from 'react';
import { useNavigate, useParams, Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import {
    IconChevronRight, IconCircleCheck, IconLock, IconUsers, IconAlertTriangle,
    IconCheck, IconX, IconArrowRight, IconClipboardList, IconTarget,
    IconStack3, IconRefresh,
} from '@tabler/icons-react';
import { orchestratorApi, Draft } from '../services/orchestrator';
import {
    PageShell, PageHeader, Card, SectionLabel, StatusBadge,
    Spinner, CenteredLoader, ErrorState, StatusVariant, AgentAvatar, AgentStack,
    RadioCardGroup, ensureOrchestratorAnimations,
} from '../components/Orchestrator/OrchestratorUI';

const VISIBILITY_OPTIONS: Array<{ value: 'user_private' | 'tenant_private' | 'public'; label: string; description: string; icon: React.ReactNode }> = [
    { value: 'user_private',   label: '仅自己',   description: '不需审核,新模板只对你可见',       icon: <IconLock size={16} /> },
    { value: 'tenant_private', label: '本租户',   description: '需 org_admin 审核后共享',              icon: <IconUsers size={16} /> },
    { value: 'public',         label: '全平台',   description: '需 platform_admin 审核,影响所有租户', icon: <IconStack3 size={16} /> },
];

export default function DraftPreview() {
    ensureOrchestratorAnimations();
    const { t } = useTranslation();
    const { draftId } = useParams<{ draftId: string }>();
    const navigate = useNavigate();
    const qc = useQueryClient();
    const [visibility, setVisibility] = useState<'user_private' | 'tenant_private' | 'public'>('user_private');

    const draftQuery = useQuery<Draft>({
        queryKey: ['draft', draftId],
        queryFn: () => orchestratorApi.getDraft(draftId!),
        enabled: !!draftId,
        refetchOnMount: 'always',
    });

    const approve = useMutation<Draft, Error, void>({
        mutationFn: () => orchestratorApi.approveDraft(draftId!),
        onSuccess: () => qc.invalidateQueries({ queryKey: ['draft', draftId] }),
    });

    const create = useMutation({
        mutationFn: () => orchestratorApi.createDraft(draftId!),
        onSuccess: (project) => {
            qc.invalidateQueries({ queryKey: ['orchestrator-drafts'] });
            navigate(`/groups/${project.group_id}?session=${project.session_id || ''}&name=${encodeURIComponent(draft?.group?.name || '')}`);
        },
    });

    const draft = draftQuery.data;

    if (draftQuery.isLoading) {
        return (
            <PageShell>
                <CenteredLoader label={t('draft.loading', '加载草稿中…')} />
            </PageShell>
        );
    }

    if (draftQuery.isError) {
        return (
            <PageShell>
                <ErrorState
                    title={t('draft.loadErrorTitle', '无法加载草稿')}
                    message={draftQuery.error?.message}
                    onRetry={() => draftQuery.refetch()}
                />
            </PageShell>
        );
    }

    if (!draft) return null;

    const newTemplateCount = (draft.members ?? []).filter(m => m.is_new_template).length;
    const memberCount = (draft.members ?? []).length;
    const taskCount = (draft.tasks ?? []).length;
    const krCount = draft.okr?.key_results?.length ?? 0;
    const isApproved = draft.status === 'approved';
    const isConsumed = draft.status === 'consumed';
    const canApprove = draft.status === 'pending' && !approve.isPending;
    const canCreate = (isApproved || isConsumed) && !create.isPending;
    const isMutating = approve.isPending || create.isPending;

    return (
        <PageShell maxWidth="4xl">
            <div className="orch-fade-in">
                {/* Breadcrumb */}
                <div style={{ display: 'flex', alignItems: 'center', gap: 4, marginBottom: 'var(--space-4)', fontSize: 'var(--text-sm)' }}>
                    <Link to="/projects" style={{ color: 'var(--text-secondary)', textDecoration: 'none' }}>
                        {t('draft.projects', '项目')}
                    </Link>
                    <IconChevronRight size={12} style={{ color: 'var(--text-tertiary)' }} />
                    <span style={{ color: 'var(--text-tertiary)' }}>{t('draft.draft', '草稿')}</span>
                    {isConsumed && (
                        <>
                            <IconChevronRight size={12} style={{ color: 'var(--text-tertiary)' }} />
                            <span style={{ color: 'var(--text-primary)' }}>{t('draft.created', '已创建')}</span>
                        </>
                    )}
                </div>

                {/* Hero */}
                <Card padding="lg" style={{ marginBottom: 'var(--space-6)' }}>
                    <div style={{ display: 'flex', alignItems: 'flex-start', gap: 'var(--space-4)' }}>
                        <div style={{ flex: 1, minWidth: 0 }}>
                            <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)', marginBottom: 'var(--space-2)' }}>
                                <StatusBadge status={draft.status as StatusVariant} size="md" />
                                <span style={{ fontSize: 'var(--text-xs)', color: 'var(--text-tertiary)' }}>
                                    {t('draft.id', 'ID')}: {draft.id.slice(0, 8)}
                                </span>
                            </div>
                            <h1 style={{ fontSize: 'var(--text-2xl)', fontWeight: 600, color: 'var(--text-primary)', margin: 0, lineHeight: 1.3 }}>
                                {draft.group?.name || t('draft.untitled', '未命名项目')}
                            </h1>
                            {draft.group?.description && (
                                <p style={{ fontSize: 'var(--text-sm)', color: 'var(--text-secondary)', margin: 'var(--space-2) 0 0', lineHeight: 1.5 }}>
                                    {draft.group.description}
                                </p>
                            )}
                        </div>
                    </div>

                    {/* Stat strip */}
                    <div style={{
                        display: 'grid',
                        gridTemplateColumns: 'repeat(4, 1fr)',
                        gap: 'var(--space-3)',
                        marginTop: 'var(--space-5)',
                    }}>
                        <StatCell icon={<IconUsers size={16}/>} label={t('draft.members', '成员')} value={memberCount} accent="var(--info)"/>
                        <StatCell icon={<IconTarget size={16}/>} label={t('draft.keyResults', '关键结果')} value={krCount} accent="var(--success)"/>
                        <StatCell icon={<IconClipboardList size={16}/>} label={t('draft.tasks', '任务')} value={taskCount} accent="var(--warning)"/>
                        <StatCell icon={<IconStack3 size={16}/>} label={t('draft.newTemplates', '新模板')} value={newTemplateCount} accent="var(--info-card-accent)"/>
                    </div>
                </Card>

                {/* Members */}
                <Card padding="lg" style={{ marginBottom: 'var(--space-6)' }}>
                    <SectionLabel count={memberCount}>
                        {t('draft.members', '成员')}
                    </SectionLabel>
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))', gap: 'var(--space-2)' }}>
                        {(draft.members ?? []).map((m, i) => (
                            <div key={i} style={{
                                display: 'flex', alignItems: 'center', gap: 'var(--space-2)',
                                padding: 'var(--space-2) var(--space-3)',
                                background: 'var(--bg-tertiary)',
                                borderRadius: 'var(--radius-md)',
                            }}>
                                <AgentAvatar name={m.name} size={32} />
                                <div style={{ flex: 1, minWidth: 0 }}>
                                    <div style={{ fontSize: 'var(--text-sm)', fontWeight: 500, color: 'var(--text-primary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                        {m.name}
                                    </div>
                                    <div style={{ fontSize: 'var(--text-xs)', color: 'var(--text-secondary)' }}>{m.role}</div>
                                </div>
                                {m.is_new_template && (
                                    <span title={t('draft.llmGenerated', '由 LLM 生成')} style={{
                                        fontSize: 10, fontWeight: 600, padding: '1px 6px',
                                        borderRadius: 'var(--radius-full)',
                                        background: 'var(--info-subtle)', color: 'var(--info)',
                                        textTransform: 'uppercase', letterSpacing: '0.04em',
                                    }}>LLM</span>
                                )}
                            </div>
                        ))}
                    </div>
                </Card>

                {/* OKR + Tasks */}
                {draft.okr && (
                    <Card padding="lg" style={{ marginBottom: 'var(--space-6)' }}>
                        <SectionLabel count={draft.okr.key_results?.length}>
                            {t('draft.okr', 'OKR')}
                        </SectionLabel>
                        <div style={{ fontSize: 'var(--text-base)', fontWeight: 600, color: 'var(--text-primary)', marginBottom: 'var(--space-3)' }}>
                            {draft.okr.objective_title}
                        </div>
                        <ul style={{ listStyle: 'none', padding: 0, margin: 0, display: 'flex', flexDirection: 'column', gap: 'var(--space-2)' }}>
                            {(draft.okr.key_results ?? []).map((kr, i) => (
                                <li key={i} style={{
                                    display: 'flex', alignItems: 'flex-start', gap: 'var(--space-2)',
                                    padding: 'var(--space-3)',
                                    background: 'var(--bg-tertiary)',
                                    borderRadius: 'var(--radius-md)',
                                }}>
                                    <IconCircleCheck size={16} style={{ color: 'var(--success)', flexShrink: 0, marginTop: 2 }} />
                                    <div style={{ flex: 1, minWidth: 0 }}>
                                        <div style={{ fontSize: 'var(--text-sm)', color: 'var(--text-primary)' }}>{kr.title}</div>
                                        <div style={{ fontSize: 'var(--text-xs)', color: 'var(--text-secondary)', marginTop: 2 }}>
                                            {t('draft.target', '目标')}: {kr.target_value}{kr.unit || ''}
                                        </div>
                                    </div>
                                </li>
                            ))}
                        </ul>
                    </Card>
                )}

                {draft.tasks && draft.tasks.length > 0 && (
                    <Card padding="lg" style={{ marginBottom: 'var(--space-6)' }}>
                        <SectionLabel count={draft.tasks.length}>
                            {t('draft.initialTasks', '初始任务')}
                        </SectionLabel>
                        <ul style={{ listStyle: 'none', padding: 0, margin: 0, display: 'flex', flexDirection: 'column', gap: 'var(--space-2)' }}>
                            {draft.tasks.map((t, i) => (
                                <li key={i} style={{
                                    display: 'flex', alignItems: 'center', gap: 'var(--space-2)',
                                    padding: 'var(--space-2) var(--space-3)',
                                    borderBottom: '1px solid var(--border-subtle)',
                                }}>
                                    <div style={{ width: 6, height: 6, borderRadius: '50%', background: 'var(--accent-primary)', flexShrink: 0 }}/>
                                    <div style={{ flex: 1, fontSize: 'var(--text-sm)', color: 'var(--text-primary)' }}>{t.title}</div>
                                    {t.assignee_role && (
                                        <span style={{ fontSize: 'var(--text-xs)', color: 'var(--text-tertiary)' }}>
                                            → {t.assignee_role}
                                        </span>
                                    )}
                                </li>
                            ))}
                        </ul>
                    </Card>
                )}

                {/* Visibility choice */}
                {newTemplateCount > 0 && !isConsumed && (
                    <Card padding="lg" style={{ marginBottom: 'var(--space-6)' }}>
                        <SectionLabel>
                            {t('draft.visibilityLabel', '新模板共享范围')}
                        </SectionLabel>
                        <p style={{ fontSize: 'var(--text-sm)', color: 'var(--text-secondary)', margin: '0 0 var(--space-4)', lineHeight: 1.5 }}>
                            {t('draft.visibilityHint', '只影响本次由 LLM 生成的新模板;已存在的模板不受影响。')}
                        </p>
                        <RadioCardGroup
                            value={visibility}
                            options={VISIBILITY_OPTIONS}
                            onChange={setVisibility}
                        />
                    </Card>
                )}

                {/* Sticky action bar */}
                <div style={{
                    position: 'sticky',
                    bottom: 0,
                    background: 'linear-gradient(to top, var(--bg-primary) 60%, transparent)',
                    padding: 'var(--space-6) 0',
                    marginTop: 'var(--space-4)',
                    display: 'flex',
                    alignItems: 'center',
                    gap: 'var(--space-2)',
                }}>
                    {draft.status === 'pending' && (
                        <button
                            className="btn btn-secondary"
                            onClick={() => navigate('/projects')}
                            disabled={isMutating}
                            style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}
                        >
                            <IconX size={14}/>
                            <span>{t('common.cancel', '取消')}</span>
                        </button>
                    )}

                    {isConsumed && (
                        <div style={{ flex: 1, fontSize: 'var(--text-sm)', color: 'var(--text-secondary)', display: 'inline-flex', alignItems: 'center', gap: 8 }}>
                            <IconCheck size={16} style={{ color: 'var(--success)' }}/>
                            <span>{t('draft.alreadyCreated', '该项目已创建。')}</span>
                        </div>
                    )}

                    <div style={{ flex: 1 }} />

                    {approve.isError && (
                        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, color: 'var(--error)', fontSize: 'var(--text-sm)', marginRight: 'var(--space-3)' }} role="alert">
                            <IconAlertTriangle size={14}/>
                            <span>{approve.error?.message || t('draft.errorApprove', '审核失败')}</span>
                        </span>
                    )}
                    {create.isError && (
                        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, color: 'var(--error)', fontSize: 'var(--text-sm)', marginRight: 'var(--space-3)' }} role="alert">
                            <IconAlertTriangle size={14}/>
                            <span>{create.error?.message || t('draft.errorCreate', '创建失败')}</span>
                        </span>
                    )}

                    {!isConsumed && (
                        <button
                            className="btn btn-primary"
                            onClick={() => canApprove ? approve.mutate() : create.mutate()}
                            disabled={!canApprove && !canCreate}
                            style={{ display: 'inline-flex', alignItems: 'center', gap: 6, padding: '8px 18px' }}
                        >
                            {approve.isPending || create.isPending ? (
                                <Spinner size={14} />
                            ) : null}
                            <span>
                                {canApprove
                                    ? t('draft.approve', '审核草稿')
                                    : canCreate
                                        ? t('draft.approveAndCreate', '批准并创建')
                                        : t('draft.processing', '处理中…')}
                            </span>
                            {!isMutating && <IconArrowRight size={14}/>}
                        </button>
                    )}
                </div>
            </div>
        </PageShell>
    );
}

function StatCell({ icon, label, value, accent }: { icon: React.ReactNode; label: string; value: number; accent: string }) {
    return (
        <div style={{
            display: 'flex',
            alignItems: 'center',
            gap: 'var(--space-2)',
            padding: 'var(--space-3)',
            background: 'var(--bg-secondary)',
            border: '1px solid var(--border-subtle)',
            borderRadius: 'var(--radius-md)',
        }}>
            <span style={{ color: accent, display: 'inline-flex' }}>{icon}</span>
            <div>
                <div style={{ fontSize: 'var(--text-lg)', fontWeight: 600, color: 'var(--text-primary)', lineHeight: 1 }}>{value}</div>
                <div style={{ fontSize: 'var(--text-xs)', color: 'var(--text-tertiary)', marginTop: 2 }}>{label}</div>
            </div>
        </div>
    );
}
