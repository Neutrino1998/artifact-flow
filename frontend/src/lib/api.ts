import type {
  ConversationListResponse,
  ConversationDetail,
  ChatRequest,
  ChatResponse,
  CancelResponse,
  InjectResponse,
  ResumeRequest,
  ResumeResponse,
  BulkDeleteResponse,
  ArtifactListResponse,
  ArtifactDetail,
  VersionDetail,
  LoginRequest,
  LoginResponse,
  CreateUserRequest,
  UpdateUserRequest,
  ChangePasswordRequest,
  UpdateMyProfileRequest,
  UserInfo,
  UserResponse,
  UserListResponse,
  UserImpactResponse,
  BulkImportResponse,
  BulkActionRequest,
  BulkActionResponse,
  BulkImpactResponse,
  DepartmentResponse,
  DepartmentListResponse,
  DepartmentTreeResponse,
  CreateDepartmentRequest,
  UpdateDepartmentRequest,
  MoveDepartmentRequest,
  ResolveDepartmentRequest,
  ResolveDepartmentResponse,
  UploadResponse,
} from '@/types';
import { useAuthStore } from '@/stores/authStore';

const BASE_URL = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';
const CONVERSATION_DETAIL_TTL_MS = 20_000;

type ConversationCacheItem = {
  data: ConversationDetail;
  expiresAt: number;
};

const conversationDetailCache = new Map<string, ConversationCacheItem>();
const conversationDetailInFlight = new Map<string, Promise<ConversationDetail>>();
const conversationCacheEpoch = new Map<string, number>();

type GetConversationOptions = {
  force?: boolean;
};

function nextConversationEpoch(convId: string): number {
  const next = (conversationCacheEpoch.get(convId) ?? 0) + 1;
  conversationCacheEpoch.set(convId, next);
  return next;
}

export function invalidateConversationCache(convId?: string): void {
  if (convId) {
    nextConversationEpoch(convId);
    conversationDetailCache.delete(convId);
    conversationDetailInFlight.delete(convId);
    return;
  }

  conversationDetailCache.clear();
  conversationDetailInFlight.clear();
  conversationCacheEpoch.clear();
}

function authHeaders(): Record<string, string> {
  const token = useAuthStore.getState().token;
  if (token) {
    return { Authorization: `Bearer ${token}` };
  }
  return {};
}

export class ApiError extends Error {
  status: number;
  /**
   * Parsed JSON response body when available — lets callers read structured
   * error payloads (e.g. bulk-import returns `{detail: {message, duplicate_rows}}`).
   * Undefined if the body wasn't JSON or wasn't parsed.
   */
  body?: unknown;
  constructor(status: number, message: string, body?: unknown) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.body = body;
  }
}

/**
 * Best-effort 把 FastAPI 错误响应转成可读字符串。
 * - {"detail": "string"}                    → 直接用
 * - {"detail": [{msg, ...}, ...]}（422）   → 拼接 msg 字段，去掉 Pydantic 的 "Value error, " 前缀
 * - 其他非 JSON / 无 detail                  → 退回原始 body
 */
function formatApiError(status: number, body: string): string {
  if (!body) return `API ${status}`;
  try {
    const parsed = JSON.parse(body);
    const detail = parsed?.detail;
    if (typeof detail === 'string') return detail;
    if (Array.isArray(detail)) {
      const msgs = detail
        .map((d: { msg?: string }) => d?.msg)
        .filter((m): m is string => typeof m === 'string')
        .map((m) => m.replace(/^Value error,\s*/i, ''));
      if (msgs.length) return msgs.join('；');
    }
  } catch {
    // not JSON, fall through
  }
  return `API ${status}: ${body}`;
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    headers: {
      'Content-Type': 'application/json',
      ...authHeaders(),
      ...options?.headers,
    },
    ...options,
  });
  if (res.status === 401) {
    useAuthStore.getState().logout();
    throw new ApiError(401, 'Session expired');
  }
  if (!res.ok) {
    const body = await res.text().catch(() => '');
    throw new ApiError(res.status, formatApiError(res.status, body));
  }
  if (res.status === 204) {
    return undefined as T;
  }
  return res.json();
}

