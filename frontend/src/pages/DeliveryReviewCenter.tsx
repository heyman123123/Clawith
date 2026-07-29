import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useParams } from 'react-router-dom';
import {
    deliveryReviewApi,
    humanReviewApi,
    DeliveryRoundItem,
    HumanReviewItem,
} from '../services/api';
import { useToast } from '../components/Toast/ToastProvider';
import { EmptyState, ErrorBanner, PageHeader } from '../components/UI';

/**
 * P3 + P7 — 交付验收中心
 */
const DeliveryReviewCenter: React.FC = () => {
    const { t } = useTranslation();
    const toast = useToast();
    const params = useParams<{ workflowId?: string }>();
    const workflowId = params.workflowId || '';

    const [rounds, setRounds] = useState<DeliveryRoundItem[]>([]);
    const [reviews, setReviews] = useState<HumanReviewItem[]>([]);
    const [quality, setQuality] = useState<number>(80);
    const [coverage, setCoverage] = useState<number>(80);
    const [notes, setNotes] = useState('');
    const [submitting, setSubmitting] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const load = React.useCallback(async () => {
        if (!workflowId) return;
        try {
            const [r, h] = await Promise.all([
                deliveryReviewApi.listRounds(workflowId),
                humanReviewApi.list({ status: 'open', limit: 50 }),
            ]);
            setRounds(r);
            setReviews(h);
            setError(null);
        } catch (err) {
            setError((err as Error).message);
        }
    }, [workflowId]);

    useEffect(() => {
        load();
    }, [load]);

    const submit = async () => {
        if (!workflowId) return;
        setSubmitting(true);
        setError(null);
        try {
            await deliveryReviewApi.submitRound(workflowId, {
                quality_score: quality,
                coverage_score: coverage,
                quality_notes: notes,
                coverage_notes: notes,
            });
            toast.success(t('delivery.submitted', '已提交验收轮次'));
            await load();
        } catch (err) {
            const msg = (err as Error).message;
            setError(msg);
            toast.error(msg);
        } finally {
            setSubmitting(false);
        }
    };

    const resolveReview = async (id: string, decision: 'approved' | 'rejected') => {
        try {
            await humanReviewApi.resolve(id, { decision, notes: '' });
            toast.success(
                decision === 'approved'
                    ? t('delivery.approved', '已批准')
                    : t('delivery.rejected', '已驳回')
            );
            await load();
        } catch (err) {
            const msg = (err as Error).message;
            setError(msg);
            toast.error(msg);
        }
    };

    const decisionTone = (decision: string) => {
        if (decision === 'approved') return 'ao-tone-success';
        if (decision === 'rejected') return 'ao-tone-error';
        return 'ao-page-muted';
    };

    return (
        <div className="ao-page">
            <PageHeader
                title={t('delivery.title', '交付验收中心')}
                subtitle={t(
                    'delivery.subtitle',
                    '需求 §4.11 / §8.3 — 质量 60% + 范围 40% 双维度评分，≥90 通过；最多 3 轮整改。'
                )}
            />

            {error ? (
                <div style={{ marginBottom: 12 }}>
                    <ErrorBanner message={error} onRetry={load} tone="error" />
                </div>
            ) : null}

            <section className="ao-panel" style={{ marginBottom: 16 }}>
                <h2 className="ao-section-title">{t('delivery.submit_round', '提交一轮验收')}</h2>
                <div className="ao-grid-2">
                    <label>
                        <span className="ao-field-label">
                            {t('delivery.quality', '质量分 (60%)')} — {quality}
                        </span>
                        <input
                            type="range"
                            min={0}
                            max={100}
                            value={quality}
                            onChange={(e) => setQuality(Number(e.target.value))}
                            className="ao-input-block"
                        />
                    </label>
                    <label>
                        <span className="ao-field-label">
                            {t('delivery.coverage', '范围分 (40%)')} — {coverage}
                        </span>
                        <input
                            type="range"
                            min={0}
                            max={100}
                            value={coverage}
                            onChange={(e) => setCoverage(Number(e.target.value))}
                            className="ao-input-block"
                        />
                    </label>
                </div>
                <textarea
                    value={notes}
                    onChange={(e) => setNotes(e.target.value)}
                    placeholder={t('delivery.notes_placeholder', '整改意见 / 备注')}
                    className="ao-input-block textarea"
                    style={{ marginTop: 12 }}
                />
                <div className="ao-inline-actions" style={{ marginTop: 12 }}>
                    <button
                        type="button"
                        onClick={submit}
                        disabled={submitting || !workflowId}
                        className="btn btn-primary"
                    >
                        {submitting ? t('delivery.submitting', '提交中…') : t('delivery.submit', '提交本轮验收')}
                    </button>
                    <span className="ao-page-muted">
                        {t('delivery.preview', '本轮预览分')}: {Math.round(0.6 * quality + 0.4 * coverage)}
                    </span>
                </div>
            </section>

            <section className="ao-grid-2">
                <div className="ao-panel">
                    <h2 className="ao-section-title">{t('delivery.rounds', '历史轮次')}</h2>
                    {rounds.length === 0 ? (
                        <EmptyState
                            title={t('delivery.empty_title', '尚无验收记录')}
                            description={t('delivery.empty', '暂无验收记录')}
                        />
                    ) : (
                        <table className="ao-table">
                            <thead>
                                <tr>
                                    <th>#</th>
                                    <th>质量</th>
                                    <th>范围</th>
                                    <th>最终</th>
                                    <th>结论</th>
                                </tr>
                            </thead>
                            <tbody>
                                {rounds.map((round) => (
                                    <tr key={round.id}>
                                        <td>{round.round_no}</td>
                                        <td>{round.quality_score ?? '—'}</td>
                                        <td>{round.coverage_score ?? '—'}</td>
                                        <td>{round.final_score ?? '—'}</td>
                                        <td className={decisionTone(round.decision)}>{round.decision}</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    )}
                </div>

                <div className="ao-panel">
                    <h2 className="ao-section-title">
                        {t('delivery.reviews', '人审队列 (审批卡 / 决策卡 / 高危技能审核 / 质检异常)')}
                    </h2>
                    {reviews.length === 0 ? (
                        <EmptyState
                            title={t('delivery.reviews_empty_title', '队列为空')}
                            description={t('delivery.reviews_empty', '队列为空')}
                        />
                    ) : (
                        <ul className="ao-stack" style={{ listStyle: 'none', margin: 0, padding: 0 }}>
                            {reviews.map((r) => (
                                <li key={r.id} className="ao-callout">
                                    <div className="ao-toolbar" style={{ marginBottom: 8 }}>
                                        <span style={{ fontWeight: 600 }}>{r.kind}</span>
                                        <span className="ao-page-subtle">
                                            {new Date(r.created_at).toLocaleString()}
                                        </span>
                                    </div>
                                    <pre className="ao-pre">{JSON.stringify(r.payload, null, 2)}</pre>
                                    <div className="ao-inline-actions" style={{ marginTop: 8 }}>
                                        <button
                                            type="button"
                                            onClick={() => resolveReview(r.id, 'approved')}
                                            className="btn ao-success"
                                        >
                                            批准
                                        </button>
                                        <button
                                            type="button"
                                            onClick={() => resolveReview(r.id, 'rejected')}
                                            className="btn ao-danger"
                                        >
                                            驳回
                                        </button>
                                    </div>
                                </li>
                            ))}
                        </ul>
                    )}
                </div>
            </section>
        </div>
    );
};

export default DeliveryReviewCenter;
