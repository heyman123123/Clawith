import { useEffect } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { IconArrowRight, IconBriefcase2, IconUsersGroup } from '@tabler/icons-react';
import { groupApi } from '../services/groupApi';
import { hrReviewApi } from '../services/hrReviewApi';
import { projectApi } from '../services/projectApi';
import type { CSSProperties } from 'react';

import { isHrReviewBoardGroup } from '../utils/hrReviewBoard';

export default function Projects() {
    const { t } = useTranslation();
    const navigate = useNavigate();
    const queryClient = useQueryClient();
    const { data: projects = [] } = useQuery({ queryKey: ['projects'], queryFn: projectApi.list });
    const { data: groups = [], isFetching: groupsFetching } = useQuery({
        queryKey: ['groups'],
        queryFn: groupApi.list,
    });
    const { data: shareholderGroup, isFetching: shareholderFetching } = useQuery({
        queryKey: ['shareholder-group'],
        queryFn: projectApi.shareholderGroup,
    });
    const hrReviewBoard = groups.find((group) => isHrReviewBoardGroup(group));
    const provisioning = !hrReviewBoard || !shareholderGroup;

    useEffect(() => {
        let cancelled = false;
        const ensureResidentGroups = async () => {
            try {
                const tasks: Promise<unknown>[] = [];
                if (!hrReviewBoard) {
                    tasks.push(hrReviewApi.ensureBoard());
                }
                if (!shareholderGroup) {
                    tasks.push(projectApi.createShareholderGroup());
                }
                if (tasks.length === 0) return;
                await Promise.all(tasks);
                if (cancelled) return;
                await Promise.all([
                    queryClient.invalidateQueries({ queryKey: ['groups'] }),
                    queryClient.invalidateQueries({ queryKey: ['shareholder-group'] }),
                ]);
            } catch (error) {
                console.error('Failed to provision resident governance groups', error);
            }
        };
        void ensureResidentGroups();
        return () => {
            cancelled = true;
        };
    }, [hrReviewBoard, shareholderGroup, queryClient]);

    const hrButtonLabel = hrReviewBoard
        ? '去 HR 群提需求'
        : (groupsFetching || provisioning ? '正在补齐 HR 评审群…' : 'HR 评审群尚未就绪');
    const shareholderButtonLabel = shareholderGroup
        ? t('projects.enterShareholderGroup')
        : (shareholderFetching || provisioning
            ? t('projects.shareholderGroupProvisioning')
            : t('projects.shareholderGroupProvisioning'));

    return <div style={{ maxWidth: 1080, margin: '0 auto', padding: '36px 32px 56px' }}>
        <div style={{ marginBottom: 28 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, color: 'var(--accent, #635bff)' }}>
                <IconBriefcase2 size={24} /><span style={{ fontWeight: 700 }}>需求与执行</span>
            </div>
            <h1 style={{ margin: '10px 0 8px', fontSize: 28 }}>在 HR 群提出需求，确认方案后自动进入执行群</h1>
            <p style={{ margin: 0, color: 'var(--text-secondary, #6b7280)' }}>
                新建会话描述你的目标与交付物，HR 团队会给出多套组建方案；确认后会创建智能体与执行群，并启动初始化对话。
            </p>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10, marginTop: 14 }}>
                <button
                    type="button"
                    onClick={() => hrReviewBoard && navigate(`/groups/${hrReviewBoard.id}`)}
                    disabled={!hrReviewBoard}
                    style={primaryStyle}
                >
                    <IconUsersGroup size={15} />
                    {hrButtonLabel}
                    <IconArrowRight size={16} />
                </button>
                <button
                    type="button"
                    onClick={() => shareholderGroup && navigate(`/groups/${shareholderGroup.group_id}`)}
                    disabled={!shareholderGroup}
                    style={secondaryStyle}
                >
                    <IconUsersGroup size={15} />
                    {shareholderButtonLabel}
                </button>
            </div>
        </div>

        {projects.length > 0 && (
            <section style={cardStyle}>
                <h2 style={headingStyle}>执行中的需求</h2>
                <p style={{ color: 'var(--text-secondary, #6b7280)', margin: '0 0 16px' }}>
                    已确认方案并进入执行阶段的工作流。
                </p>
                {projects.map((project) => (
                    <div
                        key={project.id}
                        style={{
                            display: 'flex',
                            justifyContent: 'space-between',
                            gap: 12,
                            borderTop: '1px solid var(--border, #e5e7eb)',
                            padding: '13px 0',
                        }}
                    >
                        <div>
                            <strong>{project.name}</strong>
                            <div style={{ color: 'var(--text-secondary, #6b7280)', fontSize: 13, marginTop: 4 }}>
                                {project.members.length} 位成员 · {project.status === 'active' ? '治理与团队已就绪' : project.status}
                                {project.failure_reason ? ` · ${project.failure_reason}` : ''}
                            </div>
                        </div>
                        <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', justifyContent: 'end', gap: 8 }}>
                            {project.decision_group_id && (
                                <button onClick={() => navigate(`/groups/${project.decision_group_id}`)} style={secondaryStyle}>
                                    决策群
                                </button>
                            )}
                            {project.group_id && (
                                <button onClick={() => navigate(`/groups/${project.group_id}`)} style={primaryStyle}>
                                    执行群
                                </button>
                            )}
                        </div>
                    </div>
                ))}
            </section>
        )}
    </div>;
}

const cardStyle: CSSProperties = { background: 'var(--bg-card, #fff)', border: '1px solid var(--border, #e5e7eb)', borderRadius: 14, padding: 22, boxShadow: '0 1px 2px rgba(0,0,0,.03)' };
const headingStyle: CSSProperties = { margin: '0 0 10px', fontSize: 18 };
const primaryStyle: CSSProperties = { display: 'inline-flex', alignItems: 'center', gap: 8, border: 0, borderRadius: 8, padding: '10px 14px', color: '#fff', background: 'var(--accent, #635bff)', fontWeight: 650, cursor: 'pointer' };
const secondaryStyle: CSSProperties = { display: 'inline-flex', alignItems: 'center', gap: 8, border: '1px solid var(--border, #d1d5db)', borderRadius: 8, padding: '7px 10px', background: 'transparent', color: 'inherit', cursor: 'pointer' };
