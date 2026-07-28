import type { CSSProperties } from 'react';

export interface LoadingStateProps {
    label?: string;
    rows?: number;
}

const skeletonKeyframes = `
@keyframes ui-loading-pulse {
  0%, 100% { opacity: 0.55; }
  50% { opacity: 1; }
}
`;

function ensurePulseStyles() {
    if (typeof document === 'undefined') return;
    if (document.getElementById('ui-loading-pulse-keyframes')) return;
    const style = document.createElement('style');
    style.id = 'ui-loading-pulse-keyframes';
    style.textContent = skeletonKeyframes;
    document.head.appendChild(style);
}

const rowStyle: CSSProperties = {
    height: '14px',
    borderRadius: '6px',
    background: 'var(--bg-tertiary)',
    animation: 'ui-loading-pulse 1.6s ease-in-out infinite',
};

const labelStyle: CSSProperties = {
    color: 'var(--text-secondary)',
    fontSize: '13px',
    marginBottom: '12px',
};

/**
 * 脉动 placeholder。rows 控制占位行数（不含顶部 label）。
 * 当 label 省略时只渲染骨架行。
 */
export function LoadingState({ label, rows = 3 }: LoadingStateProps) {
    ensurePulseStyles();
    const count = Math.max(1, Math.min(rows, 8));
    const widths = [88, 72, 94, 64, 80, 60, 90, 70];
    return (
        <div
            role="status"
            aria-live="polite"
            aria-busy="true"
            style={{
                padding: '24px',
                background: 'var(--bg-elevated)',
                border: '1px solid var(--border-subtle)',
                borderRadius: '12px',
            }}
        >
            {label ? <div style={labelStyle}>{label}</div> : null}
            <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
                {Array.from({ length: count }).map((_, idx) => (
                    <div
                        key={idx}
                        style={{
                            ...rowStyle,
                            width: `${widths[idx % widths.length]}%`,
                        }}
                    />
                ))}
            </div>
        </div>
    );
}

export default LoadingState;