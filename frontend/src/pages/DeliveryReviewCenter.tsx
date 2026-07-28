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
import { EmptyState, ErrorBanner } from '../components/UI';

/**
 * P3 + P7 — 交付验收中心
 *
 * - 顶部"提交一轮验收"卡片：质量分 (60%) + 范围分 (40%) 双维度
 * - 下方"历史轮次" 列表
 * - 右侧抽屉：人审队列 (审批卡 / 决策卡 / 高危技能审核 / 质检异常人工复核)
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
                    : t('delivery.rejected', '已驳回'),
            );
            await load();
        } catch (err) {
            const msg = (err as Error).message;
            setError(msg);
            toast.error(msg);
        }
    };

    return (
        <div className="p-6 max-w-7xl mx-auto">
            <header className="mb-4">
                <h1 className="text-2xl font-semibold">{t('delivery.title', '交付验收中心')}</h1>
                <p className="text-gray-500 text-sm">
                    {t(
                        'delivery.subtitle',
                        '需求 §4.11 / §8.3 — 质量 60% + 范围 40% 双维度评分，≥90 通过；最多 3 轮整改。'
                    )}
                </p>
            </header>

            {error ? (
                <div className="mb-3">
                    <ErrorBanner
                        message={error}
                        onRetry={load}
                        tone="error"
                    />
                </div>
            ) : null}

            <section className="border rounded-lg p-4 mb-6" style={{ background: 'var(--bg-elevated)', borderColor: 'var(--border-subtle)' }}>
                <h2 className="text-lg font-medium mb-3">{t('delivery.submit_round', '提交一轮验收')}</h2>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <label className="flex flex-col gap-1">
                        <span className="text-sm text-gray-600">
                            {t('delivery.quality', '质量分 (60%)')} — {quality}
                        </span>
                        <input
                            type="range"
                            min={0}
                            max={100}
                            value={quality}
                            onChange={(e) => setQuality(Number(e.target.value))}
                        />
                    </label>
                    <label className="flex flex-col gap-1">
                        <span className="text-sm text-gray-600">
                            {t('delivery.coverage', '范围分 (40%)')} — {coverage}
                        </span>
                        <input
                            type="range"
                            min={0}
                            max={100}
                            value={coverage}
                            onChange={(e) => setCoverage(Number(e.target.value))}
                        />
                    </label>
                </div>
                <textarea
                    value={notes}
                    onChange={(e) => setNotes(e.target.value)}
                    placeholder={t('delivery.notes_placeholder', '整改意见 / 备注')}
                    className="w-full mt-3 border rounded-md px-3 py-2 min-h-[80px]"
                />
                <div className="mt-3 flex items-center gap-3">
                    <button
                        onClick={submit}
                        disabled={submitting || !workflowId}
                        className="px-4 py-2 bg-blue-600 text-white rounded-md disabled:opacity-50"
                    >
                        {submitting ? t('delivery.submitting', '提交中…') : t('delivery.submit', '提交本轮验收')}
                    </button>
                    <span className="text-sm text-gray-500">
                        {t('delivery.preview', '本轮预览分')}:{' '}
                        {Math.round(0.6 * quality + 0.4 * coverage)}
                    </span>
                </div>
            </section>

            <section className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                <div className="border rounded-lg p-4" style={{ background: 'var(--bg-elevated)', borderColor: 'var(--border-subtle)' }}>
                    <h2 className="text-lg font-medium mb-3">{t('delivery.rounds', '历史轮次')}</h2>
                    {rounds.length === 0 ? (
                        <EmptyState
                            title={t('delivery.empty_title', '尚无验收记录')}
                            description={t('delivery.empty', '暂无验收记录')}
                        />
                    ) : (
                        <table className="w-full text-sm">
                            <thead>
                                <tr className="text-left border-b">
                                    <th>#</th>
                                    <th>质量</th>
                                    <th>范围</th>
                                    <th>最终</th>
                                    <th>结论</th>
                                </tr>
                            </thead>
                            <tbody>
                                {rounds.map((round) => (
                                    <tr key={round.id} className="border-b last:border-b-0">
                                        <td>{round.round_no}</td>
                                        <td>{round.quality_score ?? '—'}</td>
                                        <td>{round.coverage_score ?? '—'}</td>
                                        <td>{round.final_score ?? '—'}</td>
                                        <td>
                                            <span
                                                className={
                                                    round.decision === 'approved'
                                                        ? 'text-green-600'
                                                        : round.decision === 'rejected'
                                                        ? 'text-red-600'
                                                        : 'text-gray-500'
                                                }
                                            >
                                                {round.decision}
                                            </span>
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    )}
                </div>

                <div className="border rounded-lg p-4" style={{ background: 'var(--bg-elevated)', borderColor: 'var(--border-subtle)' }}>
                    <h2 className="text-lg font-medium mb-3">
                        {t('delivery.reviews', '人审队列 (审批卡 / 决策卡 / 高危技能审核 / 质检异常)')}
                    </h2>
                    {reviews.length === 0 ? (
                        <EmptyState
                            title={t('delivery.reviews_empty_title', '队列为空')}
                            description={t('delivery.reviews_empty', '队列为空')}
                        />
                    ) : (
                        <ul className="space-y-3">
                            {reviews.map((r) => (
                                <li key={r.id} className="border rounded-md p-3">
                                    <div className="flex items-center justify-between">
                                        <span className="text-sm font-medium">{r.kind}</span>
                                        <span className="text-xs text-gray-500">
                                            {new Date(r.created_at).toLocaleString()}
                                        </span>
                                    </div>
                                    <pre className="text-xs bg-gray-50 rounded p-2 my-2 max-h-32 overflow-auto">
                                        {JSON.stringify(r.payload, null, 2)}
                                    </pre>
                                    <div className="flex gap-2">
                                        <button
                                            onClick={() => resolveReview(r.id, 'approved')}
                                            className="px-3 py-1 text-sm bg-green-600 text-white rounded"
                                        >
                                            批准
                                        </button>
                                        <button
                                            onClick={() => resolveReview(r.id, 'rejected')}
                                            className="px-3 py-1 text-sm bg-red-600 text-white rounded"
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