import type { MessageFeedbackRequest, MessageFeedbackResponse } from '@/types';

export type FeedbackRating = MessageFeedbackResponse['rating'];
export type FeedbackTag = NonNullable<MessageFeedbackRequest['tags']>[number];

export const FEEDBACK_TAG_LABELS: Record<FeedbackTag, string> = {
  resolved_problem: '解决了我的问题',
  followed_instructions: '遵循了我的指示',
  high_quality: '代码/输出质量好',
  fast_efficient: '快速高效',
  helpful_initiative: '有帮助的自主行为',
  incorrect_incomplete: '不正确或不完整',
  failed_instructions: '没有遵循我的指示',
  biased_out_of_scope: '偏题 / 超出范围',
  lost_context: '丢失上下文',
  slow_or_broken: '速度慢或有故障',
  safety_or_legal: '安全或法律问题',
  other: '其他',
};

export const POSITIVE_FEEDBACK_TAGS: FeedbackTag[] = [
  'resolved_problem',
  'followed_instructions',
  'high_quality',
  'fast_efficient',
  'helpful_initiative',
  'other',
];

export const NEGATIVE_FEEDBACK_TAGS: FeedbackTag[] = [
  'incorrect_incomplete',
  'failed_instructions',
  'biased_out_of_scope',
  'lost_context',
  'slow_or_broken',
  'safety_or_legal',
  'other',
];

export function feedbackTagsFor(rating: FeedbackRating): FeedbackTag[] {
  return rating === 'positive' ? POSITIVE_FEEDBACK_TAGS : NEGATIVE_FEEDBACK_TAGS;
}
