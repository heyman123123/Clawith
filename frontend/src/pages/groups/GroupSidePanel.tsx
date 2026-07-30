import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { IconChevronLeft, IconPlus, IconRefresh, IconRobot, IconSettings, IconUser, IconX } from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import { aiMonitoringApi } from '../../services/aiMonitoringApi';
import { useAuthStore } from '../../stores';
import AIInteractionDetailDrawer from '../../components/AIInteractionDetailDrawer';
import AIInteractionList from '../../components/AIInteractionList';
import { AIMonitoringStatsTable } from '../../components/AIMonitoringCenter';
import { groupApi } from '../../services/groupApi';
import GroupTextFileEditor from './GroupTextFileEditor';
import GroupWorkspaceTab from './GroupWorkspaceTab';
import GroupMemoryTab from './GroupMemoryTab';
import GroupWorkflowTab from './GroupWorkflowTab';
import type { AIAgentStatsRow } from '../../types/aiMonitoring';
import type { GroupMember } from '../../types/group';

type PanelTab = 'members' | 'monitoring' | 'workflow' | 'announcement' | 'workspace' | 'memory';

const PANEL_WIDTH_KEY = 'groups.panelWidth';
// Default sized by measurement, not taste: a long realistic member name — 12 CJK chars plus the
// "Manager" badge — measures ~221px, and the row chrome (16*2 body padding + 24 avatar + 8 gap)
// adds 64, so ~285px fits it on one line; 300 leaves a little slack. Min/max bound the drag.
const PANEL_DEFAULT_WIDTH = 360;
const PANEL_MIN_WIDTH = 280;
const PANEL_MAX_WIDTH = 560;

const clampWidth = (value: number) =>
    Math.min(PANEL_MAX_WIDTH, Math.max(PANEL_MIN_WIDTH, value));

interface GroupSidePanelProps {
    groupId: string;
    groupName: string;
    members: GroupMember[];
    leaderParticipantId?: string | null;
    myParticipantId?: string;
    isManager: boolean;
    onInvite: () => void;
    onOpenSettings: () => void;
    onClose: () => void;
}

/**
 * The group-level side panel: a fixed header naming the group (so it reads as group-scoped and does
 * not change when the session switches), then tabs for members, announcement, files and memory. It
 * is view-and-invite only — renaming, removing members and dissolving live in the settings modal
 * behind the gear.
 */
