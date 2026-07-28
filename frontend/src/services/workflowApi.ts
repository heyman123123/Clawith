/**
 * Workflow API client (P1.5)
 *
 * One AO `WorkflowRun` is spawned per confirmed `ProjectWorkflow`. The frontend currently has
 * no backend route for these yet (P1.3 in flight), so every function here ships with a typed
 * mock that the panel can render against. The signatures mirror the backend schemas in
 * `backend/app/models/workflow_run.py` (`WorkflowRun`, `WorkflowRunStep`) and the role cast in
 * `backend/app/models/project_workflow.py`, so the only swap needed once the endpoints land is
 * replacing the `mock*` helpers with `fetchJson<...>(...)` calls.
 */

import { createRandomUUID } from '../utils/randomUUID.ts';

type Fetcher = <T>(url: string, options?: RequestInit) => Promise<T>;

let cachedFetcher: Fetcher | null = null;

const getFetcher = async (): Promise<Fetcher> => {
    if (cachedFetcher) return cachedFetcher;
    const module = await import('./api.ts');
    cachedFetcher = module.fetchJson as Fetcher;
    return cachedFetcher;
};

export type WorkflowRunStatus =
    | 'draft'
    | 'composing'
    | 'queued'
    | 'running'
    | 'paused'
    | 'awaiting_approval'
    | 'quality_retry'
    | 'succeeded'
    | 'failed'
    | 'cancelled';

export type WorkflowStepStatus =
    | 'pending'
    | 'running'
    | 'awaiting_approval'
    | 'quality_checking'
    | 'succeeded'
    | 'quality_failed'
    | 'failed'
    | 'skipped'
    | 'cancelled';

/** Power slot a member occupies in the four-power cast. */
export type WorkflowRoleKind = 'scheduler' | 'quality' | 'delivery' | 'executor' | 'stakeholder';

export interface WorkflowMember {
    agent_id: string;
    name: string;
    role_key: string;
    role_title: string;
    role_kind: WorkflowRoleKind;
    is_group_leader: boolean;
}

export interface WorkflowStep {
    id: string;
    step_id: string;
    step_order: number;
    role: string;
    role_kind: WorkflowRoleKind;
    task: string;
    status: WorkflowStepStatus;
    quality_score: number | null;
    depends_on: string[];
    started_at: string | null;
    completed_at: string | null;
    output_excerpt: string | null;
}

export interface WorkflowStatus {
    id: string;
    workflow_id: string;
    workflow_name: string;
    status: WorkflowRunStatus;
    member_count: number;
    final_score: number | null;
    latest_version: number;
    started_at: string | null;
    completed_at: string | null;
}

/* ──────────────────────────────────────────────────────────────────────────
 * Pure helpers — exported so unit tests can verify bucket/sort logic without
 * loading React. The Panel itself uses these via plain imports.
 * ────────────────────────────────────────────────────────────────────────── */

/**
 * Stable, deterministic 31-bit hash of an arbitrary string. Used to seed the
 * mock fixtures so that the same workflowId always yields the same cast
 * (avoiding UI flicker on refetch) while different workflowIds diverge.
 */
export function hashString(input: string): number {
    let hash = 0;
    for (let i = 0; i < input.length; i += 1) {
        hash = (hash << 5) - hash + input.charCodeAt(i);
        hash |= 0;
    }
    return Math.abs(hash);
}

export function bucketMembersByKind(members: WorkflowMember[]): Array<{ kind: WorkflowRoleKind; members: WorkflowMember[] }> {
    const order: WorkflowRoleKind[] = ['scheduler', 'quality', 'delivery', 'executor', 'stakeholder'];
    const buckets = new Map<WorkflowRoleKind, WorkflowMember[]>();
    for (const member of members) {
        const list = buckets.get(member.role_kind) ?? [];
        list.push(member);
        buckets.set(member.role_kind, list);
    }
    return order
        .filter((kind) => (buckets.get(kind)?.length ?? 0) > 0)
        .map((kind) => ({ kind, members: buckets.get(kind) ?? [] }));
}

