// ProjectDetail page - shows Task Board + Chief Chat for a single Group.
import React from 'react';
import { useParams } from 'react-router-dom';
import { TaskBoard } from './components/TaskBoard';
import { ChiefChat } from './components/ChiefChat';

// In real wiring, fetch group + chief_run_id from /api/groups/{id}.
// For v1, the orchestrator's createDraft response includes both.
export function ProjectDetail() {
  const { groupId } = useParams<{ groupId: string }>();

  // TODO: fetch group + chief_run_id from API; placeholder UUIDs for v1 demo.
  const tenantId = '00000000-0000-0000-0000-000000000000';
  const chiefRunId = '00000000-0000-0000-0000-000000000000';

  return (
    <div className="grid grid-cols-3 gap-4 h-[calc(100vh-80px)]">
      <div className="col-span-2 overflow-hidden">
        {groupId ? (
          <TaskBoard tenantId={tenantId} groupId={groupId} />
        ) : (
          <div className="p-8">未指定 Group</div>
        )}
      </div>
      <div className="p-4">
        <ChiefChat chiefRunId={chiefRunId} />
      </div>
    </div>
  );
}
