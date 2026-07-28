import { IconSun, IconMoon } from '@tabler/icons-react';
import { useTranslation } from 'react-i18next';
import { useTheme } from '../../hooks/useTheme';

export interface ThemeToggleProps {
    className?: string;
    style?: React.CSSProperties;
    onClick?: () => void;
}

/**
 * 顶部栏 / 侧栏使用的太阳-月亮切换按钮。
 * 与 Layout.tsx 中既有 sidebar 风格一致（btn-ghost）。
 *
 * 如果传入了 `onClick`，则用外部 handler；否则用 hook 内的 toggle。
 */
export function ThemeToggle({ className, style, onClick }: ThemeToggleProps) {
    const { theme, toggle } = useTheme();
    const { t } = useTranslation();
    const isDark = theme === 'dark';
    const handleClick = onClick ?? toggle;
    return (
        <button
            type="button"
            className={className ?? 'btn btn-ghost'}
            onClick={handleClick}
            aria-label={isDark ? t('common.lightMode', '浅色模式') : t('common.darkMode', '深色模式')}
            title={isDark ? t('common.lightMode', '浅色模式') : t('common.darkMode', '深色模式')}
            style={{
                padding: '4px 8px',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                ...style,
            }}
        >
            {isDark ? <IconSun size={16} stroke={1.5} /> : <IconMoon size={16} stroke={1.5} />}
        </button>
    );
}

export default ThemeToggle;