export function sortStepsByOrder(steps: WorkflowStep[]): WorkflowStep[] {
    return steps.slice().sort((a, b) => a.step_order - b.step_order);
}

export function summarizeSteps(steps: WorkflowStep[]): { total: number; done: number; active: number; pending: number } {
    return steps.reduce(
        (acc, step) => {
            acc.total += 1;
            if (step.status === 'succeeded') acc.done += 1;
            else if (step.status === 'pending' || step.status === 'skipped' || step.status === 'cancelled') acc.pending += 1;
            else acc.active += 1;
            return acc;
        },
        { total: 0, done: 0, active: 0, pending: 0 },
    );
}

/* ──────────────────────────────────────────────────────────────────────────
 * Mock fixtures — used until P1.3 wires up the backend.
 * The fixtures are deterministic per workflowId so tests and refresh don't flicker.
 * ────────────────────────────────────────────────────────────────────────── */

const STATUS_PALETTE: WorkflowRunStatus[] = [
    'running',
    'paused',
    'awaiting_approval',
    'quality_retry',
    'succeeded',
    'failed',
    'cancelled',
];

const STEP_STATUS_PALETTE: WorkflowStepStatus[] = [
    'pending',
    'running',
    'succeeded',
    'quality_failed',
    'failed',
    'skipped',
];

const pickFromPalette = <T,>(palette: readonly T[], seed: number, offset = 0): T => {
    const index = (seed + offset) % palette.length;
    return palette[index];
};

const buildMockMembers = (workflowId: string, groupId: string | undefined): WorkflowMember[] => {
    const seed = hashString(`${workflowId}:${groupId ?? 'none'}`);
    const executorCount = (seed % 4) + 2; // 2..5 executors
    const hasSecondQuality = seed % 3 === 0; // 1~2 quality

    const members: WorkflowMember[] = [
        {
            agent_id: `${workflowId}-scheduler`,
            name: '项目调度官',
            role_key: 'scheduler',
            role_title: '项目调度官',
            role_kind: 'scheduler',
            is_group_leader: true,
        },
        {
            agent_id: `${workflowId}-quality-1`,
            name: '质量评审官',
            role_key: 'quality_lead',
            role_title: '质量评审官',
            role_kind: 'quality',
            is_group_leader: false,
        },
    ];

    if (hasSecondQuality) {
        members.push({
            agent_id: `${workflowId}-quality-2`,
            name: '合规复核官',
            role_key: 'quality_compliance',
            role_title: '合规复核官',
            role_kind: 'quality',
            is_group_leader: false,
        });
    }

    members.push({
        agent_id: `${workflowId}-delivery`,
        name: '交付协调官',
        role_key: 'delivery',
        role_title: '交付协调官',
        role_kind: 'delivery',
        is_group_leader: false,
    });

    const executorTitles = ['数据采集员', '前端工程师', '后端工程师', '数据分析师', '法务顾问'];
    for (let i = 0; i < executorCount; i += 1) {
        members.push({
            agent_id: `${workflowId}-exec-${i + 1}`,
            name: executorTitles[i % executorTitles.length],
            role_key: `executor_${i + 1}`,
            role_title: executorTitles[i % executorTitles.length],
            role_kind: 'executor',
            is_group_leader: false,
        });
    }

    return members;
};

