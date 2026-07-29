import React, { useEffect, useMemo, useState } from 'react';
import { useParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { aoAssetsApi } from '../services/api';
import { EmptyState, ErrorBanner, LoadingState } from '../components/UI';

/**
 * P7 — 群文件夹 8 类资产浏览器（需求 §4.7 + §8.5）
 *
 * Eight canonical buckets per ``AssetCategory`` enum:
 *  00-工作流定义
 *  01-步骤输出
 *  02-过程记录
 *  03-质量管控
 *  04-交付验收
 *  05-技能档案
 *  06-最终交付
 *  07-历史迭代
 */

const CATEGORIES: { key: string; label: string; description: string }[] = [
    { key: '00-工作流定义', label: '工作流定义', description: 'AO YAML / 角色 / DAG 描述' },
    { key: '01-步骤输出', label: '步骤输出', description: '执行 Agent 的输出物' },
    { key: '02-过程记录', label: '过程记录', description: '消息 / 日志 / 决策快照' },
    { key: '03-质量管控', label: '质量管控', description: '质检报告 / 反馈 / 整改' },
    { key: '04-交付验收', label: '交付验收', description: '验收轮次 / 评分' },
    { key: '05-技能档案', label: '技能档案', description: '沙箱运行 / 学习记录' },
    { key: '06-最终交付', label: '最终交付', description: '客户交付物 / 文档' },
    { key: '07-历史迭代', label: '历史迭代', description: '上一版本的资产快照' },
];

const LEGACY_TO_BUCKET: Record<string, string> = {
    requirement: '00-工作流定义',
    execution: '01-步骤输出',
    quality: '03-质量管控',
    delivery: '04-交付验收',
};

interface AssetEntry {
    rel_path: string;
    category: string;
    byte_size: number;
    hash: string;
    orphaned?: boolean;
}

const AssetBrowser: React.FC = () => {
    const { t } = useTranslation();
    const params = useParams<{ workflowId?: string }>();
    const workflowId = params.workflowId || '';
    const [activeCategory, setActiveCategory] = useState<string>(CATEGORIES[0].key);
    const [assets, setAssets] = useState<AssetEntry[]>([]);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const loadAssets = React.useCallback(async () => {
        if (!workflowId) return;
        setLoading(true);
        setError(null);
        try {
            const data = await aoAssetsApi.list(workflowId, { sync: true }).catch(() => null);
            if (!data) {
                setAssets([]);
                return;
            }
            const bucket: AssetEntry[] = (data.items || []).map((item) => {
                const fromPath = CATEGORIES.find((c) => item.rel_path.startsWith(`${c.key}/`));
                const category =
                    fromPath?.key ||
                    LEGACY_TO_BUCKET[item.category] ||
                    item.category ||
                    CATEGORIES[0].key;
                return {
                    rel_path: item.rel_path,
                    category,
                    byte_size: item.byte_size,
                    hash: item.hash,
                    orphaned: item.orphaned,
                };
            });
            setAssets(bucket);
        } catch (err) {
            setError((err as Error).message);
            setAssets([]);
        } finally {
            setLoading(false);
        }
    }, [workflowId]);

    useEffect(() => {
        loadAssets();
    }, [loadAssets]);

    const grouped = useMemo(() => {
        const map = new Map<string, AssetEntry[]>();
        assets.forEach((entry) => {
            const list = map.get(entry.category) || [];
            list.push(entry);
            map.set(entry.category, list);
        });
        return map;
    }, [assets]);

    return (
        <div className="p-6 max-w-7xl mx-auto">
            <header className="mb-4">
                <h1 className="text-2xl font-semibold">
                    {t('asset.title', '群文件夹 (8 类资产)')}
                </h1>
                <p className="text-gray-500 text-sm">
                    {t(
                        'asset.subtitle',
                        '需求 §4.7 + §8.5 — 八桶资产目录：定义/输出/过程/质量/验收/技能/交付/历史'
                    )}
                </p>
            </header>

            <nav className="flex flex-wrap gap-2 mb-4">
                {CATEGORIES.map((cat) => {
                    const active = activeCategory === cat.key;
                    return (
                        <button
                            key={cat.key}
                            type="button"
                            onClick={() => setActiveCategory(cat.key)}
                            className={`px-3 py-1 rounded-md text-sm border ${
                                active
                                    ? 'bg-blue-600 text-white border-blue-600'
                                    : 'bg-white text-gray-700 hover:bg-gray-50'
                            }`}
                            title={cat.description}
                        >
                            {cat.key}
                        </button>
                    );
                })}
            </nav>

            {error ? (
                <div className="mb-3">
                    <ErrorBanner
                        message={`${t('asset.api_unavailable', '资产 API 暂不可用')}：${error}`}
                        onRetry={loadAssets}
                        tone="warning"
                    />
                </div>
            ) : null}

            {loading ? (
                <LoadingState label={t('asset.loading', '加载资产…')} rows={4} />
            ) : assets.length === 0 ? (
                <EmptyState
                    title={t('asset.empty_title', '该工程尚无 workflow 资产')}
                    description={t(
                        'asset.empty',
                        '目前仅展示交付验收轮次（04 桶）。其他桶资产 API 尚未上线。',
                    )}
                />
            ) : (
                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
                    {CATEGORIES.map((cat) => {
                        const entries = grouped.get(cat.key) || [];
                        return (
                            <section
                                key={cat.key}
                                className="border rounded-lg p-3 flex flex-col"
                                style={{ background: 'var(--bg-elevated)', borderColor: 'var(--border-subtle)' }}
                            >
                                <header className="mb-2">
                                    <h2 className="font-medium">{cat.key}</h2>
                                    <p className="text-xs text-gray-500">{cat.description}</p>
                                </header>
                                {entries.length === 0 ? (
                                    <div className="text-xs text-gray-400">—</div>
                                ) : (
                                    <ul className="text-xs space-y-1">
                                        {entries.map((e) => (
                                            <li
                                                key={e.rel_path}
                                                className={
                                                    e.orphaned
                                                        ? 'text-gray-400 line-through'
                                                        : 'text-gray-700'
                                                }
                                            >
                                                {e.rel_path}{' '}
                                                <span className="text-gray-400">
                                                    ({e.byte_size}B)
                                                </span>
                                            </li>
                                        ))}
                                    </ul>
                                )}
                            </section>
                        );
                    })}
                </div>
            )}
        </div>
    );
};

export default AssetBrowser;