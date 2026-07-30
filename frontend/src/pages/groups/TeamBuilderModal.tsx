import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { IconArrowLeft, IconCheck, IconRobot, IconSparkles, IconX } from '@tabler/icons-react';
import { teamBuilderApi } from '../../services/teamBuilderApi';
import type { TeamBuildDraft, TeamPlan, TeamPlanMember, TeamProvisionJob } from '../../types/teamBuilder';
import { createRandomUUID } from '../../utils/randomUUID';

const DRAFT_STORAGE_KEY = 'groups.teamBuilder.draftId';
const JOB_STORAGE_KEY = 'groups.teamBuilder.jobId';
const POLL_INTERVAL_MS = 2000;

export const hasTeamBuilderRecovery = () => Boolean(
    localStorage.getItem(DRAFT_STORAGE_KEY) || localStorage.getItem(JOB_STORAGE_KEY),
);

const persistDraft = (draftId: string) => localStorage.setItem(DRAFT_STORAGE_KEY, draftId);
const persistJob = (jobId: string) => localStorage.setItem(JOB_STORAGE_KEY, jobId);
const clearRecovery = () => {
    localStorage.removeItem(DRAFT_STORAGE_KEY);
    localStorage.removeItem(JOB_STORAGE_KEY);
};

interface TeamBuilderModalProps {
    onClose?: () => void;
    onCompleted: (target: { groupId: string; sessionId: string }) => void;
    embedded?: boolean;
}

const jobStatusLabel: Record<string, string> = {
    queued: '排队中',
    validating: '校验方案',
    provisioning_agents: '创建智能体',
    waiting_for_agents: '等待智能体就绪',
    creating_group: '创建群聊',
    activating: '激活群主',
    completed: '已完成',
    retryable_failed: '重试中',
    failed: '失败',
};

const memberStatusLabel: Record<string, string> = {
    pending: '待处理',
    resolving: '解析中',
    creating: '创建中',
    waiting: '等待中',
    ready: '就绪',
    failed: '失败',
};

function clonePlan(plan: TeamPlan): TeamPlan {
    return structuredClone(plan);
}

