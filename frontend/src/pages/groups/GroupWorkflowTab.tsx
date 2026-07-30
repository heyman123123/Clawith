import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { IconAlertTriangle, IconArrowRight, IconCheck, IconCircleCheck, IconClock, IconPlayerPlay, IconSettings } from '@tabler/icons-react';
import { groupWorkflowApi } from '../../services/groupWorkflowApi';
import type { GroupMember } from '../../types/group';
import type { GroupWorkflow, WorkflowItem } from '../../types/groupWorkflow';
import WorkflowManageModal from './WorkflowManageModal';

interface GroupWorkflowTabProps {
    groupId: string;
    members: GroupMember[];
    myParticipantId?: string;
    isManager: boolean;
}

const sourceLabel: Record<string, string> = { default: '协作推进', agile: '敏捷需求', product_research: '产研协作', ai: 'AI 生成' };
const stateLabel: Record<string, string> = { pending: '待推进', in_progress: '进行中', blocked: '已阻塞', awaiting_approval: '待确认', done: '已完成' };

export default function GroupWorkflowTab({ groupId, members, myParticipantId, isManager }: GroupWorkflowTabProps) {
    const client = useQueryClient();
    const [manageOpen, setManageOpen] = useState(false);
    const workflowQuery = useQuery({ queryKey: ['group-workflow', groupId], queryFn: () => groupWorkflowApi.get(groupId), staleTime: 8_000 });
    const workflow = workflowQuery.data;
    const refresh = () => void client.invalidateQueries({ queryKey: ['group-workflow', groupId] });
    const start = useMutation({ mutationFn: (item: WorkflowItem) => groupWorkflowApi.patchItem(groupId, item.id, 'in_progress', item.version), onSuccess: refresh });
    const unblock = useMutation({ mutationFn: (item: WorkflowItem) => groupWorkflowApi.patchItem(groupId, item.id, 'unblock', item.version), onSuccess: refresh });
    const evidence = useMutation({ mutationFn: ({ item, ref }: { item: WorkflowItem; ref: string }) => groupWorkflowApi.evidence(groupId, item.id, { ref }, item.version), onSuccess: refresh });
    const block = useMutation({ mutationFn: ({ item, reason }: { item: WorkflowItem; reason: string }) => groupWorkflowApi.block(groupId, item.id, reason, item.version), onSuccess: refresh });
    const approve = useMutation({ mutationFn: (stageId: string) => groupWorkflowApi.confirmStage(groupId, stageId), onSuccess: refresh });
    if (workflowQuery.isLoading) return <div className="workflow-loading">正在载入推进指挥台…</div>;
    if (!workflow || workflowQuery.isError) return <div className="workflow-loading">暂时无法载入工作流。<button type="button" onClick={() => workflowQuery.refetch()}>重试</button></div>;
    const done = workflow.items.filter((item) => item.status === 'done').length;
    const percent = workflow.items.length ? Math.round(done / workflow.items.length * 100) : 0;
    const grouped = ['pending', 'in_progress', 'awaiting_approval', 'blocked', 'done'].map((status) => [status, workflow.items.filter((item) => item.status === status)] as const).filter(([, items]) => items.length);
    const currentStage = workflow.stages.find((stage) => stage.id === workflow.current_stage_id);
    const memberName = (participantId: string | null) => members.find((member) => member.participant_id === participantId)?.display_name ?? '未指派';
    const action = (item: WorkflowItem) => {
        if (item.assignee_participant_id !== myParticipantId && workflow.leader_participant_id !== myParticipantId) return null;
        if (item.status === 'pending') return <button type="button" onClick={() => start.mutate(item)}><IconPlayerPlay size={13} />开始</button>;
        if (item.status === 'blocked') return <button type="button" onClick={() => unblock.mutate(item)}>恢复</button>;
        if (item.status === 'in_progress') return <><button type="button" onClick={() => { const ref = window.prompt('提交可复核的证据（链接、文件路径或测试结果）'); if (ref) evidence.mutate({ item, ref }); }}><IconCheck size={13} />交付证据</button><button type="button" className="quiet" onClick={() => { const reason = window.prompt('说明当前阻塞原因'); if (reason) block.mutate({ item, reason }); }}>阻塞</button></>;
        return null;
    };
    return <div className="group-workflow-tab">
        <div className="workflow-hero">
            <div><span className="workflow-eyebrow">{sourceLabel[workflow.source]}</span><h3>{workflow.name}</h3><p>{currentStage ? `当前阶段：${currentStage.title}` : '流程已完成'}</p></div>
            {isManager && <button type="button" className="group-icon-btn" title="管理工作流" onClick={() => setManageOpen(true)}><IconSettings size={16} /></button>}
            <div className="workflow-progress"><strong>{percent}%</strong><span>已完成</span><i><b style={{ width: `${percent}%` }} /></i></div>
        </div>
        <div className="workflow-stage-rail">{workflow.stages.map((stage, index) => <div className={`workflow-stage ${stage.status}`} key={stage.id}><span>{stage.status === 'completed' ? <IconCircleCheck size={13} /> : index + 1}</span><strong>{stage.title}</strong>{stage.requires_approval && <em>需确认</em>}</div>)}</div>
        {workflow.leader_next_action && <div className="workflow-leader-action"><IconArrowRight size={16} /><div><strong>群主下一步</strong><span>{String(workflow.leader_next_action.kind ?? '推进当前阶段').replace(/_/g, ' ')}</span></div></div>}
        {workflow.status === 'paused' && <div className="workflow-paused"><IconClock size={15} />流程已暂停，等待管理员恢复。</div>}
        {currentStage?.status === 'awaiting_approval' && <div className="workflow-gate"><IconAlertTriangle size={16} /><div><strong>{currentStage.title} 等待人工确认</strong><span>{currentStage.acceptance_criteria.join('；')}</span></div>{isManager && <button type="button" onClick={() => approve.mutate(currentStage.id)}>确认并进入下一阶段</button>}</div>}
        <div className="workflow-queues">{grouped.map(([status, items]) => <section key={status}><header>{stateLabel[status]} <span>{items.length}</span></header>{items.map((item) => <article className={`workflow-item ${item.status}`} key={item.id}><div><strong>{item.title}</strong><small>{memberName(item.assignee_participant_id)}</small></div><p>{item.description}</p>{item.blocked_reason && <div className="workflow-blocker"><IconAlertTriangle size={13} />{item.blocked_reason}</div>}{item.evidence.length > 0 && <div className="workflow-evidence">证据 {item.evidence.map((entry) => String(entry.ref ?? entry.url ?? '已提交')).join(' · ')}</div>}<footer>{action(item)}</footer></article>)}</section>)}</div>
        {manageOpen && <WorkflowManageModal groupId={groupId} workflow={workflow as GroupWorkflow} onClose={() => setManageOpen(false)} onChanged={refresh} />}
    </div>;
}
