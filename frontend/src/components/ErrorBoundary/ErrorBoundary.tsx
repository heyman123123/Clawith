// Top-level error boundary that catches render-time exceptions
// and shows a friendly fallback instead of a white screen.
import React, { Component, ErrorInfo, ReactNode } from 'react';

interface State {
    hasError: boolean;
    error?: Error;
}

interface Props {
    children: ReactNode;
}

export class ErrorBoundary extends Component<Props, State> {
    constructor(props: Props) {
        super(props);
        this.state = { hasError: false };
    }

    static getDerivedStateFromError(error: Error): State {
        return { hasError: true, error };
    }

    componentDidCatch(error: Error, info: ErrorInfo) {
        // eslint-disable-next-line no-console
        console.error('ErrorBoundary caught:', error, info);
    }

    render() {
        if (this.state.hasError) {
            return (
                <div style={{
                    display: 'flex', flexDirection: 'column',
                    alignItems: 'center', justifyContent: 'center',
                    minHeight: '60vh', padding: 'var(--space-8)',
                    textAlign: 'center', gap: 'var(--space-4)',
                }}>
                    <div style={{ fontSize: 48 }}>⚠</div>
                    <div style={{ fontSize: 'var(--text-xl)', fontWeight: 600, color: 'var(--text-primary)' }}>
                        页面出错了
                    </div>
                    <div style={{ fontSize: 'var(--text-sm)', color: 'var(--text-secondary)', maxWidth: '50ch' }}>
                        {this.state.error?.message || '组件渲染时出现异常'}
                    </div>
                    <button
                        className="btn btn-primary"
                        onClick={() => {
                            this.setState({ hasError: false, error: undefined });
                            window.location.reload();
                        }}
                        style={{ marginTop: 'var(--space-2)' }}
                    >
                        重新加载
                    </button>
                </div>
            );
        }
        return this.props.children;
    }
}
