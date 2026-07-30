import { IconChevronLeft, IconChevronRight } from '@tabler/icons-react';
import { useTranslation } from 'react-i18next';
import type { AIInteractionPage, AIInteractionSummary } from '../types/aiMonitoring';

const formatTokens = (value: number) => value >= 1_000_000 ? `${(value / 1_000_000).toFixed(1)}M` : value >= 1_000 ? `${(value / 1_000).toFixed(1)}K` : String(value || 0);
const formatTime = (value: string) => new Intl.DateTimeFormat(undefined, { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' }).format(new Date(value));

export default function AIInteractionList({
    data, loading, error, onSelect, onPage,
}: {
    data?: AIInteractionPage;
    loading?: boolean;
    error?: boolean;
    onSelect: (id: string) => void;
    onPage: (page: number) => void;
}) {
    const { t } = useTranslation();
    if (loading) return <div style={{ padding: '16px', color: 'var(--text-tertiary)', fontSize: '12px' }}>{t('common.loading')}</div>;
    if (error) return <div style={{ padding: '16px', color: 'var(--error)', fontSize: '12px' }}>{t('dashboard.aiMonitoring.loadFailed')}</div>;
    if (!data || data.interactions.length === 0) return <div style={{ padding: '16px', color: 'var(--text-tertiary)', fontSize: '12px' }}>{t('dashboard.aiMonitoring.empty')}</div>;
    const lastPage = Math.max(1, Math.ceil(data.total / data.page_size));
    return <>
        <div style={{ minWidth: '700px' }}>
            <div style={gridStyle}>
                <span>{t('dashboard.aiMonitoring.startedAt')}</span><span>{t('dashboard.aiMonitoring.finishedAt')}</span><span>{t('dashboard.aiMonitoring.agent')}</span><span>{t('dashboard.aiMonitoring.model')}</span><span>{t('dashboard.aiMonitoring.tokens')}</span><span>{t('dashboard.aiMonitoring.status')}</span>
            </div>
            {data.interactions.map((interaction) => <InteractionRow key={interaction.id} interaction={interaction} onSelect={onSelect} />)}
        </div>
        <footer style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '8px', padding: '10px 16px', borderTop: '1px solid var(--border-subtle)', color: 'var(--text-tertiary)', fontSize: '11px' }}>
            <span>{t('dashboard.aiMonitoring.records', { count: data.total })}</span>
            <div style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
                <button type="button" className="btn btn-ghost" disabled={data.page <= 1} onClick={() => onPage(data.page - 1)} aria-label={t('dashboard.aiMonitoring.previousPage')}><IconChevronLeft size={15} /></button>
                <span style={{ minWidth: '54px', textAlign: 'center', fontVariantNumeric: 'tabular-nums' }}>{data.page} / {lastPage}</span>
                <button type="button" className="btn btn-ghost" disabled={data.page >= lastPage} onClick={() => onPage(data.page + 1)} aria-label={t('dashboard.aiMonitoring.nextPage')}><IconChevronRight size={15} /></button>
            </div>
        </footer>
    </>;
}

const gridStyle = { display: 'grid', gridTemplateColumns: '132px 132px minmax(92px, 1fr) minmax(120px, 1fr) 88px 64px', gap: '10px', alignItems: 'center', padding: '9px 16px', fontSize: '10px', color: 'var(--text-tertiary)', textTransform: 'uppercase' } as const;

function InteractionRow({ interaction, onSelect }: { interaction: AIInteractionSummary; onSelect: (id: string) => void }) {
    const { t } = useTranslation();
    return <button type="button" onClick={() => onSelect(interaction.id)} style={{ ...gridStyle, width: '100%', border: 0, borderTop: '1px solid var(--border-subtle)', background: 'transparent', color: 'var(--text-secondary)', textAlign: 'left', textTransform: 'none', cursor: 'pointer', fontSize: '12px' }}>
        <span>{formatTime(interaction.started_at)}</span><span>{formatTime(interaction.finished_at)}</span>
        <span title={interaction.agent_name || undefined} style={truncate}>{interaction.agent_name || t('dashboard.aiMonitoring.platform')}</span>
        <span title={interaction.model_label || `${interaction.provider}/${interaction.model_name}`} style={truncate}>{interaction.model_label || `${interaction.provider}/${interaction.model_name}`}</span>
        <span>{formatTokens(interaction.total_tokens)}{interaction.token_source === 'estimated' ? ' · ~' : ''}</span>
        <span style={{ color: interaction.status === 'error' ? 'var(--error)' : 'var(--status-running)' }}>{interaction.status === 'error' ? t('dashboard.aiMonitoring.error') : t('dashboard.aiMonitoring.success')}</span>
    </button>;
}

const truncate = { minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' } as const;
