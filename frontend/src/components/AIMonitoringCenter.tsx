import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { IconChevronLeft, IconRefresh } from '@tabler/icons-react';
import { useTranslation } from 'react-i18next';
import { aiMonitoringApi, type AIMonitoringSortBy } from '../services/aiMonitoringApi';
import { useAuthStore } from '../stores';
import type { AIAgentStatsRow, AIGroupStatsRow } from '../types/aiMonitoring';
import AIInteractionDetailDrawer from './AIInteractionDetailDrawer';
import AIInteractionList from './AIInteractionList';

const PAGE_SIZE = 20;
const formatTokens = (value: number) => value >= 1_000_000 ? `${(value / 1_000_000).toFixed(1)}M` : value >= 1_000 ? `${(value / 1_000).toFixed(1)}K` : String(value || 0);
const utcToday = () => new Date().toISOString().slice(0, 10);

export function AIMonitoringCenter() {
    const { t } = useTranslation();
    const user = useAuthStore((state) => state.user);
    const isAdmin = user?.role === 'org_admin' || user?.role === 'platform_admin' || !!user?.is_platform_admin;
    const [rangeMode, setRangeMode] = useState<'24h' | 'day'>('24h');
    const [date, setDate] = useState(utcToday);
    const [sortBy, setSortBy] = useState<AIMonitoringSortBy>('failures');
    const [selectedGroup, setSelectedGroup] = useState<AIGroupStatsRow | null>(null);
    const [selectedAgent, setSelectedAgent] = useState<AIAgentStatsRow | null>(null);
    const [page, setPage] = useState(1);
    const [selectedId, setSelectedId] = useState<string | null>(null);

    const rangeFilters = {
        date: rangeMode === 'day' ? date : null,
        range: rangeMode === '24h' ? '24h' as const : null,
    };

    const groupStats = useQuery({
        queryKey: ['ai-monitoring-group-stats', user?.tenant_id, rangeMode, date, sortBy],
        queryFn: () => aiMonitoringApi.groupStats({ ...rangeFilters, sortBy, order: 'desc' }),
        enabled: isAdmin,
        refetchInterval: 15_000,
        staleTime: 10_000,
    });

    const agentStats = useQuery({
        queryKey: ['ai-monitoring-agent-stats', user?.tenant_id, selectedGroup?.group_id, rangeMode, date, sortBy],
        queryFn: () => aiMonitoringApi.agentStats({
            groupId: selectedGroup?.group_id,
            ...rangeFilters,
            sortBy,
            order: 'desc',
        }),
        enabled: isAdmin && !!selectedGroup?.group_id,
        refetchInterval: 15_000,
        staleTime: 10_000,
    });

    const interactions = useQuery({
        queryKey: ['ai-monitoring-group-agent-interactions', user?.tenant_id, selectedGroup?.group_id, selectedAgent?.agent_id, rangeMode, date, page],
        queryFn: () => aiMonitoringApi.groupInteractions(selectedGroup!.group_id!, page, PAGE_SIZE, {
            agentId: selectedAgent?.agent_id,
            unassigned: selectedAgent != null && selectedAgent.agent_id == null,
            ...rangeFilters,
        }),
        enabled: isAdmin && !!selectedGroup?.group_id && !!selectedAgent,
        refetchInterval: 15_000,
        staleTime: 10_000,
    });

    if (!isAdmin) return null;

    const headline = selectedAgent
        ? agentStats.data
        : selectedGroup
            ? agentStats.data
            : groupStats.data;

    const refresh = () => {
        void groupStats.refetch();
        if (selectedGroup) void agentStats.refetch();
        if (selectedAgent) void interactions.refetch();
    };

    const back = () => {
        if (selectedAgent) {
            setSelectedAgent(null);
            setSelectedId(null);
            setPage(1);
            return;
        }
        setSelectedGroup(null);
        setSelectedAgent(null);
        setSelectedId(null);
        setPage(1);
    };

    return <section style={{ border: '1px solid var(--border-subtle)', borderRadius: 'var(--radius-lg)', overflow: 'hidden', marginTop: '24px' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: '12px', alignItems: 'flex-start', padding: '14px 16px', borderBottom: '1px solid var(--border-subtle)' }}>
            <div>
                <h3 style={{ margin: 0, fontSize: '14px', fontWeight: 600, color: 'var(--text-primary)' }}>{t('dashboard.aiMonitoring.title')}</h3>
                <p style={{ margin: '4px 0 0', fontSize: '11px', color: 'var(--text-tertiary)' }}>{t('dashboard.aiMonitoring.retention')}</p>
            </div>
            <button type="button" className="btn btn-ghost" onClick={refresh} disabled={groupStats.isFetching || agentStats.isFetching || interactions.isFetching} style={{ display: 'inline-flex', alignItems: 'center', gap: '6px', fontSize: '12px' }}>
                <IconRefresh size={14} />
                {t('dashboard.aiMonitoring.refresh')}
            </button>
        </div>

        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px', alignItems: 'center', padding: '10px 16px', borderBottom: '1px solid var(--border-subtle)' }}>
            <button type="button" className={`btn ${rangeMode === '24h' ? 'btn-primary' : 'btn-ghost'}`} onClick={() => { setRangeMode('24h'); setPage(1); }} style={{ fontSize: '12px' }}>
                {t('dashboard.aiMonitoring.range24h')}
            </button>
            <button type="button" className={`btn ${rangeMode === 'day' ? 'btn-primary' : 'btn-ghost'}`} onClick={() => { setRangeMode('day'); setPage(1); }} style={{ fontSize: '12px' }}>
                {t('dashboard.aiMonitoring.rangeDay')}
            </button>
            {rangeMode === 'day' && (
                <input
                    type="date"
                    value={date}
                    onChange={(event) => { setDate(event.target.value); setPage(1); }}
                    style={{ fontSize: '12px', padding: '4px 8px', borderRadius: '6px', border: '1px solid var(--border-subtle)', background: 'transparent', color: 'var(--text-primary)' }}
                />
            )}
            <label style={{ marginLeft: 'auto', display: 'inline-flex', alignItems: 'center', gap: '6px', fontSize: '12px', color: 'var(--text-tertiary)' }}>
                {t('dashboard.aiMonitoring.sortBy')}
                <select
                    value={sortBy}
                    onChange={(event) => setSortBy(event.target.value as AIMonitoringSortBy)}
                    style={{ fontSize: '12px', padding: '4px 8px', borderRadius: '6px', border: '1px solid var(--border-subtle)', background: 'transparent', color: 'var(--text-primary)' }}
                >
                    <option value="failures">{t('dashboard.aiMonitoring.sortFailures')}</option>
                    <option value="tokens">{t('dashboard.aiMonitoring.sortTokens')}</option>
                    <option value="calls">{t('dashboard.aiMonitoring.sortCalls')}</option>
                </select>
            </label>
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', borderBottom: '1px solid var(--border-subtle)' }}>
            {[
                [t('dashboard.aiMonitoring.calls'), headline?.calls ?? 0],
                [t('dashboard.aiMonitoring.successes'), headline?.successes ?? 0],
                [t('dashboard.aiMonitoring.failures'), headline?.failures ?? 0],
                [t('dashboard.aiMonitoring.tokens'), formatTokens(headline?.total_tokens ?? 0)],
            ].map(([label, value]) => <div key={String(label)} style={{ padding: '12px 16px', borderRight: '1px solid var(--border-subtle)' }}>
                <div style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>{label}</div>
                <div style={{ marginTop: '3px', fontSize: '20px', fontWeight: 600, color: 'var(--text-primary)' }}>{value}</div>
            </div>)}
        </div>

        {(selectedGroup || selectedAgent) && (
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', padding: '9px 16px', fontSize: '12px', color: 'var(--text-secondary)', borderBottom: '1px solid var(--border-subtle)' }}>
                <button type="button" className="btn btn-ghost" onClick={back} style={{ display: 'inline-flex', alignItems: 'center', gap: '4px', fontSize: '12px', padding: '2px 6px' }}>
                    <IconChevronLeft size={14} />
                    {selectedAgent ? t('dashboard.aiMonitoring.backToAgents') : t('dashboard.aiMonitoring.backToGroups')}
                </button>
                <span style={{ color: 'var(--text-tertiary)' }}>/</span>
                <span>{selectedGroup?.group_name || t('dashboard.aiMonitoring.unknownGroup')}</span>
                {selectedAgent && <>
                    <span style={{ color: 'var(--text-tertiary)' }}>/</span>
                    <span>{selectedAgent.agent_name || t('dashboard.aiMonitoring.platform')}</span>
                    <span style={{ color: 'var(--text-tertiary)' }}>{t('dashboard.aiMonitoring.agentCalls')}</span>
                </>}
            </div>
        )}

        {selectedAgent ? (
            <div style={{ overflowX: 'auto' }}>
                <AIInteractionList data={interactions.data} loading={interactions.isLoading} error={interactions.isError} onSelect={setSelectedId} onPage={setPage} />
            </div>
        ) : selectedGroup ? (
            <>
                <div style={{ padding: '9px 16px', fontSize: '11px', color: 'var(--text-tertiary)' }}>{t('dashboard.aiMonitoring.agentSummary')}</div>
                <StatsTable
                    kind="agent"
                    rows={agentStats.data?.agents ?? []}
                    loading={agentStats.isLoading}
                    error={agentStats.isError}
                    onSelectAgent={(row) => { setSelectedAgent(row); setPage(1); }}
                />
            </>
        ) : (
            <>
                <div style={{ padding: '9px 16px', fontSize: '11px', color: 'var(--text-tertiary)' }}>{t('dashboard.aiMonitoring.groupSummary')}</div>
                <StatsTable
                    kind="group"
                    rows={groupStats.data?.groups ?? []}
                    loading={groupStats.isLoading}
                    error={groupStats.isError}
                    onSelectGroup={(row) => { setSelectedGroup(row); setSelectedAgent(null); setPage(1); }}
                />
            </>
        )}

        <AIInteractionDetailDrawer interactionId={selectedId} onClose={() => setSelectedId(null)} />
    </section>;
}