export default function TeamBuilderModal({ onClose, onCompleted, embedded = false }: TeamBuilderModalProps) {
    const { t } = useTranslation();
    const [draft, setDraft] = useState<TeamBuildDraft | null>(null);
    const [job, setJob] = useState<TeamProvisionJob | null>(null);
    const [requirement, setRequirement] = useState('');
    const [groupName, setGroupName] = useState('');
    const [editablePlan, setEditablePlan] = useState<TeamPlan | null>(null);
    const [showAdvancedJson, setShowAdvancedJson] = useState(false);
    const [planText, setPlanText] = useState('');
    const [restoring, setRestoring] = useState(true);
    const [submitting, setSubmitting] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const plan = editablePlan ?? draft?.reviewed_plan ?? draft?.generated_plan ?? null;
    const isProvisioning = Boolean(job) || draft?.status === 'confirmed';
    const leader = useMemo(() => plan?.members.find((member) => member.is_leader), [plan]);
    const step = isProvisioning ? 3 : draft && plan ? 2 : 1;

    useEffect(() => {
        let cancelled = false;
        const restore = async () => {
            const savedJobId = localStorage.getItem(JOB_STORAGE_KEY);
            const savedDraftId = localStorage.getItem(DRAFT_STORAGE_KEY);
            try {
                if (savedJobId) {
                    const restoredJob = await teamBuilderApi.getJob(savedJobId);
                    const restoredDraft = await teamBuilderApi.getDraft(restoredJob.draft_id);
                    if (cancelled) return;
                    setJob(restoredJob);
                    setDraft(restoredDraft);
                    setRequirement(restoredDraft.requirement);
                    const nextPlan = restoredDraft.reviewed_plan ?? restoredDraft.generated_plan;
                    setEditablePlan(nextPlan ? clonePlan(nextPlan) : null);
                    setPlanText(nextPlan ? JSON.stringify(nextPlan, null, 2) : '');
                    return;
                }
                if (savedDraftId) {
                    const restoredDraft = await teamBuilderApi.getDraft(savedDraftId);
                    if (cancelled) return;
                    setDraft(restoredDraft);
                    setRequirement(restoredDraft.requirement);
                    const nextPlan = restoredDraft.reviewed_plan ?? restoredDraft.generated_plan;
                    setEditablePlan(nextPlan ? clonePlan(nextPlan) : null);
                    setPlanText(nextPlan ? JSON.stringify(nextPlan, null, 2) : '');
                }
            } catch (restoreError: any) {
                if (!cancelled) {
                    clearRecovery();
                    setError(restoreError?.message ?? t('groups.teamBuilderRestoreFailed', '无法恢复搭建进度'));
                }
            } finally {
                if (!cancelled) setRestoring(false);
            }
        };
        void restore();
        return () => { cancelled = true; };
    }, [t]);

    useEffect(() => {
        if (!job || job.status === 'completed' || job.status === 'failed') return undefined;
        let cancelled = false;
        const poll = async () => {
            try {
                const next = await teamBuilderApi.getJob(job.id);
                if (!cancelled) setJob(next);
            } catch (pollError: any) {
                if (!cancelled) setError(pollError?.message ?? t('groups.teamBuilderProgressFailed', '无法读取创建进度'));
            }
        };
        const timer = window.setInterval(() => void poll(), POLL_INTERVAL_MS);
        void poll();
        return () => {
            cancelled = true;
            window.clearInterval(timer);
        };
    }, [job?.id, job?.status, t]);

    useEffect(() => {
        if (job?.status !== 'completed' || !job.group_id || !job.session_id) return;
        clearRecovery();
        onCompleted({ groupId: job.group_id, sessionId: job.session_id });
    }, [job, onCompleted]);

    const updateMember = (memberKey: string, patch: Partial<TeamPlanMember>) => {
        setEditablePlan((prev) => {
            if (!prev) return prev;
            return {
                ...prev,
                members: prev.members.map((member) =>
                    member.member_key === memberKey ? { ...member, ...patch } : member,
                ),
            };
        });
    };

    const syncPlanTextFromCards = () => {
        if (!editablePlan) return;
        setPlanText(JSON.stringify(editablePlan, null, 2));
    };

    const createDraft = async () => {
        if (!requirement.trim() || submitting) return;
        setSubmitting(true);
        setError(null);
        try {
            const nextDraft = await teamBuilderApi.createDraft({
                requirement: requirement.trim(),
                group_name: groupName.trim() || undefined,
            });
            setDraft(nextDraft);
            const nextPlan = nextDraft.reviewed_plan ?? nextDraft.generated_plan;
            setEditablePlan(nextPlan ? clonePlan(nextPlan) : null);
            setPlanText(nextPlan ? JSON.stringify(nextPlan, null, 2) : '');
            setShowAdvancedJson(false);
            persistDraft(nextDraft.id);
            if (nextDraft.status !== 'ready') {
                setError(nextDraft.error_message ?? t('groups.teamBuilderPlanFailed', '方案生成失败，请稍后重试'));
            }
        } catch (createError: any) {
            setError(createError?.message ?? t('groups.teamBuilderPlanFailed', '方案生成失败，请稍后重试'));
        } finally {
            setSubmitting(false);
        }
    };

    const confirm = async () => {
        if (!draft || submitting) return;
        let reviewedPlan: TeamPlan;
        try {
            if (showAdvancedJson) {
                reviewedPlan = JSON.parse(planText) as TeamPlan;
            } else if (editablePlan) {
                reviewedPlan = editablePlan;
            } else {
                setError(t('groups.teamBuilderPlanJsonInvalid', '方案格式无效，请检查后重试'));
                return;
            }
        } catch {
            setError(t('groups.teamBuilderPlanJsonInvalid', '方案格式无效，请检查后重试'));
            return;
        }
        setSubmitting(true);
        setError(null);
        try {
            const updatedDraft = await teamBuilderApi.updateDraft(draft.id, reviewedPlan);
            setDraft(updatedDraft);
            const nextPlan = updatedDraft.reviewed_plan;
            setEditablePlan(nextPlan ? clonePlan(nextPlan) : null);
            setPlanText(nextPlan ? JSON.stringify(nextPlan, null, 2) : '');
            const nextJob = await teamBuilderApi.confirmDraft(
                updatedDraft.id,
                updatedDraft.plan_version,
                createRandomUUID(),
            );
            setJob(nextJob);
            persistDraft(updatedDraft.id);
            persistJob(nextJob.id);
        } catch (confirmError: any) {
            setError(confirmError?.message ?? t('groups.teamBuilderConfirmFailed', '确认方案失败'));
        } finally {
            setSubmitting(false);
        }
    };

    const goBack = () => {
        if (!draft || job) return;
        clearRecovery();
        setDraft(null);
        setEditablePlan(null);
        setPlanText('');
        setShowAdvancedJson(false);
        setError(null);
    };

    const title = isProvisioning
        ? t('groups.teamBuilderProgressTitle', '正在搭建团队')
        : draft ? t('groups.teamBuilderReviewTitle', '确认团队方案')
            : t('groups.teamBuilderTitle', '智能搭建团队');

    const workspace = (
        <div className={embedded ? 'team-builder-workspace' : 'group-modal team-builder-modal'}>
            <div className="group-modal-header">
                <h3><IconSparkles size={17} stroke={1.8} /> {title}</h3>
                {!embedded && <button type="button" className="group-icon-btn" onClick={onClose}>
                    <IconX size={16} stroke={1.7} />
                </button>}
            </div>

            <div className="team-builder-steps" aria-label="搭建步骤">
                {[
                    t('groups.teamBuilderStepNeed', '描述需求'),
                    t('groups.teamBuilderStepReview', '确认方案'),
                    t('groups.teamBuilderStepCreate', '创建团队'),
                ].map((label, index) => {
                    const n = index + 1;
                    return (
                        <div key={label} className={`team-builder-step ${step === n ? 'active' : ''} ${step > n ? 'done' : ''}`}>
                            <span>{n}</span>
                            <strong>{label}</strong>
                        </div>
                    );
                })}
            </div>

            {restoring ? (
                <div className="group-empty-hint">{t('common.loading', '加载中...')}</div>
            ) : isProvisioning && job ? (
                <div className="team-builder-content">
                    <p className="team-builder-lead">{t('groups.teamBuilderProgressHint', '正在创建缺失智能体、建立群聊并以你的身份激活群主。')}</p>
                    <div className="team-builder-status">
                        {t(`groups.teamBuilderStatus.${job.status}`, jobStatusLabel[job.status] ?? job.status)}
                    </div>
                    <div className="team-builder-members">
                        {job.members.map((member) => {
                            const planMember = plan?.members.find((item) => item.member_key === member.member_key);
                            return (
                                <div className="team-builder-member" key={member.member_key}>
                                    <IconRobot size={15} stroke={1.6} />
                                    <span>{planMember?.name ?? member.member_key}</span>
                                    <span className={`team-builder-member-status ${member.status}`}>
                                        {memberStatusLabel[member.status] ?? member.status}
                                    </span>
                                </div>
                            );
                        })}
                    </div>
                    {(job.error_message || error) && <p className="team-builder-error">{job.error_message ?? error}</p>}
                    {job.status === 'retryable_failed' && (
                        <p className="team-builder-note">{t('groups.teamBuilderRetrying', '系统会安全地继续重试此任务。你可以关闭窗口，进度会被保留。')}</p>
                    )}
                </div>
            ) : draft && plan ? (
                <div className="team-builder-content">
                    <p className="team-builder-lead">{t('groups.teamBuilderReviewHint', '确认后才会创建缺失的智能体和群聊。群主将接收你的首条指令并公开分发任务。')}</p>
                    <div className="team-builder-plan-summary">
                        <label className="team-builder-plan-label" htmlFor="team-builder-plan-name">
                            {t('groups.teamBuilderName', '团队名称')}
                        </label>
                        <input
                            id="team-builder-plan-name"
                            className="input"
                            value={editablePlan?.group_name ?? plan.group_name}
                            onChange={(event) => setEditablePlan((prev) => prev ? { ...prev, group_name: event.target.value } : prev)}
                        />
                        <label className="team-builder-plan-label" htmlFor="team-builder-plan-goal">
                            {t('groups.teamBuilderGoal', '目标')}
                        </label>
                        <textarea
                            id="team-builder-plan-goal"
                            className="team-builder-requirement"
                            rows={3}
                            value={editablePlan?.goal ?? plan.goal}
                            onChange={(event) => setEditablePlan((prev) => prev ? { ...prev, goal: event.target.value } : prev)}
                        />
                    </div>
                    {leader && (
                        <div className="team-builder-leader">
                            <IconRobot size={16} stroke={1.7} />
                            <span>{t('groups.teamBuilderLeader', '群主')}：<strong>{leader.name}</strong></span>
                        </div>
                    )}
                    <div className="team-builder-role-cards">
                        {(editablePlan?.members ?? plan.members).map((member) => (
                            <div className={`team-builder-role-card ${member.is_leader ? 'leader' : ''}`} key={member.member_key}>
                                <div className="team-builder-role-card-head">
                                    <IconRobot size={15} stroke={1.6} />
                                    <input
                                        className="input"
                                        value={member.name}
                                        onChange={(event) => updateMember(member.member_key, { name: event.target.value })}
                                        aria-label={t('groups.teamBuilderMemberName', '角色名称')}
                                    />
                                    <span className={`team-builder-source ${member.source}`}>
                                        {t(`groups.teamBuilderSource.${member.source}`, member.source === 'existing' ? '已有' : '新建')}
                                    </span>
                                    {member.is_leader && <span className="group-badge-leader">{t('groups.teamBuilderLeader', '群主')}</span>}
                                </div>
                                <label className="team-builder-plan-label">
                                    {t('groups.teamBuilderRole', '职责简述')}
                                </label>
                                <input
                                    className="input"
                                    value={member.role_description}
                                    onChange={(event) => updateMember(member.member_key, { role_description: event.target.value })}
                                />
                                <label className="team-builder-plan-label">
                                    {t('groups.teamBuilderResponsibility', '负责事项')}
                                </label>
                                <textarea
                                    className="team-builder-requirement"
                                    rows={2}
                                    value={member.responsibility}
                                    onChange={(event) => updateMember(member.member_key, { responsibility: event.target.value })}
                                />
                            </div>
                        ))}
                    </div>
                    <button
                        type="button"
                        className="team-builder-advanced-toggle"
                        onClick={() => {
                            if (!showAdvancedJson) syncPlanTextFromCards();
                            setShowAdvancedJson((value) => !value);
                        }}
                    >
                        {showAdvancedJson
                            ? t('groups.teamBuilderHideJson', '收起高级 JSON')
                            : t('groups.teamBuilderShowJson', '高级：编辑 JSON')}
                    </button>
                    {showAdvancedJson && (
                        <textarea
                            id="team-builder-plan"
                            className="team-builder-plan-input"
                            value={planText}
                            onChange={(event) => setPlanText(event.target.value)}
                            spellCheck={false}
                        />
                    )}
                </div>
            ) : (
                <div className="team-builder-content">
                    <p className="team-builder-lead">{t('groups.teamBuilderFormHint', '描述目标，系统会先生成可确认的群主和成员方案。')}</p>
                    <label className="team-builder-plan-label" htmlFor="team-builder-name">
                        {t('groups.teamBuilderName', '团队名称（可选）')}
                    </label>
                    <input id="team-builder-name" className="input" value={groupName} onChange={(event) => setGroupName(event.target.value)} />
                    <label className="team-builder-plan-label" htmlFor="team-builder-requirement">
                        {t('groups.teamBuilderRequirement', '需求')}
                    </label>
                    <textarea
                        id="team-builder-requirement"
                        className="team-builder-requirement"
                        value={requirement}
                        onChange={(event) => setRequirement(event.target.value)}
                        placeholder={t('groups.teamBuilderRequirementPlaceholder', '例如：为下月新品发布建立一个可公开协作的团队，完成调研、方案和交付物。')}
                    />
                </div>
            )}

            {error && <p className="team-builder-error team-builder-error-footer">{error}</p>}
            <div className="group-create-footer">
                <button
                    type="button"
                    className="btn btn-sm"
                    onClick={draft && !job ? goBack : () => onClose?.()}
                >
                    {draft && !job ? <><IconArrowLeft size={14} />{t('common.back', '返回')}</> : t('common.cancel', '取消')}
                </button>
                {!restoring && !draft && (
                    <button type="button" className="btn btn-sm btn-primary" disabled={!requirement.trim() || submitting} onClick={() => void createDraft()}>
                        {submitting ? t('groups.teamBuilderGenerating', '正在生成方案…') : t('groups.teamBuilderGenerate', '生成方案')}
                    </button>
                )}
                {!restoring && draft && plan && !isProvisioning && (
                    <button type="button" className="btn btn-sm btn-primary" disabled={submitting} onClick={() => void confirm()}>
                        <IconCheck size={14} stroke={2} />
                        {submitting ? t('common.loading', '加载中...') : t('groups.teamBuilderConfirm', '确认并创建')}
                    </button>
                )}
            </div>
        </div>
    );

    if (embedded) return workspace;
    return <div className="group-modal-backdrop" onClick={onClose}>{workspace}</div>;
}
