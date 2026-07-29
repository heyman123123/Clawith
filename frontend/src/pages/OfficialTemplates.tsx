import React, { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { officialTemplatesApi, OfficialTemplateItem } from '../services/api';
import { EmptyState, ErrorBanner, LoadingState, PageHeader } from '../components/UI';
import { useToast } from '../components/Toast/ToastProvider';

/**
 * P7 模板库 — 渲染 30 个官方模板，HR/项目调度可以快速选用。
 */
const OfficialTemplates: React.FC = () => {
    const { t } = useTranslation();
    const toast = useToast();
    const [templates, setTemplates] = useState<OfficialTemplateItem[]>([]);
    const [keyword, setKeyword] = useState('');
    const [activeTags, setActiveTags] = useState<string[]>([]);
    const [error, setError] = useState<string | null>(null);
    const [loading, setLoading] = useState(true);
    const [launchingSlug, setLaunchingSlug] = useState<string | null>(null);

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
            const matchesKeyword =
                !lower ||
                tpl.title.toLowerCase().includes(lower) ||
                tpl.summary.toLowerCase().includes(lower) ||
                (tpl.keywords || []).some((k) => k.toLowerCase().includes(lower));
            const matchesTag =
                !activeTags.length || activeTags.every((tag) => (tpl.tags || []).includes(tag));
            return matchesKeyword && matchesTag;
        });
    }, [templates, keyword, activeTags]);

    const toggleTag = (tag: string) => {
        setActiveTags((current) =>
            current.includes(tag) ? current.filter((item) => item !== tag) : [...current, tag]
        );
    };

    const launchSkeleton = async (tpl: OfficialTemplateItem) => {
        if (!tpl.id) {
            toast.error(t('templates.missing_id', '模板缺少 id，无法生成 YAML'));
            return;
        }
        setLaunchingSlug(tpl.slug);
        try {
            const skeleton = await officialTemplatesApi.skeleton(tpl.id);
            await navigator.clipboard.writeText(skeleton.yaml_text);
            toast.success(
                t(
                    'templates.skeleton_copied',
                    `已生成可启动 YAML（${skeleton.step_count} 步）并复制到剪贴板`
                )
            );
        } catch (err) {
            toast.error((err as Error).message || t('templates.skeleton_failed', '生成 YAML 失败'));
        } finally {
            setLaunchingSlug(null);
        }
    };

    return (
        <div className="ao-page">
            <PageHeader
                title={t('templates.title', '官方模板库 (30)')}
                subtitle={t(
                    'templates.subtitle',
                    '需求 §8.7 — 30 个官方编排模板，覆盖产品/数据/运营/财务/合规/HR/法务 等场景。'
                )}
                actions={
                    <Link to="/hr-review" className="btn btn-primary">
                        {t('templates.to_hr', '回到 HR 入口')}
                    </Link>
                }
            />

            <div className="ao-stack" style={{ marginBottom: 20 }}>
                <input
                    value={keyword}
                    onChange={(e) => setKeyword(e.target.value)}
                    placeholder={t('templates.search_placeholder', '搜索标题 / 关键词 / 摘要')}
                    className="ao-input-block form-input"
                />
                <div className="ao-inline-actions">
                    {allTags.map((tag) => (
                        <button
                            key={tag}
                            type="button"
                            onClick={() => toggleTag(tag)}
                            className={`ao-chip${activeTags.includes(tag) ? ' is-active' : ''}`}
                        >
                            #{tag}
                        </button>
                    ))}
                </div>
            </div>

            {loading ? (
                <LoadingState label={t('templates.loading', '加载模板…')} rows={6} />
            ) : error ? (
                <ErrorBanner message={error} onRetry={fetchTemplates} tone="error" />
            ) : filtered.length === 0 ? (
                <EmptyState
                    title={t('templates.empty_title', '没有匹配的模板')}
                    description={t('templates.empty', '没有匹配的模板')}
                />
            ) : (
                <div className="ao-grid-3">
                    {filtered.map((tpl) => (
                        <article key={tpl.slug} className="ao-panel" style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                            <header className="ao-toolbar" style={{ marginBottom: 0 }}>
                                <h2 className="ao-section-title" style={{ margin: 0, fontSize: 16 }}>
                                    {tpl.title}
                                </h2>
                                <span className="ao-badge">v{tpl.quality_threshold}</span>
                            </header>
                            <p className="ao-page-muted" style={{ margin: 0, lineHeight: 1.5 }}>
                                {tpl.summary}
                            </p>
                            <div className="ao-inline-actions">
                                {(tpl.tags || []).map((tag) => (
                                    <span key={tag} className="ao-chip ao-chip-static">
                                        #{tag}
                                    </span>
                                ))}
                            </div>
                            <div className="ao-page-subtle">
                                {t('templates.roles', '推荐角色')}: {(tpl.recommended_roles || []).join(' / ')}
                            </div>
                            <div className="ao-page-subtle">
                                {t('templates.provider', '驱动')}: {tpl.ao_provider || 'default'} ·{' '}
                                {tpl.ao_model || '—'}
                            </div>
                            <button
                                type="button"
                                disabled={!tpl.id || launchingSlug === tpl.slug}
                                onClick={() => launchSkeleton(tpl)}
                                className="btn btn-primary"
                                style={{ marginTop: 'auto', alignSelf: 'flex-start' }}
                            >
                                {launchingSlug === tpl.slug
                                    ? t('templates.launching', '生成中…')
                                    : t('templates.launch', '一键生成 YAML')}
                            </button>
                        </article>
                    ))}
                </div>
            )}
        </div>
    );
};

export default OfficialTemplates;
