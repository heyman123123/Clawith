import { useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { IconArrowRight, IconRobot, IconSparkles } from '@tabler/icons-react';
import { useTranslation } from 'react-i18next';
import TeamBuilderModal from './groups/TeamBuilderModal';
import { teamBuilderApi } from '../services/teamBuilderApi';
import type { TeamBuildHistoryItem } from '../types/teamBuilder';
import './groups/groups.css';

const displayDate = (value: string) => new Intl.DateTimeFormat(undefined, {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
}).format(new Date(value));

function BuildHistoryRow({ item, onOpen }: {
    item: TeamBuildHistoryItem;
    onOpen: (item: TeamBuildHistoryItem) => void;
}) {
    const { t } = useTranslation();
    const plan = item.draft.reviewed_plan ?? item.draft.generated_plan;
    const isComplete = item.job?.status === 'completed' && item.job.group_id && item.job.session_id;

    return (
        <button type="button" className="team-builder-history-row" onClick={() => onOpen(item)}>
            <span className="team-builder-history-icon"><IconRobot size={15} stroke={1.7} /></span>
            <span className="team-builder-history-copy">
                <strong>{plan?.group_name ?? item.draft.requirement}</strong>
                <span>{displayDate(item.draft.updated_at)}</span>
            </span>
            <span className={`team-builder-history-status ${item.job?.status ?? item.draft.status}`}>
                {isComplete
                    ? t('groups.teamBuilderOpenGroup', '进入群聊')
                    : t('groups.teamBuilderContinue', '继续搭建')}
            </span>
            <IconArrowRight size={15} stroke={1.8} />
        </button>
    );
}

export default function TeamBuilderPage() {
    const { t } = useTranslation();
    const navigate = useNavigate();
    const queryClient = useQueryClient();
    const history = useQuery({
        queryKey: ['team-builder-history'],
        queryFn: () => teamBuilderApi.listHistory(),
        refetchInterval: 5_000,
    });
    const completedCount = useMemo(
        () => (history.data ?? []).filter((item) => item.job?.status === 'completed').length,
        [history.data],
    );

    const openHistoryItem = (item: TeamBuildHistoryItem) => {
        if (item.job?.status === 'completed' && item.job.group_id) {
            navigate(item.job.session_id
                ? `/groups/${item.job.group_id}/${item.job.session_id}`
                : `/groups/${item.job.group_id}`,
            );
            return;
        }
        if (item.job?.id) localStorage.setItem('groups.teamBuilder.jobId', item.job.id);
        else localStorage.removeItem('groups.teamBuilder.jobId');
        localStorage.setItem('groups.teamBuilder.draftId', item.draft.id);
        window.location.reload();
    };

    return (
        <div className="team-builder-page">
            <header className="team-builder-page-header">
                <div>
                    <span className="team-builder-eyebrow"><IconSparkles size={14} stroke={1.8} /> {t('groups.teamBuilderEntry', '智能搭建团队')}</span>
                    <h1>{t('groups.teamBuilderPageTitle', '组建一个能落地执行的团队')}</h1>
                    <p>{t('groups.teamBuilderPageLead', '先确认角色与群主，再由系统创建团队、激活群主，并在群聊中开始协作。')}</p>
                </div>
                <div className="team-builder-page-stat">
                    <strong>{completedCount}</strong>
                    <span>{t('groups.teamBuilderCompletedCount', '已创建团队')}</span>
                </div>
            </header>

            <div className="team-builder-page-grid">
                <section className="team-builder-page-card team-builder-page-workbench">
                    <TeamBuilderModal
                        embedded
                        onClose={() => navigate('/groups')}
                        onCompleted={({ groupId, sessionId }) => {
                            void queryClient.invalidateQueries({ queryKey: ['groups'] });
                            void queryClient.invalidateQueries({ queryKey: ['team-builder-history'] });
                            navigate(`/groups/${groupId}/${sessionId}`);
                        }}
                    />
                </section>

                <aside className="team-builder-page-card team-builder-history">
                    <div className="team-builder-history-header">
                        <div>
                            <h2>{t('groups.teamBuilderHistoryTitle', '我的团队')}</h2>
                            <p>{t('groups.teamBuilderHistoryHint', '已完成的团队可直接进入对应群聊。')}</p>
                        </div>
                    </div>
                    <div className="team-builder-history-list">
                        {history.isLoading && <p className="team-builder-history-empty">{t('common.loading', '加载中...')}</p>}
                        {history.isError && <p className="team-builder-error">{t('groups.teamBuilderHistoryFailed', '无法读取团队列表')}</p>}
                        {history.data?.map((item) => <BuildHistoryRow key={item.draft.id} item={item} onOpen={openHistoryItem} />)}
                        {!history.isLoading && !history.isError && history.data?.length === 0 && (
                            <p className="team-builder-history-empty">{t('groups.teamBuilderHistoryEmpty', '还没有搭建记录。创建第一个协作团队吧。')}</p>
                        )}
                    </div>
                </aside>
            </div>
        </div>
    );
}
