import type {
  ConversationListResponse,
  ConversationDetail,
  ChatRequest,
  ChatResponse,
  ResumeRequest,
  ResumeResponse,
  ArtifactListResponse,
  ArtifactDetail,
  VersionListResponse,
  VersionDetail,
  LoginRequest,
  LoginResponse,
} from '@/types';
import { useAuthStore } from '@/stores/authStore';

const BASE_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

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

export function getConversation(convId: string) {
  return request<ConversationDetail>(`/api/v1/chat/${convId}`);
}

export function sendMessage(body: ChatRequest) {
  return request<ChatResponse>('/api/v1/chat', {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

export function deleteConversation(convId: string) {
  return request(`/api/v1/chat/${convId}`, { method: 'DELETE' });
}

export function resumeExecution(convId: string, body: ResumeRequest) {
  return request<ResumeResponse>(`/api/v1/chat/${convId}/resume`, {
    method: 'POST',
    body: JSON.stringify(body),
  });
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

export function listVersions(sessionId: string, artifactId: string) {
  return request<VersionListResponse>(
    `/api/v1/artifacts/${sessionId}/${artifactId}/versions`
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