export function AIMonitoringStatsTable({
    kind, rows, loading, error, onSelectGroup, onSelectAgent, compact = false,
}: {
    kind: 'group' | 'agent';
    rows: Array<AIGroupStatsRow | AIAgentStatsRow>;
    loading?: boolean;
    error?: boolean;
    onSelectGroup?: (row: AIGroupStatsRow) => void;
    onSelectAgent?: (row: AIAgentStatsRow) => void;
    compact?: boolean;
}) {
    const { t } = useTranslation();
    if (loading) return <div style={{ padding: '16px', color: 'var(--text-tertiary)', fontSize: '12px' }}>{t('common.loading')}</div>;
    if (error) return <div style={{ padding: '16px', color: 'var(--error)', fontSize: '12px' }}>{t('dashboard.aiMonitoring.loadFailed')}</div>;
    if (rows.length === 0) return <div style={{ padding: '16px', color: 'var(--text-tertiary)', fontSize: '12px' }}>{t('dashboard.aiMonitoring.empty')}</div>;
    const nameHeader = kind === 'group' ? t('dashboard.aiMonitoring.group') : t('dashboard.aiMonitoring.agent');
    const columns = compact
        ? 'minmax(88px, 1.2fr) 48px 48px 48px 56px'
        : 'minmax(140px, 1.4fr) 72px 72px 72px 88px';
    const tableGridStyle = { ...gridStyle, gridTemplateColumns: columns, padding: compact ? '8px 12px' : gridStyle.padding };
    return <div style={{ overflowX: 'auto' }}>
        <div style={{ minWidth: compact ? '420px' : '640px' }}>
            <div style={tableGridStyle}>
                <span>{nameHeader}</span>
                <span>{t('dashboard.aiMonitoring.calls')}</span>
                <span>{t('dashboard.aiMonitoring.successes')}</span>
                <span>{t('dashboard.aiMonitoring.failures')}</span>
                <span>{t('dashboard.aiMonitoring.tokens')}</span>
            </div>
            {rows.map((row) => {
                const key = kind === 'group'
                    ? ((row as AIGroupStatsRow).group_id ?? 'group')
                    : ((row as AIAgentStatsRow).agent_id ?? 'platform');
                const name = kind === 'group'
                    ? ((row as AIGroupStatsRow).group_name || t('dashboard.aiMonitoring.unknownGroup'))
                    : ((row as AIAgentStatsRow).agent_name || t('dashboard.aiMonitoring.platform'));
                return (
                    <button
                        key={key}
                        type="button"
                        onClick={() => {
                            if (kind === 'group') onSelectGroup?.(row as AIGroupStatsRow);
                            else onSelectAgent?.(row as AIAgentStatsRow);
                        }}
                        style={{ ...tableGridStyle, width: '100%', border: 0, borderTop: '1px solid var(--border-subtle)', background: 'transparent', color: 'var(--text-secondary)', textAlign: 'left', textTransform: 'none', cursor: 'pointer', fontSize: '12px' }}
                    >
                        <span title={name} style={truncate}>{name}</span>
                        <span>{row.calls}</span>
                        <span>{row.successes}</span>
                        <span style={{ color: row.failures > 0 ? 'var(--error)' : undefined }}>{row.failures}</span>
                        <span>{formatTokens(row.total_tokens)}</span>
                    </button>
                );
            })}
        </div>
    </div>;
}

function StatsTable(props: {
    kind: 'group' | 'agent';
    rows: Array<AIGroupStatsRow | AIAgentStatsRow>;
    loading?: boolean;
    error?: boolean;
    onSelectGroup?: (row: AIGroupStatsRow) => void;
    onSelectAgent?: (row: AIAgentStatsRow) => void;
}) {
    return <AIMonitoringStatsTable {...props} />;
}

const gridStyle = {
    display: 'grid',
    gridTemplateColumns: 'minmax(140px, 1.4fr) 72px 72px 72px 88px',
    gap: '10px',
    alignItems: 'center',
    padding: '9px 16px',
    fontSize: '10px',
    color: 'var(--text-tertiary)',
    textTransform: 'uppercase',
} as const;

const truncate = { minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' } as const;
