import { useEffect, useMemo, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { IconBriefcase2, IconUsersGroup } from '@tabler/icons-react';
import HrProposalCard from '../components/HrProposalCard';
import { hrReviewApi } from '../services/hrReviewApi';
import { projectApi } from '../services/projectApi';
import type { CSSProperties } from 'react';
import type { HrTeamProposal, TeamPlanProposal } from '../types/project';
import './groups/groups.css';

type WizardStep = 'draft' | 'proposals_ready' | 'proposal_selected' | 'provisioned' | 'kickoff_sent';

function toHrProposals(proposals: TeamPlanProposal[]): HrTeamProposal[] {
    return proposals.map((proposal) => ({
        id: proposal.id,
        label: proposal.label,
        card_summary: proposal.card_summary || proposal.label,
        roles: proposal.roles.map((role) => ({
            ...role,
            duties: role.duties || role.role_description || '',
            soul: role.soul || '',
            suggested_tools: role.suggested_tools || [],
            suggested_permissions: role.suggested_permissions,
        })),
    }));
}

export default function Projects() {
    const { t } = useTranslation();
    const navigate = useNavigate();
    const queryClient = useQueryClient();
    const { data: projects = [] } = useQuery({ queryKey: ['projects'], queryFn: projectApi.list });
    const { data: shareholderGroup, isFetching: shareholderFetching } = useQuery({
        queryKey: ['shareholder-group'],
        queryFn: projectApi.shareholderGroup,
    });

    const [step, setStep] = useState<WizardStep>('draft');
    const [name, setName] = useState('');
    const [requirements, setRequirements] = useState('');
    const [hrSessionId, setHrSessionId] = useState<string | null>(null);
    const [proposals, setProposals] = useState<TeamPlanProposal[]>([]);
    const [selectedProposalId, setSelectedProposalId] = useState<string | null>(null);
    const [workflowId, setWorkflowId] = useState<string | null>(null);
    const [executionGroupId, setExecutionGroupId] = useState<string | null>(null);
    const [executionSessionId, setExecutionSessionId] = useState<string | null>(null);
    const [kickoffText, setKickoffText] = useState('');
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState('');

    useEffect(() => {
        let cancelled = false;
        const ensureShareholder = async () => {
            if (shareholderGroup) return;
            try {
                await projectApi.createShareholderGroup();
                if (!cancelled) {
                    await queryClient.invalidateQueries({ queryKey: ['shareholder-group'] });
                }
            } catch (reason) {
                console.error('Failed to provision shareholder group', reason);
            }
        };
        void ensureShareholder();
        return () => {
            cancelled = true;
        };
    }, [shareholderGroup, queryClient]);

    useEffect(() => {
        if (step !== 'provisioned' || !workflowId || kickoffText.trim()) return;
        let cancelled = false;
        const loadDraft = async () => {
            setBusy(true);
            setError('');
            try {
                const draft = await projectApi.kickoffDraft(workflowId);
                if (!cancelled) {
                    setKickoffText(draft.content);
                    setExecutionGroupId(draft.group_id);
                    setExecutionSessionId(draft.session_id);
                }
            } catch (reason) {
                if (!cancelled) {
                    setError(reason instanceof Error ? reason.message : '生成启动文案失败');
                }
            } finally {
                if (!cancelled) setBusy(false);
            }
        };
        void loadDraft();
        return () => {
            cancelled = true;
        };
    }, [step, workflowId, kickoffText]);

    const selectedProposal = useMemo(
        () => proposals.find((item) => item.id === selectedProposalId) || null,
        [proposals, selectedProposalId],
    );
    const cardProposals = useMemo(() => toHrProposals(proposals), [proposals]);

    const shareholderButtonLabel = shareholderGroup
        ? t('projects.enterShareholderGroup')
        : (shareholderFetching
            ? t('projects.shareholderGroupProvisioning')
            : t('projects.shareholderGroupProvisioning'));

    const resetWizard = () => {
        setStep('draft');
        setName('');
        setRequirements('');
        setHrSessionId(null);
        setProposals([]);
        setSelectedProposalId(null);
        setWorkflowId(null);
        setExecutionGroupId(null);
        setExecutionSessionId(null);
        setKickoffText('');
        setError('');
    };

    const generateProposals = async () => {
        if (!name.trim() || !requirements.trim() || busy) return;
        setBusy(true);
        setError('');
        try {
            const session = await projectApi.buildTeamPlan({
                name: name.trim(),
                requirements: requirements.trim(),
            });
            if (!session.proposals || session.proposals.length < 3) {
                throw new Error('生成的方案不足 3 套，请重试');
            }
            setHrSessionId(session.hr_review_session_id);
            setProposals(session.proposals);
            setSelectedProposalId(null);
            setStep('proposals_ready');
        } catch (reason) {
            setError(reason instanceof Error ? reason.message : '生成方案失败');
        } finally {
            setBusy(false);
        }
    };

    const selectProposalLocally = async (proposalId: string) => {
        setSelectedProposalId(proposalId);
        setStep('proposal_selected');
        setError('');
    };

    const createTeam = async () => {
        if (!hrSessionId || !selectedProposalId || busy) return;
        setBusy(true);
        setError('');
        try {
            const selection = await hrReviewApi.selectProposal(hrSessionId, selectedProposalId, {
                send_kickoff: false,
            });
            if (!selection.workflow_id || !selection.group_id || !selection.session_id) {
                throw new Error('团队已创建，但未返回执行群信息');
            }
            setWorkflowId(selection.workflow_id);
            setExecutionGroupId(selection.group_id);
            setExecutionSessionId(selection.session_id);
            setKickoffText('');
            setStep('provisioned');
            await queryClient.invalidateQueries({ queryKey: ['projects'] });
        } catch (reason) {
            setError(reason instanceof Error ? reason.message : '创建团队失败');
        } finally {
            setBusy(false);
        }
    };

    const regenerateKickoff = async () => {
        if (!workflowId || busy) return;
        setBusy(true);
        setError('');
        try {
            const draft = await projectApi.kickoffDraft(workflowId);
            setKickoffText(draft.content);
            setExecutionGroupId(draft.group_id);
            setExecutionSessionId(draft.session_id);
        } catch (reason) {
            setError(reason instanceof Error ? reason.message : '重新生成文案失败');
        } finally {
            setBusy(false);
        }
    };

    const sendKickoff = async () => {
        if (!workflowId || busy) return;
        setBusy(true);
        setError('');
        try {
            let content = kickoffText.trim();
            if (!content) {
                const draft = await projectApi.kickoffDraft(workflowId);
                content = draft.content;
                setKickoffText(content);
            }
            const result = await projectApi.kickoffSend(workflowId, content);
            setStep('kickoff_sent');
            await queryClient.invalidateQueries({ queryKey: ['projects'] });
            navigate(`/groups/${result.group_id}/${result.session_id}`);
        } catch (reason) {
            setError(reason instanceof Error ? reason.message : '发送启动消息失败');
        } finally {
            setBusy(false);
        }
    };

    const continueKickoff = (projectId: string, groupId: string | null) => {
        setWorkflowId(projectId);
        setExecutionGroupId(groupId);
        setExecutionSessionId(null);
        setKickoffText('');
        setStep('provisioned');
        setError('');
        window.scrollTo({ top: 0, behavior: 'smooth' });
    };

    return (
        <div style={{ maxWidth: 1080, margin: '0 auto', padding: '36px 32px 56px' }}>
            <div style={{ marginBottom: 28 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, color: 'var(--accent, #635bff)' }}>
                    <IconBriefcase2 size={24} />
                    <span style={{ fontWeight: 700 }}>需求与执行</span>
                </div>
                <h1 style={{ margin: '10px 0 8px', fontSize: 28 }}>填写需求 → 生成方案 → 确认创建 → 启动</h1>
                <p style={{ margin: 0, color: 'var(--text-secondary, #6b7280)' }}>
                    用表单完成组队：确认创建后可编辑启动文案，再以你的身份 @群主 发送到执行群。
                </p>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10, marginTop: 14 }}>
                    <button
                        type="button"
                        onClick={() => shareholderGroup && navigate(`/groups/${shareholderGroup.group_id}`)}
                        disabled={!shareholderGroup}
                        style={secondaryStyle}
                    >
                        <IconUsersGroup size={15} />
                        {shareholderButtonLabel}
                    </button>
                    {(step !== 'draft' || name || requirements) && (
                        <button type="button" onClick={resetWizard} style={secondaryStyle}>
                            重新开始
                        </button>
                    )}
                </div>
            </div>

            <section style={{ ...cardStyle, marginBottom: 24 }}>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: 16, fontSize: 13, color: 'var(--text-secondary, #6b7280)' }}>
                    {['填写需求', '确认方案', '创建团队', '启动'].map((label, index) => {
                        const activeIndex =
                            step === 'draft' ? 0
                                : step === 'proposals_ready' ? 1
                                    : step === 'proposal_selected' ? 2
                                        : 3;
                        return (
                            <span
                                key={label}
                                style={{
                                    padding: '4px 10px',
                                    borderRadius: 999,
                                    background: index <= activeIndex ? 'var(--accent, #635bff)' : 'transparent',
                                    color: index <= activeIndex ? '#fff' : 'inherit',
                                    border: '1px solid var(--border, #d1d5db)',
                                    fontWeight: index === activeIndex ? 700 : 500,
                                }}
                            >
                                {index + 1}. {label}
                            </span>
                        );
                    })}
                </div>

                {step === 'draft' && (
                    <div style={{ display: 'grid', gap: 12 }}>
                        <h2 style={headingStyle}>1. 填写需求</h2>
                        <label style={labelStyle}>
                            项目名称
                            <input
                                value={name}
                                onChange={(event) => setName(event.target.value)}
                                placeholder="例如：跨境电商 Shopify 建站"
                                style={inputStyle}
                            />
                        </label>
                        <label style={labelStyle}>
                            需求描述
                            <textarea
                                value={requirements}
                                onChange={(event) => setRequirements(event.target.value)}
                                placeholder="描述目标、交付物、约束与期望角色"
                                rows={8}
                                style={{ ...inputStyle, resize: 'vertical' }}
                            />
                        </label>
                        <button
                            type="button"
                            onClick={() => void generateProposals()}
                            disabled={busy || !name.trim() || !requirements.trim()}
                            style={primaryStyle}
                        >
                            {busy ? '正在生成方案…' : '生成方案'}
                        </button>
                    </div>
                )}

                {(step === 'proposals_ready' || step === 'proposal_selected') && (
                    <div>
                        <h2 style={headingStyle}>2. 确认方案</h2>
                        <p style={{ color: 'var(--text-secondary, #6b7280)', marginTop: 0 }}>
                            项目「{name}」· 选择一套组建方案后进入创建。
                        </p>
                        <HrProposalCard
                            proposals={cardProposals}
                            disabled={busy}
                            onConfirm={selectProposalLocally}
                        />
                        {step === 'proposal_selected' && selectedProposal && (
                            <div style={{ marginTop: 18, borderTop: '1px solid var(--border, #e5e7eb)', paddingTop: 16 }}>
                                <h2 style={headingStyle}>3. 确认创建</h2>
                                <p style={{ margin: '0 0 12px', color: 'var(--text-secondary, #6b7280)' }}>
                                    已选「{selectedProposal.label}」，共 {selectedProposal.roles.length} 个角色。
                                    创建后会生成智能体与执行群，但不会自动发启动消息。
                                </p>
                                <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
                                    <button type="button" onClick={() => setStep('proposals_ready')} style={secondaryStyle} disabled={busy}>
                                        返回重选
                                    </button>
                                    <button type="button" onClick={() => void createTeam()} style={primaryStyle} disabled={busy}>
                                        {busy ? '正在创建团队…' : '创建团队'}
                                    </button>
                                </div>
                            </div>
                        )}
                    </div>
                )}

                {(step === 'provisioned' || step === 'kickoff_sent') && (
                    <div style={{ display: 'grid', gap: 12 }}>
                        <h2 style={headingStyle}>4. 启动团队</h2>
                        <p style={{ margin: 0, color: 'var(--text-secondary, #6b7280)' }}>
                            可编辑下方文案。点击「生成并发送」将以你的身份发到执行群，并 @群主。
                        </p>
                        <textarea
                            value={kickoffText}
                            onChange={(event) => setKickoffText(event.target.value)}
                            rows={12}
                            placeholder={busy ? '正在生成启动文案…' : '启动文案'}
                            style={{ ...inputStyle, resize: 'vertical', fontFamily: 'inherit' }}
                        />
                        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
                            <button type="button" onClick={() => void regenerateKickoff()} style={secondaryStyle} disabled={busy || !workflowId}>
                                重新生成文案
                            </button>
                            <button type="button" onClick={() => void sendKickoff()} style={primaryStyle} disabled={busy || !workflowId || step === 'kickoff_sent'}>
                                {busy ? '处理中…' : '生成并发送'}
                            </button>
                            {executionGroupId && executionSessionId && (
                                <button
                                    type="button"
                                    onClick={() => navigate(`/groups/${executionGroupId}/${executionSessionId}`)}
                                    style={secondaryStyle}
                                >
                                    打开执行群
                                </button>
                            )}
                        </div>
                    </div>
                )}

                {error && (
                    <p style={{ color: '#b91c1c', margin: '14px 0 0' }}>{error}</p>
                )}
            </section>

            {projects.length > 0 && (
                <section style={cardStyle}>
                    <h2 style={headingStyle}>执行中的需求</h2>
                    <p style={{ color: 'var(--text-secondary, #6b7280)', margin: '0 0 16px' }}>
                        已确认方案并进入执行阶段的工作流。
                    </p>
                    {projects.map((project) => (
                        <div
                            key={project.id}
                            style={{
                                display: 'flex',
                                justifyContent: 'space-between',
                                gap: 12,
                                borderTop: '1px solid var(--border, #e5e7eb)',
                                padding: '13px 0',
                            }}
                        >
                            <div>
                                <strong>{project.name}</strong>
                                <div style={{ color: 'var(--text-secondary, #6b7280)', fontSize: 13, marginTop: 4 }}>
                                    {project.members.length} 位成员 · {project.status === 'active' ? '治理与团队已就绪' : project.status}
                                    {project.failure_reason ? ` · ${project.failure_reason}` : ''}
                                    {project.status === 'active' && !project.kickoff_sent_at ? ' · 待启动' : ''}
                                </div>
                            </div>
                            <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', justifyContent: 'end', gap: 8 }}>
                                {project.status === 'active' && !project.kickoff_sent_at && (
                                    <button
                                        type="button"
                                        onClick={() => continueKickoff(project.id, project.group_id)}
                                        style={primaryStyle}
                                    >
                                        继续启动
                                    </button>
                                )}
                                {project.decision_group_id && (
                                    <button type="button" onClick={() => navigate(`/groups/${project.decision_group_id}`)} style={secondaryStyle}>
                                        决策群
                                    </button>
                                )}
                                {project.group_id && (
                                    <button type="button" onClick={() => navigate(`/groups/${project.group_id}`)} style={primaryStyle}>
                                        执行群
                                    </button>
                                )}
                            </div>
                        </div>
                    ))}
                </section>
            )}
        </div>
    );
}

