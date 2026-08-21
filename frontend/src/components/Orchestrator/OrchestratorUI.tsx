// Shared UI primitives for the Orchestrator pages. All components use the
// existing Clawith design tokens (CSS vars in index.css) and the prebuilt
// .btn / .btn-primary / .btn-secondary classes so they look native to the
// rest of the app and react correctly to the dark/light theme.
import React from 'react';

/* ─── Page shell ───────────────────────────────────────────────── */

export function PageShell({ children, maxWidth = '4xl' }: { children: React.ReactNode; maxWidth?: 'md' | 'lg' | 'xl' | '2xl' | '3xl' | '4xl' | '5xl' | '6xl' }) {
    const widths: Record<string, string> = {
        md: '36rem', lg: '48rem', xl: '56rem', '2xl': '64rem',
        '3xl': '72rem', '4xl': '80rem', '5xl': '88rem', '6xl': '96rem',
    };
    return (
        <div style={{ maxWidth: widths[maxWidth], margin: '0 auto', padding: 'var(--space-8) var(--space-6) var(--space-12)' }}>
            {children}
        </div>
    );
}

export function PageHeader({ eyebrow, title, description, actions }: {
    eyebrow?: string;
    title: string;
    description?: string;
    actions?: React.ReactNode;
}) {
    return (
        <div style={{ marginBottom: 'var(--space-8)' }}>
            {eyebrow && (
                <div style={{
                    fontSize: 'var(--text-xs)',
                    color: 'var(--text-tertiary)',
                    textTransform: 'uppercase',
                    letterSpacing: '0.08em',
                    fontWeight: 600,
                    marginBottom: 'var(--space-2)',
                }}>
                    {eyebrow}
                </div>
            )}
            <div style={{ display: 'flex', alignItems: 'flex-start', gap: 'var(--space-4)' }}>
                <div style={{ flex: 1 }}>
                    <h1 style={{
                        fontSize: 'var(--text-3xl)',
                        fontWeight: 600,
                        color: 'var(--text-primary)',
                        margin: 0,
                        letterSpacing: '-0.02em',
                    }}>{title}</h1>
                    {description && (
                        <p style={{
                            fontSize: 'var(--text-base)',
                            color: 'var(--text-secondary)',
                            margin: 'var(--space-2) 0 0',
                            lineHeight: 1.5,
                            maxWidth: '60ch',
                        }}>{description}</p>
                    )}
                </div>
                {actions && (
                    <div style={{ display: 'flex', gap: 'var(--space-2)', flexShrink: 0 }}>
                        {actions}
                    </div>
                )}
            </div>
        </div>
    );
}

/* ─── Status badge ──────────────────────────────────────────────── */

export type StatusVariant = 'pending' | 'approved' | 'rejected' | 'consumed' | 'expired' | 'error' | 'active' | 'paused' | 'degraded' | 'stopped';

const STATUS_STYLES: Record<StatusVariant, { color: string; bg: string; border: string; label: string }> = {
    pending:   { color: 'var(--text-secondary)', bg: 'var(--bg-tertiary)', border: 'var(--border-subtle)', label: '待审核' },
    approved:  { color: 'var(--info)',            bg: 'var(--info-subtle)',   border: 'var(--info)',            label: '已审核' },
    rejected:  { color: 'var(--error)',           bg: 'var(--error-subtle)',  border: 'var(--error)',           label: '已拒绝' },
    consumed:  { color: 'var(--success)',         bg: 'var(--success-subtle)', border: 'var(--success)',         label: '已生效' },
    expired:   { color: 'var(--warning)',         bg: 'var(--warning-subtle)', border: 'var(--warning)',         label: '已过期' },
    error:     { color: 'var(--error)',           bg: 'var(--error-subtle)',  border: 'var(--error)',           label: '错误' },
    active:    { color: 'var(--success)',         bg: 'var(--success-subtle)', border: 'var(--success)',         label: '运行中' },
    paused:    { color: 'var(--warning)',         bg: 'var(--warning-subtle)', border: 'var(--warning)',         label: '已暂停' },
    degraded:  { color: 'var(--warning)',         bg: 'var(--warning-subtle)', border: 'var(--warning)',         label: '降级' },
    stopped:   { color: 'var(--text-tertiary)',   bg: 'var(--bg-tertiary)',   border: 'var(--border-subtle)',  label: '已停止' },
};

