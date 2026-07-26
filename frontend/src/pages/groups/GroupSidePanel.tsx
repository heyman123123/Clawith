import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { IconAlertTriangle, IconBuildingBank, IconChartBar, IconCheck, IconCircleDot, IconPlus, IconRobot, IconSend, IconSettings, IconSparkles, IconUser, IconX } from '@tabler/icons-react';
import { groupApi } from '../../services/groupApi';
import GroupTextFileEditor from './GroupTextFileEditor';
import GroupWorkspaceTab from './GroupWorkspaceTab';
import GroupMemoryTab from './GroupMemoryTab';
import type { GroupMember, ProjectGroupDecision, ProjectGroupOverview, ProjectGroupTask, ShareholderBoard } from '../../types/group';

type PanelTab = 'members' | 'dashboard' | 'tasks' | 'decisions' | 'shareholder' | 'announcement' | 'workspace' | 'memory';

const PANEL_WIDTH_KEY = 'groups.panelWidth';
// Default sized by measurement, not taste: a long realistic member name — 12 CJK chars plus the
// "Manager" badge — measures ~221px, and the row chrome (16*2 body padding + 24 avatar + 8 gap)
// adds 64, so ~285px fits it on one line; 300 leaves a little slack. Min/max bound the drag.
const PANEL_DEFAULT_WIDTH = 300;
const PANEL_MIN_WIDTH = 240;
const PANEL_MAX_WIDTH = 520;

const clampWidth = (value: number) =>
    Math.min(PANEL_MAX_WIDTH, Math.max(PANEL_MIN_WIDTH, value));

