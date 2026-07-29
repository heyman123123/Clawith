import React, { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  BarChart, Bar, LineChart, Line, XAxis, YAxis, CartesianGrid,
  Tooltip, Legend, ResponsiveContainer,
} from 'recharts';
import { fetchJson } from '../services/api';
import { useToast } from '../components/Toast/ToastProvider';
import { ErrorBanner, LoadingState, PageHeader } from '../components/UI';

interface DashboardPayload {
  dates: string[];
  efficiency: {
    workflows_started: number[];
    workflows_succeeded: number[];
    workflows_failed: number[];
    steps_dispatched: number[];
  };
  quality: {
    steps_quality_passed: number[];
    steps_quality_failed: number[];
    steps_delivery_approved: number[];
    steps_delivery_rejected: number[];
    quality_score_avg: number[];
  };
  evolution: {
    evolution_events: number[];
    evolution_rollbacks: number[];
  };
  skill: {
    skill_learning_total: number[];
    skill_learning_approved: number[];
    skill_learning_rejected: number[];
    sandbox_runs_total: number[];
    sandbox_runs_blocked: number[];
  };
  cost: {
    tokens_input_total: number[];
    tokens_output_total: number[];
  };
}

interface LevelBucket {
  title: string;
  icon: string;
  cards: { title: string; value: string; delta: number }[];
  chart?: React.ReactNode;
}

const COLORS = ['#818cf8', '#22c55e', '#f59e0b', '#ef4444', '#a78bfa', '#06b6d4'];

const axisProps = {
  stroke: 'var(--text-tertiary)',
  tick: { fill: 'var(--text-tertiary)', fontSize: 11 },
};

const tooltipStyle = {
  background: 'var(--bg-elevated)',
  border: '1px solid var(--border-subtle)',
  borderRadius: 8,
  color: 'var(--text-primary)',
};

