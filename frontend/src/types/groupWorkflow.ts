export type WorkflowSource = 'default' | 'agile' | 'product_research' | 'ai';
export type WorkflowStatus = 'active' | 'paused' | 'awaiting_approval' | 'completed';
export type WorkflowStageStatus = 'pending' | 'active' | 'awaiting_approval' | 'completed' | 'blocked';
export type WorkflowItemStatus = 'pending' | 'in_progress' | 'blocked' | 'awaiting_approval' | 'done';

export interface WorkflowStage {
    id: string;
    key: string;
    title: string;
    goal: string;
    position: number;
    status: WorkflowStageStatus;
    requires_approval: boolean;
    acceptance_criteria: string[];
    owner_participant_id: string | null;
    started_at: string | null;
    completed_at: string | null;
}

export interface WorkflowItem {
    id: string;
    stage_id: string;
    item_key: string;
    title: string;
    description: string;
    assignee_participant_id: string | null;
    status: WorkflowItemStatus;
    evidence: Record<string, unknown>[];
    blocked_reason: string | null;
    version: number;
    updated_at: string;
}

export interface WorkflowLeaderAction {
    id: string;
    kind: string | null;
    stage_id: string | null;
    item_id: string | null;
    payload: Record<string, unknown>;
}

export interface GroupWorkflow {
    id: string;
    group_id: string;
    leader_participant_id: string | null;
    name: string;
    source: WorkflowSource;
    status: WorkflowStatus;
    current_stage_id: string | null;
    version: number;
    created_at: string;
    updated_at: string;
    stages: WorkflowStage[];
    items: WorkflowItem[];
    leader_next_action: WorkflowLeaderAction | null;
}

export interface WorkflowEvent {
    id: string;
    event_type: string;
    actor_participant_id: string | null;
    stage_id: string | null;
    item_id: string | null;
    source: string;
    payload: Record<string, unknown>;
    created_at: string;
}

export interface WorkflowEventPage {
    items: WorkflowEvent[];
    page: number;
    page_size: number;
    total: number;
}

export interface WorkflowDraft {
    id: string;
    group_id: string;
    request: { prompt?: string };
    plan: Record<string, unknown> | null;
    status: 'generating' | 'ready' | 'failed' | 'confirmed' | 'cancelled';
    error_code: string | null;
    error_message: string | null;
    confirmed_at: string | null;
    created_at: string;
    updated_at: string;
}
