import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const api = readFileSync(new URL('../src/services/groupWorkflowApi.ts', import.meta.url), 'utf8');
const panel = readFileSync(new URL('../src/pages/groups/GroupWorkflowTab.tsx', import.meta.url), 'utf8');
const sidePanel = readFileSync(new URL('../src/pages/groups/GroupSidePanel.tsx', import.meta.url), 'utf8');
const modal = readFileSync(new URL('../src/pages/groups/WorkflowManageModal.tsx', import.meta.url), 'utf8');
const realtime = readFileSync(new URL('../src/hooks/useGroupRealtime.ts', import.meta.url), 'utf8');

test('workflow panel is state-led and highlights leader action and approval gates', () => {
  assert.match(panel, /leader_next_action/);
  assert.match(panel, /awaiting_approval/);
  assert.match(panel, /workflow-stage-rail/);
  assert.match(panel, /workflow-queues/);
});

test('workflow tab sits immediately before the announcement tab and uses exact API routes', () => {
  assert.match(sidePanel, /key: 'workflow'[\s\S]*?key: 'announcement'/);
  assert.match(api, /\/groups\/\$\{groupId\}\/workflow/);
  assert.match(api, /expected_version/);
  assert.match(realtime, /workflow\.changed/);
});

test('manager panel provides presets, AI draft confirmation, pagination, and pause controls', () => {
  assert.match(modal, /groupWorkflowApi\.preset/);
  assert.match(modal, /groupWorkflowApi\.createDraft/);
  assert.match(modal, /groupWorkflowApi\.confirmDraft/);
  assert.match(modal, /groupWorkflowApi\.pause/);
  assert.match(modal, /groupWorkflowApi\.events/);
});
