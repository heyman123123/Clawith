import { useEffect } from 'react';
import { createPortal } from 'react-dom';
import { IconClock, IconX } from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { aiMonitoringApi } from '../services/aiMonitoringApi';

const formatTime = (value: string) => new Intl.DateTimeFormat(undefined, {
    dateStyle: 'medium', timeStyle: 'medium',
}).format(new Date(value));

export default function AIInteractionDetailDrawer({ interactionId, onClose }: {
    interactionId: string | null;
    onClose: () => void;
}) {
    const { t } = useTranslation();
    const detail = useQuery({
        queryKey: ['ai-monitoring-detail', interactionId],
        queryFn: () => aiMonitoringApi.detail(interactionId!),
        enabled: Boolean(interactionId),
    });

    useEffect(() => {
        if (!interactionId) return undefined;
        const closeOnEscape = (event: KeyboardEvent) => {
            if (event.key === 'Escape') onClose();
        };
        window.addEventListener('keydown', closeOnEscape);
        return () => window.removeEventListener('keydown', closeOnEscape);
    }, [interactionId, onClose]);

    if (!interactionId || typeof document === 'undefined') return null;

    return createPortal(
        <div
            role="presentation"
            onClick={onClose}
            style={{ position: 'fixed', inset: 0, zIndex: 10050, background: 'rgba(16, 22, 31, 0.28)' }}
        >
            <aside
                role="dialog"
                aria-modal="true"
                aria-label={t('dashboard.aiMonitoring.details')}
                onClick={(event) => event.stopPropagation()}
                style={{
                    position: 'absolute', top: 0, right: 0, height: '100%', width: 'min(560px, 100vw)',
                    display: 'flex', flexDirection: 'column', background: 'var(--bg-primary)',
                    borderLeft: '1px solid var(--border-default)', boxShadow: '-18px 0 48px rgba(16, 22, 31, 0.16)',
                }}
            >
                <header style={{ display: 'flex', justifyContent: 'space-between', gap: '12px', padding: '18px 20px', borderBottom: '1px solid var(--border-subtle)' }}>
                    <div>
                        <div style={{ color: 'var(--text-primary)', fontSize: '16px', fontWeight: 650 }}>{t('dashboard.aiMonitoring.details')}</div>
                        {detail.data && <div style={{ marginTop: '4px', color: 'var(--text-tertiary)', fontSize: '12px' }}>{detail.data.agent_name || t('dashboard.aiMonitoring.platform')} · {detail.data.model_label || `${detail.data.provider}/${detail.data.model_name}`}</div>}
                    </div>
                    <button type="button" className="btn btn-ghost" onClick={onClose} aria-label={t('common.close')}><IconX size={18} /></button>
                </header>
                <div style={{ overflow: 'auto', padding: '20px' }}>
                    {detail.isLoading && <div style={{ color: 'var(--text-tertiary)', fontSize: '13px' }}>{t('common.loading')}</div>}
                    {detail.isError && <div style={{ color: 'var(--error)', fontSize: '13px' }}>{t('dashboard.aiMonitoring.loadFailed')}</div>}
                    {detail.data && <>
                        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px', marginBottom: '20px' }}>
                            <TimeCard label={t('dashboard.aiMonitoring.startedAt')} value={formatTime(detail.data.started_at)} />
                            <TimeCard label={t('dashboard.aiMonitoring.finishedAt')} value={formatTime(detail.data.finished_at)} />
                        </div>
                        <DetailBlock label={t('dashboard.aiMonitoring.context')} value={JSON.stringify(detail.data.request_context, null, 2)} />
                        {detail.data.response_content && <DetailBlock label={t('dashboard.aiMonitoring.response')} value={detail.data.response_content} />}
                        {detail.data.error && <DetailBlock label={t('dashboard.aiMonitoring.errorDetails')} value={JSON.stringify(detail.data.error, null, 2)} error />}
                    </>}
                </div>
            </aside>
        </div>,
        document.body,
    );
}

function TimeCard({ label, value }: { label: string; value: string }) {
    return <div style={{ padding: '10px', border: '1px solid var(--border-subtle)', borderRadius: 'var(--radius-md)', background: 'var(--bg-secondary)' }}>
        <div style={{ display: 'flex', gap: '5px', alignItems: 'center', color: 'var(--text-tertiary)', fontSize: '10px', textTransform: 'uppercase', letterSpacing: '0.04em' }}><IconClock size={12} />{label}</div>
        <div style={{ marginTop: '5px', color: 'var(--text-primary)', fontSize: '12px', fontVariantNumeric: 'tabular-nums' }}>{value}</div>
    </div>;
}

function DetailBlock({ label, value, error = false }: { label: string; value: string; error?: boolean }) {
    return <section style={{ marginTop: '16px' }}>
        <div style={{ marginBottom: '6px', color: error ? 'var(--error)' : 'var(--text-tertiary)', fontSize: '11px', fontWeight: 600 }}>{label}</div>
        <pre style={{ maxHeight: '310px', overflow: 'auto', margin: 0, padding: '12px', borderRadius: 'var(--radius-md)', background: 'var(--bg-secondary)', color: 'var(--text-secondary)', fontSize: '11px', lineHeight: 1.55, whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{value}</pre>
    </section>;
}