const MetricsDashboard: React.FC = () => {
  const { t } = useTranslation();
  const toast = useToast();
  const [payload, setPayload] = useState<DashboardPayload | null>(null);
  const [days, setDays] = useState(14);
  const [backfilling, setBackfilling] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  async function fetchDashboard() {
    setLoading(true);
    setError(null);
    try {
      const data = await fetchJson<DashboardPayload>(`/workflow-metrics/dashboard?days=${days}`);
      setPayload(data);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : 'failed to load';
      setError(msg);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    fetchDashboard();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [days]);

  const latestIdx = useMemo(() => {
    if (!payload) return -1;
    return payload.dates.length - 1;
  }, [payload]);

  const fmt = (n: number | null | undefined) => {
    if (n == null) return '-';
    if (n < 1000) return String(n);
    if (n < 1_000_000) return `${(n / 1000).toFixed(n < 10_000 ? 1 : 0)}K`;
    return `${(n / 1_000_000).toFixed(1)}M`;
  };

  const last = (arr: number[]) => (latestIdx >= 0 ? arr[latestIdx] : 0);
  const prev = (arr: number[]) => (latestIdx >= 1 ? arr[latestIdx - 1] : 0);
  const delta = (arr: number[]) => last(arr) - prev(arr);

  const chartData = useMemo(() => {
    if (!payload) return [] as Array<Record<string, number | string>>;
    return payload.dates.map((d, idx) => ({
      date: d.slice(5),
      workflows_succeeded: payload.efficiency.workflows_succeeded[idx],
      workflows_failed: payload.efficiency.workflows_failed[idx],
      avg_score: Number((payload.quality.quality_score_avg[idx] || 0).toFixed(1)),
      approval: payload.quality.steps_delivery_approved[idx],
      evolve: payload.evolution.evolution_events[idx],
      rollback: payload.evolution.evolution_rollbacks[idx],
      learning_total: payload.skill.skill_learning_total[idx],
      skill_blocked: payload.skill.sandbox_runs_blocked[idx],
      tokens_in: payload.cost.tokens_input_total[idx],
      tokens_out: payload.cost.tokens_output_total[idx],
    }));
  }, [payload]);

  const levels: LevelBucket[] = useMemo(() => {
    if (!payload) return [];

    const efficiency = {
      workflows_succeeded: payload.efficiency.workflows_succeeded,
      workflows_failed: payload.efficiency.workflows_failed,
      steps_dispatched: payload.efficiency.steps_dispatched,
    };
    const quality = {
      steps_quality_passed: payload.quality.steps_quality_passed,
      steps_quality_failed: payload.quality.steps_quality_failed,
      steps_delivery_approved: payload.quality.steps_delivery_approved,
      avg_score: payload.quality.quality_score_avg,
    };
    const evolution = {
      evolution_events: payload.evolution.evolution_events,
      evolution_rollbacks: payload.evolution.evolution_rollbacks,
    };
    const skill = {
      skill_learning_total: payload.skill.skill_learning_total,
      skill_learning_approved: payload.skill.skill_learning_approved,
      sandbox_runs_total: payload.skill.sandbox_runs_total,
      sandbox_runs_blocked: payload.skill.sandbox_runs_blocked,
    };
    const cost = {
      tokens_in: payload.cost.tokens_input_total,
      tokens_out: payload.cost.tokens_output_total,
    };

    return [
      {
        title: t('metrics.level1_title', '效率'),
        icon: '⚡',
        cards: [
          { title: t('metrics.succeeded', '已成功'), value: fmt(last(efficiency.workflows_succeeded)), delta: delta(efficiency.workflows_succeeded) },
          { title: t('metrics.failed', '失败'), value: fmt(last(efficiency.workflows_failed)), delta: delta(efficiency.workflows_failed) },
          { title: t('metrics.steps', '派发步骤'), value: fmt(last(efficiency.steps_dispatched)), delta: delta(efficiency.steps_dispatched) },
        ],
        chart: (
          <ResponsiveContainer width="100%" height={140}>
            <LineChart data={chartData}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--border-subtle)" />
              <XAxis dataKey="date" {...axisProps} />
              <YAxis {...axisProps} />
              <Tooltip contentStyle={tooltipStyle} />
              <Legend wrapperStyle={{ color: 'var(--text-secondary)', fontSize: 12 }} />
              <Line dataKey="workflows_succeeded" stroke={COLORS[1]} />
              <Line dataKey="workflows_failed" stroke={COLORS[3]} />
            </LineChart>
          </ResponsiveContainer>
        ),
      },
      {
        title: t('metrics.level2_title', '质量'),
        icon: '🛡️',
        cards: [
          { title: t('metrics.qc_passed', '步检通过'), value: fmt(last(quality.steps_quality_passed)), delta: delta(quality.steps_quality_passed) },
          { title: t('metrics.qc_failed', '步检不通过'), value: fmt(last(quality.steps_quality_failed)), delta: delta(quality.steps_quality_failed) },
          { title: t('metrics.delivery_approved', '交付批准'), value: fmt(last(quality.steps_delivery_approved)), delta: delta(quality.steps_delivery_approved) },
          { title: t('metrics.avg_score', '平均分'), value: `${(last(quality.avg_score) || 0).toFixed(1)}`, delta: 0 },
        ],
        chart: (
          <ResponsiveContainer width="100%" height={140}>
            <LineChart data={chartData}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--border-subtle)" />
              <XAxis dataKey="date" {...axisProps} />
              <YAxis domain={[0, 100]} {...axisProps} />
              <Tooltip contentStyle={tooltipStyle} />
              <Legend wrapperStyle={{ color: 'var(--text-secondary)', fontSize: 12 }} />
              <Line dataKey="avg_score" stroke={COLORS[2]} />
              <Line dataKey="approval" stroke={COLORS[1]} />
            </LineChart>
          </ResponsiveContainer>
        ),
      },
      {
        title: t('metrics.level3_title', '进化'),
        icon: '🌱',
        cards: [
          { title: t('metrics.evolve', '进化事件'), value: fmt(last(evolution.evolution_events)), delta: delta(evolution.evolution_events) },
          { title: t('metrics.rollback', '回滚'), value: fmt(last(evolution.evolution_rollbacks)), delta: delta(evolution.evolution_rollbacks) },
        ],
        chart: (
          <ResponsiveContainer width="100%" height={140}>
            <BarChart data={chartData}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--border-subtle)" />
              <XAxis dataKey="date" {...axisProps} />
              <YAxis {...axisProps} />
              <Tooltip contentStyle={tooltipStyle} />
              <Legend wrapperStyle={{ color: 'var(--text-secondary)', fontSize: 12 }} />
              <Bar dataKey="evolve" fill={COLORS[0]} />
              <Bar dataKey="rollback" fill={COLORS[3]} />
            </BarChart>
          </ResponsiveContainer>
        ),
      },
      {
        title: t('metrics.level4_title', '技能'),
        icon: '🛠️',
        cards: [
          { title: t('metrics.learning', '学习记录'), value: fmt(last(skill.skill_learning_total)), delta: delta(skill.skill_learning_total) },
          { title: t('metrics.learning_ok', '已批准'), value: fmt(last(skill.skill_learning_approved)), delta: delta(skill.skill_learning_approved) },
          { title: t('metrics.sandbox', '沙箱运行'), value: fmt(last(skill.sandbox_runs_total)), delta: delta(skill.sandbox_runs_total) },
          { title: t('metrics.sandbox_blocked', '沙箱拦截'), value: fmt(last(skill.sandbox_runs_blocked)), delta: delta(skill.sandbox_runs_blocked) },
        ],
        chart: (
          <ResponsiveContainer width="100%" height={140}>
            <LineChart data={chartData}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--border-subtle)" />
              <XAxis dataKey="date" {...axisProps} />
              <YAxis {...axisProps} />
              <Tooltip contentStyle={tooltipStyle} />
              <Legend wrapperStyle={{ color: 'var(--text-secondary)', fontSize: 12 }} />
              <Line dataKey="learning_total" stroke={COLORS[4]} />
              <Line dataKey="skill_blocked" stroke={COLORS[3]} />
            </LineChart>
          </ResponsiveContainer>
        ),
      },
      {
        title: t('metrics.level5_title', '成本'),
        icon: '💎',
        cards: [
          { title: t('metrics.tokens_in', '输入 Token'), value: fmt(last(cost.tokens_in)), delta: delta(cost.tokens_in) },
          { title: t('metrics.tokens_out', '输出 Token'), value: fmt(last(cost.tokens_out)), delta: delta(cost.tokens_out) },
        ],
        chart: (
          <ResponsiveContainer width="100%" height={140}>
            <BarChart data={chartData}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--border-subtle)" />
              <XAxis dataKey="date" {...axisProps} />
              <YAxis {...axisProps} />
              <Tooltip contentStyle={tooltipStyle} />
              <Legend wrapperStyle={{ color: 'var(--text-secondary)', fontSize: 12 }} />
              <Bar dataKey="tokens_in" fill={COLORS[0]} />
              <Bar dataKey="tokens_out" fill={COLORS[5]} />
            </BarChart>
          </ResponsiveContainer>
        ),
      },
    ];
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [payload, chartData, t, latestIdx]);

  async function triggerBackfill() {
    setBackfilling(true);
    try {
      await fetchJson('/workflow-metrics/backfill', { method: 'POST' });
      toast.success(t('metrics.backfill_ok', '回填完成'));
      await fetchDashboard();
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : 'failed';
      setError(msg);
      toast.error(msg);
    } finally {
      setBackfilling(false);
    }
  }

  return (
    <div className="ao-page">
      <PageHeader
        title={t('metrics.title', '五级仪表盘')}
        subtitle={t('metrics.subtitle', '效率 / 质量 / 进化 / 技能 / 成本')}
        actions={
          <div className="ao-inline-actions">
            <select value={days} onChange={(e) => setDays(Number(e.target.value))}>
              {[7, 14, 30, 60, 90].map((d) => (
                <option key={d} value={d}>
                  {d} {t('metrics.days', '天')}
                </option>
              ))}
            </select>
            <button
              type="button"
              onClick={triggerBackfill}
              disabled={backfilling}
              className="btn btn-primary"
            >
              {backfilling
                ? t('metrics.backfill_running', '回填中…')
                : t('metrics.backfill_now', '立即回填')}
            </button>
          </div>
        }
      />

      {error && (
        <div style={{ marginBottom: 12 }}>
          <ErrorBanner message={error} onRetry={fetchDashboard} tone="error" />
        </div>
      )}

      {loading && !payload ? (
        <LoadingState label={t('metrics.loading', '加载指标…')} rows={5} />
      ) : (
        <div className="ao-stack">
          {levels.map((level, idx) => (
            <section key={level.title} className="ao-panel">
              <header className="ao-toolbar" style={{ marginBottom: 12 }}>
                <h2 className="ao-section-title" style={{ margin: 0 }}>
                  <span style={{ marginRight: 8 }}>{level.icon}</span>
                  {idx + 1}. {level.title}
                </h2>
              </header>

              <div
                style={{
                  display: 'grid',
                  gridTemplateColumns: 'repeat(auto-fill, minmax(140px, 1fr))',
                  gap: 12,
                  marginBottom: 12,
                }}
              >
                {level.cards.map((card) => (
                  <div key={card.title} className="ao-stat">
                    <div className="ao-stat-label">{card.title}</div>
                    <div className="ao-stat-value">{card.value}</div>
                    {card.delta !== 0 && (
                      <div className={card.delta > 0 ? 'ao-delta-up' : 'ao-delta-down'}>
                        {card.delta > 0 ? '+' : ''}
                        {card.delta}
                      </div>
                    )}
                  </div>
                ))}
              </div>
              {level.chart}
            </section>
          ))}
        </div>
      )}
    </div>
  );
};

export default MetricsDashboard;
