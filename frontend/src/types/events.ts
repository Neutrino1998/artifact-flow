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
  COMPACTION_WAIT = 'compaction_wait',
  SUBAGENT_INSTRUCTION = 'subagent_instruction',
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
}

export interface LLMChunkData {
  content?: string;
  reasoning_content?: string;
}

export interface LLMCompleteData {
  content: string;
  token_usage?: {
    input_tokens: number;
    output_tokens: number;
    total_tokens: number;
  };
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

export interface MetricsEvent {
  type: 'llm_complete' | 'tool_complete';
  agent?: string;
  model?: string;
  token_usage?: TokenUsage;
  duration_ms?: number;
  started_at?: string;
  completed_at?: string;
  tool?: string;
  success?: boolean;
}

export interface ExecutionMetrics {
  started_at: string;
  completed_at?: string;
  total_duration_ms?: number;
  total_token_usage: TokenUsage;
  events: MetricsEvent[];
}

export interface CompleteData {
  response: string;
  execution_metrics?: ExecutionMetrics;
}

export interface ErrorData {
  error: string;
  code?: string;
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
