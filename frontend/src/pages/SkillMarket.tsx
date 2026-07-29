import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { fetchJson } from '../services/api';
import { useToast } from '../components/Toast/ToastProvider';
import { EmptyState, ErrorBanner, LoadingState, PageHeader } from '../components/UI';

interface Listing {
  listing_id: string;
  skill_id: string;
  title: string;
  summary: string;
  keywords: string[];
  risk_level: 'low' | 'medium' | 'high';
  status: 'draft' | 'in_review' | 'published' | 'disabled' | 'rejected';
  share_scope: 'private' | 'team' | 'company';
  install_count: number;
  published_at: string | null;
}

interface SandboxRun {
  run_id: string;
  status: string;
  exit_code: number | null;
  duration_ms: number | null;
  stdout: string;
  stderr: string;
  error: string | null;
  risk_level: string;
  requires_human_review: boolean;
  rationale: string;
}

const riskTone = (level: string) =>
  level === 'high' ? 'ao-tone-error' : level === 'medium' ? 'ao-tone-warning' : 'ao-tone-success';

const SkillMarket: React.FC = () => {
  const { t } = useTranslation();
  const toast = useToast();
  const [listings, setListings] = useState<Listing[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [codeDraft, setCodeDraft] = useState('');
  const [language, setLanguage] = useState<'python' | 'bash' | 'node'>('python');
  const [allowNetwork, setAllowNetwork] = useState(false);
  const [sandboxRunning, setSandboxRunning] = useState(false);
  const [sandboxError, setSandboxError] = useState<string | null>(null);
  const [sandboxResult, setSandboxResult] = useState<SandboxRun | null>(null);
  const [approvalNote, setApprovalNote] = useState('');

  async function fetchListings() {
    setLoading(true);
    setError(null);
    try {
      const data = await fetchJson<Listing[]>('/skill-market');
      setListings(data || []);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : 'failed';
      setError(msg);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    fetchListings();
  }, []);

  async function runSandbox(listingId: string) {
    setSandboxRunning(true);
    setSandboxError(null);
    setSandboxResult(null);
    try {
      const data = await fetchJson<SandboxRun>(`/skill-market/${listingId}/sandbox`, {
        method: 'POST',
        body: JSON.stringify({
          code: codeDraft,
          language,
          timeout: 30,
          allow_network: allowNetwork,
        }),
      });
      setSandboxResult(data);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : 'sandbox failed';
      setSandboxError(msg);
    } finally {
      setSandboxRunning(false);
    }
  }

  async function openApproval(listingId: string) {
    if (!sandboxResult) {
      const msg = t('skill.run_sandbox_first', '请先跑通沙箱再申请审批');
      setSandboxError(msg);
      toast.warning(msg);
      return;
    }
    try {
      await fetchJson(`/skill-market/${listingId}/request-approval`, {
        method: 'POST',
        body: JSON.stringify({
          sandbox_run_id: sandboxResult.run_id,
          rationale: approvalNote,
          kind: 'high_risk_publish',
        }),
      });
      toast.success(t('skill.approval_opened', '审批请求已提交'));
      setApprovalNote('');
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : 'failed';
      setSandboxError(msg);
      toast.error(msg);
    }
  }

  async function disable(listingId: string) {
    try {
      await fetchJson(`/skill-market/${listingId}/disable`, { method: 'POST' });
      await fetchListings();
      toast.success(t('skill.disabled', '已下架'));
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : 'failed';
      setError(msg);
      toast.error(msg);
    }
  }

  async function publishListing(listingId: string) {
    try {
      await fetchJson(`/skill-market/${listingId}/publish`, { method: 'POST' });
      await fetchListings();
      toast.success(t('skill.published', '已上架'));
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : 'failed';
      setError(msg);
      toast.error(msg);
    }
  }

  return (
    <div className="ao-page">
      <PageHeader
        title={t('skill.market_title', '技能市场')}
        subtitle={t('skill.market_subtitle', '浏览、沙箱验证与高危技能审批')}
      />

      {error && (
        <div style={{ marginBottom: 12 }}>
          <ErrorBanner message={error} onRetry={fetchListings} tone="error" />
        </div>
      )}

      {loading ? (
        <LoadingState label={t('skill.loading', '加载中…')} rows={4} />
      ) : (
        <div className="ao-stack">
          {listings.length === 0 && (
            <EmptyState
              title={t('skill.empty_title', '技能市场为空')}
              description={t('skill.empty', '还没有上架的技能')}
            />
          )}
          {listings.map((listing) => (
            <article key={listing.listing_id} className="ao-panel">
              <header className="ao-toolbar" style={{ marginBottom: 8 }}>
                <div>
                  <h2 className="ao-section-title" style={{ marginBottom: 4 }}>
                    {listing.title}
                  </h2>
                  <div className="ao-page-muted">{listing.summary}</div>
                  <div className="ao-inline-actions" style={{ marginTop: 8 }}>
                    {listing.keywords.map((k) => (
                      <span key={k} className="ao-chip ao-chip-static">
                        {k}
                      </span>
                    ))}
                  </div>
                </div>
                <div style={{ textAlign: 'right', minWidth: 120 }}>
                  <div className={`ao-page-subtle ${riskTone(listing.risk_level)}`}>
                    {t('skill.risk', '风险')}: {listing.risk_level}
                  </div>
                  <div className="ao-page-subtle">
                    {t('skill.scope', '范围')}: {listing.share_scope}
                  </div>
                  <div className="ao-page-subtle">
                    {t('skill.installs', '安装数')}: {listing.install_count}
                  </div>
                </div>
              </header>

              <div className="ao-inline-actions">
                <button
                  type="button"
                  className="btn btn-secondary"
                  onClick={() =>
                    setExpanded(expanded === listing.listing_id ? null : listing.listing_id)
                  }
                >
                  {expanded === listing.listing_id
                    ? t('skill.hide_panel', '收起')
                    : t('skill.sandbox_btn', '沙箱 + 审批')}
                </button>
                {listing.status === 'draft' || listing.status === 'disabled' ? (
                  <button
                    type="button"
                    className="btn btn-primary"
                    onClick={() => publishListing(listing.listing_id)}
                  >
                    {t('skill.publish', '上架')}
                  </button>
                ) : (
                  <button
                    type="button"
                    className="btn ao-danger"
                    onClick={() => disable(listing.listing_id)}
                  >
                    {t('skill.disable', '下架')}
                  </button>
                )}
              </div>

              {expanded === listing.listing_id && (
                <div style={{ marginTop: 16, paddingTop: 14, borderTop: '1px solid var(--border-subtle)' }}>
                  <div className="ao-stack">
                    <div>
                      <label className="ao-field-label">{t('skill.code_label', '代码')}</label>
                      <textarea
                        value={codeDraft}
                        onChange={(e) => setCodeDraft(e.target.value)}
                        rows={6}
                        className="ao-input-block textarea"
                        style={{ fontFamily: 'var(--font-mono)', fontSize: 12 }}
                        placeholder='print("hello")'
                      />
                    </div>
                    <div className="ao-inline-actions">
                      <select
                        value={language}
                        onChange={(e) => setLanguage(e.target.value as 'python' | 'bash' | 'node')}
                      >
                        <option value="python">python</option>
                        <option value="bash">bash</option>
                        <option value="node">node</option>
                      </select>
                      <label className="ao-page-muted" style={{ display: 'inline-flex', gap: 6, alignItems: 'center' }}>
                        <input
                          type="checkbox"
                          checked={allowNetwork}
                          onChange={(e) => setAllowNetwork(e.target.checked)}
                        />
                        {t('skill.allow_network', '允许网络')}
                      </label>
                      <button
                        type="button"
                        className="btn btn-primary"
                        disabled={sandboxRunning || !codeDraft.trim()}
                        onClick={() => runSandbox(listing.listing_id)}
                      >
                        {sandboxRunning
                          ? t('skill.sandbox_running', '运行中…')
                          : t('skill.run_sandbox', '运行沙箱')}
                      </button>
                    </div>

                    {sandboxError && <ErrorBanner message={sandboxError} tone="error" />}

                    {sandboxResult && (
                      <div className="ao-callout">
                        <div className="ao-page-muted">
                          {t('skill.status', '状态')}: <b style={{ color: 'var(--text-primary)' }}>{sandboxResult.status}</b>
                        </div>
                        <div className="ao-page-subtle">
                          {t('skill.exit', '退出码')}: {sandboxResult.exit_code}
                        </div>
                        <div className="ao-page-subtle">
                          {t('skill.duration', '耗时 ms')}: {sandboxResult.duration_ms}
                        </div>
                        <div className={riskTone(sandboxResult.risk_level)}>
                          {t('skill.risk_verdict', '风险判定')}: {sandboxResult.risk_level}
                          {sandboxResult.requires_human_review
                            ? ` (${t('skill.needs_review', '需人审')})`
                            : ''}
                        </div>
                        <div className="ao-page-muted">{sandboxResult.rationale}</div>
                        <pre className="ao-pre">{sandboxResult.stdout}</pre>
                      </div>
                    )}

                    {sandboxResult?.requires_human_review && (
                      <div className="ao-callout warning">
                        <input
                          type="text"
                          value={approvalNote}
                          onChange={(e) => setApprovalNote(e.target.value)}
                          placeholder={t('skill.approval_reason', '说明风险点 / 申请发布理由')}
                          className="ao-input-block"
                        />
                        <div style={{ marginTop: 8 }}>
                          <button
                            type="button"
                            className="btn btn-secondary"
                            onClick={() => openApproval(listing.listing_id)}
                          >
                            {t('skill.request_approval', '提交高危审批')}
                          </button>
                        </div>
                      </div>
                    )}
                  </div>
                </div>
              )}
            </article>
          ))}
        </div>
      )}
    </div>
  );
};

export default SkillMarket;
