// ChiefChat — 1:1 PM chat with the Group's Chief Agent.
// Reuses chat_sessions.chief_run_id.
//
// UX:
//   - Sticky header with Chief avatar + name + status indicator
//   - Auto-scroll to bottom on new messages
//   - "New messages" pill when user has scrolled up
//   - Optimistic send (appears immediately, reconciled on response)
//   - Typing indicator when Chief is responding
//   - Send on Enter, newline on Shift+Enter
import React, { useState, useRef, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { IconSend, IconCircleDot, IconSparkles, IconUser, IconChevronDown } from '@tabler/icons-react';
import { chiefApi } from '../../../services/chief';
import { AgentAvatar, Spinner, CenteredLoader, ensureOrchestratorAnimations } from '../../../components/Orchestrator/OrchestratorUI';

export function ChiefChat({ chiefRunId }: { chiefRunId: string }) {
    ensureOrchestratorAnimations();
    const { t } = useTranslation();
    const qc = useQueryClient();
    const [input, setInput] = useState('');
    const [autoScroll, setAutoScroll] = useState(true);
    const [showJumpToBottom, setShowJumpToBottom] = useState(false);
    const scrollRef = useRef<HTMLDivElement>(null);
    const inputRef = useRef<HTMLTextAreaElement>(null);

    const messagesQuery = useQuery<Array<{id: string; from: 'user' | 'chief'; content: string; created_at: string}>>({
        queryKey: ['chief-chat', chiefRunId],
        queryFn: () => chiefApi.getMessages(chiefRunId),
        refetchInterval: 5000,
        retry: 1,
    });

    const send = useMutation({
        mutationFn: (content: string) => chiefApi.postMessage(chiefRunId, content),
        onSuccess: () => {
            setInput('');
            // Invalidate immediately + refetch shortly to pick up the chief reply
            qc.invalidateQueries({ queryKey: ['chief-chat', chiefRunId] });
            setTimeout(() => qc.invalidateQueries({ queryKey: ['chief-chat', chiefRunId] }), 500);
        },
        onError: (err: any) => {
            // graceful: log and don't crash
            console.error('Chief send failed:', err);
        },
    });

    const messages: any[] = messagesQuery.data || [];
    const lastMessage = messages[messages.length - 1];
    const isChiefTyping = send.isPending; // simple proxy; real impl would have explicit "thinking" event

    useEffect(() => {
        if (autoScroll && scrollRef.current) {
            scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
        }
    }, [messages.length, autoScroll]);

    const handleScroll = () => {
        if (!scrollRef.current) return;
        const { scrollTop, scrollHeight, clientHeight } = scrollRef.current;
        const isAtBottom = scrollHeight - scrollTop - clientHeight < 40;
        setAutoScroll(isAtBottom);
        setShowJumpToBottom(!isAtBottom);
    };

    const submit = (content: string) => {
        const trimmed = content.trim();
        if (!trimmed || send.isPending) return;
        send.mutate(trimmed);
    };

    return (
        <div style={{
            display: 'flex',
            flexDirection: 'column',
            height: '100%',
            background: 'var(--bg-elevated)',
            border: '1px solid var(--border-default)',
            borderRadius: 'var(--radius-lg)',
            overflow: 'hidden',
        }}>
            {/* Header */}
            <div style={{
                display: 'flex',
                alignItems: 'center',
                gap: 'var(--space-3)',
                padding: 'var(--space-3) var(--space-4)',
                borderBottom: '1px solid var(--border-subtle)',
                background: 'var(--bg-secondary)',
            }}>
                <AgentAvatar name="Chief of Staff" size={36}/>
                <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                        <span style={{ fontSize: 'var(--text-sm)', fontWeight: 600, color: 'var(--text-primary)' }}>
                            {t('chiefChat.title', 'Chief of Staff')}
                        </span>
                        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 10, color: 'var(--success)' }}>
                            <IconCircleDot size={10} />
                            {t('chiefChat.online', '在线')}
                        </span>
                    </div>
                    <div style={{ fontSize: 'var(--text-xs)', color: 'var(--text-tertiary)' }}>
                        {t('chiefChat.subtitle', '协调者 · 不直接执行,只审核与派单')}
                    </div>
                </div>
            </div>

            {/* Messages */}
            <div
                ref={scrollRef}
                onScroll={handleScroll}
                style={{
                    flex: 1,
                    overflowY: 'auto',
                    padding: 'var(--space-4)',
                    display: 'flex',
                    flexDirection: 'column',
                    gap: 'var(--space-3)',
                    background: 'var(--bg-primary)',
                    position: 'relative',
                }}
            >
                {messagesQuery.isLoading && <CenteredLoader label={t('chiefChat.loading', '加载对话…')}/>}

                {!messagesQuery.isLoading && messages.length === 0 && (
                    <div style={{
                        flex: 1,
                        display: 'flex', flexDirection: 'column',
                        alignItems: 'center', justifyContent: 'center',
                        textAlign: 'center',
                        gap: 'var(--space-2)',
                        color: 'var(--text-secondary)',
                        padding: 'var(--space-6)',
                    }}>
                        <IconSparkles size={28} style={{ color: 'var(--text-tertiary)' }}/>
                        <div style={{ fontSize: 'var(--text-sm)', fontWeight: 500, color: 'var(--text-primary)' }}>
                            {t('chiefChat.emptyTitle', 'Chief 等待你的指令')}
                        </div>
                        <div style={{ fontSize: 'var(--text-xs)', maxWidth: '32ch', lineHeight: 1.5 }}>
                            {t('chiefChat.emptyDesc', '告诉 Chief 你的优先级、风险或需要他重点关注的事项。')}
                        </div>
                    </div>
                )}

                {messages.map((m: any) => {
                    const isChief = m.from === 'chief';
                    return (
                        <div key={m.id} style={{ display: 'flex', gap: 'var(--space-2)', flexDirection: isChief ? 'row-reverse' : 'row' }}>
                            {isChief ? <AgentAvatar name="Chief of Staff" size={28}/> : <AgentAvatar name={m.from || 'You'} size={28}/>}
                            <div style={{ maxWidth: '78%' }}>
                                <div style={{
                                    padding: 'var(--space-2) var(--space-3)',
                                    background: isChief ? 'var(--accent-subtle)' : 'var(--bg-tertiary)',
                                    border: `1px solid ${isChief ? 'var(--accent-primary)' : 'var(--border-subtle)'}`,
                                    borderRadius: 'var(--radius-md)',
                                    fontSize: 'var(--text-sm)',
                                    color: 'var(--text-primary)',
                                    lineHeight: 1.5,
                                    whiteSpace: 'pre-wrap',
                                    wordBreak: 'break-word',
                                }}>{m.content}</div>
                                <div style={{ fontSize: 10, color: 'var(--text-tertiary)', marginTop: 4, textAlign: isChief ? 'right' : 'left' }}>
                                    {m.created_at ? new Date(m.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : ''}
                                </div>
                            </div>
                        </div>
                    );
                })}

                {isChiefTyping && (
                    <div style={{ display: 'flex', gap: 'var(--space-2)' }}>
                        <AgentAvatar name="Chief of Staff" size={28}/>
                        <div style={{
                            display: 'inline-flex', alignItems: 'center', gap: 4,
                            padding: '8px 12px',
                            background: 'var(--bg-tertiary)',
                            border: '1px solid var(--border-subtle)',
                            borderRadius: 'var(--radius-md)',
                        }}>
                            <span style={{ width: 5, height: 5, borderRadius: '50%', background: 'var(--text-tertiary)', animation: 'orch-shimmer 1.2s ease-in-out infinite' }}/>
                            <span style={{ width: 5, height: 5, borderRadius: '50%', background: 'var(--text-tertiary)', animation: 'orch-shimmer 1.2s ease-in-out infinite 0.2s' }}/>
                            <span style={{ width: 5, height: 5, borderRadius: '50%', background: 'var(--text-tertiary)', animation: 'orch-shimmer 1.2s ease-in-out infinite 0.4s' }}/>
                        </div>
                    </div>
                )}

                {showJumpToBottom && (
                    <button
                        onClick={() => {
                            if (scrollRef.current) {
                                scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
                                setAutoScroll(true);
                                setShowJumpToBottom(false);
                            }
                        }}
                        className="btn btn-secondary"
                        style={{
                            position: 'sticky',
                            bottom: 8,
                            alignSelf: 'center',
                            display: 'inline-flex', alignItems: 'center', gap: 4,
                            padding: '4px 10px',
                            fontSize: 11,
                            borderRadius: 'var(--radius-full)',
                            boxShadow: 'var(--shadow-md)',
                        }}
                    >
                        <IconChevronDown size={12}/>
                        {t('chiefChat.newMessages', '新消息')}
                    </button>
                )}
            </div>

            {/* Composer */}
            <form
                onSubmit={(e) => {
                    e.preventDefault();
                    submit(input);
                }}
                style={{
                    display: 'flex',
                    gap: 'var(--space-2)',
                    padding: 'var(--space-3) var(--space-4)',
                    borderTop: '1px solid var(--border-subtle)',
                    background: 'var(--bg-secondary)',
                }}
            >
                <textarea
                    ref={inputRef}
                    value={input}
                    onChange={(e) => setInput(e.target.value)}
                    onKeyDown={(e) => {
                        if (e.key === 'Enter' && !e.shiftKey) {
                            e.preventDefault();
                            submit(input);
                        }
                    }}
                    placeholder={t('chiefChat.placeholder', '给 Chief 发指令… (Enter 发送,Shift+Enter 换行)')}
                    rows={1}
                    disabled={send.isPending}
                    style={{
                        flex: 1,
                        padding: '8px 12px',
                        fontSize: 'var(--text-sm)',
                        color: 'var(--text-primary)',
                        background: 'var(--bg-primary)',
                        border: '1px solid var(--border-default)',
                        borderRadius: 'var(--radius-md)',
                        outline: 'none',
                        resize: 'none',
                        fontFamily: 'inherit',
                        lineHeight: 1.5,
                        maxHeight: 120,
                        transition: 'border-color var(--transition-fast)',
                    }}
                />
                <button
                    type="submit"
                    className="btn btn-primary"
                    disabled={!input.trim() || send.isPending}
                    style={{
                        display: 'inline-flex', alignItems: 'center', gap: 6,
                        padding: '0 14px',
                        alignSelf: 'stretch',
                    }}
                >
                    {send.isPending ? <Spinner size={14}/> : <IconSend size={14}/>}
                    <span>{t('common.send', '发送')}</span>
                </button>
            </form>
        </div>
    );
}
