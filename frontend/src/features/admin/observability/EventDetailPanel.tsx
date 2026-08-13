'use client';

import { useCallback, useState } from 'react';
import { useCopyFeedback } from '@/hooks/useCopyFeedback';
import * as api from '@/lib/api';
import type { AdminEventItem } from '@/lib/api';
import { triggerBlobDownload } from '@/lib/download';
import { parseUtcIso } from '@/lib/time';
import {
  formatLlmTokenUsage,
  formatNativeToolCalls,
  nativeToolCalls,
} from './eventDiagnostics';

export function serializeEventToText(event: AdminEventItem): string {
  const lines: string[] = [];
  const d = event.data;
  lines.push(`ID: ${event.id}`);
  lines.push(`类型: ${event.event_type}`);
  lines.push(`Agent: ${event.agent_name || '-'}`);
  lines.push(`时间: ${parseUtcIso(event.created_at).toLocaleString('zh-CN')}`);

  if (d != null && event.event_type === 'llm_complete') {
    lines.push(`模型: ${(d.model as string) || '-'}`);
    lines.push(`耗时: ${d.duration_ms as number}ms`);
    if (d.token_usage != null) {
      const t = d.token_usage as Record<string, number>;
      lines.push(`Tokens: ${formatLlmTokenUsage(t)}`);
    }
    if (d.reasoning_content != null) lines.push(`\n--- Reasoning ---\n${d.reasoning_content as string}`);
    if (d.content != null) lines.push(`\n--- Response ---\n${d.content as string}`);
    const calls = nativeToolCalls(d);
    if (calls.length > 0) lines.push(`\n--- Tool Calls ---\n${formatNativeToolCalls(calls)}`);
  }
  if (d != null && (event.event_type === 'tool_start' || event.event_type === 'tool_complete')) {
    if (d.call_id != null) lines.push(`Call ID: ${d.call_id as string}`);
    lines.push(`工具: ${(d.tool as string) || '-'}`);
    if (d.reason != null) lines.push(`调用说明: ${d.reason as string}`);
    if (d.duration_ms != null) lines.push(`耗时: ${d.duration_ms}ms`);
    if (d.success != null) lines.push(`状态: ${d.success ? 'OK' : 'FAIL'}`);
    if (d.params != null) lines.push(`\n--- Params ---\n${JSON.stringify(d.params, null, 2)}`);
    if (d.result_data != null) lines.push(`\n--- Result ---\n${typeof d.result_data === 'string' ? d.result_data : JSON.stringify(d.result_data, null, 2)}`);
    if (d.error != null) lines.push(`\n--- Error ---\n${d.error as string}`);
    if (d.metadata != null) lines.push(`\n--- Metadata ---\n${JSON.stringify(d.metadata, null, 2)}`);
  }
  if (d != null && event.event_type === 'agent_start' && d.system_prompt != null) {
    lines.push(`\n--- System Prompt ---\n${d.system_prompt as string}`);
  }
  if (d != null && event.event_type === 'agent_start' && d.reminder != null) {
    lines.push(`\n--- Reminder ---\n${d.reminder as string}`);
  }
  if (d != null && event.event_type === 'agent_start') {
    if (d.model != null) lines.push(`模型: ${d.model as string}`);
  }
  if (d != null && event.event_type === 'error') {
    lines.push(`\n--- Error ---\n${(d.error as string) || JSON.stringify(d, null, 2)}`);
  }
  if (d != null && !['llm_complete', 'tool_start', 'tool_complete', 'agent_start', 'error'].includes(event.event_type)) {
    lines.push(`\n--- Data ---\n${JSON.stringify(d, null, 2)}`);
  }
  return lines.join('\n');
}

export default function EventDetailPanel({
  event,
  conversationId,
  messageId,
  onClose,
}: {
  event: AdminEventItem;
  conversationId: string | null;
  messageId: string | null;
  onClose: () => void;
}) {
  const { copied, copy } = useCopyFeedback();

  const handleCopy = useCallback(() => {
    copy(serializeEventToText(event));
  }, [event, copy]);

  return (
    <div className="w-[360px] flex-shrink-0 flex flex-col overflow-hidden border-l border-border dark:border-border-dark">
      <div className="px-4 pt-3 pb-2 border-b border-border dark:border-border-dark flex items-center justify-between">
        <h3 className="text-sm font-semibold text-text-primary dark:text-text-primary-dark">
          {event.event_type}
        </h3>
        <div className="flex items-center gap-1">
          <button
            onClick={handleCopy}
            className="p-1 rounded-md text-text-tertiary dark:text-text-tertiary-dark hover:text-text-secondary dark:hover:text-text-secondary-dark hover:bg-surface dark:hover:bg-bg-dark transition-colors"
            title="复制全部内容"
          >
            {copied ? (
              <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                <path d="M3.5 8.5l3 3 6-7" />
              </svg>
            ) : (
              <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                <rect x="5" y="5" width="9" height="9" rx="1" />
                <path d="M11 5V3a1 1 0 00-1-1H3a1 1 0 00-1 1v7a1 1 0 001 1h2" />
              </svg>
            )}
          </button>
          <button
            onClick={onClose}
            className="p-1 rounded-md text-text-tertiary dark:text-text-tertiary-dark hover:text-text-secondary dark:hover:text-text-secondary-dark transition-colors"
          >
            <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
              <path d="M4 4l8 8M12 4l-8 8" />
            </svg>
          </button>
        </div>
      </div>
      <div className="flex-1 overflow-y-auto px-4 py-3">
        <EventDetail event={event} conversationId={conversationId} messageId={messageId} />
      </div>
    </div>
  );
}

