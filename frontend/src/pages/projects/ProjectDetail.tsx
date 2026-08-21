// ProjectDetail — the working surface for a single Group's orchestrator project.
//
// Layout:
//   ┌────────────────────────────────────────────────────────────┐
//   │ Header (group name + status + member stack + actions)     │
//   ├──────────────────────────────────────────┬─────────────────┤
//   │                                          │                 │
//   │   TaskBoard (Kanban)                     │  ChiefChat      │
//   │   flex-1                                  │  360px          │
//   │                                          │                 │
//   └──────────────────────────────────────────┴─────────────────┘
import React, { useMemo } from 'react';
import { useParams, useSearchParams, Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useQuery } from '@tanstack/react-query';
import { IconChevronRight, IconUsers, IconStack3, IconClipboardList } from '@tabler/icons-react';
import { TaskBoard } from './components/TaskBoard';
import { ChiefChat } from './components/ChiefChat';
import { useAuthStore } from '../../stores';
import { ensureOrchestratorAnimations, AgentStack, StatusBadge, CenteredLoader } from '../../components/Orchestrator/OrchestratorUI';

export default function ProjectDetail() {
    ensureOrchestratorAnimations();
    const { t } = useTranslation();
    const { groupId } = useParams<{ groupId: string }>();
    const [searchParams] = useSearchParams();
    const tenantId = useAuthStore((s) => s.user?.tenant_id) ?? '';
    const chiefRunId = searchParams.get('chiefRunId') ?? '';

    if (!tenantId) {
        return <CenteredLoader label={t('projectDetail.loadingAuth', '加载中…')}/>;
    }
    if (!groupId) {
        return (
            <div style={{ padding: 'var(--space-6)', color: 'var(--text-secondary)' }}>
                {t('projectDetail.noGroup', '未指定 Group')}
            </div>
        );
    }

    return (
        <div className="orch-fade-in" style={{
            display: 'flex', flexDirection: 'column',
            height: 'calc(100vh - var(--header-height))',
            background: 'var(--bg-primary)',
        }}>
            {/* Header */}
            <ProjectHeader groupId={groupId} />

            {/* Body: 2-col grid */}
            <div style={{
                flex: 1,
                display: 'grid',
                gridTemplateColumns: 'minmax(0, 1fr) 380px',
                gap: 'var(--space-4)',
                padding: 'var(--space-4)',
                minHeight: 0,
            }}>
                <div style={{ minHeight: 0, minWidth: 0 }}>
                    <TaskBoard tenantId={tenantId} groupId={groupId} />
                </div>
                <div style={{ minHeight: 0 }}>
                    {chiefRunId ? (
                        <ChiefChat chiefRunId={chiefRunId} />
                    ) : (
                        <div style={{
                            display: 'flex', alignItems: 'center', justifyContent: 'center',
                            flexDirection: 'column', gap: 'var(--space-2)',
                            height: '100%',
                            background: 'var(--bg-elevated)',
                            border: '1px dashed var(--border-subtle)',
                            borderRadius: 'var(--radius-lg)',
                            color: 'var(--text-tertiary)',
                            fontSize: 'var(--text-sm)',
                            padding: 'var(--space-6)',
                            textAlign: 'center',
                        }}>
                            <IconUsers size={28}/>
                            <div style={{ color: 'var(--text-secondary)' }}>
                                {t('projectDetail.noChiefRun', 'Chief 1:1 聊天暂未启用')}
                            </div>
                            <div style={{ fontSize: 'var(--text-xs)', maxWidth: '32ch', lineHeight: 1.5 }}>
                                {t('projectDetail.noChiefRunHint', '当 Group 通过 Orchestrator 草稿创建时,会自动关联一个 Chief Run。')}
                            </div>
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
}

function ProjectHeader({ groupId }: { groupId: string }) {
    const { t } = useTranslation();
    // We don't have a /api/groups/{id} endpoint to fetch the group record here.
    // For v1, the header just shows the groupId. A future iteration can add
    // a /api/groups/{id} fetch (or include the chief run in DraftPreview's
    // navigation state) to render the real name + member stack.
    return (
        <div style={{
            display: 'flex',
            alignItems: 'center',
            gap: 'var(--space-3)',
            padding: 'var(--space-3) var(--space-5)',
            borderBottom: '1px solid var(--border-subtle)',
            background: 'var(--bg-secondary)',
        }}>
            <nav style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 'var(--text-sm)' }}>
                <Link to="/projects" style={{ color: 'var(--text-secondary)', textDecoration: 'none' }}>
                    {t('nav.projects', '项目')}
                </Link>
                <IconChevronRight size={12} style={{ color: 'var(--text-tertiary)' }}/>
                <span style={{ color: 'var(--text-primary)' }}>
                    {t('projectDetail.workingProject', 'Project')}
                </span>
            </nav>
            <div style={{ flex: 1 }} />
            <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)' }}>
                <code style={{
                    fontSize: 11,
                    color: 'var(--text-tertiary)',
                    background: 'var(--bg-tertiary)',
                    padding: '2px 8px',
                    borderRadius: 'var(--radius-sm)',
                    fontFamily: 'var(--font-mono)',
                }}>{groupId.slice(0, 8)}</code>
            </div>
        </div>
    );
}
