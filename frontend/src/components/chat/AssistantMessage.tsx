'use client';

import { memo, useEffect, useState } from 'react';
import { useStreamStore, interleaveFlowItems } from '@/stores/streamStore';
import { useConversationStore } from '@/stores/conversationStore';
import { useCopyFeedback } from '@/hooks/useCopyFeedback';
import MarkdownBlock from '@/components/markdown/MarkdownBlock';
import { CopyIcon } from '@/components/ui/CopyIcon';
import { FeedbackRatingIcon } from '@/components/ui/FeedbackRatingIcon';
import * as api from '@/lib/api';
import type { MessageFeedbackResponse } from '@/types';
import type { FeedbackRating, FeedbackTag } from '@/lib/messageFeedback';
import { reconstructFlow } from '@/lib/reconstructSegments';
import AgentSegmentBlock from './AgentSegmentBlock';
import InjectFlowBlock from './InjectFlowBlock';
import CompactionFlowBlock from './CompactionFlowBlock';
import ErrorFlowBlock from './ErrorFlowBlock';
import ProcessingFlow from './ProcessingFlow';
import MessageFeedbackDialog from './MessageFeedbackDialog';

interface AssistantMessageProps {
  content: string;
  messageId?: string;
  feedback?: MessageFeedbackResponse | null;
  /** Persisted turn metrics from the message row; shape matches ExecutionMetrics in events.ts. */
  executionMetrics?: {
    total_duration_ms?: number | null;
    cached_input_tokens_partial?: boolean;
    total_token_usage?: {
      total_tokens?: number | null;
      cached_input_tokens?: number | null;
    } | null;
  } | null;
}

