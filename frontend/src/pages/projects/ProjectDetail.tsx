// ProjectDetail page - shows Task Board + Chief Chat for a single Group.
// tenant_id comes from useAuthStore; chief_run_id is appended by DraftPreview
// when navigating to /projects/{groupId}?chiefRunId={chief_run_id}.
import React from 'react';
import { useParams, useSearchParams } from 'react-router-dom';
import { TaskBoard } from './components/TaskBoard';
import { ChiefChat } from './components/ChiefChat';
import { useAuthStore } from '../../stores';

export default function ProjectDetail() {
  const { groupId } = useParams<{ groupId: string }>();
  const [searchParams] = useSearchParams();
  const tenantId = useAuthStore((s) => s.user?.tenant_id) ?? '';
  // DraftPreview appends `chiefRunId` after successful creation so we don't
  // need an extra /api/groups/{id} round-trip just to find the Chief.
  const chiefRunId = searchParams.get('chiefRunId') ?? '';

  return (
    <div className="grid grid-cols-3 gap-4 h-[calc(100vh-80px)]">
      <div className="col-span-2 overflow-hidden">
        {groupId && tenantId ? (
          <TaskBoard tenantId={tenantId} groupId={groupId} />
        ) : (
          <div className="p-8 text-gray-500">未指定 Group</div>
        )}
      </div>
      <div className="p-4">
        {chiefRunId ? (
          <ChiefChat chiefRunId={chiefRunId} />
        ) : (
          <div className="text-xs text-gray-400 p-2">
            Chief Run ID 未设置(草稿创建完成后会自动关联)
          </div>
        )}
      </div>
    </div>
  );
}
