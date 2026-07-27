import type { HrTeamProposal } from '../types/project';

const HR_REVIEW_SESSION_MARKER = /<!--hr_review_session:([0-9a-f-]+)-->/i;
const FENCED_JSON_BLOCK = /```(?:json)?\s*([\s\S]*?)```/i;

export interface ParsedHrProposalsMessage {
    hrReviewSessionId?: string;
    proposals: HrTeamProposal[];
    displayText: string;
}

function normalizeProposal(raw: unknown, index: number): HrTeamProposal | null {
    if (!raw || typeof raw !== 'object') return null;
    const proposal = raw as Record<string, unknown>;
    const id = String(proposal.id || `proposal_${index + 1}`).trim();
    const label = String(proposal.label || `方案 ${index + 1}`).trim();
    const cardSummary = String(proposal.card_summary || '').trim();
    const rolesRaw = proposal.roles;
    if (!Array.isArray(rolesRaw) || rolesRaw.length === 0) return null;

    const roles = rolesRaw
        .map((roleRaw) => {
            if (!roleRaw || typeof roleRaw !== 'object') return null;
            const role = roleRaw as Record<string, unknown>;
            const name = String(role.name || '').trim();
            if (!name) return null;
            return {
                key: String(role.key || name).trim(),
                name,
                duties: String(role.duties || role.role_description || '').trim(),
                soul: String(role.soul || '').trim(),
                role_description: String(role.role_description || role.duties || '').trim(),
                personality: String(role.personality || '').trim(),
                boundaries: String(role.boundaries || '').trim(),
                is_group_leader: Boolean(role.is_group_leader),
                suggested_tools: Array.isArray(role.suggested_tools)
                    ? role.suggested_tools.map((tool) => String(tool))
                    : [],
                suggested_permissions:
                    role.suggested_permissions && typeof role.suggested_permissions === 'object'
                        ? (role.suggested_permissions as Record<string, string>)
                        : undefined,
            };
        })
        .filter((role): role is NonNullable<typeof role> => role !== null);

    if (!id || !label || roles.length === 0) return null;
    return {
        id,
        label,
        card_summary: cardSummary || roles.map((role) => role.name).join('、'),
        roles,
    };
}

function parseJsonPayload(text: string): {
    hrReviewSessionId?: string;
    proposals?: HrTeamProposal[];
} | null {
    const match = text.match(FENCED_JSON_BLOCK);
    const jsonText = match?.[1]?.trim() ?? text.trim();
    const start = jsonText.indexOf('{');
    const end = jsonText.lastIndexOf('}');
    if (start < 0 || end < start) return null;

    try {
        const payload = JSON.parse(jsonText.slice(start, end + 1)) as Record<string, unknown>;
        const hrReviewSessionId = payload.hr_review_session_id
            ? String(payload.hr_review_session_id)
            : undefined;
        const proposalsRaw = payload.proposals;
        if (!Array.isArray(proposalsRaw)) return null;

        const proposals = proposalsRaw
            .map((item, index) => normalizeProposal(item, index))
            .filter((item): item is HrTeamProposal => item !== null);

        if (proposals.length === 0) return null;
        return { hrReviewSessionId, proposals };
    } catch {
        return null;
    }
}

/** Detect HR Secretary proposal cards in group message content. */
export function parseHrProposalsMessage(content: string): ParsedHrProposalsMessage | null {
    const markerMatch = content.match(HR_REVIEW_SESSION_MARKER);
    const hrReviewSessionId = markerMatch?.[1];
    const parsed = parseJsonPayload(content);
    if (!parsed) return null;

    const proposals = parsed.proposals;
    if (!proposals || proposals.length === 0) return null;

    const displayText = content
        .replace(HR_REVIEW_SESSION_MARKER, '')
        .replace(FENCED_JSON_BLOCK, '')
        .trim();

    return {
        hrReviewSessionId: hrReviewSessionId || parsed.hrReviewSessionId,
        proposals,
        displayText,
    };
}

export function hasHrProposalsContent(content: string): boolean {
    return parseHrProposalsMessage(content) !== null;
}
