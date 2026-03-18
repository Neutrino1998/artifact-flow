import type {
  ConversationListResponse,
  ConversationDetail,
  ChatRequest,
  ChatResponse,
  CancelResponse,
  InjectResponse,
  ResumeRequest,
  ResumeResponse,
  ArtifactListResponse,
  ArtifactDetail,
  VersionDetail,
  LoginRequest,
  LoginResponse,
  CreateUserRequest,
  UpdateUserRequest,
  UserResponse,
  UserListResponse,
  UploadResponse,
} from '@/types';
import { useAuthStore } from '@/stores/authStore';

const BASE_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';
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
    throw new Error('Session expired');
  }
  if (!res.ok) {
    const body = await res.text().catch(() => '');
    throw new Error(`API ${res.status}: ${body}`);
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
export function listConversations(limit = 20, offset = 0) {
  return request<ConversationListResponse>(
    `/api/v1/chat?limit=${limit}&offset=${offset}`
  );
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

// User Management (Admin)
export function listUsers(limit = 50, offset = 0) {
  return request<UserListResponse>(
    `/api/v1/auth/users?limit=${limit}&offset=${offset}`
  );
}

export function createUser(body: CreateUserRequest) {
  return request<UserResponse>('/api/v1/auth/users', {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

export function updateUser(userId: string, body: UpdateUserRequest) {
  return request<UserResponse>(`/api/v1/auth/users/${userId}`, {
    method: 'PUT',
    body: JSON.stringify(body),
  });
}
