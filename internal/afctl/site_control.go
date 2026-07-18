package afctl

import (
	"crypto/rand"
	"encoding/base64"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

func (c *Controller) SiteInit(preset string) error {
	if _, err := os.Stat(filepath.Join(c.Root, "deploy", ".env")); err == nil {
		return fmt.Errorf("legacy deploy/.env exists; use site migrate-v1 so credentials are preserved")
	} else if !errors.Is(err, os.ErrNotExist) {
		return err
	}
	content, err := siteTOML(preset)
	if err != nil {
		return err
	}
	if _, err := os.Stat(c.sitePath()); err == nil {
		return fmt.Errorf("%s already exists; site init never overwrites it", c.sitePath())
	} else if !errors.Is(err, os.ErrNotExist) {
		return err
	}
	for _, dir := range c.initialDirectories() {
		if err := os.MkdirAll(dir, 0o755); err != nil {
			return err
		}
	}
	if err := os.WriteFile(c.sitePath(), []byte(content), 0o644); err != nil {
		return err
	}
	if err := c.writeInitialEnv(preset); err != nil {
		return err
	}
	_, _ = fmt.Fprintf(c.Out, "initialized %s preset at %s\n", preset, c.Root)
	_, _ = fmt.Fprintf(c.Out, "edit %s and place TLS material under %s before apply\n", c.envPath(), filepath.Join(c.controlDir(), "certs"))
	return nil
}

func (c *Controller) SiteMigrateV1(preset, sandboxRuntime string) error {
	if sandboxRuntime != "runsc" && sandboxRuntime != "runc" {
		return fmt.Errorf("sandbox runtime must be explicitly runsc or runc")
	}
	legacyEnv := filepath.Join(c.Root, "deploy", ".env")
	if _, err := os.Stat(legacyEnv); err != nil {
		return fmt.Errorf("legacy environment: %w", err)
	}
	if _, err := os.Stat(c.sitePath()); err == nil {
		return fmt.Errorf("%s already exists; migration never overwrites v2 control state", c.sitePath())
	} else if !errors.Is(err, os.ErrNotExist) {
		return err
	}
	content, err := siteTOML(preset)
	if err != nil {
		return err
	}
	content = strings.Replace(content, "sandbox_runtime = \"runsc\"", "sandbox_runtime = \""+sandboxRuntime+"\"", 1)
	for _, dir := range c.initialDirectories() {
		if err := os.MkdirAll(dir, 0o755); err != nil {
			return err
		}
	}
	if err := os.WriteFile(c.sitePath(), []byte(content), 0o644); err != nil {
		return err
	}
	data, err := os.ReadFile(legacyEnv)
	if err != nil {
		return err
	}
	var lines []string
	for _, line := range strings.Split(string(data), "\n") {
		if strings.HasPrefix(strings.TrimSpace(line), "AF_ENABLE_SANDBOX=") {
			continue
		}
		lines = append(lines, line)
	}
	if err := os.WriteFile(c.envPath(), []byte(strings.Join(lines, "\n")), 0o600); err != nil {
		return err
	}
	legacyCerts := filepath.Join(c.Root, "deploy", "certs")
	if info, err := os.Stat(legacyCerts); err == nil && info.IsDir() {
		if err := copyLegacyCerts(legacyCerts, filepath.Join(c.controlDir(), "certs")); err != nil {
			return err
		}
	}
	for _, legacySite := range []string{
		filepath.Join(c.Root, ".artifactflow", "current", "config", "site"),
		filepath.Join(c.Root, "config", "site"),
	} {
		if info, err := os.Stat(legacySite); err == nil && info.IsDir() {
			if err := copyLegacySiteConfig(legacySite, filepath.Join(c.controlDir(), "site")); err != nil {
				return err
			}
			break
		}
	}
	_, _ = fmt.Fprintln(c.Out, "migrated v1 target-local environment, certificates, and site config")
	_, _ = fmt.Fprintln(c.Out, "legacy current/.fleet-state were intentionally not imported; apply one v2 full bundle to establish state.json")
	return nil
}

func (c *Controller) initialDirectories() []string {
	return []string{c.controlDir(), filepath.Join(c.controlDir(), "certs"), filepath.Join(c.controlDir(), "site"), filepath.Join(c.controlDir(), "maintenance"), filepath.Join(c.controlDir(), "autoheal"), c.releasesDir(), filepath.Join(c.Root, "bin")}
}

func copyLegacyCerts(source, destination string) error {
	entries, err := os.ReadDir(source)
	if err != nil {
		return err
	}
	for _, entry := range entries {
		if entry.IsDir() || entry.Type()&os.ModeSymlink != 0 {
			continue
		}
		name := entry.Name()
		if name != "server.crt" && name != "server.key" {
			continue
		}
		mode := os.FileMode(0o644)
		if name == "server.key" {
			mode = 0o600
		}
		if err := copyFile(filepath.Join(source, name), filepath.Join(destination, name), mode); err != nil {
			return err
		}
	}
	return nil
}

func copyLegacySiteConfig(source, destination string) error {
	for _, name := range []string{"notifications.json", "welcome_tips.json", "branding.json"} {
		path := filepath.Join(source, name)
		info, err := os.Lstat(path)
		if errors.Is(err, os.ErrNotExist) {
			continue
		}
		if err != nil {
			return err
		}
		if !info.Mode().IsRegular() {
			return fmt.Errorf("legacy site config %s must be a regular file", path)
		}
		if err := copyFile(path, filepath.Join(destination, name), 0o644); err != nil {
			return err
		}
	}
	return nil
}

func randomSecret(n int, padding bool) (string, error) {
	data := make([]byte, n)
	if _, err := rand.Read(data); err != nil {
		return "", err
	}
	if padding {
		return base64.URLEncoding.EncodeToString(data), nil
	}
	return base64.RawURLEncoding.EncodeToString(data), nil
}

func (c *Controller) writeInitialEnv(preset string) error {
	if _, err := os.Stat(c.envPath()); err == nil {
		return fmt.Errorf("%s already exists; refusing partial initialization", c.envPath())
	}
	jwt, err := randomSecret(32, false)
	if err != nil {
		return err
	}
	credential, err := randomSecret(32, true)
	if err != nil {
		return err
	}
	pg, err := randomSecret(24, false)
	if err != nil {
		return err
	}
	var tls string
	if preset == "public" {
		tls = "AF_DOMAIN=CHANGE_ME\nAF_ACME_EMAIL=CHANGE_ME\n"
	}
	content := fmt.Sprintf(`ARTIFACTFLOW_JWT_SECRET=%s
ARTIFACTFLOW_CREDENTIAL_KEY=%s
ARTIFACTFLOW_REDIS_URL=redis://redis:6379
ARTIFACTFLOW_REDIS_KEY_PREFIX=af
POSTGRES_DB=artifactflow
POSTGRES_USER=artifactflow
POSTGRES_PASSWORD=%s
ARTIFACTFLOW_DATABASE_URL=postgresql+asyncpg://artifactflow:%s@postgres:5432/artifactflow
%sDASHSCOPE_API_KEY=
`, jwt, credential, pg, pg, tls)
	return os.WriteFile(c.envPath(), []byte(content), 0o600)
}

func (c *Controller) SiteValidate() (Site, error) {
	site, err := LoadSite(c.sitePath())
	if err != nil {
		return Site{}, err
	}
	if err := validateEnv(c.envPath(), site); err != nil {
		return Site{}, err
	}
	if site.Executor == "ansible" {
		_, _ = fmt.Fprintln(c.Err, "warning: executor=ansible is experimental and has not completed physical multi-host acceptance")
	}
	_, _ = fmt.Fprintf(c.Out, "site valid: executor=%s tls=%s infra=%s sandbox_runtime=%s\n", site.Executor, site.TLS, site.Infra, site.SandboxRuntime)
	return site, nil
}

func validateEnv(path string, site Site) error {
	data, err := os.ReadFile(path)
	if err != nil {
		return err
	}
	values := map[string]string{}
	for number, line := range strings.Split(string(data), "\n") {
		line = strings.TrimSpace(line)
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		key, value, ok := strings.Cut(line, "=")
		if !ok || strings.TrimSpace(key) != key || key == "" {
			return fmt.Errorf("%s:%d: expected KEY=value", path, number+1)
		}
		if _, exists := values[key]; exists {
			return fmt.Errorf("%s:%d: duplicate key %s", path, number+1, key)
		}
		values[key] = value
	}
	for _, key := range []string{"ARTIFACTFLOW_JWT_SECRET", "ARTIFACTFLOW_CREDENTIAL_KEY", "ARTIFACTFLOW_REDIS_URL", "ARTIFACTFLOW_REDIS_KEY_PREFIX"} {
		if values[key] == "" || strings.Contains(values[key], "CHANGE_ME") {
			return fmt.Errorf("%s requires non-placeholder %s", path, key)
		}
	}
	if values["ARTIFACTFLOW_DATABASE_URL"] == "" && values["ARTIFACTFLOW_DATABASE_URLS"] == "" {
		return fmt.Errorf("%s requires ARTIFACTFLOW_DATABASE_URL or ARTIFACTFLOW_DATABASE_URLS", path)
	}
	if site.Infra == "bundled" {
		for _, key := range []string{"POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD"} {
			if values[key] == "" || strings.Contains(values[key], "CHANGE_ME") {
				return fmt.Errorf("bundled infra requires non-placeholder %s", key)
			}
		}
	}
	if site.TLS == "acme" {
		for _, key := range []string{"AF_DOMAIN", "AF_ACME_EMAIL"} {
			if values[key] == "" || strings.Contains(values[key], "CHANGE_ME") {
				return fmt.Errorf("ACME TLS requires non-placeholder %s", key)
			}
		}
		if value := values["AF_HTTP_PORT"]; value != "" && value != "80" {
			return fmt.Errorf("ACME TLS requires AF_HTTP_PORT=80")
		}
		if value := values["AF_HTTPS_PORT"]; value != "" && value != "443" {
			return fmt.Errorf("ACME TLS requires AF_HTTPS_PORT=443")
		}
	}
	if _, exists := values["AF_ENABLE_SANDBOX"]; exists {
		return fmt.Errorf("AF_ENABLE_SANDBOX is removed: sandbox is always enabled; choose sandbox_runtime in site.toml")
	}
	if runtimeValue, exists := values["ARTIFACTFLOW_SANDBOX_RUNTIME"]; exists && runtimeValue != site.SandboxRuntime {
		return fmt.Errorf("ARTIFACTFLOW_SANDBOX_RUNTIME=%s conflicts with site.toml sandbox_runtime=%s; site.toml is authoritative", runtimeValue, site.SandboxRuntime)
	}
	return nil
}
