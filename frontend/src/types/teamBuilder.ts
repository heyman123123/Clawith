/** Durable intelligent-team draft and provisioning contracts. */

export type TeamBuildDraftStatus =
    | 'generating' | 'ready' | 'invalid' | 'confirmed' | 'expired' | 'cancelled';
export type TeamProvisionJobStatus =
    | 'queued' | 'validating' | 'provisioning_agents' | 'waiting_for_agents'
    | 'creating_group' | 'activating' | 'completed' | 'retryable_failed' | 'failed';

export type TeamWorkflowPreset = 'default' | 'agile' | 'product_research' | 'custom';

export interface TeamPlanMember {
    member_key: string;
    name: string;
    role_description: string;
    responsibility: string;
    source: 'existing' | 'new';
    existing_agent_id: string | null;
    template_id: string | null;
    skill_ids: string[];
    is_leader: boolean;
}

export interface TeamPlanDelegation {
    from_member_key: string;
    to_member_key: string;
    instruction: string;
}

export interface TeamPlanWorkflowStage {
    key: string;
    title: string;
    goal: string;
    requires_approval: boolean;
}

export interface TeamPlanWorkflow {
    preset: TeamWorkflowPreset;
    name: string;
    stages: TeamPlanWorkflowStage[];
}

export interface TeamPlan {
    group_name: string;
    goal: string;
    assumptions: string[];
    phases: string[];
    members: TeamPlanMember[];
    delegations: TeamPlanDelegation[];
    workflow?: TeamPlanWorkflow | null;
}

export interface TeamBuildDraft {
    id: string;
    status: TeamBuildDraftStatus;
    requirement: string;
    constraints: Record<string, unknown>;
    generated_plan: TeamPlan | null;
    reviewed_plan: TeamPlan | null;
    plan_version: number;
    confirmed_plan_version: number | null;
    error_code: string | null;
    error_message: string | null;
    created_at: string;
    updated_at: string;
}

export interface TeamProvisionMember {
    member_key: string;
    source: 'existing' | 'new';
    status: 'pending' | 'resolving' | 'creating' | 'waiting' | 'ready' | 'failed';
    agent_id: string | null;
    participant_id: string | null;
    error_code: string | null;
    error_message: string | null;
}

export interface TeamProvisionJob {
    id: string;
    draft_id: string;
    status: TeamProvisionJobStatus;
    group_id: string | null;
    leader_participant_id: string | null;
    session_id: string | null;
    activation_message_id: string | null;
    error_code: string | null;
    error_message: string | null;
    members: TeamProvisionMember[];
}

export interface TeamProvisionJobSummary {
    id: string;
    draft_id: string;
    status: TeamProvisionJobStatus;
    group_id: string | null;
    session_id: string | null;
    error_message: string | null;
}

export interface TeamBuildHistoryItem {
    draft: TeamBuildDraft;
    job: TeamProvisionJobSummary | null;
}