function EventDetail({
  event,
  conversationId,
  messageId,
}: {
  event: AdminEventItem;
  conversationId: string | null;
  messageId: string | null;
}) {
  const d = event.data;

  return (
    <div className="space-y-3 text-sm">
      <div className="space-y-1">
        <DetailRow label="ID" value={String(event.id)} />
        <DetailRow label="类型" value={event.event_type} />
        <DetailRow label="Agent" value={event.agent_name || '-'} />
        <DetailRow label="时间" value={parseUtcIso(event.created_at).toLocaleString('zh-CN')} />
      </div>

      {d != null && event.event_type === 'llm_complete' ? (
        <div className="space-y-2">
          <DetailRow label="模型" value={(d.model as string) || '-'} />
          <DetailRow label="耗时" value={`${d.duration_ms as number}ms`} />
          {d.token_usage != null ? (
            <DetailRow label="Tokens" value={formatLlmTokenUsage(d.token_usage as Record<string, number>)} />
          ) : null}
          {d.reasoning_content != null ? <DetailBlock label="Reasoning" content={d.reasoning_content as string} /> : null}
          {d.content != null ? <DetailBlock label="Response" content={d.content as string} /> : null}
          {nativeToolCalls(d).length > 0 ? (
            <DetailBlock label="Tool Calls" content={formatNativeToolCalls(nativeToolCalls(d))} />
          ) : null}
          <ReconstructSection
            mode="call"
            conversationId={conversationId}
            messageId={messageId}
            event={event}
          />
        </div>
      ) : null}

      {d != null && (event.event_type === 'tool_start' || event.event_type === 'tool_complete') ? (
        <div className="space-y-2">
          {d.call_id != null ? <DetailRow label="Call ID" value={d.call_id as string} /> : null}
          <DetailRow label="工具" value={(d.tool as string) || '-'} />
          {d.reason != null ? <DetailBlock label="调用说明" content={d.reason as string} /> : null}
          {d.duration_ms != null ? <DetailRow label="耗时" value={`${d.duration_ms}ms`} /> : null}
          {d.success != null ? <DetailRow label="状态" value={d.success ? 'OK' : 'FAIL'} /> : null}
          {d.params != null ? <DetailBlock label="Params" content={JSON.stringify(d.params, null, 2)} /> : null}
          {d.result_data != null ? (
            <DetailBlock label="Result" content={typeof d.result_data === 'string' ? d.result_data : JSON.stringify(d.result_data, null, 2)} />
          ) : null}
          {d.error != null ? <DetailBlock label="Error" content={d.error as string} /> : null}
          {d.metadata != null ? <DetailBlock label="Metadata" content={JSON.stringify(d.metadata, null, 2)} /> : null}
        </div>
      ) : null}

      {event.event_type === 'agent_start' ? (
        <>
          {d?.model != null ? <DetailRow label="Model" value={d.model as string} /> : null}
          {d?.system_prompt != null ? <DetailBlock label="System Prompt" content={d.system_prompt as string} /> : null}
          {d?.reminder != null ? (
            <DetailBlock label="Reminder（动态，并入末条消息）" content={d.reminder as string} />
          ) : null}
          <ReconstructSection
            mode="prompt"
            conversationId={conversationId}
            messageId={messageId}
            event={event}
          />
        </>
      ) : null}

      {d != null && event.event_type === 'error' ? (
        <DetailBlock label="Error" content={(d.error as string) || JSON.stringify(d, null, 2)} />
      ) : null}

      {d != null && !['llm_complete', 'tool_start', 'tool_complete', 'agent_start', 'error'].includes(event.event_type) ? (
        <DetailBlock label="Data" content={JSON.stringify(d, null, 2)} />
      ) : null}
    </div>
  );
}

function DetailRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex gap-2">
      <span className="flex-shrink-0 w-14 text-text-tertiary dark:text-text-tertiary-dark text-xs">{label}</span>
      <span className="text-text-primary dark:text-text-primary-dark text-xs break-all">{value}</span>
    </div>
  );
}

