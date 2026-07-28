import type { ReactNode } from 'react';

export interface PageHeaderProps {
    title: string;
    subtitle?: string;
    actions?: ReactNode;
    breadcrumb?: ReactNode;
}

/**
 * 顶部固定样式页面标题区：与 Layout 风格一致，
 * 标题 / 副标题 / 面包屑 / 右侧操作区。
 */
export function PageHeader({ title, subtitle, actions, breadcrumb }: PageHeaderProps) {
    return (
        <header
            style={{
                marginBottom: '20px',
                padding: '16px 20px',
                background: 'var(--bg-elevated)',
                border: '1px solid var(--border-subtle)',
                borderRadius: '12px',
                boxShadow: 'var(--shadow-sm, 0 1px 3px rgba(0,0,0,0.06))',
            }}
        >
            {breadcrumb ? (
                <div
                    style={{
                        fontSize: '12px',
                        color: 'var(--text-tertiary)',
                        marginBottom: '6px',
                    }}
                >
                    {breadcrumb}
                </div>
            ) : null}
            <div
                style={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    gap: '16px',
                    flexWrap: 'wrap',
                }}
            >
                <div style={{ minWidth: 0 }}>
                    <h1
                        style={{
                            fontSize: '20px',
                            fontWeight: 600,
                            color: 'var(--text-primary)',
                            margin: 0,
                            lineHeight: 1.3,
                        }}
                    >
                        {title}
                    </h1>
                    {subtitle ? (
                        <p
                            style={{
                                margin: '4px 0 0',
                                fontSize: '13px',
                                color: 'var(--text-secondary)',
                                lineHeight: 1.5,
                            }}
                        >
                            {subtitle}
                        </p>
                    ) : null}
                </div>
                {actions ? (
                    <div style={{ display: 'flex', gap: '8px', alignItems: 'center', flexShrink: 0 }}>
                        {actions}
                    </div>
                ) : null}
            </div>
        </header>
    );
}

export default PageHeader;