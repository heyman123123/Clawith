import type React from 'react';
import { useQuery } from '@tanstack/react-query';
import {
    IconCheck,
    IconClock,
    IconClipboardCheck,
    IconHandStop,
    IconHourglass,
    IconLoader2,
    IconRobot,
    IconShieldCheck,
    IconTruckDelivery,
    IconX,
    IconAlertTriangle,
} from '@tabler/icons-react';

import {
    bucketMembersByKind,
    sortStepsByOrder,
    summarizeSteps,
    workflowApi,
    type WorkflowMember,
    type WorkflowRoleKind,
    type WorkflowRunStatus,
    type WorkflowStep,
    type WorkflowStepStatus,
} from '../services/workflowApi';

interface WorkflowPanelProps {
    workflowId: string;
    groupId?: string;
    sessionId?: string;
    /** Optional workflow name to display under the status badge. */
    workflowName?: string;
    /** When true, render the panel compact (collapses role descriptions). */
    compact?: boolean;
}

type IconComponent = React.ComponentType<{ size?: number; stroke?: number }>;

const RUN_STATUS_META: Record<WorkflowRunStatus, { label: string; tone: RunStatusTone; icon: IconComponent }> = {
    draft: { label: '草稿', tone: 'idle', icon: IconHourglass },
    composing: { label: '编排中', tone: 'pending', icon: IconLoader2 },
    queued: { label: '排队中', tone: 'pending', icon: IconHourglass },
    running: { label: '执行中', tone: 'active', icon: IconLoader2 },
    paused: { label: '已暂停', tone: 'paused', icon: IconHandStop },
    awaiting_approval: { label: '等待审批', tone: 'pending', icon: IconClock },
    quality_retry: { label: '质检整改', tone: 'retry', icon: IconAlertTriangle },
    succeeded: { label: '已完成', tone: 'success', icon: IconCheck },
    failed: { label: '已失败', tone: 'failed', icon: IconX },
    cancelled: { label: '已取消', tone: 'idle', icon: IconHandStop },
};

type RunStatusTone = 'active' | 'pending' | 'paused' | 'retry' | 'success' | 'failed' | 'idle';

const STEP_STATUS_META: Record<WorkflowStepStatus, { label: string; tone: StepStatusTone }> = {
    pending: { label: '待开始', tone: 'pending' },
    running: { label: '执行中', tone: 'running' },
    awaiting_approval: { label: '等审批', tone: 'pending' },
    quality_checking: { label: '质检中', tone: 'running' },
    succeeded: { label: '已完成', tone: 'succeeded' },
    quality_failed: { label: '质检未过', tone: 'retry' },
    failed: { label: '失败', tone: 'failed' },
    skipped: { label: '跳过', tone: 'idle' },
    cancelled: { label: '已取消', tone: 'idle' },
};

type StepStatusTone = 'pending' | 'running' | 'succeeded' | 'retry' | 'failed' | 'idle';

const ROLE_KIND_META: Record<WorkflowRoleKind, { label: string; tone: RoleTone; icon: typeof IconRobot }> = {
    scheduler: { label: '调度', tone: 'scheduler', icon: IconClipboardCheck },
    quality: { label: '质控', tone: 'quality', icon: IconShieldCheck },
    delivery: { label: '交付', tone: 'delivery', icon: IconTruckDelivery },
    executor: { label: '执行', tone: 'executor', icon: IconRobot },
    stakeholder: { label: '干系人', tone: 'stakeholder', icon: IconRobot },
};

type RoleTone = 'scheduler' | 'quality' | 'delivery' | 'executor' | 'stakeholder';

interface RoleBucket {
    kind: WorkflowRoleKind;
    label: string;
    members: WorkflowMember[];
}

const bucketMembers = (members: WorkflowMember[]): RoleBucket[] =>
    bucketMembersByKind(members).map((bucket) => ({
        kind: bucket.kind,
        label: ROLE_KIND_META[bucket.kind].label,
        members: bucket.members,
    }));

const StatusBadge = ({ status }: { status: WorkflowRunStatus }) => {
    const meta = RUN_STATUS_META[status];
    const Icon = meta.icon;
    return (
        <span className={`wf-panel-status wf-panel-status--${meta.tone}`}>
            <Icon size={13} stroke={2} />
            <span>{meta.label}</span>
        </span>
    );
};

const StepStatusPill = ({ status }: { status: WorkflowStepStatus }) => {
    const meta = STEP_STATUS_META[status];
    return (
        <span className={`wf-panel-step-pill wf-panel-step-pill--${meta.tone}`}>
            {meta.label}
        </span>
    );
};

