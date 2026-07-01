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
// Tool Registry Types (Admin) — B-4 工具 unit 管理
// ============================================================

export type ToolParamSpec = S['ToolParamSpec'];
export type ToolMemberSpec = S['ToolMemberSpec'];
export type CreateToolUnitRequest = S['CreateToolUnitRequest'];
export type UpdateToolUnitRequest = S['UpdateToolUnitRequest'];
export type MountUnitRequest = S['MountUnitRequest'];
export type SetCredentialRequest = S['SetCredentialRequest'];
export type ToolMemberResponse = S['ToolMemberResponse'];
export type MountedAgentResponse = S['MountedAgentResponse'];
export type MountResponse = S['MountResponse'];
export type CredentialStatusResponse = S['CredentialStatusResponse'];
export type ToolUnitResponse = S['ToolUnitResponse'];
export type ToolUnitListResponse = S['ToolUnitListResponse'];
export type AgentSummaryResponse = S['AgentSummaryResponse'];
export type AgentListResponse = S['AgentListResponse'];

// ============================================================
// Skill Types (C-3) — 用户侧 skill 列举 + 个人 enable toggle
// ============================================================

export type SkillItem = S['SkillItem'];
export type SkillListResponse = S['SkillListResponse'];

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
  // Skill slugs the user activated for this turn (composer skill picker). Each
  // visible skill's instructions are injected + its agent-disabled tools enabled;
  // activation is sticky across the conversation. Relaxes the empty-input
  // requirement, so an activation-only send with no text is allowed.
  activate_skills?: string[];
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
