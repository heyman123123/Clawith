import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { IconCheck, IconUsersGroup } from '@tabler/icons-react';
import type { HrTeamProposal } from '../types/project';
import HrProposalModal from './HrProposalModal';

interface HrProposalCardProps {
    proposals: HrTeamProposal[];
    disabled?: boolean;
    onConfirm: (proposalId: string) => Promise<void>;
}

export default function HrProposalCard({
    proposals,
    disabled = false,
    onConfirm,
}: HrProposalCardProps) {
    const { t } = useTranslation();
    const [detailProposal, setDetailProposal] = useState<HrTeamProposal | null>(null);
    const [confirmingId, setConfirmingId] = useState<string | null>(null);
    const [error, setError] = useState('');

    const handleConfirm = async (proposalId: string) => {
        if (disabled || confirmingId) return;
        setConfirmingId(proposalId);
        setError('');
        try {
            await onConfirm(proposalId);
            setDetailProposal(null);
        } catch (reason) {
            setError(reason instanceof Error ? reason.message : t('hrProposal.confirmFailed', '方案确认失败，请稍后重试。'));
        } finally {
            setConfirmingId(null);
        }
    };

    return (
        <div className="hr-proposal-card-stack">
            <div className="hr-proposal-card-head">
                <IconUsersGroup size={16} stroke={1.7} />
                <strong>{t('hrProposal.title', '团队组建方案')}</strong>
                <span className="hr-proposal-card-count">{proposals.length}</span>
            </div>
            <p className="hr-proposal-card-hint">
                {t('hrProposal.hint', '请选择一套方案；点击「查看详情」可预览完整 soul 与工具配置。')}
            </p>
            <div className="hr-proposal-card-grid">
                {proposals.map((proposal) => {
                    const roleNames = proposal.roles.map((role) => role.name).join('、');
                    const isConfirming = confirmingId === proposal.id;
                    const cardDisabled = disabled || Boolean(confirmingId);
                    return (
                        <article key={proposal.id} className="hr-proposal-card">
                            <div className="hr-proposal-card-top">
                                <strong>{proposal.label}</strong>
                                <span className="hr-proposal-card-tag">
                                    {proposal.roles.length} {t('hrProposal.members', '位成员')}
                                </span>
                            </div>
                            <p className="hr-proposal-card-summary">{proposal.card_summary}</p>
                            <p className="hr-proposal-card-roles">{roleNames}</p>
                            <div className="hr-proposal-card-actions">
                                <button
                                    type="button"
                                    className="btn btn-ghost hr-proposal-card-detail-btn"
                                    disabled={cardDisabled}
                                    onClick={() => setDetailProposal(proposal)}
                                >
                                    {t('hrProposal.viewDetails', '查看详情')}
                                </button>
                                <button
                                    type="button"
                                    className="btn btn-primary hr-proposal-card-confirm-btn"
                                    disabled={cardDisabled}
                                    onClick={() => void handleConfirm(proposal.id)}
                                >
                                    {isConfirming
                                        ? t('hrProposal.confirming', '正在确认…')
                                        : t('hrProposal.confirm', '确认此方案')}
                                </button>
                            </div>
                        </article>
                    );
                })}
            </div>
            {error && <p className="hr-proposal-card-error">{error}</p>}
            {detailProposal && (
                <HrProposalModal
                    proposal={detailProposal}
                    confirming={confirmingId === detailProposal.id}
                    disabled={disabled || Boolean(confirmingId)}
                    onClose={() => setDetailProposal(null)}
                    onConfirm={() => void handleConfirm(detailProposal.id)}
                />
            )}
        </div>
    );
}

export function HrProposalLeaderBadge() {
    const { t } = useTranslation();
    return (
        <span className="hr-proposal-leader-badge">
            <IconCheck size={13} stroke={2} />
            {t('hrProposal.groupLeader', '项目群主')}
        </span>
    );
}