const cardStyle: CSSProperties = { background: 'var(--bg-card, #fff)', border: '1px solid var(--border, #e5e7eb)', borderRadius: 14, padding: 22, boxShadow: '0 1px 2px rgba(0,0,0,.03)' };
const headingStyle: CSSProperties = { margin: '0 0 10px', fontSize: 18 };
const labelStyle: CSSProperties = { display: 'grid', gap: 6, fontSize: 14, fontWeight: 600 };
const inputStyle: CSSProperties = {
    width: '100%',
    boxSizing: 'border-box',
    border: '1px solid var(--border, #d1d5db)',
    borderRadius: 8,
    padding: '10px 12px',
    fontSize: 14,
    fontWeight: 400,
    background: 'var(--bg-input, #fff)',
    color: 'inherit',
};
const primaryStyle: CSSProperties = { display: 'inline-flex', alignItems: 'center', gap: 8, border: 0, borderRadius: 8, padding: '10px 14px', color: '#fff', background: 'var(--accent, #635bff)', fontWeight: 650, cursor: 'pointer' };
const secondaryStyle: CSSProperties = { display: 'inline-flex', alignItems: 'center', gap: 8, border: '1px solid var(--border, #d1d5db)', borderRadius: 8, padding: '7px 10px', background: 'transparent', color: 'inherit', cursor: 'pointer' };