interface GroupSidePanelProps {
    groupId: string;
    groupName: string;
    members: GroupMember[];
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
    onInvite,
    onOpenSettings,
    onClose,
}: GroupSidePanelProps) {
    const { t } = useTranslation();
    const [tab, setTab] = useState<PanelTab>('members');
    const [tasks, setTasks] = useState<ProjectGroupTask[] | null>(null);
    const [tasksError, setTasksError] = useState(false);
    const [startingTasks, setStartingTasks] = useState(false);
    const [overview, setOverview] = useState<ProjectGroupOverview | null>(null);
    const [overviewError, setOverviewError] = useState(false);
    const [shareholderBoard, setShareholderBoard] = useState<ShareholderBoard | null>(null);
    const [shareholderBoardError, setShareholderBoardError] = useState(false);
    const [shareholderProjectIds, setShareholderProjectIds] = useState<string[]>([]);
    const [shareholderDecision, setShareholderDecision] = useState('');
    const [dispatchingShareholderDecision, setDispatchingShareholderDecision] = useState(false);
    const [shareholderDispatchError, setShareholderDispatchError] = useState('');
    const [decisions, setDecisions] = useState<ProjectGroupDecision[] | null>(null);
    const [decisionsError, setDecisionsError] = useState(false);
    const [decisionDrafts, setDecisionDrafts] = useState<Record<string, string>>({});
    const [generatingDecisionId, setGeneratingDecisionId] = useState<string | null>(null);
    const [replyingDecisionId, setReplyingDecisionId] = useState<string | null>(null);
    const [decisionReplyErrors, setDecisionReplyErrors] = useState<Record<string, string>>({});

    const reloadProjectTasks = () => {
        setTasksError(false);
        return groupApi.projectTasks(groupId)
            .then(setTasks)
            .catch(() => {
                setTasks([]);
                setTasksError(true);
            });
    };

    const reloadProjectOverview = () => {
        setOverviewError(false);
        return groupApi.projectOverview(groupId)
            .then(setOverview)
            .catch(() => {
                setOverview(null);
                setOverviewError(true);
            });
    };

    const reloadShareholderBoard = () => {
        setShareholderBoardError(false);
        return groupApi.shareholderBoard(groupId)
            .then((board) => {
                setShareholderBoard(board);
                setShareholderProjectIds((current) => current.filter((id) => board.projects.some((project) => project.workflow_id === id)));
            })
            .catch(() => {
                setShareholderBoard(null);
                setShareholderBoardError(true);
            });
    };

    const reloadProjectDecisions = () => {
        setDecisionsError(false);
        return groupApi.projectDecisions(groupId)
            .then(setDecisions)
            .catch(() => {
                setDecisions([]);
                setDecisionsError(true);
            });
    };

    useEffect(() => {
        if (tab !== 'tasks') return;
        void reloadProjectTasks();
    }, [groupId, tab]);

    useEffect(() => {
        if (tab !== 'dashboard') return;
        void reloadProjectOverview();
    }, [groupId, tab]);

    useEffect(() => {
        if (tab !== 'shareholder') return;
        void reloadShareholderBoard();
    }, [groupId, tab]);

    useEffect(() => {
        if (tab !== 'decisions') return;
        void reloadProjectDecisions();
    }, [groupId, tab]);

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

    const people = members.filter((member) => member.participant_type === 'user');
    const agents = members.filter((member) => member.participant_type === 'agent');

    const clearDecisionError = (decisionId: string) => {
        setDecisionReplyErrors((current) => {
            if (!current[decisionId]) return current;
            const next = { ...current };
            delete next[decisionId];
            return next;
        });
    };

    const generateDecisionDraft = (decision: ProjectGroupDecision) => {
        if (generatingDecisionId || replyingDecisionId) return;
        setGeneratingDecisionId(decision.id);
        clearDecisionError(decision.id);
        void groupApi.generateProjectDecisionDraft(
            groupId,
            decision.id,
            (decisionDrafts[decision.id] || '').trim(),
        )
            .then(({ draft }) => {
                setDecisionDrafts((current) => ({ ...current, [decision.id]: draft }));
            })
            .catch((error: unknown) => {
                setDecisionReplyErrors((current) => ({
                    ...current,
                    [decision.id]: error instanceof Error
                        ? error.message
                        : t('groups.decisionDraftFailed', 'AI 建议生成失败，请重试。'),
                }));
            })
            .finally(() => setGeneratingDecisionId(null));
    };

    const submitDecisionModification = (decision: ProjectGroupDecision) => {
        const instruction = (decisionDrafts[decision.id] || '').trim();
        if (!instruction || replyingDecisionId || generatingDecisionId) return;
        setReplyingDecisionId(decision.id);
        clearDecisionError(decision.id);
        void groupApi.replyProjectDecision(groupId, decision.id, instruction, 'modification')
            .then(() => {
                setDecisionDrafts((current) => {
                    const next = { ...current };
                    delete next[decision.id];
                    return next;
                });
                return reloadProjectDecisions();
            })
            .catch((error: unknown) => {
                setDecisionReplyErrors((current) => ({
                    ...current,
                    [decision.id]: error instanceof Error
                        ? error.message
                        : t('groups.decisionReplyFailed', 'AI 修改指令提交失败，请重试。'),
                }));
            })
            .finally(() => setReplyingDecisionId(null));
    };

    const toggleShareholderProject = (workflowId: string) => {
        setShareholderDispatchError('');
        setShareholderProjectIds((current) => current.includes(workflowId)
            ? current.filter((id) => id !== workflowId)
            : [...current, workflowId]);
    };

    const dispatchShareholderDecision = () => {
        const content = shareholderDecision.trim();
        if (!content || shareholderProjectIds.length === 0 || dispatchingShareholderDecision) return;
        setDispatchingShareholderDecision(true);
        setShareholderDispatchError('');
        void groupApi.dispatchShareholderDecision(groupId, shareholderProjectIds, content)
            .then(() => {
                setShareholderDecision('');
                setShareholderProjectIds([]);
                return reloadShareholderBoard();
            })
            .catch((error: unknown) => {
                setShareholderDispatchError(error instanceof Error ? error.message : t('groups.shareholderDispatchFailed', '股东决策下发失败，请重试。'));
            })
            .finally(() => setDispatchingShareholderDecision(false));
    };

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
                    {member.role === 'owner' && (
                        <span className="group-badge-manager">{t('groups.owner', '项目群主')}</span>
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
        { key: 'dashboard', label: t('groups.projectDashboard', '看板') },
        { key: 'tasks', label: t('groups.tasks', '任务') },
        { key: 'decisions', label: t('groups.reviewRoom', '评审室') },
        { key: 'shareholder', label: t('groups.shareholderBoard', '股东决策') },
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

                {tab === 'dashboard' && (
                    <div className="project-dashboard">
                        {overview === null && !overviewError && <div className="group-member-hint">{t('common.loading', '加载中...')}</div>}
                        {overviewError && <div className="project-dashboard-empty"><IconChartBar size={18} /><span>{t('groups.noProjectDashboard', '当前群尚未关联项目看板。')}</span></div>}
                        {overview && <>
                            <section className="project-dashboard-hero">
                                <div><span>{t('groups.projectPulse', '项目脉冲')}</span><strong>{overview.project_name}</strong><p>{t('groups.projectDashboardHint', '执行进展、负责人、交付成效与风险在此同步。')}</p></div>
                                <div className="project-progress-orb"><b>{overview.milestones.length ? overview.milestone_progress_percent : overview.progress_percent}%</b><small>{overview.milestones.length ? t('groups.milestoneProgress', '里程碑') : t('groups.projectProgress', '已完成')}</small></div>
                            </section>
                            {overview.milestones.length > 0 && (
                                <section className="project-milestone-strip" aria-label={t('groups.milestoneStrip', '里程碑进度')}>
                                    {overview.milestones.map((milestone) => (
                                        <article key={milestone.id} className={`project-milestone-chip ${milestone.status}`} title={milestone.description || milestone.title}>
                                            <span className="project-milestone-chip-title">{milestone.title}</span>
                                            <span className="project-milestone-chip-meta">{milestone.completed_tasks}/{milestone.total_tasks || '—'}</span>
                                        </article>
                                    ))}
                                </section>
                            )}
                            <div className="project-dashboard-metrics">
                                <div><span>{t('groups.completedTasks', '已完成')}</span><strong>{overview.completed_tasks}<small>/{overview.total_tasks}</small></strong></div>
                                <div><span>{t('groups.activeTasks', '进行中')}</span><strong>{overview.active_tasks}</strong></div>
                                <div className={overview.blocked_tasks + overview.failed_tasks > 0 ? 'risk' : ''}><span>{t('groups.projectBlockers', '卡点')}</span><strong>{overview.blocked_tasks + overview.failed_tasks}</strong></div>
                            </div>
                            <section className="project-dashboard-section">
                                <div className="project-dashboard-section-title"><span>{t('groups.responsibilityBoard', '责任看板')}</span><small>{t('groups.responsibilityBoardHint', '谁负责什么，以及最近成效')}</small></div>
                                <div className="project-task-board">{overview.tasks.map((task) => <article key={task.id} className={`project-board-task ${task.status}`}>
                                    <div><span className={`project-task-status ${task.status}`}>{task.status === 'done' ? t('groups.taskDone', '已完成') : task.status === 'failed' ? t('groups.taskFailed', '失败') : task.status === 'blocked' ? t('groups.taskBlocked', '阻塞') : task.status === 'doing' ? t('groups.taskDoing', '执行中') : t('groups.taskPending', '待开始')}</span><strong>{task.title}</strong></div>
                                    <p>{task.agent_name} · {task.priority}</p>
                                    <small>{task.latest_outcome || task.description || t('groups.noTaskOutcome', '尚无可展示的交付结果。')}</small>
                                </article>)}</div>
                            </section>
                            <section className="project-dashboard-section">
                                <div className="project-dashboard-section-title"><span><IconAlertTriangle size={14} /> {t('groups.projectBlockers', '卡点面板')}</span><small>{t('groups.projectBlockersHint', '失败或依赖阻塞会在这里集中显示。')}</small></div>
                                {overview.blockers.length ? <div className="project-blocker-list">{overview.blockers.map((blocker) => <article key={blocker.task_id}><div><strong>{blocker.title}</strong><span>{blocker.agent_name} · {blocker.status === 'failed' ? t('groups.taskFailed', '失败') : t('groups.taskBlocked', '阻塞')}</span></div><p>{blocker.reason || t('groups.noBlockerReason', '暂无具体原因，请查看任务日志。')}</p></article>)}</div> : <div className="project-dashboard-clear"><IconCheck size={15} /> {t('groups.noProjectBlockers', '当前没有卡点，项目可以继续流转。')}</div>}
                            </section>
                        </>}
                    </div>
                )}

                {tab === 'shareholder' && (
                    <div className="shareholder-board">
                        {shareholderBoard === null && !shareholderBoardError && <div className="group-member-hint">{t('common.loading', '加载中...')}</div>}
                        {shareholderBoardError && <div className="project-dashboard-empty"><IconBuildingBank size={18} /><span>{t('groups.notShareholderGroup', '当前群不是股东群。可在项目流程中创建股东群。')}</span></div>}
                        {shareholderBoard && <>
                            <section className="shareholder-board-hero">
                                <IconBuildingBank size={20} />
                                <div><strong>{t('groups.shareholderBoardTitle', '公司治理决策台')}</strong><p>{t('groups.shareholderBoardHint', '先在群内讨论确认，再选择项目下发至对应决策群主。')}</p></div>
                            </section>
                            <section className="shareholder-dispatch-form">
                                <div className="project-dashboard-section-title"><span>{t('groups.selectProjectsToDispatch', '选择下发项目')}</span><small>{shareholderProjectIds.length} {t('groups.projectsSelected', '个已选择')}</small></div>
                                <div className="shareholder-project-list">{shareholderBoard.projects.map((project) => <label key={project.workflow_id} className={`shareholder-project-option ${shareholderProjectIds.includes(project.workflow_id) ? 'selected' : ''}`}>
                                    <input type="checkbox" checked={shareholderProjectIds.includes(project.workflow_id)} onChange={() => toggleShareholderProject(project.workflow_id)} />
                                    <span><strong>{project.name}</strong><small>{project.completed_tasks}/{project.total_tasks} {t('groups.tasks', '任务')} · {project.blocker_count} {t('groups.projectBlockers', '卡点')} · {project.decision_leader_name}</small></span>
                                </label>)}</div>
                                {shareholderBoard.projects.length === 0 && <div className="project-dashboard-empty">{t('groups.noRoutableProjects', '暂无已就绪的项目决策群。')}</div>}
                                <textarea
                                    className="project-decision-input shareholder-decision-input"
                                    value={shareholderDecision}
                                    disabled={dispatchingShareholderDecision}
                                    placeholder={t('groups.shareholderDecisionPlaceholder', '填写已在股东群确认的项目进展、资源管控或跨项目决策…')}
                                    onChange={(event) => { setShareholderDecision(event.target.value); setShareholderDispatchError(''); }}
                                />
                                {shareholderDispatchError && <div className="project-decision-error">{shareholderDispatchError}</div>}
                                <button type="button" className="shareholder-dispatch-button" disabled={!shareholderDecision.trim() || shareholderProjectIds.length === 0 || dispatchingShareholderDecision} onClick={dispatchShareholderDecision}>
                                    <IconSend size={14} />
                                    {dispatchingShareholderDecision ? t('groups.dispatchingShareholderDecision', '正在下发…') : t('groups.confirmShareholderDispatch', '确认并下发至项目决策群')}
                                </button>
                            </section>
                            <section className="project-dashboard-section">
                                <div className="project-dashboard-section-title"><span>{t('groups.shareholderDispatchHistory', '最近下发')}</span><small>{t('groups.dispatchReceiptHint', '每项记录均可追溯到目标项目。')}</small></div>
                                {shareholderBoard.dispatches.length ? <div className="shareholder-dispatch-list">{shareholderBoard.dispatches.map((dispatch) => <article key={dispatch.id}><div><strong>{dispatch.project_name}</strong><span>{dispatch.status === 'dispatched' ? t('groups.dispatched', '已下发') : dispatch.status}</span></div><p>{dispatch.content}</p></article>)}</div> : <div className="project-dashboard-empty">{t('groups.noShareholderDispatches', '暂未下发公司级决策。')}</div>}
                            </section>
                        </>}
                    </div>
                )}

                {tab === 'tasks' && (
                    <div className="project-panel-list">
                        {tasks === null && <div className="group-member-hint">{t('common.loading', '加载中...')}</div>}
                        {tasksError && <div className="group-member-hint">{t('groups.noProjectTasks', '此群尚未创建项目任务。')}</div>}
                        {tasks?.map((task) => (
                            <div key={task.id} className="group-member-row" style={{ alignItems: 'flex-start' }}>
                                <span className="group-avatar sm agent"><IconRobot size={14} stroke={1.6} /></span>
                                <div className="group-member-body">
                                    <div className="group-member-name">{task.title}</div>
                                    <div className="group-member-hint">{task.agent_name} · {task.status === 'blocked' ? '被依赖阻塞' : task.status}</div>
                                    {task.dependency_task_ids.length > 0 && (
                                        <div className="group-member-hint">{t('groups.taskDependencies', '依赖')} {task.dependency_task_ids.length} 项任务</div>
                                    )}
                                </div>
                            </div>
                        ))}
                        {tasks?.length === 0 && !tasksError && (
                            <>
                                <div className="group-member-hint">{t('groups.noProjectTasks', '此群尚未创建项目任务。')}</div>
                                <button
                                    type="button"
                                    className="group-invite-btn"
                                    disabled={startingTasks}
                                    onClick={() => {
                                        setStartingTasks(true);
                                        void groupApi.startProjectTasks(groupId)
                                            .then(reloadProjectTasks)
                                            .finally(() => setStartingTasks(false));
                                    }}
                                >
                                    <IconPlus size={14} stroke={1.8} />
                                    {startingTasks ? t('groups.startingTasks', '正在启动...') : t('groups.startProjectTasks', '启动任务流')}
                                </button>
                            </>
                        )}
                    </div>
                )}

                {tab === 'decisions' && (
                    <div className="project-panel-list">
                        <p className="group-member-hint" style={{ marginBottom: 12 }}>
                            {t('groups.decisionAuditHint', '历史审计（不再生成待办）')}
                        </p>
                        {decisions === null && <div className="group-member-hint">{t('common.loading', '加载中...')}</div>}
                        {decisionsError && <div className="group-member-hint">{t('groups.noProjectDecisions', '此群没有待你决策的事项。')}</div>}
                        {decisions?.map((decision) => {
                            const isPending = decision.status === 'pending';
                            const draft = decisionDrafts[decision.id] || '';
                            const isGenerating = generatingDecisionId === decision.id;
                            const isReplying = replyingDecisionId === decision.id;
                            const replyError = decisionReplyErrors[decision.id];
                            return (
                                <article key={decision.id} className="project-decision-card">
                                    <div className="project-decision-kicker">
                                        <IconCircleDot size={13} stroke={1.8} />
                                        <span>{decision.requesting_agent_name || t('groups.projectLeader', '项目群主')} {t('groups.decisionRequested', '请求决策')}</span>
                                        {!isPending && (
                                            <span className="group-badge-deleted">{decision.status}</span>
                                        )}
                                    </div>
                                    <h4>{decision.title}</h4>
                                    <p>{decision.context}</p>
                                    {!isPending && decision.response && (
                                        <div className="project-decision-readonly">
                                            <strong>{t('groups.decisionResponse', '决策回执')}</strong>
                                            <p>{decision.response}</p>
                                        </div>
                                    )}
                                    {isPending && <>
                                        <div className="project-decision-ai-hint">
                                            <IconSparkles size={13} stroke={1.8} />
                                            {t('groups.decisionAiHint', 'AI 会生成建议内容；在决策群审阅确认后，再下发项目群执行。')}
                                        </div>
                                        <div className="project-decision-input-wrap">
                                            <textarea
                                                className="project-decision-input"
                                                value={draft}
                                                disabled={isGenerating || isReplying}
                                                aria-label={t('groups.decisionAiInputLabel', '给群主的修改指令')}
                                                placeholder={t('groups.decisionAiPlaceholder', '例如：将测试预算改为 300 美元，优先验证美国市场，并暂停其他渠道。')}
                                                onChange={(event) => {
                                                    setDecisionDrafts((current) => ({
                                                        ...current,
                                                        [decision.id]: event.target.value,
                                                    }));
                                                    clearDecisionError(decision.id);
                                                }}
                                                onKeyDown={(event) => {
                                                    if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') {
                                                        event.preventDefault();
                                                        submitDecisionModification(decision);
                                                    }
                                                }}
                                            />
                                            <button
                                                type="button"
                                                className="project-decision-ai-action"
                                                disabled={isGenerating || isReplying}
                                                title={t('groups.generateDecisionDraft', 'AI 生成建议')}
                                                aria-label={t('groups.generateDecisionDraft', 'AI 生成建议')}
                                                onClick={() => generateDecisionDraft(decision)}
                                            >
                                                <IconSparkles size={13} />
                                                {isGenerating ? t('groups.generatingDecisionDraft', '生成中…') : 'AI'}
                                            </button>
                                        </div>
                                        {replyError && <div className="project-decision-error">{replyError}</div>}
                                        <button
                                            type="button"
                                            className="project-decision-submit"
                                            disabled={!draft.trim() || isGenerating || isReplying}
                                            onClick={() => submitDecisionModification(decision)}
                                        >
                                            <IconCheck size={14} stroke={1.8} />
                                            {t('groups.sendToProjectLeader', '确认后下发项目群')}
                                        </button>
                                        {isGenerating && <div className="project-decision-sending"><IconSparkles size={13} /> {t('groups.generatingDecisionDraft', '正在生成建议…')}</div>}
                                        {isReplying && <div className="project-decision-sending"><IconCheck size={13} /> {t('groups.sendingDecision', '正在提交…')}</div>}
                                    </>}
                                </article>
                            );
                        })}
                        {decisions?.length === 0 && !decisionsError && (
                            <div className="project-decision-empty">
                                <IconCheck size={18} stroke={1.8} />
                                <span>{t('groups.noProjectDecisions', '此群没有待你决策的事项。')}</span>
                            </div>
                        )}
                    </div>
                )}

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
        </aside>
    );
}
