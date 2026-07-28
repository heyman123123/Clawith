import { useCallback, useEffect, useState } from 'react';

export type Theme = 'light' | 'dark';

const STORAGE_KEY = 'theme';
const VALID: Theme[] = ['light', 'dark'];

function isTheme(value: unknown): value is Theme {
    return typeof value === 'string' && (VALID as string[]).includes(value);
}

function readInitialTheme(): Theme {
    if (typeof window === 'undefined') return 'light';
    const stored = localStorage.getItem(STORAGE_KEY);
    if (isTheme(stored)) return stored;
    const attr = document.documentElement.getAttribute('data-theme');
    if (isTheme(attr)) return attr;
    return 'light';
}

function applyTheme(theme: Theme) {
    if (typeof document === 'undefined') return;
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem(STORAGE_KEY, theme);
}

/**
 * 全局主题 hook：
 * - 读取 localStorage('theme') + html[data-theme]
 * - 同步 document.documentElement[data-theme]
 * - 监听 window 'storage' 事件以跨标签同步
 */
export function useTheme() {
    const [theme, setThemeState] = useState<Theme>(readInitialTheme);

    useEffect(() => {
        applyTheme(theme);
    }, [theme]);

    useEffect(() => {
        const onStorage = (event: StorageEvent) => {
            if (event.key !== STORAGE_KEY) return;
            if (isTheme(event.newValue) && event.newValue !== theme) {
                setThemeState(event.newValue);
            }
        };
        window.addEventListener('storage', onStorage);
        return () => window.removeEventListener('storage', onStorage);
    }, [theme]);

    const setTheme = useCallback((next: Theme) => {
        setThemeState(next);
    }, []);

    const toggle = useCallback(() => {
        setThemeState((prev) => (prev === 'dark' ? 'light' : 'dark'));
    }, []);

    return { theme, setTheme, toggle } as const;
}