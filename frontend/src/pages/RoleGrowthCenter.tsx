import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { roleGrowthApi } from '../services/api';
import { useToast } from '../components/Toast/ToastProvider';
import { EmptyState, ErrorBanner, LoadingState, PageHeader } from '../components/UI';

interface EvolvedAgent {
  agent_id: string;
  name: string;
  role_description: string;
  current_version_no: number;
  current_source: string;
  quality_score: number | null;
  summary: string | null;
}

interface RoleVersion {
  id: string;
  version_no: number;
  source: string;
  quality_score: number | null;
  summary: string | null;
  soul_md_preview: string;
  is_current: boolean;
}

const RoleGrowthCenter: React.FC = () => {
  const { t } = useTranslation();
  const toast = useToast();
  const [agents, setAgents] = useState<EvolvedAgent[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [versions, setVersions] = useState<RoleVersion[]>([]);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [rollingBack, setRollingBack] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function loadAgents() {
    setLoading(true);
    setError(null);
    try {
      const data = await roleGrowthApi.listAgents();
      setAgents(data.items || []);
      if (!selectedId && data.items?.length) {
        setSelectedId(data.items[0].agent_id);
      }
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'failed to load');
    } finally {
      setLoading(false);
    }
  }

  async function loadVersions(agentId: string) {
    setDetailLoading(true);
    try {
      const data = await roleGrowthApi.listVersions(agentId);
      setVersions(data.versions || []);
    } catch (e: unknown) {
      toast.error(e instanceof Error ? e.message : 'failed to load versions');
      setVersions([]);
    } finally {
      setDetailLoading(false);
    }
  }

  useEffect(() => {
    loadAgents();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (selectedId) loadVersions(selectedId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId]);

  async function onRollback() {
    if (!selectedId) return;
    setRollingBack(true);
    try {
      await roleGrowthApi.rollback(selectedId, '一键回滚（角色成长中心）');
      toast.success(t('roleGrowth.rollbackOk', '已回滚到上一版本'));
      await loadAgents();
      await loadVersions(selectedId);
    } catch (e: unknown) {
      toast.error(e instanceof Error ? e.message : 'rollback failed');
    } finally {
      setRollingBack(false);
    }
  }

  const selected = agents.find((a) => a.agent_id === selectedId) || null;

  return (
    <div className="ao-page">
      <PageHeader
        title={t('roleGrowth.title', '角色成长中心')}
        subtitle={t(
          'roleGrowth.subtitle',
          '查看角色 soul 版本历史，支持一键回退到上一版本'
        )}
      />

      {error && (
        <div style={{ marginBottom: 12 }}>
          <ErrorBanner message={error} onRetry={loadAgents} />
        </div>
      )}
      {loading && <LoadingState label={t('common.loading', '加载中…')} />}

      {!loading && !error && agents.length === 0 && (
        <EmptyState
          title={t('roleGrowth.empty', '暂无进化记录')}
          description={t(
            'roleGrowth.emptyHint',
            '工作流角色在完成质检进化后会出现在这里'
          )}
        />
      )}

      {!loading && agents.length > 0 && (
        <div className="ao-split" style={{ marginTop: 8 }}>
          <aside className="ao-panel" style={{ padding: 0, overflow: 'hidden' }}>
            {agents.map((agent) => {
              const active = agent.agent_id === selectedId;
              return (
                <button
                  key={agent.agent_id}
                  type="button"
                  onClick={() => setSelectedId(agent.agent_id)}
                  className={`ao-list-item${active ? ' is-active' : ''}`}
                >
                  <div style={{ fontWeight: 600, fontSize: 14 }}>{agent.name}</div>
                  <div className="ao-page-subtle" style={{ marginTop: 4 }}>
                    v{agent.current_version_no} · {agent.current_source}
                    {agent.quality_score != null ? ` · 质量 ${agent.quality_score}` : ''}
                  </div>
                </button>
              );
            })}
          </aside>

          <section className="ao-panel" style={{ minHeight: 320 }}>
            {selected && (
              <div className="ao-toolbar" style={{ alignItems: 'flex-start' }}>
                <div>
                  <h2 className="ao-section-title" style={{ marginBottom: 4 }}>
                    {selected.name}
                  </h2>
                  <p className="ao-page-muted" style={{ margin: 0 }}>
                    {selected.role_description || '—'}
                  </p>
                </div>
                <button
                  type="button"
                  disabled={rollingBack || versions.length < 2}
                  onClick={onRollback}
                  className="btn btn-secondary"
                >
                  {rollingBack
                    ? t('roleGrowth.rollingBack', '回滚中…')
                    : t('roleGrowth.rollback', '回滚上一版')}
                </button>
              </div>
            )}

            {detailLoading && <LoadingState label={t('common.loading', '加载中…')} />}

            {!detailLoading &&
              versions.map((v) => (
                <div
                  key={v.id}
                  style={{
                    padding: '12px 0',
                    borderTop: '1px solid var(--border-subtle)',
                  }}
                >
                  <div className="ao-inline-actions" style={{ fontSize: 14, fontWeight: 600 }}>
                    <span>v{v.version_no}</span>
                    <span className="ao-page-muted" style={{ fontWeight: 500 }}>
                      {v.source}
                    </span>
                    {v.is_current && <span className="ao-badge">current</span>}
                    {v.quality_score != null && (
                      <span className="ao-page-subtle" style={{ marginLeft: 'auto' }}>
                        质量分 {v.quality_score}
                      </span>
                    )}
                  </div>
                  {v.summary && (
                    <p className="ao-page-muted" style={{ margin: '6px 0 0' }}>
                      {v.summary}
                    </p>
                  )}
                  {v.soul_md_preview && <pre className="ao-pre">{v.soul_md_preview}</pre>}
                </div>
              ))}
          </section>
        </div>
      )}
    </div>
  );
};

export default RoleGrowthCenter;