export default function GroupSidePanel({
    groupId,
    groupName,
    members,
    leaderParticipantId,
    myParticipantId,
    isManager,
    onInvite,
    onOpenSettings,
    onClose,
}: GroupSidePanelProps) {
    const { t } = useTranslation();
    const [tab, setTab] = useState<PanelTab>('members');
    const [monitoringPage, setMonitoringPage] = useState(1);
    const [selectedInteractionId, setSelectedInteractionId] = useState<string | null>(null);
    const [selectedAgent, setSelectedAgent] = useState<AIAgentStatsRow | null>(null);
    const [rangeMode, setRangeMode] = useState<'24h' | 'day'>('24h');
    const [date, setDate] = useState(() => new Date().toISOString().slice(0, 10));
    const user = useAuthStore((state) => state.user);
    const isAdmin = user?.role === 'org_admin' || user?.role === 'platform_admin' || !!user?.is_platform_admin;
    const rangeFilters = {
        date: rangeMode === 'day' ? date : null,
        range: rangeMode === '24h' ? '24h' as const : null,
    };
    const agentStats = useQuery({
        queryKey: ['group-ai-monitoring-agents', groupId, rangeMode, date],
        queryFn: () => aiMonitoringApi.agentStats({ groupId, ...rangeFilters, sortBy: 'failures', order: 'desc' }),
        enabled: isAdmin && tab === 'monitoring',
        staleTime: 10_000,
        refetchInterval: 15_000,
    });
    const monitoring = useQuery({
        queryKey: ['group-ai-monitoring', groupId, selectedAgent?.agent_id, rangeMode, date, monitoringPage],
        queryFn: () => aiMonitoringApi.groupInteractions(groupId, monitoringPage, 20, {
            agentId: selectedAgent?.agent_id,
            unassigned: selectedAgent != null && selectedAgent.agent_id == null,
            ...rangeFilters,
        }),
        enabled: isAdmin && tab === 'monitoring' && !!selectedAgent,
        staleTime: 10_000,
        refetchInterval: 15_000,
    });

    // The panel's left edge is a resize handle; the width is remembered across sessions.
    const [width, setWidth] = useState(() => {
        const stored = Number(localStorage.getItem(PANEL_WIDTH_KEY));
        return Number.isFinite(stored) && stored > 0 ? clampWidth(stored) : PANEL_DEFAULT_WIDTH;
    });
    useEffect(() => {
        localStorage.setItem(PANEL_WIDTH_KEY, String(width));
    }, [width]);

    const dragRef = useRef<{ startX: number; startWidth: number } | null>(null);
    const onResizeStart = (event: React.PointerEvent<HTMLDivElement>) => {
        dragRef.current = { startX: event.clientX, startWidth: width };
        event.currentTarget.setPointerCapture(event.pointerId);
        document.body.style.userSelect = 'none';
    };
    const onResizeMove = (event: React.PointerEvent<HTMLDivElement>) => {
        if (!dragRef.current) return;
        // The panel is on the right, so dragging its left edge leftward widens it.
        const delta = dragRef.current.startX - event.clientX;
        setWidth(clampWidth(dragRef.current.startWidth + delta));
    };
    const onResizeEnd = (event: React.PointerEvent<HTMLDivElement>) => {
        dragRef.current = null;
        event.currentTarget.releasePointerCapture(event.pointerId);
        document.body.style.userSelect = '';
    };

    const leader = members.find((member) => member.participant_id === leaderParticipantId);
    const people = members.filter((member) => member.participant_type === 'user' && member !== leader);
    const agents = members.filter((member) => member.participant_type === 'agent' && member !== leader);

    const renderMember = (member: GroupMember) => (
        <div key={member.id} className="group-member-row">
            <span className={`group-avatar sm ${member.participant_type === 'agent' ? 'agent' : ''}`}>
                {member.participant_type === 'agent'
                    ? <IconRobot size={14} stroke={1.6} />
                    : member.display_name.slice(0, 1).toUpperCase()}
            </span>
            <div className="group-member-body">
                <div className="group-member-name">
                    {member.display_name}
                    {member.role === 'manager' && (
                        <span className="group-badge-manager">{t('groups.manager', '群管理')}</span>
                    )}
                    {member.participant_id === leaderParticipantId && (
                        <span className="group-badge-leader">{t('groups.teamBuilderLeader', '群主')}</span>
                    )}
                    {member.is_deleted && (
                        <span className="group-badge-deleted">{t('groups.deletedBadge', '已删除')}</span>
                    )}
                </div>
                {(member.role_description || member.title) && (
                    <div className="group-member-hint">{member.role_description || member.title}</div>
                )}
            </div>
        </div>
    );

    const TABS: { key: PanelTab; label: string }[] = [
        { key: 'members', label: `${t('groups.members', '成员')} · ${members.length}` },
        ...(isAdmin ? [{ key: 'monitoring' as const, label: t('groups.aiMonitoring', 'AI 监控') }] : []),
        { key: 'workflow', label: t('groups.workflow', '推进') },
        { key: 'announcement', label: t('groups.announcement', '群公告') },
        { key: 'workspace', label: t('groups.workspace', '文件') },
        { key: 'memory', label: t('groups.memory', '记忆') },
    ];

    return (
        <aside className="group-side-panel" style={{ width, flexBasis: width }}>
            <div
                className="group-panel-resizer"
                onPointerDown={onResizeStart}
                onPointerMove={onResizeMove}
                onPointerUp={onResizeEnd}
                onPointerCancel={onResizeEnd}
                title={t('groups.dragToResize', '拖动调整宽度')}
            />
            <div className="group-panel-topbar">
                <span className="group-panel-groupname" title={groupName}>{groupName}</span>
                <div className="group-column-actions">
                    <button
                        type="button"
                        className="group-icon-btn"
                        title={t('groups.settings', '群设置')}
                        onClick={onOpenSettings}
                    >
                        <IconSettings size={16} stroke={1.7} />
                    </button>
                    <button type="button" className="group-icon-btn" onClick={onClose}>
                        <IconX size={16} stroke={1.7} />
                    </button>
                </div>
            </div>

            <div className="group-panel-header">
                <div className="group-tabs scrollable">
                    {TABS.map(({ key, label }) => (
                        <button
                            key={key}
                            type="button"
                            className={`group-tab ${tab === key ? 'active' : ''}`}
                            onClick={() => setTab(key)}
                        >
                            {label}
                        </button>
                    ))}
                </div>
            </div>

            <div className="group-panel-body">
                {tab === 'members' && (
                    <>
                        <button type="button" className="group-invite-btn" onClick={onInvite}>
                            <IconPlus size={14} stroke={1.8} />
                            {t('groups.inviteTitle', '邀请成员')}
                        </button>

                        {leader && <>
                            <div className="group-panel-label">
                                <IconRobot size={12} stroke={1.7} />
                                {t('groups.teamBuilderLeader', '群主')}
                            </div>
                            {renderMember(leader)}
                        </>}

                        {agents.length > 0 && (
                            <>
                                <div className="group-panel-label">
                                    <IconRobot size={12} stroke={1.7} />
                                    {t('groups.tabAgents', '智能体')} · {agents.length}
                                </div>
                                {agents.map(renderMember)}
                            </>
                        )}

                        <div className="group-panel-label">
                            <IconUser size={12} stroke={1.7} />
                            {t('groups.tabPeople', '成员')} · {people.length}
                        </div>
                        {people.map(renderMember)}
                    </>
                )}

                {tab === 'monitoring' && isAdmin && <div style={{ margin: '-8px -12px' }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', gap: '8px', alignItems: 'flex-start', padding: '10px 12px' }}>
                        <div style={{ color: 'var(--text-tertiary)', fontSize: '11px' }}>{t('groups.aiMonitoringNote', '仅显示本群会话触发的 AI 调用 · 脱敏上下文')}</div>
                        <button
                            type="button"
                            className="btn btn-ghost"
                            onClick={() => { void agentStats.refetch(); if (selectedAgent) void monitoring.refetch(); }}
                            style={{ display: 'inline-flex', alignItems: 'center', gap: '4px', fontSize: '11px', flexShrink: 0 }}
                        >
                            <IconRefresh size={12} />
                            {t('dashboard.aiMonitoring.refresh')}
                        </button>
                    </div>
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px', padding: '0 12px 10px' }}>
                        <button type="button" className={`btn ${rangeMode === '24h' ? 'btn-primary' : 'btn-ghost'}`} onClick={() => { setRangeMode('24h'); setMonitoringPage(1); }} style={{ fontSize: '11px' }}>
                            {t('dashboard.aiMonitoring.range24h')}
                        </button>
                        <button type="button" className={`btn ${rangeMode === 'day' ? 'btn-primary' : 'btn-ghost'}`} onClick={() => { setRangeMode('day'); setMonitoringPage(1); }} style={{ fontSize: '11px' }}>
                            {t('dashboard.aiMonitoring.rangeDay')}
                        </button>
                        {rangeMode === 'day' && (
                            <input
                                type="date"
                                value={date}
                                onChange={(event) => { setDate(event.target.value); setMonitoringPage(1); }}
                                style={{ fontSize: '11px', padding: '2px 6px', borderRadius: '6px', border: '1px solid var(--border-subtle)', background: 'transparent', color: 'var(--text-primary)' }}
                            />
                        )}
                    </div>
                    {selectedAgent ? (
                        <>
                            <div style={{ display: 'flex', alignItems: 'center', gap: '6px', padding: '0 12px 8px', fontSize: '12px', color: 'var(--text-secondary)' }}>
                                <button
                                    type="button"
                                    className="btn btn-ghost"
                                    onClick={() => { setSelectedAgent(null); setSelectedInteractionId(null); setMonitoringPage(1); }}
                                    style={{ display: 'inline-flex', alignItems: 'center', gap: '2px', fontSize: '11px', padding: '2px 4px' }}
                                >
                                    <IconChevronLeft size={12} />
                                    {t('dashboard.aiMonitoring.backToAgents')}
                                </button>
                                <span style={{ color: 'var(--text-tertiary)' }}>/</span>
                                <span>{selectedAgent.agent_name || t('dashboard.aiMonitoring.platform')}</span>
                            </div>
                            <div style={{ overflowX: 'auto' }}>
                                <AIInteractionList data={monitoring.data} loading={monitoring.isLoading} error={monitoring.isError} onSelect={setSelectedInteractionId} onPage={setMonitoringPage} />
                            </div>
                        </>
                    ) : (
                        <AIMonitoringStatsTable
                            kind="agent"
                            compact
                            rows={agentStats.data?.agents ?? []}
                            loading={agentStats.isLoading}
                            error={agentStats.isError}
                            onSelectAgent={(row) => { setSelectedAgent(row); setMonitoringPage(1); }}
                        />
                    )}
                </div>}

                {tab === 'workflow' && <GroupWorkflowTab
                    groupId={groupId}
                    members={members}
                    myParticipantId={myParticipantId}
                    isManager={isManager}
                />}

                {tab === 'announcement' && (
                    <GroupTextFileEditor
                        queryKey={['group-announcement', groupId]}
                        note={t('groups.announcementNote', '群公告会注入被 @ 智能体的上下文，用于约定群目标和协作规则。')}
                        placeholder={t('groups.announcementPlaceholder', '写下群目标、协作规则和对智能体的要求...')}
                        load={() => groupApi.announcement(groupId)}
                        save={(content, token) => groupApi.saveAnnouncement(groupId, content, token)}
                    />
                )}

                {tab === 'workspace' && <GroupWorkspaceTab groupId={groupId} />}

                {tab === 'memory' && <GroupMemoryTab groupId={groupId} members={members} />}
            </div>
            <AIInteractionDetailDrawer interactionId={selectedInteractionId} onClose={() => setSelectedInteractionId(null)} />
        </aside>
    );
}

