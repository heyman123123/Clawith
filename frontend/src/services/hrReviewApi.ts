import { fetchJson } from './api';
import type { HrTeamPlanSession, TeamPlanSelection } from '../types/project';

interface HrReviewSessionResponse {
    id: string;
    group_id: string;
    session_id: string;
    status: string;
    proposals: HrTeamPlanSession['proposals'];
}

function toHrTeamPlanSession(session: HrReviewSessionResponse): HrTeamPlanSession {
    return {
        hr_review_session_id: session.id,
        group_id: session.group_id,
        session_id: session.session_id,
        status: session.status,
        proposals: session.proposals,
    };
}

export const hrReviewApi = {
    ensureBoard: () =>
        fetchJson<{ group_id: string; name: string }>('/hr-review/board/ensure', {
            method: 'POST',
        }),

    attachTeamBuilding: (chatSessionId: string) =>
        fetchJson<HrReviewSessionResponse>(`/hr-review/sessions/attach-team-building`, {
            method: 'POST',
            body: JSON.stringify({ chat_session_id: chatSessionId }),
        }).then(toHrTeamPlanSession),

    getSession: (id: string) =>
        fetchJson<HrReviewSessionResponse>(`/hr-review/sessions/${id}`).then(toHrTeamPlanSession),

    getSessionByChatSession: (chatSessionId: string) =>
        fetchJson<HrReviewSessionResponse>(`/hr-review/sessions/by-chat/${chatSessionId}`)
            .then(toHrTeamPlanSession)
            .catch(() => null),

    selectProposal: (sessionId: string, proposalId: string) =>
        fetchJson<TeamPlanSelection>(`/hr-review/sessions/${sessionId}/select`, {
            method: 'POST',
            body: JSON.stringify({ proposal_id: proposalId }),
        }),
};