export function StatusBadge({ status, size = 'sm' }: { status: string; size?: 'sm' | 'md' }) {
    const variant = (STATUS_STYLES as any)[status] || STATUS_STYLES.pending;
    const padding = size === 'md' ? '3px 10px' : '2px 8px';
    const fontSize = size === 'md' ? 'var(--text-sm)' : 'var(--text-xs)';
    return (
        <span style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: '4px',
            padding,
            fontSize,
            fontWeight: 500,
            color: variant.color,
            background: variant.bg,
            border: `1px solid ${variant.border}`,
            borderRadius: 'var(--radius-full)',
            whiteSpace: 'nowrap',
        }}>
            <span style={{
                width: 6, height: 6, borderRadius: '50%',
                background: variant.color,
                flexShrink: 0,
            }}/>
            {variant.label}
        </span>
    );
}

/* ─── Card ─────────────────────────────────────────────────────── */

export function Card({ children, padding = 'md', style }: {
    children: React.ReactNode;
    padding?: 'none' | 'sm' | 'md' | 'lg';
    style?: React.CSSProperties;
}) {
    const pad = { none: '0', sm: 'var(--space-3)', md: 'var(--space-5)', lg: 'var(--space-6)' }[padding];
    return (
        <div style={{
            background: 'var(--bg-elevated)',
            border: '1px solid var(--border-default)',
            borderRadius: 'var(--radius-lg)',
            padding: pad,
            ...style,
        }}>{children}</div>
    );
}

export function SectionLabel({ children, count, action }: { children: React.ReactNode; count?: number; action?: React.ReactNode }) {
    return (
        <div style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            marginBottom: 'var(--space-3)',
        }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)' }}>
                <span style={{
                    fontSize: 'var(--text-xs)',
                    fontWeight: 600,
                    color: 'var(--text-tertiary)',
                    textTransform: 'uppercase',
                    letterSpacing: '0.06em',
                }}>{children}</span>
                {count !== undefined && (
                    <span style={{
                        fontSize: 'var(--text-xs)',
                        color: 'var(--text-tertiary)',
                        background: 'var(--bg-tertiary)',
                        padding: '1px 7px',
                        borderRadius: 'var(--radius-full)',
                        fontWeight: 500,
                    }}>{count}</span>
                )}
            </div>
            {action}
        </div>
    );
}

/* ─── Loading ──────────────────────────────────────────────────── */

export function Spinner({ size = 16 }: { size?: number }) {
    return (
        <span style={{
            display: 'inline-block',
            width: size, height: size,
            border: '2px solid var(--border-default)',
            borderTopColor: 'var(--accent-primary)',
            borderRadius: '50%',
            animation: 'orch-spin 0.7s linear infinite',
        }}/>
    );
}

export function SkeletonLine({ width = '100%' }: { width?: string | number }) {
    return (
        <div style={{
            height: 12,
            width,
            background: 'var(--bg-tertiary)',
            borderRadius: 'var(--radius-sm)',
            animation: 'orch-shimmer 1.4s ease-in-out infinite',
        }}/>
    );
}

export function CenteredLoader({ label }: { label?: string }) {
    return (
        <div style={{
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            justifyContent: 'center',
            padding: 'var(--space-12)',
            gap: 'var(--space-3)',
        }}>
            <Spinner size={24}/>
            {label && <span style={{ color: 'var(--text-secondary)', fontSize: 'var(--text-sm)' }}>{label}</span>}
        </div>
    );
}

/* ─── Empty / Error states ─────────────────────────────────────── */

export function EmptyState({ icon, title, description, action }: {
    icon?: React.ReactNode;
    title: string;
    description?: string;
    action?: React.ReactNode;
}) {
    return (
        <div style={{
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            justifyContent: 'center',
            padding: 'var(--space-12) var(--space-6)',
            textAlign: 'center',
            background: 'var(--bg-secondary)',
            border: '1px dashed var(--border-default)',
            borderRadius: 'var(--radius-lg)',
            gap: 'var(--space-3)',
        }}>
            {icon && (
                <div style={{
                    width: 48, height: 48,
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                    background: 'var(--bg-tertiary)',
                    borderRadius: 'var(--radius-full)',
                    color: 'var(--text-tertiary)',
                }}>{icon}</div>
            )}
            <div style={{ fontSize: 'var(--text-lg)', fontWeight: 600, color: 'var(--text-primary)' }}>{title}</div>
            {description && (
                <div style={{
                    fontSize: 'var(--text-sm)',
                    color: 'var(--text-secondary)',
                    maxWidth: '44ch',
                    lineHeight: 1.5,
                }}>{description}</div>
            )}
            {action}
        </div>
    );
}

export function ErrorState({ title, message, onRetry }: { title: string; message?: string; onRetry?: () => void }) {
    return (
        <EmptyState
            icon={<span style={{ fontSize: 24 }}>⚠</span>}
            title={title}
            description={message}
            action={onRetry && (
                <button className="btn btn-primary" onClick={onRetry} style={{ marginTop: 'var(--space-2)' }}>
                    重试
                </button>
            )}
        />
    );
}

