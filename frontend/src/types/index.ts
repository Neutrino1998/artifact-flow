// ============================================================
// Auth Types
// ============================================================

export interface LoginRequest {
  username: string;
  password: string;
}

export interface UserInfo {
  id: string;
  username: string;
  display_name: string | null;
  role: string;
}

export interface LoginResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
  user: UserInfo;
}

// ============================================================
// User Management Types (Admin)
// ============================================================

export interface CreateUserRequest {
  username: string;
  password: string;
  display_name?: string;
  role: string;
}

export interface UpdateUserRequest {
  display_name?: string;
  password?: string;
  role?: string;
  is_active?: boolean;
}

export interface UserResponse {
  id: string;
  username: string;
  display_name: string | null;
  role: string;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface UserListResponse {
  users: UserResponse[];
  total: number;
}

// ============================================================
// Chat Types
// ============================================================

export interface ChatRequest {
  content: string;
  conversation_id?: string | null;
  parent_message_id?: string | null;
}

export interface ChatResponse {
  conversation_id: string;
  message_id: string;
  thread_id: string;
  stream_url: string;
}

export interface ResumeRequest {
  thread_id: string;
  message_id: string;
  approved: boolean;
}

export interface ResumeResponse {
  stream_url: string;
}

export interface MessageResponse {
  id: string;
  parent_id: string | null;
  content: string;
  response: string | null;
  created_at: string;
  children: string[];
}

export interface ConversationSummary {
  id: string;
  title: string | null;
  message_count: number;
  created_at: string;
  updated_at: string;
}

export interface ConversationListResponse {
  conversations: ConversationSummary[];
  total: number;
  has_more: boolean;
}

export interface ConversationDetail {
  id: string;
  title: string | null;
  active_branch: string | null;
  messages: MessageResponse[];
  session_id: string;
  created_at: string;
  updated_at: string;
}

// ============================================================
// Artifact Types
// ============================================================

export interface ArtifactSummary {
  id: string;
  content_type: string;
  title: string;
  current_version: number;
  created_at: string;
  updated_at: string;
}

export interface ArtifactListResponse {
  session_id: string;
  artifacts: ArtifactSummary[];
}

export interface ArtifactDetail {
  id: string;
  session_id: string;
  content_type: string;
  title: string;
  content: string;
  current_version: number;
  created_at: string;
  updated_at: string;
}

export interface VersionSummary {
  version: number;
  update_type: string;
  created_at: string;
}

export interface VersionListResponse {
  artifact_id: string;
  session_id: string;
  versions: VersionSummary[];
}

export interface VersionDetail {
  version: number;
  content: string;
  update_type: string;
  changes: [string, string][] | null;
  created_at: string;
}