// Auth
export function login(body: LoginRequest) {
  // Login does not need auth headers
  return fetch(`${BASE_URL}/api/v1/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }).then(async (res) => {
    if (!res.ok) {
      const text = await res.text().catch(() => '');
      throw new Error(`Login failed: ${text}`);
    }
    return res.json() as Promise<LoginResponse>;
  });
}

// Conversations
export function listConversations(limit = 20, offset = 0, query?: string) {
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  if (query) params.set('q', query);
  return request<ConversationListResponse>(`/api/v1/chat?${params}`);
}

export function getConversation(convId: string, options?: GetConversationOptions) {
  const force = options?.force ?? false;
  const now = Date.now();

  if (!force) {
    const cached = conversationDetailCache.get(convId);
    if (cached && cached.expiresAt > now) {
      return Promise.resolve(cached.data);
    }
  }

  const inFlight = conversationDetailInFlight.get(convId);
  if (inFlight) {
    return inFlight;
  }

  const requestEpoch = conversationCacheEpoch.get(convId) ?? 0;
  const req = request<ConversationDetail>(`/api/v1/chat/${convId}`)
    .then((detail) => {
      // Skip cache write if cache was invalidated while request was in-flight.
      if ((conversationCacheEpoch.get(convId) ?? 0) === requestEpoch) {
        conversationDetailCache.set(convId, {
          data: detail,
          expiresAt: Date.now() + CONVERSATION_DETAIL_TTL_MS,
        });
      }
      return detail;
    })
    .finally(() => {
      if (conversationDetailInFlight.get(convId) === req) {
        conversationDetailInFlight.delete(convId);
      }
    });

  conversationDetailInFlight.set(convId, req);
  return req;
}

export async function sendMessage(body: ChatRequest) {
  const res = await request<ChatResponse>('/api/v1/chat', {
    method: 'POST',
    body: JSON.stringify(body),
  });
  // Message/branch updates make cached conversation detail stale.
  invalidateConversationCache(body.conversation_id ?? res.conversation_id);
  return res;
}

export async function deleteConversation(convId: string) {
  const res = await request(`/api/v1/chat/${convId}`, { method: 'DELETE' });
  invalidateConversationCache(convId);
  return res;
}

export async function bulkDeleteConversations(ids: string[]) {
  const res = await request<BulkDeleteResponse>('/api/v1/chat/bulk-delete', {
    method: 'POST',
    body: JSON.stringify({ ids }),
  });
  for (const id of res.deleted) invalidateConversationCache(id);
  return res;
}

export async function getActiveStream(conversationId: string) {
  return request<{ conversation_id: string; message_id: string; stream_url: string }>(
    `/api/v1/chat/${conversationId}/active-stream`
  );
}

export async function cancelExecution(convId: string) {
  return request<CancelResponse>(`/api/v1/chat/${convId}/cancel`, { method: 'POST' });
}

export async function injectMessage(convId: string, content: string) {
  return request<InjectResponse>(`/api/v1/chat/${convId}/inject`, {
    method: 'POST',
    body: JSON.stringify({ content }),
  });
}

export async function resumeExecution(convId: string, body: ResumeRequest) {
  const res = await request<ResumeResponse>(`/api/v1/chat/${convId}/resume`, {
    method: 'POST',
    body: JSON.stringify(body),
  });
  invalidateConversationCache(convId);
  return res;
}

// Artifacts
export function listArtifacts(sessionId: string) {
  return request<ArtifactListResponse>(`/api/v1/artifacts/${sessionId}`);
}

export function getArtifact(sessionId: string, artifactId: string) {
  return request<ArtifactDetail>(
    `/api/v1/artifacts/${sessionId}/${artifactId}`
  );
}

export function getVersion(
  sessionId: string,
  artifactId: string,
  version: number
) {
  return request<VersionDetail>(
    `/api/v1/artifacts/${sessionId}/${artifactId}/versions/${version}`
  );
}

export async function uploadFile(sessionId: string, file: File): Promise<UploadResponse> {
  const formData = new FormData();
  formData.append('file', file);

  const res = await fetch(`${BASE_URL}/api/v1/artifacts/${sessionId}/upload`, {
    method: 'POST',
    headers: authHeaders(),
    body: formData,
  });

  if (res.status === 401) {
    useAuthStore.getState().logout();
    throw new Error('Session expired');
  }
  if (!res.ok) {
    const body = await res.text().catch(() => '');
    throw new Error(`Upload failed: ${body}`);
  }
  return res.json();
}

export async function uploadFileNewSession(file: File): Promise<UploadResponse> {
  const formData = new FormData();
  formData.append('file', file);

  const res = await fetch(`${BASE_URL}/api/v1/artifacts/upload`, {
    method: 'POST',
    headers: authHeaders(),
    body: formData,
  });

  if (res.status === 401) {
    useAuthStore.getState().logout();
    throw new Error('Session expired');
  }
  if (!res.ok) {
    const body = await res.text().catch(() => '');
    throw new Error(`Upload failed: ${body}`);
  }
  return res.json();
}

export async function exportArtifact(
  sessionId: string,
  artifactId: string,
  format: string
): Promise<Blob> {
  const res = await fetch(
    `${BASE_URL}/api/v1/artifacts/${sessionId}/${artifactId}/export?format=${format}`,
    { headers: authHeaders() }
  );

  if (res.status === 401) {
    useAuthStore.getState().logout();
    throw new Error('Session expired');
  }
  if (!res.ok) {
    const body = await res.text().catch(() => '');
    throw new Error(`Export failed: ${body}`);
  }
  return res.blob();
}

// Message Events (historical replay)
export interface MessageEventItem {
  id: string;
  event_type: string;
  agent_name: string | null;
  data: Record<string, unknown> | null;
  created_at: string;
}

export interface MessageEventsResponse {
  message_id: string;
  events: MessageEventItem[];
  total: number;
}

export function getMessageEvents(convId: string, msgId: string) {
  return request<MessageEventsResponse>(
    `/api/v1/chat/${convId}/messages/${msgId}/events`
  );
}

// Admin Observability
export interface AdminConversationSummary {
  id: string;
  title: string | null;
  user_id: string | null;
  user_display_name: string | null;
  message_count: number;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface AdminConversationListResponse {
  conversations: AdminConversationSummary[];
  total: number;
  has_more: boolean;
}

export interface AdminEventItem {
  id: number;
  event_type: string;
  agent_name: string | null;
  data: Record<string, unknown> | null;
  created_at: string;
}

export interface AdminMessageGroup {
  message_id: string;
  user_input: string;
  response: string | null;
  created_at: string;
  events: AdminEventItem[];
  execution_metrics: Record<string, unknown> | null;
}

export interface AdminConversationEventsResponse {
  conversation_id: string;
  title: string | null;
  messages: AdminMessageGroup[];
}

export function listAdminConversations(
  limit = 20,
  offset = 0,
  query?: string,
  userId?: string,
) {
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  if (query) params.set('q', query);
  if (userId) params.set('user_id', userId);
  return request<AdminConversationListResponse>(`/api/v1/admin/conversations?${params}`);
}

export function getAdminConversationEvents(convId: string) {
  return request<AdminConversationEventsResponse>(
    `/api/v1/admin/conversations/${convId}/events`
  );
}

export function listAdminConversationArtifacts(convId: string) {
  return request<ArtifactListResponse>(
    `/api/v1/admin/conversations/${convId}/artifacts`
  );
}

export function getAdminConversationArtifact(convId: string, artifactId: string) {
  return request<ArtifactDetail>(
    `/api/v1/admin/conversations/${convId}/artifacts/${artifactId}`
  );
}

export function getAdminConversationArtifactVersion(
  convId: string,
  artifactId: string,
  version: number,
) {
  return request<VersionDetail>(
    `/api/v1/admin/conversations/${convId}/artifacts/${artifactId}/versions/${version}`
  );
}

// User Management (Admin)
export function listUsers(limit = 50, offset = 0, query?: string) {
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  if (query) params.set('q', query);
  return request<UserListResponse>(`/api/v1/admin/users?${params}`);
}

export function createUser(body: CreateUserRequest) {
  return request<UserResponse>('/api/v1/admin/users', {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

export function getUser(userId: string) {
  return request<UserResponse>(`/api/v1/admin/users/${userId}`);
}

export function updateUser(userId: string, body: UpdateUserRequest) {
  return request<UserResponse>(`/api/v1/admin/users/${userId}`, {
    method: 'PUT',
    body: JSON.stringify(body),
  });
}

export function deleteUser(userId: string) {
  return request<void>(`/api/v1/admin/users/${userId}`, { method: 'DELETE' });
}

export function getUserImpact(userId: string) {
  return request<UserImpactResponse>(`/api/v1/admin/users/${userId}/impact`);
}

// PR5a — Bulk user actions
export function bulkUserAction(body: BulkActionRequest) {
  return request<BulkActionResponse>('/api/v1/admin/users/bulk-action', {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

export function getUsersBulkImpact(ids: string[]) {
  const params = new URLSearchParams();
  for (const id of ids) params.append('ids', id);
  return request<BulkImpactResponse>(`/api/v1/admin/users/bulk-impact?${params}`);
}

/**
 * 批量导入用户（CSV）— PR3。
 *
 * 错误：
 * - 400 + dict detail（`{message, duplicate_rows}`）→ 文件内 username 重复，
 *   ApiError.body 含原始 JSON 给 UI 渲染重复行号
 * - 400 + string detail → CSV 解析错（缺 username 列 / 行数超限 / 空文件）
 * - 422 → 字节超限
 */
export async function bulkImportUsers(file: File): Promise<BulkImportResponse> {
  const formData = new FormData();
  formData.append('file', file);

  const res = await fetch(`${BASE_URL}/api/v1/admin/users/bulk-import`, {
    method: 'POST',
    headers: authHeaders(),
    body: formData,
  });

  if (res.status === 401) {
    useAuthStore.getState().logout();
    throw new ApiError(401, 'Session expired');
  }

  if (!res.ok) {
    const text = await res.text().catch(() => '');
    let parsed: unknown = undefined;
    try { parsed = JSON.parse(text); } catch { /* not JSON */ }
    throw new ApiError(res.status, formatApiError(res.status, text), parsed);
  }

  return res.json() as Promise<BulkImportResponse>;
}

export function changeMyPassword(body: ChangePasswordRequest) {
  return request<void>('/api/v1/auth/me/password', {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

export function updateMyProfile(body: UpdateMyProfileRequest) {
  return request<UserInfo>('/api/v1/auth/me', {
    method: 'PATCH',
    body: JSON.stringify(body),
  });
}

export function getMe() {
  return request<UserInfo>('/api/v1/auth/me');
}

// Departments (Admin)
export function listDepartments(parentId?: string | null) {
  const params = new URLSearchParams();
  if (parentId) params.set('parent_id', parentId);
  const qs = params.toString();
  return request<DepartmentListResponse>(`/api/v1/departments${qs ? `?${qs}` : ''}`);
}

export function getDepartmentTree() {
  return request<DepartmentTreeResponse>('/api/v1/departments/tree');
}

export function getDepartment(deptId: string) {
  return request<DepartmentResponse>(`/api/v1/departments/${deptId}`);
}

export function createDepartment(body: CreateDepartmentRequest) {
  return request<DepartmentResponse>('/api/v1/departments', {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

export function renameDepartment(deptId: string, body: UpdateDepartmentRequest) {
  return request<DepartmentResponse>(`/api/v1/departments/${deptId}`, {
    method: 'PATCH',
    body: JSON.stringify(body),
  });
}

export function moveDepartment(deptId: string, body: MoveDepartmentRequest) {
  return request<DepartmentResponse>(`/api/v1/departments/${deptId}/move`, {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

export function deleteDepartment(deptId: string) {
  return request<void>(`/api/v1/departments/${deptId}`, { method: 'DELETE' });
}

export function resolveDepartmentPath(body: ResolveDepartmentRequest) {
  return request<ResolveDepartmentResponse>('/api/v1/departments/resolve', {
    method: 'POST',
    body: JSON.stringify(body),
  });
}
