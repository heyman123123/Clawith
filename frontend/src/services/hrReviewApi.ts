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
    getSession: (id: string) =>
        fetchJson<HrReviewSessionResponse>(`/hr-review/sessions/${id}`).then(toHrTeamPlanSession),

    selectProposal: (sessionId: string, proposalId: string) =>
        fetchJson<TeamPlanSelection>(`/hr-review/sessions/${sessionId}/select`, {
            method: 'POST',
            body: JSON.stringify({ proposal_id: proposalId }),
        }),
};