const RoleBucketCard = ({ bucket, compact }: { bucket: RoleBucket; compact: boolean }) => {
    const meta = ROLE_KIND_META[bucket.kind];
    const Icon = meta.icon;
    return (
        <div className={`wf-panel-role-card wf-panel-role-card--${meta.tone}`}>
            <div className="wf-panel-role-card-header">
                <span className="wf-panel-role-card-icon">
                    <Icon size={14} stroke={2} />
                </span>
                <span className="wf-panel-role-card-label">{bucket.label}</span>
                <span className="wf-panel-role-card-count">{bucket.members.length}</span>
            </div>
            {!compact && (
                <ul className="wf-panel-role-card-list">
                    {bucket.members.map((member) => (
                        <li key={member.agent_id}>
                            <span className="wf-panel-role-card-name">{member.name}</span>
                            {member.is_group_leader && (
                                <span className="wf-panel-role-card-leader">群主</span>
                            )}
                        </li>
                    ))}
                </ul>
            )}
        </div>
    );
};

export default function WorkflowPanel({ workflowId, groupId, sessionId: _sessionId, workflowName, compact }: WorkflowPanelProps) {
    const statusQuery = useQuery({
        queryKey: ['workflow-status', workflowId, groupId ?? null],
        queryFn: () => workflowApi.getWorkflowStatus(workflowId),
        enabled: Boolean(workflowId),
    });
    const membersQuery = useQuery({
        queryKey: ['workflow-members', workflowId, groupId ?? null],
        queryFn: () => workflowApi.getWorkflowMembers(workflowId, groupId),
        enabled: Boolean(workflowId),
    });
    const stepsQuery = useQuery({
        queryKey: ['workflow-steps', workflowId],
        queryFn: () => workflowApi.getWorkflowSteps(workflowId),
        enabled: Boolean(workflowId),
    });

    const status = statusQuery.data;
    const buckets = bucketMembers(membersQuery.data ?? []);
    const steps = sortStepsByOrder(stepsQuery.data ?? []);
    const counts = summarizeSteps(steps);

    return (
        <section className="wf-panel" aria-label="执行群工作流面板">
            <header className="wf-panel-header">
                <div className="wf-panel-header-text">
                    <div className="wf-panel-eyebrow">执行群工作流</div>
                    <div className="wf-panel-title">{workflowName ?? status?.workflow_name ?? workflowId}</div>
                </div>
                {status ? <StatusBadge status={status.status} /> : (
                    <span className="wf-panel-status wf-panel-status--idle">加载中</span>
                )}
            </header>

            <div className="wf-panel-summary">
                <div className="wf-panel-summary-cell">
                    <span className="wf-panel-summary-num">{counts.done}</span>
                    <span className="wf-panel-summary-label">已完成</span>
                </div>
                <div className="wf-panel-summary-cell">
                    <span className="wf-panel-summary-num">{counts.active}</span>
                    <span className="wf-panel-summary-label">进行中</span>
                </div>
                <div className="wf-panel-summary-cell">
                    <span className="wf-panel-summary-num">{counts.pending}</span>
                    <span className="wf-panel-summary-label">待开始</span>
                </div>
                <div className="wf-panel-summary-cell">
                    <span className="wf-panel-summary-num">{status?.member_count ?? membersQuery.data?.length ?? 0}</span>
                    <span className="wf-panel-summary-label">成员</span>
                </div>
            </div>

            <div className="wf-panel-section">
                <h4 className="wf-panel-section-title">四权名册</h4>
                {buckets.length === 0 ? (
                    <div className="wf-panel-empty">暂无成员</div>
                ) : (
                    <div className="wf-panel-role-grid">
                        {buckets.map((bucket) => (
                            <RoleBucketCard key={bucket.kind} bucket={bucket} compact={Boolean(compact)} />
                        ))}
                    </div>
                )}
            </div>

            <div className="wf-panel-section">
                <h4 className="wf-panel-section-title">进度</h4>
                {stepsQuery.isLoading ? (
                    <div className="wf-panel-empty">加载步骤…</div>
                ) : steps.length === 0 ? (
                    <div className="wf-panel-empty">暂无步骤</div>
                ) : (
                    <ol className="wf-panel-steps">
                        {steps.map((step: WorkflowStep) => (
                            <li key={step.id} className={`wf-panel-step wf-panel-step--${STEP_STATUS_META[step.status].tone}`}>
                                <div className="wf-panel-step-order">{step.step_order}</div>
                                <div className="wf-panel-step-body">
                                    <div className="wf-panel-step-row">
                                        <span className="wf-panel-step-id">{step.step_id}</span>
                                        <span className="wf-panel-step-role">{step.role}</span>
                                        <StepStatusPill status={step.status} />
                                    </div>
                                    <div className="wf-panel-step-task">{step.task}</div>
                                    {typeof step.quality_score === 'number' && (
                                        <div className="wf-panel-step-score">质检分 {step.quality_score}</div>
                                    )}
                                </div>
                            </li>
                        ))}
                    </ol>
                )}
            </div>
        </section>
    );
}

/** Re-exported for unit tests in tests/. */
export const __test__ = {
    RUN_STATUS_META,
    STEP_STATUS_META,
    ROLE_KIND_META,
    bucketMembers,
};