/* ─── Member / Agent avatar ────────────────────────────────────── */

const AGENT_PALETTE = [
    '#7c3aed', '#0ea5e9', '#10b981', '#f59e0b', '#ec4899', '#06b6d4',
    '#f97316', '#a855f7', '#22c55e', '#3b82f6', '#eab308', '#ef4444',
];

function hashStr(s: string): number {
    let h = 0;
    for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0;
    return Math.abs(h);
}

export function AgentAvatar({ name, size = 32 }: { name: string; size?: number }) {
    const initials = name.split(/\s+/).map(w => w[0]).filter(Boolean).slice(0, 2).join('').toUpperCase() || '?';
    const color = AGENT_PALETTE[hashStr(name) % AGENT_PALETTE.length];
    return (
        <span
            title={name}
            style={{
                display: 'inline-flex',
                alignItems: 'center',
                justifyContent: 'center',
                width: size, height: size,
                borderRadius: '50%',
                background: color,
                color: '#fff',
                fontSize: size * 0.4,
                fontWeight: 600,
                flexShrink: 0,
                userSelect: 'none',
            }}
        >{initials}</span>
    );
}

export function AgentStack({ names, max = 4, size = 24 }: { names: string[]; max?: number; size?: number }) {
    const visible = names.slice(0, max);
    const overflow = Math.max(0, names.length - max);
    return (
        <span style={{ display: 'inline-flex' }}>
            {visible.map((n, i) => (
                <span key={i} style={{ marginLeft: i === 0 ? 0 : -size / 3, border: '2px solid var(--bg-elevated)', borderRadius: '50%' }}>
                    <AgentAvatar name={n} size={size} />
                </span>
            ))}
            {overflow > 0 && (
                <span style={{
                    marginLeft: -size / 3,
                    width: size, height: size,
                    display: 'inline-flex',
                    alignItems: 'center', justifyContent: 'center',
                    borderRadius: '50%',
                    background: 'var(--bg-tertiary)',
                    border: '2px solid var(--bg-elevated)',
                    color: 'var(--text-secondary)',
                    fontSize: 11,
                    fontWeight: 600,
                }}>+{overflow}</span>
            )}
        </span>
    );
}

/* ─── Radio card group (for visibility selector) ────────────────── */

export function RadioCardGroup<T extends string>({ value, options, onChange }: {
    value: T;
    options: Array<{ value: T; label: string; description: string; icon?: React.ReactNode }>;
    onChange: (v: T) => void;
}) {
    return (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 'var(--space-3)' }}>
            {options.map((opt) => {
                const selected = opt.value === value;
                return (
                    <label
                        key={opt.value}
                        style={{
                            display: 'block',
                            padding: 'var(--space-4)',
                            background: selected ? 'var(--accent-subtle)' : 'var(--bg-tertiary)',
                            border: `1px solid ${selected ? 'var(--accent-primary)' : 'var(--border-default)'}`,
                            borderRadius: 'var(--radius-md)',
                            cursor: 'pointer',
                            transition: 'all var(--transition-fast)',
                        }}
                    >
                        <input
                            type="radio"
                            name="orch-radio"
                            value={opt.value}
                            checked={selected}
                            onChange={() => onChange(opt.value)}
                            style={{ position: 'absolute', opacity: 0, pointerEvents: 'none' }}
                        />
                        <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)', marginBottom: 4 }}>
                            {opt.icon && <span style={{ color: 'var(--text-secondary)' }}>{opt.icon}</span>}
                            <span style={{ fontSize: 'var(--text-sm)', fontWeight: 600, color: 'var(--text-primary)' }}>{opt.label}</span>
                        </div>
                        <div style={{ fontSize: 'var(--text-xs)', color: 'var(--text-secondary)', lineHeight: 1.5 }}>
                            {opt.description}
                        </div>
                    </label>
                );
            })}
        </div>
    );
}

/* ─── Animations (injected once) ───────────────────────────────── */

let _animationsInjected = false;
export function ensureOrchestratorAnimations() {
    if (_animationsInjected || typeof document === 'undefined') return;
    _animationsInjected = true;
    const id = 'orchestrator-animations';
    if (document.getElementById(id)) return;
    const style = document.createElement('style');
    style.id = id;
    style.textContent = `
        @keyframes orch-spin {
            to { transform: rotate(360deg); }
        }
        @keyframes orch-shimmer {
            0%, 100% { opacity: 0.5; }
            50% { opacity: 1; }
        }
        @keyframes orch-fade-in {
            from { opacity: 0; transform: translateY(4px); }
            to { opacity: 1; transform: translateY(0); }
        }
        .orch-fade-in { animation: orch-fade-in 200ms ease-out; }
    `;
    document.head.appendChild(style);
}
