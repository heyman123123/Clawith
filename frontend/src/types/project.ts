export interface ProjectTeamRole {
    key: string;
    name: string;
    role_description: string;
    personality: string;
    boundaries: string;
    is_group_leader: boolean;
    duties?: string;
    soul?: string;
    suggested_tools?: string[];
    suggested_permissions?: Record<string, string>;
}

export interface HrProposalRole extends ProjectTeamRole {
    duties: string;
    soul: string;
    suggested_tools: string[];
    suggested_permissions?: Record<string, string>;
}

export interface HrTeamProposal {
    id: string;
    label: string;
    card_summary: string;
    roles: HrProposalRole[];
}

export interface TeamPlan {
    planner_name: string;
    project_name: string;
    requirements: string;
    roles: ProjectTeamRole[];
    wake_up_message: string;
}

export interface TeamPlanProposal {
    id: string;
    label: string;
    roles: ProjectTeamRole[];
}

export interface HrTeamPlanSession {
    hr_review_session_id: string;
    group_id: string;
    session_id: string;
    status: string;
    proposals: TeamPlanProposal[];
}

export interface TeamPlanSelection {
    roles: ProjectTeamRole[];
    wake_up_message: string;
    project_name: string;
    requirements: string;
    planner_name?: string;
    workflow_id?: string;
    group_id?: string;
    session_id?: string;
    hr_review_session_id?: string;
}

export interface ProjectWorkflowMember {
    agent_id: string;
    role_key: string;
    role_title: string;
    is_group_leader: boolean;
}

export interface ProjectWorkflow {
    id: string;
    name: string;
    template_key: string;
    requirements: string;
    status: 'planning' | 'provisioning' | 'active' | 'failed' | string;
    team_plan: TeamPlan;
    group_id: string | null;
    decision_group_id: string | null;
    group_leader_agent_id: string | null;
    failure_reason: string | null;
    created_at: string;
    kickoff_sent_at: string | null;
    members: ProjectWorkflowMember[];
}

export interface ShareholderGroup {
    group_id: string;
    name: string;
    created_at: string;
}
