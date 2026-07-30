import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const api = readFileSync(new URL('../src/services/aiMonitoringApi.ts', import.meta.url), 'utf8');
const center = readFileSync(new URL('../src/components/AIMonitoringCenter.tsx', import.meta.url), 'utf8');
const drawer = readFileSync(new URL('../src/components/AIInteractionDetailDrawer.tsx', import.meta.url), 'utf8');
const list = readFileSync(new URL('../src/components/AIInteractionList.tsx', import.meta.url), 'utf8');
const groupPanel = readFileSync(new URL('../src/pages/groups/GroupSidePanel.tsx', import.meta.url), 'utf8');
const dashboard = readFileSync(new URL('../src/pages/Dashboard.tsx', import.meta.url), 'utf8');

test('AI monitoring uses the tenant-safe overview and detail endpoints', () => {
  assert.match(api, /\/ai-monitoring\/overview\?page=/);
  assert.match(api, /\/ai-monitoring\/groups\/\$\{groupId\}\/interactions/);
  assert.match(api, /\/ai-monitoring\/interactions\/\$\{interactionId\}/);
});

test('AI monitoring is admin-only, refreshes, and exposes redacted detail', () => {
  assert.match(center, /user\?\.role === 'org_admin'/);
  assert.match(center, /user\?\.role === 'platform_admin'/);
  assert.match(center, /refetchInterval: 15_000/);
  assert.match(center, /AIInteractionDetailDrawer/);
  assert.match(drawer, /request_context/);
  assert.match(drawer, /response_content/);
  assert.match(drawer, /detail\.data\.error/);
  assert.match(drawer, /position: 'fixed'/);
  assert.match(list, /started_at/);
  assert.match(list, /finished_at/);
  assert.match(list, /onPage/);
  assert.match(groupPanel, /groupInteractions/);
  assert.match(groupPanel, /member !== leader/);
});

test('dashboard places the monitoring center below its main content', () => {
  assert.match(dashboard, /<AIMonitoringCenter\s*\/>/);
});
