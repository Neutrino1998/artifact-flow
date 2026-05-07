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

export type ChatRequest = S['ChatRequest'];
export type ChatResponse = S['ChatResponse'];
export type InjectResponse = S['InjectResponse'];
export type CancelResponse = S['CancelResponse'];
export type ResumeRequest = S['ResumeRequest'];
export type ResumeResponse = S['ResumeResponse'];
export type MessageResponse = S['MessageResponse'];
export type ConversationSummary = S['ConversationSummary'];
export type ConversationListResponse = S['ConversationListResponse'];
export type ConversationDetail = S['ConversationDetailResponse'];

// ============================================================
// Artifact Types
// ============================================================

export type ArtifactSummary = S['ArtifactSummary'];
export type ArtifactListResponse = S['ArtifactListResponse'];
export type ArtifactDetail = S['ArtifactResponse'];
export type VersionSummary = S['VersionSummary'];
export type VersionDetail = S['VersionDetailResponse'];
export type UploadResponse = S['UploadResponse'];
