import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { IconCheck, IconPlayerPause, IconPlayerPlay, IconSparkles, IconX } from '@tabler/icons-react';
import { groupWorkflowApi } from '../../services/groupWorkflowApi';
import type { GroupWorkflow } from '../../types/groupWorkflow';

interface WorkflowManageModalProps {
    groupId: string;
    workflow: GroupWorkflow;
    onClose: () => void;
    onChanged: () => void;
}

const PRESETS: { value: 'default' | 'agile' | 'product_research'; label: string; detail: string }[] = [
    { value: 'default', label: '协作推进', detail: '适用于跨角色的一般协作' },
    { value: 'agile', label: '敏捷需求', detail: '需求、排期、开发、验收、复盘' },
    { value: 'product_research', label: '产研协作', detail: '立项、方案、评审、实现、发布' },
];

export default function WorkflowManageModal({ groupId, workflow, onClose, onChanged }: WorkflowManageModalProps) {
    const client = useQueryClient();
    const [selected, setSelected] = useState<'default' | 'agile' | 'product_research'>(
        workflow.source === 'ai' ? 'default' : workflow.source,
    );
    const [request, setRequest] = useState('');
    const [draftId, setDraftId] = useState<string | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [page, setPage] = useState(1);
    const events = useQuery({
        queryKey: ['group-workflow-events', groupId, page],
        queryFn: () => groupWorkflowApi.events(groupId, page),
    });
    const changed = () => {
        void client.invalidateQueries({ queryKey: ['group-workflow', groupId] });
        void client.invalidateQueries({ queryKey: ['group-workflow-events', groupId] });
        onChanged();
    };
    const preset = useMutation({
        mutationFn: () => groupWorkflowApi.preset(groupId, selected),
        onSuccess: changed,
        onError: (value: Error) => setError(value.message),
    });
    const pause = useMutation({
        mutationFn: () => workflow.status === 'paused' ? groupWorkflowApi.resume(groupId) : groupWorkflowApi.pause(groupId),
        onSuccess: changed,
        onError: (value: Error) => setError(value.message),
    });
    const generate = useMutation({
        mutationFn: () => groupWorkflowApi.createDraft(groupId, request),
        onSuccess: (draft) => { setDraftId(draft.id); setError(draft.error_message); },
        onError: (value: Error) => setError(value.message),
    });
    const draft = useQuery({
        queryKey: ['group-workflow-draft', groupId, draftId],
        queryFn: () => groupWorkflowApi.draft(groupId, draftId!),
        enabled: Boolean(draftId),
        refetchInterval: (query) => query.state.data?.status === 'generating' ? 1000 : false,
    });
    const confirm = useMutation({
        mutationFn: () => groupWorkflowApi.confirmDraft(groupId, draftId!),
        onSuccess: changed,
        onError: (value: Error) => setError(value.message),
    });
    const busy = preset.isPending || pause.isPending || generate.isPending || confirm.isPending;

    return <div className="group-modal-backdrop" role="presentation" onMouseDown={onClose}>
        <section className="group-modal workflow-manage-modal" role="dialog" aria-modal="true" onMouseDown={(event) => event.stopPropagation()}>
            <header className="group-modal-header">
                <div>
                    <h3>工作流管理</h3>
                    <p className="workflow-manage-caption">变更流程会替换当前未完成的推进队列。</p>
                </div>
                <button type="button" className="group-icon-btn" onClick={onClose}><IconX size={16} /></button>
            </header>
            <div className="workflow-manage-content">
                <section className="workflow-manage-section">
                    <div className="workflow-manage-section-title">预设生命周期</div>
                    <div className="workflow-preset-grid">
                        {PRESETS.map((presetOption) => <button key={presetOption.value} type="button" className={`workflow-preset ${selected === presetOption.value ? 'selected' : ''}`} onClick={() => setSelected(presetOption.value)}>
                            <strong>{presetOption.label}</strong><span>{presetOption.detail}</span>
                        </button>)}
                    </div>
                    <button type="button" className="btn btn-secondary btn-sm" disabled={busy} onClick={() => preset.mutate()}>应用此模板</button>
                </section>
                <section className="workflow-manage-section">
                    <div className="workflow-manage-section-title"><IconSparkles size={14} /> AI 生成流程</div>
                    <textarea value={request} onChange={(event) => setRequest(event.target.value)} placeholder="描述此群的目标、交付方式与关键关口…" />
                    <button type="button" className="btn btn-secondary btn-sm" disabled={busy || !request.trim()} onClick={() => generate.mutate()}>生成草案</button>
                    {draft.data && <div className={`workflow-draft ${draft.data.status}`}>
                        <strong>{draft.data.status === 'ready' ? '草案已生成，尚未生效' : `草案状态：${draft.data.status}`}</strong>
                        {draft.data.plan && <pre>{JSON.stringify(draft.data.plan, null, 2)}</pre>}
                        {draft.data.status === 'ready' && <button type="button" className="btn btn-primary btn-sm" disabled={busy} onClick={() => confirm.mutate()}><IconCheck size={14} />确认并替换流程</button>}
                    </div>}
                </section>
                <section className="workflow-manage-section workflow-controls-row">
                    <div><div className="workflow-manage-section-title">流程状态</div><span>{workflow.status === 'paused' ? '流程已暂停' : '流程正在推进'}</span></div>
                    <button type="button" className="btn btn-secondary btn-sm" disabled={busy} onClick={() => pause.mutate()}>
                        {workflow.status === 'paused' ? <IconPlayerPlay size={14} /> : <IconPlayerPause size={14} />}{workflow.status === 'paused' ? '恢复' : '暂停'}
                    </button>
                </section>
                <section className="workflow-manage-section">
                    <div className="workflow-manage-section-title">生命周期记录</div>
                    <div className="workflow-event-list">{events.data?.items.map((event) => <div className="workflow-event" key={event.id}><span>{event.event_type.replace(/_/g, ' ')}</span><time>{new Date(event.created_at).toLocaleString()}</time></div>)}</div>
                    {(events.data?.total ?? 0) > (events.data?.page_size ?? 20) && <div className="workflow-pagination"><button type="button" disabled={page === 1} onClick={() => setPage((value) => value - 1)}>上一页</button><span>{page} / {Math.ceil((events.data?.total ?? 0) / (events.data?.page_size ?? 20))}</span><button type="button" disabled={page * (events.data?.page_size ?? 20) >= (events.data?.total ?? 0)} onClick={() => setPage((value) => value + 1)}>下一页</button></div>}
                </section>
                {error && <p className="workflow-form-error">{error}</p>}
            </div>
        </section>
    </div>;
}
