import {
    IconAlertTriangle,
    IconAlertCircle,
    IconInfoCircle,
    IconRefresh,
} from '@tabler/icons-react';

export interface ErrorBannerProps {
    message: string;
    onRetry?: () => void;
    tone?: 'error' | 'warning' | 'info';
}

const TONE_META: Record<NonNullable<ErrorBannerProps['tone']>, {
    color: string;
    background: string;
    border: string;
    icon: React.ReactNode;
}> = {
    error: {
        color: 'var(--error)',
        background: 'var(--error-subtle, rgba(220, 38, 38, 0.08))',
        border: 'var(--error, #dc2626)',
        icon: <IconAlertCircle size={14} stroke={2} />,
    },
    warning: {
        color: 'var(--warning)',
        background: 'var(--warning-subtle, rgba(217, 119, 6, 0.08))',
        border: 'var(--warning, #d97706)',
        icon: <IconAlertTriangle size={14} stroke={2} />,
    },
    info: {
        color: 'var(--info)',
        background: 'var(--info-subtle, rgba(37, 99, 211, 0.08))',
        border: 'var(--info, #2563eb)',
        icon: <IconInfoCircle size={14} stroke={2} />,
    },
};

/**
 * 顶部条带：错误 / 警告 / 信息。带可选 retry。
 */
export function ErrorBanner({ message, onRetry, tone = 'error' }: ErrorBannerProps) {
    const meta = TONE_META[tone];
    return (
        <div
            role={tone === 'error' ? 'alert' : 'status'}
            style={{
                display: 'flex',
                alignItems: 'center',
                gap: '10px',
                padding: '10px 14px',
                background: meta.background,
                border: `1px solid ${meta.border}`,
                borderLeft: `3px solid ${meta.border}`,
                borderRadius: '8px',
                color: 'var(--text-primary)',
                fontSize: '13px',
            }}
        >
            <span
                aria-hidden
                style={{
                    color: meta.color,
                    display: 'inline-flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    flexShrink: 0,
                }}
            >
                {meta.icon}
            </span>
            <div style={{ flex: 1, minWidth: 0, lineHeight: 1.5, wordBreak: 'break-word' }}>
                {message}
            </div>
            {onRetry ? (
                <button
                    type="button"
                    onClick={onRetry}
                    className="btn btn-ghost"
                    style={{
                        display: 'inline-flex',
                        alignItems: 'center',
                        gap: '4px',
                        padding: '4px 10px',
                        fontSize: '12px',
                        color: meta.color,
                    }}
                >
                    <IconRefresh size={12} stroke={2} />
                    <span>重试</span>
                </button>
            ) : null}
        </div>
    );
}

export default ErrorBanner;