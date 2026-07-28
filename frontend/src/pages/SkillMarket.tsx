import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { fetchJson } from '../services/api';

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

interface SandboxHookHandle {
  execute(code: string, language?: string, allowNetwork?: boolean): Promise<SandboxRun>;
  loading: boolean;
  error: string | null;
  reset(): void;
}

void (null as unknown as SandboxHookHandle); // type placeholder retained for clarity

const SkillMarket: React.FC = () => {
  const { t } = useTranslation();
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
  const [successMsg, setSuccessMsg] = useState<string | null>(null);

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

  useEffect(() => { fetchListings(); }, []);

  async function runSandbox(listingId: string) {
    setSandboxRunning(true);
    setSandboxError(null);
    setSandboxResult(null);
    try {
      const data = await fetchJson<SandboxRun>(`/skill-market/${listingId}/sandbox`, {
        method: 'POST',
        body: JSON.stringify({
          code: codeDraft, language, timeout: 30, allow_network: allowNetwork,
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
      setSandboxError(t('skill.run_sandbox_first', '请先跑通沙箱再申请审批'));
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
      setSuccessMsg(t('skill.approval_opened', '审批请求已提交'));
      setApprovalNote('');
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : 'failed';
      setSandboxError(msg);
    }
  }

  async function disable(listingId: string) {
    try {
      await fetchJson(`/skill-market/${listingId}/disable`, { method: 'POST' });
      await fetchListings();
      setSuccessMsg(t('skill.disabled', '已下架'));
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : 'failed';
      setError(msg);
    }
  }

  async function publishListing(listingId: string) {
    try {
      await fetchJson(`/skill-market/${listingId}/publish`, { method: 'POST' });
      await fetchListings();
      setSuccessMsg(t('skill.published', '已上架'));
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : 'failed';
      setError(msg);
    }
  }

  const riskColor = (level: string) =>
    level === 'high' ? 'text-red-600'
      : level === 'medium' ? 'text-yellow-600'
        : 'text-green-600';

  return (
    <div className="p-6 max-w-6xl mx-auto">
      <h1 className="text-2xl font-bold mb-4">{t('skill.market_title', '技能市场')}</h1>
      {error && <div className="mb-3 px-3 py-2 bg-red-100 text-red-700 rounded">{error}</div>}
      {successMsg && (
        <div className="mb-3 px-3 py-2 bg-green-100 text-green-700 rounded">{successMsg}</div>
      )}
      {loading ? (
        <div>{t('skill.loading', '加载中…')}</div>
      ) : (
        <div className="grid gap-3">
          {listings.length === 0 && (
            <div className="text-gray-500">{t('skill.empty', '还没有上架的技能')}</div>
          )}
          {listings.map((listing) => (
            <article
              key={listing.listing_id}
              className="border border-gray-200 bg-white rounded p-4"
            >
              <header className="flex justify-between items-start gap-4">
                <div>
                  <h2 className="text-lg font-semibold">{listing.title}</h2>
                  <div className="text-sm text-gray-500">{listing.summary}</div>
                  <div className="flex flex-wrap gap-1 mt-2">
                    {listing.keywords.map((k) => (
                      <span key={k} className="text-xs px-2 py-0.5 bg-gray-100 rounded">
                        {k}
                      </span>
                    ))}
                  </div>
                </div>
                <div className="text-right space-y-1">
                  <div className={`text-xs ${riskColor(listing.risk_level)}`}>
                    {t('skill.risk', '风险')}: {listing.risk_level}
                  </div>
                  <div className="text-xs text-gray-500">
                    {t('skill.scope', '范围')}: {listing.share_scope}
                  </div>
                  <div className="text-xs text-gray-500">
                    {t('skill.installs', '安装数')}: {listing.install_count}
                  </div>
                </div>
              </header>

              <div className="mt-3 flex gap-2 flex-wrap">
                <button
                  type="button"
                  className="px-3 py-1 border rounded hover:bg-gray-50"
                  onClick={() => setExpanded(expanded === listing.listing_id ? null : listing.listing_id)}
                >
                  {expanded === listing.listing_id
                    ? t('skill.hide_panel', '收起')
                    : t('skill.sandbox_btn', '沙箱 + 审批')}
                </button>
                {listing.status === 'draft' || listing.status === 'disabled' ? (
                  <button
                    type="button"
                    className="px-3 py-1 bg-blue-600 text-white rounded"
                    onClick={() => publishListing(listing.listing_id)}
                  >
                    {t('skill.publish', '上架')}
                  </button>
                ) : (
                  <button
                    type="button"
                    className="px-3 py-1 border border-red-500 text-red-600 rounded"
                    onClick={() => disable(listing.listing_id)}
                  >
                    {t('skill.disable', '下架')}
                  </button>
                )}
              </div>

              {expanded === listing.listing_id && (
                <div className="mt-4 space-y-3 border-t border-gray-100 pt-3">
                  <div>
                    <label className="block text-xs text-gray-500 mb-1">
                      {t('skill.code_label', '代码')}
                    </label>
                    <textarea
                      value={codeDraft}
                      onChange={(e) => setCodeDraft(e.target.value)}
                      rows={6}
                      className="w-full p-2 border rounded font-mono text-xs"
                      placeholder='print("hello")'
                    />
                  </div>
                  <div className="flex flex-wrap items-center gap-3">
                    <select
                      value={language}
                      onChange={(e) => setLanguage(e.target.value as 'python' | 'bash' | 'node')}
                      className="border rounded px-2 py-1"
                    >
                      <option value="python">python</option>
                      <option value="bash">bash</option>
                      <option value="node">node</option>
                    </select>
                    <label className="text-sm flex items-center gap-1">
                      <input
                        type="checkbox"
                        checked={allowNetwork}
                        onChange={(e) => setAllowNetwork(e.target.checked)}
                      />
                      {t('skill.allow_network', '允许网络')}
                    </label>
                    <button
                      type="button"
                      className="px-3 py-1 bg-blue-600 text-white rounded disabled:opacity-60"
                      disabled={sandboxRunning || !codeDraft.trim()}
                      onClick={() => runSandbox(listing.listing_id)}
                    >
                      {sandboxRunning
                        ? t('skill.sandbox_running', '运行中…')
                        : t('skill.run_sandbox', '运行沙箱')}
                    </button>
                  </div>
                  {sandboxError && (
                    <div className="text-sm text-red-600">{sandboxError}</div>
                  )}
                  {sandboxResult && (
                    <div className="border border-gray-200 rounded p-3 text-xs space-y-1 bg-gray-50">
                      <div>{t('skill.status', '状态')}: <b>{sandboxResult.status}</b></div>
                      <div>{t('skill.exit', '退出码')}: {sandboxResult.exit_code}</div>
                      <div>{t('skill.duration', '耗时 ms')}: {sandboxResult.duration_ms}</div>
                      <div className={riskColor(sandboxResult.risk_level)}>
                        {t('skill.risk_verdict', '风险判定')}: {sandboxResult.risk_level}
                        {sandboxResult.requires_human_review
                          ? ` (${t('skill.needs_review', '需人审')})`
                          : ''}
                      </div>
                      <div>{sandboxResult.rationale}</div>
                      <pre className="bg-white p-2 border rounded mt-1 max-h-32 overflow-auto">
{sandboxResult.stdout}
                      </pre>
                    </div>
                  )}

                  {sandboxResult && sandboxResult.requires_human_review && (
                    <div className="border border-yellow-300 bg-yellow-50 p-3 rounded space-y-2">
                      <input
                        type="text"
                        value={approvalNote}
                        onChange={(e) => setApprovalNote(e.target.value)}
                        placeholder={t('skill.approval_reason', '说明风险点 / 申请发布理由')}
                        className="w-full p-2 border rounded text-sm"
                      />
                      <button
                        type="button"
                        className="px-3 py-1 bg-yellow-600 text-white rounded"
                        onClick={() => openApproval(listing.listing_id)}
                      >
                        {t('skill.request_approval', '提交高危审批')}
                      </button>
                    </div>
                  )}
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
