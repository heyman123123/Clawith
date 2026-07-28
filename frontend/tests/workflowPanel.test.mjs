import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import {
    bucketMembersByKind,
    hashString,
    sortStepsByOrder,
    summarizeSteps,
    workflowApi,
} from '../src/services/workflowApi.ts';

/** @typedef {import('../src/services/workflowApi.ts').WorkflowMember} WorkflowMember */
/** @typedef {import('../src/services/workflowApi.ts').WorkflowRoleKind} WorkflowRoleKind */
/** @typedef {import('../src/services/workflowApi.ts').WorkflowStepStatus} WorkflowStepStatus */

test('workflowApi exports the four-power query surface', () => {
    assert.equal(typeof workflowApi.getWorkflowMembers, 'function');
    assert.equal(typeof workflowApi.getWorkflowSteps, 'function');
    assert.equal(typeof workflowApi.getWorkflowStatus, 'function');
});

test('mock members cover all four-power role kinds', async () => {
    const members = await workflowApi.getWorkflowMembers('p15-bucket-coverage');
    const kinds = new Set(members.map((member) => member.role_kind));

    // Requirement §1.4.1: scheduler / quality / delivery / executor slots must all be present.
    assert.ok(kinds.has('scheduler'), 'scheduler power slot missing');
    assert.ok(kinds.has('quality'), 'quality power slot missing');
    assert.ok(kinds.has('delivery'), 'delivery power slot missing');
    assert.ok(kinds.has('executor'), 'executor (N) power slot missing');
});

test('scheduler is flagged as the group leader', async () => {
    const members = await workflowApi.getWorkflowMembers('p15-leader-flag');
    const scheduler = members.find((member) => member.role_kind === 'scheduler');
    assert.ok(scheduler, 'no scheduler present');
    assert.equal(scheduler.is_group_leader, true);
});

test('mock steps are ordered and carry valid lifecycle statuses', async () => {
    const steps = await workflowApi.getWorkflowSteps('p15-step-coverage');
    assert.ok(steps.length > 0, 'mock steps should not be empty');
    assert.equal(steps[0].step_order, 1, 'first step must be order 1');
    for (const step of steps) {
        assert.ok(step.step_id, 'each step carries a step_id');
        assert.ok(
            ['scheduler', 'quality', 'delivery', 'executor', 'stakeholder'].includes(step.role_kind),
            `unexpected role_kind ${step.role_kind}`,
        );
    }
});

test('mock status mirrors member_count and selects a valid lifecycle state', async () => {
    const members = await workflowApi.getWorkflowMembers('p15-status-coverage');
    const status = await workflowApi.getWorkflowStatus('p15-status-coverage');
    const allowed = [
        'draft', 'composing', 'queued', 'running', 'paused',
        'awaiting_approval', 'quality_retry', 'succeeded', 'failed', 'cancelled',
    ];
    assert.ok(allowed.includes(status.status), `unexpected status ${status.status}`);
    assert.equal(status.member_count, members.length);
});

test('bucketMembersByKind groups the four-power cast in canonical order', () => {
    /** @type {WorkflowMember[]} */
    const members = [
        { agent_id: 'a', name: 'A', role_key: 'r1', role_title: 'A', role_kind: 'executor', is_group_leader: false },
        { agent_id: 'b', name: 'B', role_key: 'r2', role_title: 'B', role_kind: 'scheduler', is_group_leader: true },
        { agent_id: 'c', name: 'C', role_key: 'r3', role_title: 'C', role_kind: 'quality', is_group_leader: false },
        { agent_id: 'd', name: 'D', role_key: 'r4', role_title: 'D', role_kind: 'delivery', is_group_leader: false },
        { agent_id: 'e', name: 'E', role_key: 'r5', role_title: 'E', role_kind: 'executor', is_group_leader: false },
    ];
    const buckets = bucketMembersByKind(members);
    assert.deepEqual(
        buckets.map((bucket) => bucket.kind),
        ['scheduler', 'quality', 'delivery', 'executor'],
        'buckets must follow the four-power canonical order',
    );
    const executors = buckets.find((bucket) => bucket.kind === 'executor');
    assert.ok(executors && executors.members.length === 2, 'multi-role executors should aggregate');
});

test('sortStepsByOrder sorts by step_order ascending', () => {
    const sorted = sortStepsByOrder([
        { step_order: 3, step_id: 's3', status: 'pending' },
        { step_order: 1, step_id: 's1', status: 'pending' },
        { step_order: 2, step_id: 's2', status: 'pending' },
    ]);
    assert.deepEqual(sorted.map((s) => s.step_order), [1, 2, 3]);
});

test('summarizeSteps counts done / active / pending', () => {
    const counts = summarizeSteps([
        { status: 'succeeded' },
        { status: 'succeeded' },
        { status: 'running' },
        { status: 'pending' },
        { status: 'skipped' },
        { status: 'quality_failed' },
    ]);
    assert.equal(counts.total, 6);
    assert.equal(counts.done, 2);
    assert.equal(counts.active, 2);
    assert.equal(counts.pending, 2);
});

test('hashString is stable for the same input and divergent for different ones', () => {
    assert.equal(hashString('workflow-a'), hashString('workflow-a'));
    assert.notEqual(hashString('workflow-a'), hashString('workflow-b'));
});

test('WorkflowPanel meta covers every WorkflowRunStatus (panel source check)', () => {
    const source = readFileSync(new URL('../src/components/WorkflowPanel.tsx', import.meta.url), 'utf8');
    const required = [
        'draft', 'composing', 'queued', 'running', 'paused',
        'awaiting_approval', 'quality_retry', 'succeeded', 'failed', 'cancelled',
    ];
    for (const key of required) {
        const re = new RegExp(`\\b${key}\\s*:`);
        assert.match(source, re, `WorkflowPanel missing meta for status ${key}`);
    }
});

test('WorkflowPanel meta covers every WorkflowStepStatus (panel source check)', () => {
    const source = readFileSync(new URL('../src/components/WorkflowPanel.tsx', import.meta.url), 'utf8');
    const required = [
        'pending', 'running', 'awaiting_approval', 'quality_checking',
        'succeeded', 'quality_failed', 'failed', 'skipped', 'cancelled',
    ];
    for (const key of required) {
        const re = new RegExp(`\\b${key}\\s*:`);
        assert.match(source, re, `WorkflowPanel missing meta for step status ${key}`);
    }
});

test('Projects.tsx wires WorkflowPanel into the execution-group rail', () => {
    const source = readFileSync(new URL('../src/pages/Projects.tsx', import.meta.url), 'utf8');
    assert.match(source, /from\s+['"]\.\.\/components\/WorkflowPanel['"]/);
    assert.match(source, /<WorkflowPanel[\s\S]+workflowId=\{project\.id\}/);
});