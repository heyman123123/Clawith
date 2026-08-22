// Projects — entry point for the Intent-Driven Orchestrator.
//
// UX goals:
//   1. Zero-friction: user sees an inviting textarea, not a config form.
//   2. Inspiration on demand: 6 example goals that one-click fill the
//      textarea, so first-time users can try without thinking.
//   3. Continuity: recent drafts surface as "Continue from last" cards
//      with status badges, so returning users land directly in review.
import React, { useState, useEffect, useMemo, useRef } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import { useMutation, useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { IconRocket, IconArrowRight, IconAlertTriangle, IconSparkles } from '@tabler/icons-react';
import { orchestratorApi, Draft } from '../services/orchestrator';
import { useAuthStore } from '../stores';
import {
    PageShell, PageHeader, Card, SectionLabel, StatusBadge,
    Spinner, EmptyState, ErrorState, AgentStack, ensureOrchestratorAnimations,
} from '../components/Orchestrator/OrchestratorUI';

const EXAMPLES = [
    { key: 'marketing', icon: '📈', title: '出海 SaaS 获客方案', desc: '增长策略 + 内容 + 渠道' },
    { key: 'research',  icon: '🔍', title: '行业研究报告',         desc: '竞品 + 市场 + 趋势' },
    { key: 'product',   icon: '🛠️', title: '产品 MVP 路线图',      desc: '功能 + 优先级 + 风险' },
    { key: 'engineering', icon: '💻', title: '后端架构评审',         desc: '可扩展性 + 性能 + 安全' },
    { key: 'content',   icon: '✍️', title: '内容运营 30 天计划',    desc: '选题 + 排期 + KPI' },
    { key: 'ops',       icon: '⚙️', title: '运维事件响应 SOP',     desc: '分诊 + 升级 + 复盘' },
];

export default function Projects() {
    ensureOrchestratorAnimations();
    const { t } = useTranslation();
    const navigate = useNavigate();
    const userId = useAuthStore((s) => s.user?.id) ?? '';
    const tenantId = useAuthStore((s) => s.user?.tenant_id) ?? '';
    const [message, setMessage] = useState('');
    const textareaRef = useRef<HTMLTextAreaElement>(null);

    // Auto-resize textarea up to ~10 rows
    useEffect(() => {
        const ta = textareaRef.current;
        if (!ta) return;
        ta.style.height = 'auto';
        ta.style.height = Math.min(ta.scrollHeight, 240) + 'px';
    }, [message]);

    // Recent drafts (for "continue" cards)
    const draftsQuery = useQuery<Draft[]>({
        queryKey: ['orchestrator-drafts', userId],
        queryFn: () => orchestratorApi.listDrafts(),
        enabled: !!userId,
        staleTime: 30_000,
    });

    const propose = useMutation<Draft, Error, string>({
        mutationFn: (msg: string) => orchestratorApi.proposeDraft(msg),
        onSuccess: (draft) => {
            navigate(`/projects/draft/${draft.id}`);
        },
    });

    const recentDrafts = useMemo(() => {
        const all = draftsQuery.data ?? [];
        // Sort: not-consumed first, then by created_at desc
        return [...all]
            .sort((a, b) => {
                if (a.status === 'consumed' && b.status !== 'consumed') return 1;
                if (b.status === 'consumed' && a.status !== 'consumed') return -1;
                return new Date(b.created_at).getTime() - new Date(a.created_at).getTime();
            })
            .slice(0, 6);
    }, [draftsQuery.data]);

    const draftTitle = (d: Draft) =>
        d.group?.name?.trim() ||
        d.user_message?.split(/[。\n]/)[0]?.trim().slice(0, 60) ||
        t('projects.untitledDraft', '未命名草稿');

    return (
        <PageShell maxWidth="5xl">
            <div className="orch-fade-in">
                <PageHeader
                    eyebrow={t('projects.eyebrow', 'AI Project Orchestrator')}
                    title={t('projects.title', '把目标交给 AI 团队')}
                    description={t('projects.subtitle', '用一句话描述你的目标,Clawith 自动组建 Agent 团队、搭建群聊、拆解 OKR 与任务,由 Chief Agent 协调执行。')}
                    actions={
                        <Link to="/projects/draft/new" style={{ display: 'none' /* placeholder for future */ }}>
                            <IconRocket size={16} />
                        </Link>
                    }
                />

                {/* Main composer card */}
                <Card padding="lg" style={{ marginBottom: 'var(--space-8)' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)', marginBottom: 'var(--space-3)' }}>
                        <IconSparkles size={18} style={{ color: 'var(--accent-primary)' }}/>
                        <span style={{ fontSize: 'var(--text-sm)', fontWeight: 600, color: 'var(--text-primary)' }}>
                            {t('projects.composerLabel', '描述你的目标')}
                        </span>
                        <span style={{ fontSize: 'var(--text-xs)', color: 'var(--text-tertiary)', marginLeft: 'auto' }}>
                            {message.length} / 2000
                        </span>
                    </div>

                    <textarea
                        ref={textareaRef}
                        value={message}
                        onChange={(e) => setMessage(e.target.value.slice(0, 2000))}
                        onKeyDown={(e) => {
                            if (e.key === 'Enter' && (e.metaKey || e.ctrlKey) && message.trim() && !propose.isPending) {
                                e.preventDefault();
                                propose.mutate(message.trim());
                            }
                        }}
                        placeholder={t('projects.composerPlaceholder', '例如:帮我做一个出海 SaaS 获客方案')}
                        rows={4}
                        className="orch-textarea"
                        disabled={propose.isPending}
                        style={{
                            width: '100%',
                            padding: 'var(--space-3) var(--space-4)',
                            fontSize: 'var(--text-base)',
                            color: 'var(--text-primary)',
                            background: 'var(--bg-secondary)',
                            border: '1px solid var(--border-default)',
                            borderRadius: 'var(--radius-md)',
                            outline: 'none',
                            resize: 'none',
                            fontFamily: 'inherit',
                            lineHeight: 1.6,
                            transition: 'border-color var(--transition-fast)',
                        }}
                    />

                    <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)', marginTop: 'var(--space-4)' }}>
                        <button
                            className="btn btn-primary"
                            onClick={() => propose.mutate(message.trim())}
                            disabled={!message.trim() || propose.isPending}
                            style={{ display: 'inline-flex', alignItems: 'center', gap: 'var(--space-2)', padding: '8px 18px' }}
                        >
                            {propose.isPending ? (
                                <>
                                    <Spinner size={14} />
                                    <span>{t('projects.generating', '生成草稿中…')}</span>
                                </>
                            ) : (
                                <>
                                    <span>{t('projects.generate', '生成草稿')}</span>
                                    <IconArrowRight size={14} />
                                </>
                            )}
                        </button>
                        <span style={{ fontSize: 'var(--text-xs)', color: 'var(--text-tertiary)' }}>
                            {t('projects.cmdHint', '⌘ + Enter 快速提交')}
                        </span>
                        <div style={{ flex: 1 }} />
                        {propose.isError && (
                            <div style={{
                                display: 'inline-flex', alignItems: 'center', gap: 6,
                                color: 'var(--error)', fontSize: 'var(--text-sm)',
                            }} role="alert">
                                <IconAlertTriangle size={14} />
                                <span>{propose.error?.message || t('projects.errorGenerate', '生成失败')}</span>
                            </div>
                        )}
                    </div>
                </Card>

                {/* Recent drafts */}
                {recentDrafts.length > 0 && (
                    <div style={{ marginBottom: 'var(--space-8)' }}>
                        <SectionLabel count={recentDrafts.length}>
                            {t('projects.recentDrafts', '最近的草稿')}
                        </SectionLabel>
                        <div style={{
                            display: 'grid',
                            gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))',
                            gap: 'var(--space-3)',
                        }}>
                            {recentDrafts.map((d) => (
                                <Link
                                    key={d.id}
                                    to={d.status === 'consumed' && (d as any).error_detail?.group_id
                                        ? `/groups/${(d as any).error_detail.group_id}?name=${encodeURIComponent(d.group?.name || '')}`
                                        : `/projects/draft/${d.id}`}
                                    style={{ textDecoration: 'none' }}
                                >
                                    <Card
                                        padding="md"
                                        style={{
                                            cursor: 'pointer',
                                            transition: 'all var(--transition-fast)',
                                        }}
                                    >
                                        <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)', marginBottom: 'var(--space-2)' }}>
                                            <StatusBadge status={d.status} />
                                            <span style={{ fontSize: 'var(--text-xs)', color: 'var(--text-tertiary)', marginLeft: 'auto' }}>
                                                {new Date(d.created_at).toLocaleDateString()}
                                            </span>
                                        </div>
                                        <div style={{
                                            fontSize: 'var(--text-sm)',
                                            fontWeight: 500,
                                            color: 'var(--text-primary)',
                                            marginBottom: 'var(--space-2)',
                                            overflow: 'hidden',
                                            textOverflow: 'ellipsis',
                                            whiteSpace: 'nowrap',
                                        }}>{draftTitle(d)}</div>
                                        <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)' }}>
                                            <AgentStack names={(d.members ?? []).slice(0, 4).map(m => m.name)} size={20} />
                                            <span style={{ fontSize: 'var(--text-xs)', color: 'var(--text-tertiary)' }}>
                                                {(d.members ?? []).length} {t('projects.agents', 'agents')} · {(d.tasks ?? []).length} {t('projects.tasks', 'tasks')}
                                            </span>
                                        </div>
                                    </Card>
                                </Link>
                            ))}
                        </div>
                    </div>
                )}

                {/* Examples */}
                <div>
                    <SectionLabel>{t('projects.examples', '或者从示例开始')}</SectionLabel>
                    <div style={{
                        display: 'grid',
                        gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))',
                        gap: 'var(--space-3)',
                    }}>
                        {EXAMPLES.map((ex) => (
                            <button
                                key={ex.key}
                                onClick={() => {
                                    setMessage(ex.title);
                                    textareaRef.current?.focus();
                                }}
                                className="orch-example-card"
                                style={{
                                    display: 'flex',
                                    alignItems: 'flex-start',
                                    gap: 'var(--space-3)',
                                    padding: 'var(--space-4)',
                                    background: 'var(--bg-elevated)',
                                    border: '1px solid var(--border-default)',
                                    borderRadius: 'var(--radius-lg)',
                                    textAlign: 'left',
                                    cursor: 'pointer',
                                    color: 'inherit',
                                    transition: 'all var(--transition-fast)',
                                }}
                            >
                                <span style={{
                                    fontSize: 22,
                                    width: 36, height: 36,
                                    display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                                    background: 'var(--bg-tertiary)',
                                    borderRadius: 'var(--radius-md)',
                                    flexShrink: 0,
                                }}>{ex.icon}</span>
                                <div style={{ flex: 1, minWidth: 0 }}>
                                    <div style={{ fontSize: 'var(--text-sm)', fontWeight: 600, color: 'var(--text-primary)', marginBottom: 2 }}>
                                        {ex.title}
                                    </div>
                                    <div style={{ fontSize: 'var(--text-xs)', color: 'var(--text-secondary)' }}>
                                        {ex.desc}
                                    </div>
                                </div>
                                <IconArrowRight size={14} style={{ color: 'var(--text-tertiary)', flexShrink: 0, marginTop: 2 }} />
                            </button>
                        ))}
                    </div>
                </div>
            </div>
        </PageShell>
    );
}
