import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const api = readFileSync(new URL('../src/services/teamBuilderApi.ts', import.meta.url), 'utf8');
const modal = readFileSync(new URL('../src/pages/groups/TeamBuilderModal.tsx', import.meta.url), 'utf8');
const groupsPage = readFileSync(new URL('../src/pages/groups/GroupsPage.tsx', import.meta.url), 'utf8');
const composer = readFileSync(new URL('../src/pages/groups/MessageComposer.tsx', import.meta.url), 'utf8');
const createModal = readFileSync(new URL('../src/pages/groups/CreateGroupModal.tsx', import.meta.url), 'utf8');
const builderPage = readFileSync(new URL('../src/pages/TeamBuilderPage.tsx', import.meta.url), 'utf8');
const layout = readFileSync(new URL('../src/pages/Layout.tsx', import.meta.url), 'utf8');
const app = readFileSync(new URL('../src/App.tsx', import.meta.url), 'utf8');

test('team builder uses the durable draft and job endpoints with an idempotency key', () => {
  assert.match(api, /const root = '\/team-build-drafts'/);
  assert.match(api, /createDraft:[\s\S]*?fetchJson/);
  assert.match(api, /workflow_preset/);
  assert.match(api, /reviseDraft:[\s\S]*?\/revise/);
  assert.match(api, /applyWorkflowPreset:[\s\S]*?\/workflow-preset/);
  assert.match(api, /updateDraft:[\s\S]*?reviewed_plan/);
  assert.match(api, /confirmDraft:[\s\S]*?plan_version[\s\S]*?idempotency_key/);
  assert.match(api, /getJob:[\s\S]*?\/jobs\//);
  assert.match(modal, /createRandomUUID\(\)/);
  assert.match(modal, /POLL_INTERVAL_MS/);
  assert.match(modal, /workflowPreset/);
  assert.match(modal, /reviseScope/);
  assert.match(modal, /teamBuilderSop/);
  assert.match(modal, /teamBuilderReviseContext/);
});

test('the builder recovers durable draft and job IDs across refreshes', () => {
  assert.match(modal, /groups\.teamBuilder\.draftId/);
  assert.match(modal, /groups\.teamBuilder\.jobId/);
  assert.match(modal, /localStorage\.getItem\(JOB_STORAGE_KEY\)/);
  assert.match(builderPage, /<TeamBuilderModal/);
  assert.match(builderPage, /navigate\(`\/groups\/\$\{groupId\}\/\$\{sessionId\}`\)/);
});

test('groups route natural messages to the designated leader while keeping normal mentions available', () => {
  assert.match(composer, /defaultLeaderParticipantId/);
  assert.match(composer, /participantId: leader\.participant_id/);
  assert.match(groupsPage, /defaultLeaderParticipantId=\{activeGroup\.leader_participant_id\}/);
  assert.match(groupsPage, /teamLeaderDisplay/);
});

test('manual group creation explicitly selects an agent leader', () => {
  assert.match(createModal, /leaderParticipantId/);
  assert.match(createModal, /candidate\.participant_type === 'agent' && picked/);
  assert.match(groupsPage, /leader_participant_id: leaderParticipantId/);
});

test('team builder has its own page above groups and completed rows open their group chat', () => {
  assert.match(builderPage, /teamBuilderApi\.listHistory/);
  assert.match(builderPage, /\/groups\/\$\{item\.job\.group_id\}/);
  assert.match(layout, /to="\/team-builder"[\s\S]*?nav\.teamBuilder/);
  assert.match(layout, /to="\/team-builder"[\s\S]*?to="\/groups"/);
  assert.match(app, /path="team-builder"/);
});
