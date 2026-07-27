import { useEffect, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { IconX } from '@tabler/icons-react';
import MarkdownRenderer from './MarkdownRenderer';
import type { HrTeamProposal } from '../types/project';
import { HrProposalLeaderBadge } from './HrProposalCard';

interface HrProposalModalProps {
    proposal: HrTeamProposal;
    confirming?: boolean;
    disabled?: boolean;
    onClose: () => void;
    onConfirm: () => void;
}

export default function HrProposalModal({
    proposal,
    confirming = false,
    disabled = false,
    onConfirm,
    onClose,
}: HrProposalModalProps) {
    const { t } = useTranslation();
    const confirmRef = useRef<HTMLButtonElement>(null);

    useEffect(() => {
        const onKey = (event: KeyboardEvent) => {
            if (event.key === 'Escape' && !confirming) onClose();
        };
        window.addEventListener('keydown', onKey);
        return () => window.removeEventListener('keydown', onKey);
    }, [confirming, onClose]);

    useEffect(() => {
        setTimeout(() => confirmRef.current?.focus(), 100);
    }, []);

    return (
        <div
            className="hr-proposal-modal-overlay"
            onClick={(event) => {
                if (event.target === event.currentTarget && !confirming) onClose();
            }}
        >
            <div className="hr-proposal-modal" role="dialog" aria-modal="true" aria-labelledby="hr-proposal-modal-title">
                <header className="hr-proposal-modal-header">
                    <div>
                        <h3 id="hr-proposal-modal-title">{proposal.label}</h3>
                        <p>{proposal.card_summary}</p>
                    </div>
                    <button
                        type="button"
                        className="btn btn-ghost hr-proposal-modal-close"
                        disabled={confirming}
                        onClick={onClose}
                        aria-label={t('common.close', '关闭')}
                    >
                        <IconX size={18} stroke={1.8} />
                    </button>
                </header>

                <div className="hr-proposal-modal-body">
                    {proposal.roles.map((role) => (
                        <section key={role.key} className="hr-proposal-role">
                            <div className="hr-proposal-role-head">
                                <strong>{role.name}</strong>
                                {role.is_group_leader && <HrProposalLeaderBadge />}
                            </div>

                            {role.duties && (
                                <div className="hr-proposal-role-block">
                                    <h4>{t('hrProposal.duties', '职责')}</h4>
                                    <p>{role.duties}</p>
                                </div>
                            )}

                            {role.soul && (
                                <div className="hr-proposal-role-block">
                                    <h4>{t('hrProposal.soul', 'Soul')}</h4>
                                    <div className="hr-proposal-soul">
                                        <MarkdownRenderer content={role.soul} />
                                    </div>
                                </div>
                            )}

                            {role.suggested_tools && role.suggested_tools.length > 0 && (
                                <div className="hr-proposal-role-block">
                                    <h4>{t('hrProposal.tools', '建议工具')}</h4>
                                    <ul className="hr-proposal-tool-list">
                                        {role.suggested_tools.map((tool) => (
                                            <li key={tool}>{tool}</li>
                                        ))}
                                    </ul>
                                </div>
                            )}

                            {role.suggested_permissions && Object.keys(role.suggested_permissions).length > 0 && (
                                <div className="hr-proposal-role-block">
                                    <h4>{t('hrProposal.permissions', '建议权限')}</h4>
                                    <pre className="hr-proposal-permissions">
                                        {JSON.stringify(role.suggested_permissions, null, 2)}
                                    </pre>
                                </div>
                            )}
                        </section>
                    ))}
                </div>

                <footer className="hr-proposal-modal-footer">
                    <button type="button" className="btn btn-secondary" disabled={confirming} onClick={onClose}>
                        {t('common.confirmActions.cancelLabel', '取消')}
                    </button>
                    <button
                        ref={confirmRef}
                        type="button"
                        className="btn btn-primary"
                        disabled={disabled || confirming}
                        onClick={onConfirm}
                    >
                        {confirming
                            ? t('hrProposal.confirming', '正在确认…')
                            : t('hrProposal.confirm', '确认此方案')}
                    </button>
                </footer>
            </div>
        </div>
    );
}
