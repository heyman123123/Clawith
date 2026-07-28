import React, { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { officialTemplatesApi, OfficialTemplateItem } from '../services/api';
import { EmptyState, ErrorBanner, LoadingState } from '../components/UI';

/**
 * P7 模板库 — 渲染 30 个官方模板，HR/项目调度可以快速选用。
 * 与 :func:`backend.app.services.workflow_template_seeder.seed_official_workflow_templates`
 * 配套。
 */
const OfficialTemplates: React.FC = () => {
    const { t } = useTranslation();
    const [templates, setTemplates] = useState<OfficialTemplateItem[]>([]);
    const [keyword, setKeyword] = useState('');
    const [activeTags, setActiveTags] = useState<string[]>([]);
    const [error, setError] = useState<string | null>(null);
    const [loading, setLoading] = useState(true);

    const fetchTemplates = React.useCallback(async () => {
        setLoading(true);
        try {
            const data = await officialTemplatesApi.list();
            setTemplates(data);
            setError(null);
        } catch (err) {
            setError((err as Error).message || 'load failed');
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => {
        fetchTemplates();
    }, [fetchTemplates]);

    const allTags = useMemo(() => {
        const set = new Set<string>();
        templates.forEach((tpl) => tpl.tags?.forEach((tg) => set.add(tg)));
        return Array.from(set).sort();
    }, [templates]);

    const filtered = useMemo(() => {
        const lower = keyword.trim().toLowerCase();
        return templates.filter((tpl) => {
            const matchesKeyword = !lower
                || tpl.title.toLowerCase().includes(lower)
                || tpl.summary.toLowerCase().includes(lower)
                || (tpl.keywords || []).some((k) => k.toLowerCase().includes(lower));
            const matchesTag = !activeTags.length
                || activeTags.every((tag) => (tpl.tags || []).includes(tag));
            return matchesKeyword && matchesTag;
        });
    }, [templates, keyword, activeTags]);

    const toggleTag = (tag: string) => {
        setActiveTags((current) =>
            current.includes(tag) ? current.filter((t) => t !== tag) : [...current, tag]
        );
    };

    return (
        <div className="p-6 max-w-7xl mx-auto">
            <header className="mb-6 flex flex-col gap-2 md:flex-row md:items-center md:justify-between">
                <div>
                    <h1 className="text-2xl font-semibold">{t('templates.title', '官方模板库 (30)')}</h1>
                    <p className="text-gray-500 text-sm">
                        {t(
                            'templates.subtitle',
                            '需求 §8.7 — 30 个官方编排模板，覆盖产品/数据/运营/财务/合规/HR/法务 等场景。'
                        )}
                    </p>
                </div>
                <Link
                    to="/hr-review"
                    className="px-4 py-2 bg-blue-600 text-white rounded-md hover:bg-blue-700 self-start md:self-auto"
                >
                    {t('templates.to_hr', '回到 HR 入口')}
                </Link>
            </header>

            <div className="flex flex-col gap-4 mb-6">
                <input
                    value={keyword}
                    onChange={(e) => setKeyword(e.target.value)}
                    placeholder={t('templates.search_placeholder', '搜索标题 / 关键词 / 摘要')}
                    className="w-full border rounded-md px-3 py-2"
                />
                <div className="flex flex-wrap gap-2">
                    {allTags.map((tag) => {
                        const active = activeTags.includes(tag);
                        return (
                            <button
                                key={tag}
                                type="button"
                                onClick={() => toggleTag(tag)}
                                className={`px-3 py-1 rounded-full text-sm border ${
                                    active ? 'bg-blue-600 text-white border-blue-600' : 'bg-white text-gray-700'
                                }`}
                            >
                                #{tag}
                            </button>
                        );
                    })}
                </div>
            </div>

            {loading ? (
                <LoadingState label={t('templates.loading', '加载模板…')} rows={6} />
            ) : error ? (
                <ErrorBanner
                    message={error}
                    onRetry={fetchTemplates}
                    tone="error"
                />
            ) : filtered.length === 0 ? (
                <EmptyState
                    title={t('templates.empty_title', '没有匹配的模板')}
                    description={t('templates.empty', '没有匹配的模板')}
                />
            ) : (
                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                    {filtered.map((tpl) => (
                        <article
                            key={tpl.slug}
                            className="border rounded-lg p-4 shadow-sm flex flex-col gap-2"
                            style={{ background: 'var(--bg-elevated)', borderColor: 'var(--border-subtle)' }}
                        >
                            <header className="flex items-center justify-between">
                                <h2 className="text-lg font-medium">{tpl.title}</h2>
                                <span className="text-xs text-gray-500">v{tpl.quality_threshold}</span>
                            </header>
                            <p className="text-sm text-gray-600 line-clamp-3">{tpl.summary}</p>
                            <div className="flex flex-wrap gap-1">
                                {(tpl.tags || []).map((tag) => (
                                    <span key={tag} className="text-xs px-2 py-0.5 rounded-full bg-gray-100">
                                        #{tag}
                                    </span>
                                ))}
                            </div>
                            <div className="text-xs text-gray-500">
                                {t('templates.roles', '推荐角色')}: {(tpl.recommended_roles || []).join(' / ')}
                            </div>
                            <div className="text-xs text-gray-400">
                                {t('templates.provider', '驱动')}: {tpl.ao_provider || 'default'} ·{' '}
                                {tpl.ao_model || '—'}
                            </div>
                        </article>
                    ))}
                </div>
            )}
        </div>
    );
};

export default OfficialTemplates;