function ReconstructSection({
  mode,
  conversationId,
  messageId,
  event,
}: {
  mode: 'prompt' | 'call';
  conversationId: string | null;
  messageId: string | null;
  event: AdminEventItem;
}) {
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<
    api.AdminPromptReconstructResponse | api.AdminLlmCallReconstructResponse | null
  >(null);
  const [error, setError] = useState<string | null>(null);

  const eventId = event.event_id;
  const canReconstruct = conversationId != null && messageId != null && eventId != null;

  const handleReconstruct = useCallback(() => {
    if (conversationId == null || messageId == null || eventId == null) return;
    setLoading(true);
    setError(null);
    const request = mode === 'call'
      ? api.getAdminLlmCallReconstruct(conversationId, messageId, eventId)
      : api.getAdminPromptReconstruct(conversationId, messageId, eventId);
    request
      .then(setResult)
      .catch((err) => setError(err instanceof Error ? err.message : '重建失败'))
      .finally(() => setLoading(false));
  }, [conversationId, messageId, eventId, mode]);

  const handleDownload = useCallback(() => {
    if (!result) return;
    const response = 'response' in result ? result.response : undefined;
    const blob = new Blob([JSON.stringify({
      model: result.model,
      exposed_tool_names: result.exposed_tool_names,
      messages: result.messages,
      ...(response == null ? {} : { response }),
    }, null, 2)], { type: 'application/json;charset=utf-8' });
    const prefix = mode === 'call' ? 'model-call' : 'model-messages';
    triggerBlobDownload(`${prefix}-${messageId ?? 'msg'}-${eventId ?? 'evt'}.json`, blob);
  }, [result, messageId, eventId, mode]);

  const reconstructingCall = mode === 'call';
  const response = result != null && 'response' in result ? result.response : null;

  return (
    <div className="space-y-2 border-t border-border dark:border-border-dark pt-3">
      <div className="text-xs text-text-tertiary dark:text-text-tertiary-dark">
        {reconstructingCall
          ? '重建此次模型调用的输入 messages 和已持久化响应（不包含 tools schema 或 provider chat template 后的 token 序列）'
          : '重建此发 OpenAI-compatible messages 和实际暴露的工具名（不包含 tools schema 或 provider chat template 后的 token 序列）'}
      </div>
      <div className="flex items-center gap-2 flex-wrap">
        <button
          onClick={handleReconstruct}
          disabled={!canReconstruct || loading}
          className="px-2 py-1 rounded-md text-xs bg-accent/10 text-accent hover:bg-accent/20 disabled:opacity-50 transition-colors"
        >
          {loading ? '重建中…' : reconstructingCall ? '重建完整调用' : '重建 Messages'}
        </button>
        {result ? <button onClick={handleDownload} className="text-xs text-accent">下载 JSON</button> : null}
      </div>
      {!canReconstruct ? (
        <div className="text-xs text-text-tertiary dark:text-text-tertiary-dark">
          该事件缺少 event_id（早于此能力上线），无法重建。
        </div>
      ) : null}
      {error ? <div className="text-xs text-status-error">{error}</div> : null}
      {result ? (
        <div className="space-y-2">
          <div className="flex items-center gap-2 flex-wrap text-xs text-text-tertiary dark:text-text-tertiary-dark">
            <span>{result.messages.length} 条消息 · {result.agent_name ?? '-'}</span>
            {!result.has_reminder ? (
              <span className="px-1 py-px rounded bg-status-warning/10 text-status-warning text-[10px]">
                无持久化 reminder（旧事件：仅 system + 历史）
              </span>
            ) : null}
          </div>
          <DetailRow label="Model" value={result.model ?? '-'} />
          <DetailRow
            label="Exposed tools"
            value={result.exposed_tool_names == null ? '未采集（旧事件）' : result.exposed_tool_names.join(', ') || '（无）'}
          />
          <DetailBlock label="重建 Messages" content={JSON.stringify(result.messages, null, 2)} />
          {response != null ? (
            <DetailBlock label="模型 Response" content={JSON.stringify(response, null, 2)} />
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function DetailBlock({ label, content }: { label: string; content: string }) {
  const [expanded, setExpanded] = useState(false);
  const preview = content.length > 300 && !expanded ? `${content.slice(0, 300)}...` : content;

  return (
    <div>
      <div className="text-xs text-text-tertiary dark:text-text-tertiary-dark mb-1">{label}</div>
      <pre className="text-xs text-text-primary dark:text-text-primary-dark bg-surface dark:bg-surface-dark rounded-lg p-2 overflow-x-auto whitespace-pre-wrap break-words max-h-80 overflow-y-auto">
        {preview}
      </pre>
      {content.length > 300 ? (
        <button onClick={() => setExpanded((prev) => !prev)} className="text-xs text-accent mt-1">
          {expanded ? '收起' : '展开全部'}
        </button>
      ) : null}
    </div>
  );
}
