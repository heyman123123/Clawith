import { useMemo } from 'react';
import { Link } from 'react-router-dom';
import { IconExternalLink } from '@tabler/icons-react';

interface DecisionCardProps {
    content: string;
    decisionGroupId?: string;
}

const DECISION_SYNC_MARKER = /<!--decision_sync:([0-9a-f-]+)-->/i;
const SECTION_ICONS = ['📋', '🎯', '⚠️', '🔗'] as const;

interface ParsedSection {
    icon: string;
    title: string;
    body: string[];
}

function parseDecisionContent(raw: string) {
    const markerMatch = raw.match(DECISION_SYNC_MARKER);
    const recordId = markerMatch?.[1];
    const displayText = raw.replace(DECISION_SYNC_MARKER, '').trim();
    const lines = displayText.split('\n').map((line) => line.trim()).filter(Boolean);

    const sections: ParsedSection[] = [];
    let current: ParsedSection | null = null;

    for (const line of lines) {
        const icon = SECTION_ICONS.find((candidate) => line.startsWith(candidate));
        if (icon) {
            if (current) sections.push(current);
            const rest = line.slice(icon.length).trim();
            const colonIndex = rest.indexOf('：');
            if (colonIndex >= 0) {
                current = {
                    icon,
                    title: rest.slice(0, colonIndex).trim(),
                    body: rest.slice(colonIndex + 1).trim() ? [rest.slice(colonIndex + 1).trim()] : [],
                };
            } else {
                current = { icon, title: rest, body: [] };
            }
            continue;
        }
        if (current) current.body.push(line);
    }
    if (current) sections.push(current);

    return { recordId, displayText, sections };
}

export default function DecisionCard({ content, decisionGroupId }: DecisionCardProps) {
    const parsed = useMemo(() => parseDecisionContent(content), [content]);

    if (parsed.sections.length === 0) {
        return <div className="decision-card decision-card-plain">{parsed.displayText}</div>;
    }

    return (
        <div className="decision-card">
            {parsed.sections.map((section) => (
                <section key={`${section.icon}-${section.title}`} className="decision-card-section">
                    <div className="decision-card-section-head">
                        <span className="decision-card-icon" aria-hidden>{section.icon}</span>
                        <strong>{section.title}</strong>
                    </div>
                    {section.body.length > 0 && (
                        <div className="decision-card-section-body">
                            {section.body.map((line, index) => (
                                <p key={index}>{line}</p>
                            ))}
                        </div>
                    )}
                </section>
            ))}
            {decisionGroupId && (
                <Link to={`/groups/${decisionGroupId}`} className="decision-card-link">
                    <IconExternalLink size={14} stroke={1.7} />
                    查看决策群
                </Link>
            )}
        </div>
    );
}
