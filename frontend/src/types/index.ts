import type { components } from './api';
type S = components['schemas'];

// ============================================================
// Auth Types
// ============================================================

export type LoginRequest = S['LoginRequest'];
export type UserInfo = S['UserInfo'];
export type LoginResponse = S['LoginResponse'];

// ============================================================
// User Management Types (Admin)
// ============================================================

export type CreateUserRequest = S['CreateUserRequest'];
export type UpdateUserRequest = S['UpdateUserRequest'];
export type ChangePasswordRequest = S['ChangePasswordRequest'];
export type UpdateMyProfileRequest = S['UpdateMyProfileRequest'];
export type UserResponse = S['UserResponse'];
export type UserListResponse = S['UserListResponse'];
export type UserImpactResponse = S['UserImpactResponse'];
export type BulkImportFailedRow = S['BulkImportFailedRow'];
export type BulkImportSkippedRow = S['BulkImportSkippedRow'];
export type BulkImportResponse = S['BulkImportResponse'];
export type BulkActionRequest = S['BulkActionRequest'];
export type BulkActionResponse = S['BulkActionResponse'];
export type BulkActionFailedItem = S['BulkActionFailedItem'];
export type BulkImpactResponse = S['BulkImpactResponse'];

// ============================================================
// Department Types (Admin)
// ============================================================

export type DepartmentResponse = S['DepartmentResponse'];
export type DepartmentListResponse = S['DepartmentListResponse'];
export type DepartmentTreeNode = S['DepartmentTreeNode'];
export type DepartmentTreeResponse = S['DepartmentTreeResponse'];
export type CreateDepartmentRequest = S['CreateDepartmentRequest'];
export type UpdateDepartmentRequest = S['UpdateDepartmentRequest'];
export type MoveDepartmentRequest = S['MoveDepartmentRequest'];
export type ResolveDepartmentRequest = S['ResolveDepartmentRequest'];
export type ResolveDepartmentResponse = S['ResolveDepartmentResponse'];

// ============================================================
// Chat Types
// ============================================================

// POST /chat is multipart/form-data (a JSON `payload` field + optional file
// attachments), so ChatRequest is no longer an OpenAPI body schema. It's the
// shape of the JSON `payload` field — kept in sync by hand with the backend
// Pydantic ChatRequest (src/api/schemas/chat.py).
export type ChatRequest = {
  user_input: string;
  conversation_id?: string | null;
  parent_message_id?: string | null;
  // User pressed "compact": force a one-shot context compaction this turn.
  // Relaxes the empty-input requirement (backend injects a directive), so a
  // compact-only send with no text is allowed.
  force_compact?: boolean;
};
export type ChatResponse = S['ChatResponse'];
export type InjectResponse = S['InjectResponse'];
export type CancelResponse = S['CancelResponse'];
export type ResumeRequest = S['ResumeRequest'];
export type ResumeResponse = S['ResumeResponse'];
export type MessageResponse = S['MessageResponse'];
export type ConversationSummary = S['ConversationSummary'];
export type ConversationListResponse = S['ConversationListResponse'];
export type ConversationDetail = S['ConversationDetailResponse'];
export type BulkDeleteRequest = S['BulkDeleteRequest'];
export type BulkDeleteResponse = S['BulkDeleteResponse'];
export type BulkDeleteFailedItem = S['BulkDeleteFailedItem'];
export type StorageUsageResponse = S['StorageUsageResponse'];

// ============================================================
// Meta / client-config Types
// ============================================================

// Backend-owned runtime constants (GET /api/v1/meta). Single source of truth —
// fetched once and cached so the frontend never hardcodes server values.
export type ClientConfigResponse = S['ClientConfigResponse'];

// ============================================================
// Artifact Types
// ============================================================

export type ArtifactSummary = S['ArtifactSummary'];
export type ArtifactListResponse = S['ArtifactListResponse'];
export type ArtifactDetail = S['ArtifactResponse'];
export type VersionSummary = S['VersionSummary'];
export type VersionDetail = S['VersionDetailResponse'];