const buildMockSteps = (workflowId: string): WorkflowStep[] => {
    const seed = hashString(workflowId);
    const baseTasks = [
        { role: '项目调度官', roleKind: 'scheduler' as WorkflowRoleKind, task: '解析 AO 工作流 YAML,生成执行计划' },
        { role: '数据采集员', roleKind: 'executor' as WorkflowRoleKind, task: '采集需求文档与上下文数据' },
        { role: '前端工程师', roleKind: 'executor' as WorkflowRoleKind, task: '输出前端方案与组件草图' },
        { role: '后端工程师', roleKind: 'executor' as WorkflowRoleKind, task: '输出接口契约与数据模型' },
        { role: '数据分析师', roleKind: 'executor' as WorkflowRoleKind, task: '梳理指标与监控点位' },
        { role: '质量评审官', roleKind: 'quality' as WorkflowRoleKind, task: '步检产物并打分' },
        { role: '交付协调官', roleKind: 'delivery' as WorkflowRoleKind, task: '汇总交付包并申请验收' },
    ];

    return baseTasks.map((entry, index) => {
        const status = pickFromPalette(STEP_STATUS_PALETTE, seed, index);
        const isTerminal = status === 'succeeded' || status === 'quality_failed' || status === 'failed' || status === 'skipped';
        return {
            id: createRandomUUID(),
            step_id: `step-${index + 1}`,
            step_order: index + 1,
            role: entry.role,
            role_kind: entry.roleKind,
            task: entry.task,
            status,
            quality_score: status === 'succeeded' ? 80 + ((seed + index) % 20) : status === 'quality_failed' ? 60 + ((seed + index) % 15) : null,
            depends_on: index === 0 ? [] : [`step-${index}`],
            started_at: isTerminal || status === 'running' ? '2026-07-27T08:00:00Z' : null,
            completed_at: isTerminal ? '2026-07-27T08:30:00Z' : null,
            output_excerpt: status === 'succeeded' ? '已产出中间产物,等待质检。' : null,
        };
    });
};

const buildMockStatus = (workflowId: string, memberCount: number): WorkflowStatus => {
    const seed = hashString(workflowId);
    const status = pickFromPalette(STATUS_PALETTE, seed);
    const isTerminal = status === 'succeeded' || status === 'failed' || status === 'cancelled';
    return {
        id: workflowId,
        workflow_id: workflowId,
        workflow_name: '执行群工作流',
        status,
        member_count: memberCount,
        final_score: status === 'succeeded' ? 92 : null,
        latest_version: 1,
        started_at: '2026-07-27T08:00:00Z',
        completed_at: isTerminal ? '2026-07-27T09:00:00Z' : null,
    };
};

/* ──────────────────────────────────────────────────────────────────────────
 * Public client
 * ────────────────────────────────────────────────────────────────────────── */

/**
 * Fetch the four-power cast for a workflow run. Cast derives from `WorkflowRun.scheduler_agent_id`,
 * `quality_agent_id`, `delivery_agent_id`, `executor_agent_ids`, `stakeholder_agent_ids`.
 */
export async function getWorkflowMembers(
    workflowId: string,
    groupId?: string,
): Promise<WorkflowMember[]> {
    try {
        const fetchJson = await getFetcher();
        return await fetchJson<WorkflowMember[]>(`/workflows/${workflowId}/members`);
    } catch {
        return buildMockMembers(workflowId, groupId);
    }
}

/** Fetch the DAG steps for a workflow run. */
export async function getWorkflowSteps(workflowId: string): Promise<WorkflowStep[]> {
    try {
        const fetchJson = await getFetcher();
        return await fetchJson<WorkflowStep[]>(`/workflows/${workflowId}/steps`);
    } catch {
        return buildMockSteps(workflowId);
    }
}

/** Fetch the lifecycle status of a workflow run. */
export async function getWorkflowStatus(workflowId: string): Promise<WorkflowStatus> {
    try {
        const fetchJson = await getFetcher();
        return await fetchJson<WorkflowStatus>(`/workflows/${workflowId}/status`);
    } catch {
        const members = await getWorkflowMembers(workflowId);
        return buildMockStatus(workflowId, members.length);
    }
}

export const workflowApi = {
    getWorkflowMembers,
    getWorkflowSteps,
    getWorkflowStatus,
};