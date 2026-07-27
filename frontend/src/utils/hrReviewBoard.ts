export const HR_REVIEW_BOARD_GROUP_TYPE = 'hr_review_board';
export const HR_REVIEW_BOARD_GROUP_NAME = 'HR 评审群';

export function isHrReviewBoardGroup(
    group: { group_type?: string | null; name: string } | null | undefined,
): boolean {
    if (!group) return false;
    return group.group_type === HR_REVIEW_BOARD_GROUP_TYPE
        || group.name === HR_REVIEW_BOARD_GROUP_NAME;
}
