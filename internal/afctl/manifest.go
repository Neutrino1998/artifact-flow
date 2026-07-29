package afctl

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"regexp"
	"runtime"
	"sort"
	"strings"
	"time"
)

var releaseIDPattern = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._-]*$`)
var shaPattern = regexp.MustCompile(`^[0-9a-f]{64}$`)
var contentImagePattern = regexp.MustCompile(`^artifactflow-(caddy|postgres|redis):sha256-[0-9a-f]{64}$`)
var sandboxImagePattern = regexp.MustCompile(`^artifactflow-sandbox:sha256-[0-9a-f]{64}$`)

func LoadManifest(bundle string) (Manifest, error) {
	path := filepath.Join(bundle, "manifest.json")
	info, err := os.Lstat(path)
	if err != nil {
		return Manifest{}, err
	}
	if !info.Mode().IsRegular() {
		return Manifest{}, fmt.Errorf("%s must be a regular file", path)
	}
	var manifest Manifest
	if err := readStrictJSON(path, &manifest); err != nil {
		return Manifest{}, err
	}
	if err := manifest.Validate(); err != nil {
		return Manifest{}, fmt.Errorf("%s: %w", path, err)
	}
	return manifest, nil
}

func (m Manifest) Validate() error {
	if m.Schema != ManifestSchema {
		return fmt.Errorf("schema must be %d", ManifestSchema)
	}
	if !releaseIDPattern.MatchString(m.ReleaseID) {
		return fmt.Errorf("invalid release_id %q", m.ReleaseID)
	}
	if m.Kind != "app" && m.Kind != "config" {
		return fmt.Errorf("kind must be app or config")
	}
	if m.Platform != "linux/amd64" && m.Platform != "linux/arm64" {
		return fmt.Errorf("unsupported platform %q", m.Platform)
	}
	if m.CreatedAt == "" || m.Source == "" {
		return fmt.Errorf("created_at and source are required")
	}
	if _, err := time.Parse(time.RFC3339, m.CreatedAt); err != nil {
		return fmt.Errorf("created_at must be RFC3339")
	}
	if len(m.Artifacts) == 0 {
		return fmt.Errorf("artifacts must not be empty")
	}
	roles := map[string]bool{}
	files := map[string]bool{}
	allowed := map[string]bool{"app": true, "config": true, "deploy": true, "sandbox": true, "infra": true}
	for _, artifact := range m.Artifacts {
		if !allowed[artifact.Role] {
			return fmt.Errorf("unknown artifact role %q", artifact.Role)
		}
		if roles[artifact.Role] {
			return fmt.Errorf("duplicate artifact role %q", artifact.Role)
		}
		roles[artifact.Role] = true
		if artifact.File == "" || filepath.Base(artifact.File) != artifact.File {
			return fmt.Errorf("artifact file must be a basename: %q", artifact.File)
		}
		if files[artifact.File] {
			return fmt.Errorf("duplicate artifact file %q", artifact.File)
		}
		files[artifact.File] = true
		if !shaPattern.MatchString(artifact.SHA256) {
			return fmt.Errorf("artifact %s has invalid sha256", artifact.File)
		}
	}
	if m.Kind == "app" {
		if m.ExpectedBaseRelease != "" {
			return fmt.Errorf("app release cannot set expected_base_release")
		}
		for _, role := range []string{"app", "config", "deploy", "sandbox"} {
			if !roles[role] {
				return fmt.Errorf("app release requires %s artifact", role)
			}
		}
		if !sandboxImagePattern.MatchString(m.SandboxImage) {
			return fmt.Errorf("app release requires content-addressed sandbox_image")
		}
		images := map[string]bool{}
		for _, image := range m.Images {
			if image == "" || images[image] {
				return fmt.Errorf("app release image references must be non-empty and unique")
			}
			images[image] = true
		}
		for _, image := range []string{"artifactflow:" + m.ReleaseID, "artifactflow-frontend:" + m.ReleaseID, m.SandboxImage} {
			if !images[image] {
				return fmt.Errorf("app release must declare exact image %s", image)
			}
		}
		if roles["infra"] {
			if len(images) != 6 {
				return fmt.Errorf("app release with infra artifact must declare exactly six runtime images")
			}
			for _, prefix := range []string{"artifactflow-caddy:", "artifactflow-postgres:", "artifactflow-redis:"} {
				found := 0
				for image := range images {
					if strings.HasPrefix(image, prefix) && contentImagePattern.MatchString(image) {
						found++
					}
				}
				if found != 1 {
					return fmt.Errorf("app release with infra artifact requires exactly one content-addressed %s image", strings.TrimSuffix(prefix, ":"))
				}
			}
		} else {
			switch len(images) {
			case 3:
				// Current app-only manifests declare only app/frontend/sandbox.
			case 6:
				// Older app-only manifests included three vestigial infra refs.
				// Without an infra artifact they have no runtime authority; apply
				// ignores them and inherits the target's current infra instead.
			default:
				return fmt.Errorf("app-only release must declare exactly three application runtime images")
			}
		}
	} else {
		if !releaseIDPattern.MatchString(m.ExpectedBaseRelease) {
			return fmt.Errorf("config release requires valid expected_base_release")
		}
		if len(m.Artifacts) != 1 || !roles["config"] {
			return fmt.Errorf("config release may contain only the config artifact")
		}
		if m.SandboxImage != "" || len(m.Images) != 0 {
			return fmt.Errorf("config release inherits images and cannot declare them")
		}
	}
	return nil
}

func (m Manifest) Identity() (string, error) {
	type identity struct {
		Schema              int        `json:"schema"`
		ReleaseID           string     `json:"release_id"`
		Kind                string     `json:"kind"`
		Platform            string     `json:"platform"`
		ExpectedBaseRelease string     `json:"expected_base_release,omitempty"`
		SandboxImage        string     `json:"sandbox_image,omitempty"`
		Images              []string   `json:"images,omitempty"`
		Artifacts           []Artifact `json:"artifacts"`
	}
	artifacts := append([]Artifact(nil), m.Artifacts...)
	sort.Slice(artifacts, func(i, j int) bool { return artifacts[i].Role < artifacts[j].Role })
	images := append([]string(nil), m.Images...)
	sort.Strings(images)
	data, err := json.Marshal(identity{m.Schema, m.ReleaseID, m.Kind, m.Platform, m.ExpectedBaseRelease, m.SandboxImage, images, artifacts})
	if err != nil {
		return "", err
	}
	sum := sha256.Sum256(data)
	return hex.EncodeToString(sum[:]), nil
}

func VerifyBundle(bundle string, manifest Manifest) error {
	for _, artifact := range manifest.Artifacts {
		path := filepath.Join(bundle, artifact.File)
		info, err := os.Lstat(path)
		if err != nil {
			return fmt.Errorf("verify %s: %w", artifact.File, err)
		}
		if !info.Mode().IsRegular() {
			return fmt.Errorf("verify %s: artifact must be a regular file", artifact.File)
		}
		got, err := fileSHA256(path)
		if err != nil {
			return fmt.Errorf("verify %s: %w", artifact.File, err)
		}
		if got != artifact.SHA256 {
			return fmt.Errorf("checksum mismatch for %s: manifest=%s actual=%s", artifact.File, artifact.SHA256, got)
		}
	}
	return nil
}

func fileSHA256(path string) (string, error) {
	f, err := os.Open(path)
	if err != nil {
		return "", err
	}
	defer f.Close()
	h := sha256.New()
	if _, err := io.Copy(h, f); err != nil {
		return "", err
	}
	return hex.EncodeToString(h.Sum(nil)), nil
}

func artifactByRole(m Manifest, role string) (Artifact, bool) {
	for _, artifact := range m.Artifacts {
		if artifact.Role == role {
			return artifact, true
		}
	}
	return Artifact{}, false
}

func infraImageRefs(images []string) (caddy, postgres, redis string, err error) {
	for _, image := range images {
		switch {
		case strings.HasPrefix(image, "artifactflow-caddy:"):
			if caddy != "" {
				return "", "", "", fmt.Errorf("duplicate Caddy image")
			}
			caddy = image
		case strings.HasPrefix(image, "artifactflow-postgres:"):
			if postgres != "" {
				return "", "", "", fmt.Errorf("duplicate Postgres image")
			}
			postgres = image
		case strings.HasPrefix(image, "artifactflow-redis:"):
			if redis != "" {
				return "", "", "", fmt.Errorf("duplicate Redis image")
			}
			redis = image
		}
	}
	for name, image := range map[string]string{"Caddy": caddy, "Postgres": postgres, "Redis": redis} {
		if !contentImagePattern.MatchString(image) {
			return "", "", "", fmt.Errorf("missing or invalid content-addressed %s image", name)
		}
	}
	return caddy, postgres, redis, nil
}

func assertHostPlatform(platform string) error {
	want := strings.TrimPrefix(platform, "linux/")
	got := runtime.GOARCH
	if got != want {
		return fmt.Errorf("bundle platform %s does not match this afctl binary/host architecture linux/%s", platform, got)
	}
	return nil
}
