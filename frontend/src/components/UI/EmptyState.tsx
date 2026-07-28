import type { ReactNode } from 'react';
import { IconInbox } from '@tabler/icons-react';

export interface EmptyStateProps {
    icon?: ReactNode;
    title: string;
    description?: string;
    action?: { label: string; onClick: () => void };
}

/**
 * 统一空状态：垂直居中卡片，浅灰背景，图标 + 标题 + 描述 + 可选操作。
 * 使用 CSS 变量，自动适配 light/dark 模式。
 */
export function EmptyState({ icon, title, description, action }: EmptyStateProps) {
    return (
        <div
            style={{
                display: 'flex',
                flexDirection: 'column',
                alignItems: 'center',
                justifyContent: 'center',
                padding: '48px 24px',
                background: 'var(--bg-elevated)',
                border: '1px solid var(--border-subtle)',
                borderRadius: '12px',
                color: 'var(--text-secondary)',
                textAlign: 'center',
            }}
        >
            <div
                aria-hidden
                style={{
                    width: '44px',
                    height: '44px',
                    borderRadius: '50%',
                    background: 'var(--bg-tertiary)',
                    color: 'var(--text-tertiary)',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    marginBottom: '12px',
                }}
            >
                {icon ?? <IconInbox size={20} stroke={1.5} />}
            </div>
            <div
                style={{
                    fontSize: '14px',
                    fontWeight: 600,
                    color: 'var(--text-primary)',
                    marginBottom: description ? '4px' : 0,
                }}
            >
                {title}
            </div>
            {description ? (
                <div style={{ fontSize: '13px', color: 'var(--text-secondary)', maxWidth: '420px' }}>
                    {description}
                </div>
            ) : null}
            {action ? (
                <button
                    type="button"
                    className="btn btn-primary"
                    onClick={action.onClick}
                    style={{ marginTop: '14px' }}
                >
                    {action.label}
                </button>
            ) : null}
        </div>
    );
}

export default EmptyState;