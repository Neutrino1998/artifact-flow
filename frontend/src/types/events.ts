// ============================================================
// SSE Event Types — mirrors backend StreamEventType
// ============================================================

export enum StreamEventType {
  // Controller layer
  METADATA = 'metadata',
  COMPLETE = 'complete',
  ERROR = 'error',

  // Agent layer
  AGENT_START = 'agent_start',
  LLM_CHUNK = 'llm_chunk',
  LLM_COMPLETE = 'llm_complete',
  AGENT_COMPLETE = 'agent_complete',

  // Graph layer
  TOOL_START = 'tool_start',
  TOOL_COMPLETE = 'tool_complete',
  PERMISSION_REQUEST = 'permission_request',
  PERMISSION_RESULT = 'permission_result',
}

// ============================================================
// Per-event data shapes
// ============================================================

export interface MetadataData {
  conversation_id: string;
  thread_id: string;
  message_id: string;
}

export interface AgentStartData {
  agent_name: string;
}

export interface LLMChunkData {
  chunk: string;
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
  agent_name: string;
  has_tool_calls: boolean;
}

export interface ToolStartData {
  tool_name: string;
  params: Record<string, unknown>;
  agent: string;
}

export interface ToolCompleteData {
  tool_name: string;
  success: boolean;
  result: string;
  duration_ms: number;
}

export interface PermissionRequestData {
  tool_name: string;
  params: Record<string, unknown>;
}

export interface PermissionResultData {
  approved: boolean;
}

export interface CompleteData {
  response: string;
  execution_metrics?: Record<string, unknown>;
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
  tool?: string;
  data?: Record<string, unknown>;
}
