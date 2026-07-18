package afctl

import "time"

const (
	SiteSchema     = 1
	ManifestSchema = 1
	StateSchema    = 1
	ReleaseSchema  = 1
)

type Site struct {
	Schema              int
	Executor            string
	TLS                 string
	Infra               string
	SandboxRuntime      string
	ScratchRoot         string
	ScratchSize         string
	BackendReplicas     int
	ReadyTimeoutSeconds int
	Inventory           string
	AnsibleEEImage      string
}

type Artifact struct {
	Role   string `json:"role"`
	File   string `json:"file"`
	SHA256 string `json:"sha256"`
}

type Manifest struct {
	Schema              int        `json:"schema"`
	ReleaseID           string     `json:"release_id"`
	Kind                string     `json:"kind"`
	Platform            string     `json:"platform"`
	CreatedAt           string     `json:"created_at"`
	Source              string     `json:"source"`
	ExpectedBaseRelease string     `json:"expected_base_release,omitempty"`
	SandboxImage        string     `json:"sandbox_image,omitempty"`
	Images              []string   `json:"images,omitempty"`
	Artifacts           []Artifact `json:"artifacts"`
}

type State struct {
	Schema     int    `json:"schema"`
	Current    string `json:"current"`
	Previous   string `json:"previous,omitempty"`
	UpdatedAt  string `json:"updated_at"`
	Generation uint64 `json:"generation"`
}

type ReleaseMetadata struct {
	Schema         int      `json:"schema"`
	ReleaseID      string   `json:"release_id"`
	Kind           string   `json:"kind"`
	AppVersion     string   `json:"app_version"`
	Platform       string   `json:"platform"`
	BaseRelease    string   `json:"base_release,omitempty"`
	SandboxImage   string   `json:"sandbox_image"`
	Images         []string `json:"images"`
	Identity       string   `json:"identity"`
	MaterializedAt string   `json:"materialized_at"`
}

type CheckoutMetadata struct {
	Schema       int    `json:"schema"`
	BaseRelease  string `json:"base_release"`
	ConfigDigest string `json:"config_digest"`
	CreatedAt    string `json:"created_at"`
}

type Plan struct {
	Operation   string
	Current     string
	Target      string
	AppVersion  string
	ReleaseKind string
	Actions     []string
}

func timestamp() string { return time.Now().UTC().Format(time.RFC3339) }
