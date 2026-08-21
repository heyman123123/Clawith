// Orchestrator Settings: toggle proactive intent-detection in Chat.
import React, { useState } from 'react';
import { orchestratorApi } from '../../services/orchestrator';

export function OrchestratorSettings() {
  const [enabled, setEnabled] = useState(true);
  const [saving, setSaving] = useState(false);

  async function toggle(next: boolean) {
    setSaving(true);
    try {
      await orchestratorApi.setProactiveEnabled(next);
      setEnabled(next);
    } catch (e) {
      // silent; UI shows previous state
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="p-4 border rounded">
      <h3 className="font-medium mb-2">智能项目编排</h3>
      <label className="flex items-start gap-2 text-sm">
        <input
          type="checkbox"
          checked={enabled}
          disabled={saving}
          onChange={(e) => toggle(e.target.checked)}
        />
        <span>
          启用 AI 主动识别项目意图(在 Chat 中识别后建议创建项目)
          <br />
          <span className="text-xs text-gray-500">
            关闭后,只能通过 Projects 页面手动创建项目。
          </span>
        </span>
      </label>
    </div>
  );
}