function AssistantMessage({ content, messageId, feedback = null, executionMetrics }: AssistantMessageProps) {
  const { copied, copy } = useCopyFeedback();
  const [dialogRating, setDialogRating] = useState<FeedbackRating | null>(null);
  const [feedbackSaving, setFeedbackSaving] = useState(false);
  const [feedbackError, setFeedbackError] = useState<string | null>(null);
  const completedSegs = useStreamStore(
    (s) => messageId ? s.completedSegments.get(messageId) : undefined
  );
  const completedBlocks = useStreamStore(
    (s) => messageId ? s.completedNonAgentBlocks.get(messageId) : undefined
  );
  const conversationId = useConversationStore((s) => s.current?.id);
  const updateMessageFeedback = useConversationStore((s) => s.updateMessageFeedback);

  // Lazy-load historical segments from persisted events when session cache is empty
  useEffect(() => {
    if (!messageId || !conversationId || completedSegs !== undefined) return;

    let cancelled = false;
    api.getMessageEvents(conversationId, messageId)
      .then((res) => {
        if (cancelled || res.events.length === 0) return;
        const { segments, blocks } = reconstructFlow(res.events);
        const store = useStreamStore.getState();
        if (segments.length > 0) {
          const newMap = new Map(store.completedSegments);
          newMap.set(messageId, segments);
          useStreamStore.setState({ completedSegments: newMap });
        }
        if (blocks.length > 0) {
          const nabMap = new Map(store.completedNonAgentBlocks);
          nabMap.set(messageId, blocks);
          useStreamStore.setState({ completedNonAgentBlocks: nabMap });
        }
      })
      .catch(() => {
        // Silently ignore — historical segments are non-critical
      });

    return () => { cancelled = true; };
  }, [messageId, conversationId, completedSegs]);

  const handleCopy = () => copy(content);

  const openFeedback = (rating: FeedbackRating) => {
    setFeedbackError(null);
    setDialogRating(rating);
  };

  const submitFeedback = async (tags: FeedbackTag[], detail: string) => {
    if (!conversationId || !messageId || !dialogRating) return;
    setFeedbackSaving(true);
    setFeedbackError(null);
    try {
      const saved = await api.putMessageFeedback(conversationId, messageId, {
        rating: dialogRating,
        tags,
        detail: detail.trim() || null,
      });
      updateMessageFeedback(messageId, saved);
      setDialogRating(null);
    } catch (error) {
      setFeedbackError(error instanceof Error ? error.message : '提交反馈失败');
    } finally {
      setFeedbackSaving(false);
    }
  };

  const removeFeedback = async () => {
    if (!conversationId || !messageId) return;
    setFeedbackSaving(true);
    setFeedbackError(null);
    try {
      await api.deleteMessageFeedback(conversationId, messageId);
      updateMessageFeedback(messageId, null);
      setDialogRating(null);
    } catch (error) {
      setFeedbackError(error instanceof Error ? error.message : '撤销反馈失败');
    } finally {
      setFeedbackSaving(false);
    }
  };

  const hasSegs = completedSegs && completedSegs.length > 0;
  const hasBlocks = completedBlocks && completedBlocks.length > 0;
  const flowItems = (hasSegs || hasBlocks)
    ? interleaveFlowItems(completedSegs ?? [], completedBlocks ?? [])
    : null;
  const hasError = !!completedBlocks?.some((b) => b.kind === 'error');

  return (
    <div className="group relative">
      {/* Completed execution segments (collapsible) */}
      {flowItems && flowItems.length > 0 && (
        <div className="mb-3">
          <ProcessingFlow
            agentStepCount={completedSegs?.length ?? 0}
            isActive={false}
            defaultExpanded={false}
            hasError={hasError}
            totalDurationMs={executionMetrics?.total_duration_ms ?? null}
            totalTokens={executionMetrics?.total_token_usage?.total_tokens ?? null}
            cachedInputTokens={executionMetrics?.total_token_usage?.cached_input_tokens ?? null}
            cachedInputTokensPartial={executionMetrics?.cached_input_tokens_partial !== false}
          >
            {flowItems.map((item) => {
              if (item.kind === 'agent') {
                return (
                  <AgentSegmentBlock
                    key={item.segment.id}
                    segment={item.segment}
                    isActive={false}
                    defaultExpanded={false}
                    stepNumber={item.index + 1}
                  />
                );
              }
              if (item.kind === 'inject') {
                return <InjectFlowBlock key={item.id} content={item.content} />;
              }
              if (item.kind === 'compaction') {
                return <CompactionFlowBlock key={item.id} block={item} />;
              }
              if (item.kind === 'error') {
                return <ErrorFlowBlock key={item.id} message={item.error} requestId={item.requestId} />;
              }
              return null;
            })}
          </ProcessingFlow>
        </div>
      )}

      <MarkdownBlock diagrams>{content}</MarkdownBlock>
      {/* Action bar on hover */}
      <div className="absolute -bottom-7 left-0 flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
        <button
          onClick={handleCopy}
          className="p-1 rounded text-text-tertiary dark:text-text-tertiary-dark hover:text-text-secondary dark:hover:text-text-secondary-dark hover:bg-surface dark:hover:bg-bg-dark transition-colors"
          aria-label="Copy response"
          title={copied ? '已复制' : '复制'}
        >
          <CopyIcon copied={copied} />
        </button>
        <button
          type="button"
          onClick={() => openFeedback('positive')}
          aria-label="赞"
          aria-pressed={feedback?.rating === 'positive'}
          title="赞"
          className={`p-1 rounded transition-colors ${
            feedback?.rating === 'positive'
              ? 'bg-accent/10 text-accent'
              : 'text-text-tertiary dark:text-text-tertiary-dark hover:text-text-secondary dark:hover:text-text-secondary-dark hover:bg-surface dark:hover:bg-bg-dark'
          }`}
        >
          <FeedbackRatingIcon rating="positive" />
        </button>
        <button
          type="button"
          onClick={() => openFeedback('negative')}
          aria-label="踩"
          aria-pressed={feedback?.rating === 'negative'}
          title="踩"
          className={`p-1 rounded transition-colors ${
            feedback?.rating === 'negative'
              ? 'bg-status-warning/10 text-status-warning'
              : 'text-text-tertiary dark:text-text-tertiary-dark hover:text-text-secondary dark:hover:text-text-secondary-dark hover:bg-surface dark:hover:bg-bg-dark'
          }`}
        >
          <FeedbackRatingIcon rating="negative" />
        </button>
      </div>

      {dialogRating ? (
        <MessageFeedbackDialog
          key={dialogRating}
          rating={dialogRating}
          current={feedback?.rating === dialogRating ? feedback : null}
          saving={feedbackSaving}
          error={feedbackError}
          onSubmit={submitFeedback}
          onDelete={removeFeedback}
          onClose={() => setDialogRating(null)}
        />
      ) : null}
    </div>
  );
}

export default memo(AssistantMessage);
