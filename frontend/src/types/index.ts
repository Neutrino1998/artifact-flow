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
export type UserResponse = S['UserResponse'];
export type UserListResponse = S['UserListResponse'];

// ============================================================
// Chat Types
// ============================================================

export type ChatRequest = S['ChatRequest'];
export type ChatResponse = S['ChatResponse'];
export type InjectResponse = S['InjectResponse'];
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
// Override latest_version to use narrowed VersionDetail (tuple changes)
export type ArtifactDetail = Omit<S['ArtifactDetailResponse'], 'latest_version'> & {
  latest_version: VersionDetail | null;
};
export type VersionSummary = S['VersionSummary'];
// Narrow changes from string[][] to [string, string][] for tuple precision
export type VersionDetail = Omit<S['VersionDetailResponse'], 'changes'> & {
  changes: [string, string][] | null;
};
export type UploadResponse = S['UploadResponse'];
