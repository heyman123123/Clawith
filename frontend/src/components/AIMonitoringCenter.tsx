import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { aiMonitoringApi } from '../services/aiMonitoringApi';
import { useAuthStore } from '../stores';
import AIInteractionDetailDrawer from './AIInteractionDetailDrawer';
import AIInteractionList from './AIInteractionList';

const PAGE_SIZE = 20;
const formatTokens = (value: number) => value >= 1_000_000 ? `${(value / 1_000_000).toFixed(1)}M` : value >= 1_000 ? `${(value / 1_000).toFixed(1)}K` : String(value || 0);

export function AIMonitoringCenter() {
    const { t } = useTranslation();
    const user = useAuthStore((state) => state.user);
    const [page, setPage] = useState(1);
    const [selectedId, setSelectedId] = useState<string | null>(null);
    const isAdmin = user?.role === 'org_admin' || user?.role === 'platform_admin' || !!user?.is_platform_admin;
    const overview = useQuery({
        queryKey: ['ai-monitoring-overview', user?.tenant_id, page],
        queryFn: () => aiMonitoringApi.overview(page, PAGE_SIZE),
        enabled: isAdmin,
        refetchInterval: 15_000,
        staleTime: 10_000,
    });

    if (!isAdmin) return null;

    return <section style={{ border: '1px solid var(--border-subtle)', borderRadius: 'var(--radius-lg)', overflow: 'hidden', marginTop: '24px' }}>
        <div style={{ padding: '14px 16px', borderBottom: '1px solid var(--border-subtle)' }}>
            <h3 style={{ margin: 0, fontSize: '14px', fontWeight: 600, color: 'var(--text-primary)' }}>{t('dashboard.aiMonitoring.title')}</h3>
            <p style={{ margin: '4px 0 0', fontSize: '11px', color: 'var(--text-tertiary)' }}>{t('dashboard.aiMonitoring.retention')}</p>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', borderBottom: '1px solid var(--border-subtle)' }}>
            {[
                [t('dashboard.aiMonitoring.calls24h'), overview.data?.calls_24h ?? 0],
                [t('dashboard.aiMonitoring.tokens24h'), formatTokens(overview.data?.total_tokens_24h ?? 0)],
                [t('dashboard.aiMonitoring.errors24h'), overview.data?.errors_24h ?? 0],
            ].map(([label, value]) => <div key={String(label)} style={{ padding: '12px 16px', borderRight: '1px solid var(--border-subtle)' }}>
                <div style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>{label}</div>
                <div style={{ marginTop: '3px', fontSize: '20px', fontWeight: 600, color: 'var(--text-primary)' }}>{value}</div>
            </div>)}
        </div>
        <div style={{ padding: '9px 16px', fontSize: '11px', color: 'var(--text-tertiary)' }}>{t('dashboard.aiMonitoring.recentCalls')}</div>
        <div style={{ overflowX: 'auto' }}>
            <AIInteractionList data={overview.data} loading={overview.isLoading} error={overview.isError} onSelect={setSelectedId} onPage={setPage} />
        </div>
        <AIInteractionDetailDrawer interactionId={selectedId} onClose={() => setSelectedId(null)} />
    </section>;
}
