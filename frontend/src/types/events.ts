// ============================================================
// SSE Event Types — mirrors backend StreamEventType
// ============================================================

export enum StreamEventType {
  // Controller layer
  METADATA = 'metadata',
  COMPLETE = 'complete',
  CANCELLED = 'cancelled',
  ERROR = 'error',

  // Agent layer
  AGENT_START = 'agent_start',
  LLM_CHUNK = 'llm_chunk',
  LLM_COMPLETE = 'llm_complete',
  AGENT_COMPLETE = 'agent_complete',

  // Engine layer
  TOOL_START = 'tool_start',
  TOOL_COMPLETE = 'tool_complete',
  PERMISSION_REQUEST = 'permission_request',
  PERMISSION_RESULT = 'permission_result',

  // Input / injection layer
  QUEUED_MESSAGE = 'queued_message',
  SUBAGENT_INSTRUCTION = 'subagent_instruction',

  // Compaction layer
  COMPACTION_START = 'compaction_start',      // context compression started
  COMPACTION_SUMMARY = 'compaction_summary',  // context compression finished (persisted as history boundary)
}

// ============================================================
// Per-event data shapes
// ============================================================

export interface MetadataData {
  conversation_id: string;
  message_id: string;
}

export interface AgentStartData {
  agent: string;
  system_prompt?: string;
}

export interface LLMChunkData {
  content?: string;
  reasoning_content?: string;
}

export interface LLMCompleteData {
  content: string;
  reasoning_content?: string;
  model?: string;
  duration_ms?: number;
  token_usage?: TokenUsage;
}

export interface AgentCompleteData {
  agent: string;
  content: string;
}

export interface ToolStartData {
  tool: string;
  params: Record<string, unknown>;
}

export interface ToolCompleteData {
  tool: string;
  success: boolean;
  result_data?: unknown;
  error?: string;
  duration_ms: number;
  params?: Record<string, unknown>;
  metadata?: Record<string, unknown>;
}

export interface PermissionRequestData {
  permission_level: string;
  tool: string;
  params: Record<string, unknown>;
}

export interface PermissionResultData {
  approved: boolean;
}

export interface TokenUsage {
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
}

export interface ExecutionMetrics {
  started_at: string;
  completed_at?: string;
  total_duration_ms?: number;
  total_token_usage: TokenUsage;
}

export interface CompleteData {
  response: string;
  execution_metrics?: ExecutionMetrics;
}

export interface ErrorData {
  error: string;
  code?: string;
}

export interface CompactionStartData {
  /** input tokens of the LLM call that tripped the threshold */
  last_input_tokens: number;
  /** output tokens of the same call */
  last_output_tokens: number;
}

export interface CompactionSummaryData {
  /** false → compaction LLM failed; this event is a paired terminator for compaction_start
   *  (also marks the turn ERROR backend-side). Treat as a status marker only —
   *  content is empty and error carries the message. Optional: pre-existing DB
   *  records lack this field and should be treated as success=true. */
  success?: boolean;
  /** the compacted summary text (memory-aid frame prepended + compact_agent's output);
   *  empty string when success=false */
  content: string;
  /** token cost of the compact_agent LLM call itself; zeros when success=false */
  token_usage: TokenUsage;
  /** compact_agent LLM duration; 0 when success=false */
  duration_ms: number;
  /** compact_agent's model id (for display parity with llm_complete events) */
  model?: string;
  /** non-null when success=false */
  error: string | null;
}

// ============================================================
// Unified SSE Event
// ============================================================

export interface SSEEvent {
  type: StreamEventType | string;
  timestamp: string;
  agent?: string;
  data?: Record<string, unknown>;
}
