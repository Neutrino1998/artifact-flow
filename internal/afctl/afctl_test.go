package afctl

import (
	"archive/tar"
	"bytes"
	"compress/gzip"
	"context"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"slices"
	"strings"
	"testing"
)

type fakeRunner struct {
	commands []Command
	failName string
}

func (r *fakeRunner) Run(_ context.Context, command Command) error {
	r.commands = append(r.commands, command)
	if command.Name == r.failName {
		return fmt.Errorf("forced %s failure", command.Name)
	}
	return nil
}

func (r *fakeRunner) Output(_ context.Context, command Command) (string, error) {
	r.commands = append(r.commands, command)
	if command.Name == r.failName {
		return "", fmt.Errorf("forced %s failure", command.Name)
	}
	if command.Name == "docker" && len(command.Args) > 0 && command.Args[0] == "info" {
		return `{"runsc":{}}`, nil
	}
	return "ok", nil
}

func writeTestSite(t *testing.T, root, runtimeName string) {
	t.Helper()
	control := filepath.Join(root, "control")
	if err := os.MkdirAll(filepath.Join(control, "certs"), 0o755); err != nil {
		t.Fatal(err)
	}
	site := fmt.Sprintf(`schema = 1
executor = "local"
tls = "static"
infra = "external"
sandbox_runtime = %q
scratch_root = "/test/sandbox"
backend_replicas = 2
ready_timeout_seconds = 1
`, runtimeName)
	if err := os.WriteFile(filepath.Join(control, "site.toml"), []byte(site), 0o644); err != nil {
		t.Fatal(err)
	}
	env := `ARTIFACTFLOW_JWT_SECRET=test-secret
ARTIFACTFLOW_CREDENTIAL_KEY=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa=
ARTIFACTFLOW_REDIS_URL=redis://redis:6379
ARTIFACTFLOW_REDIS_KEY_PREFIX=af
ARTIFACTFLOW_DATABASE_URL=postgresql+asyncpg://af:test@db:5432/af
`
	if err := os.WriteFile(filepath.Join(control, ".env"), []byte(env), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(control, "certs", "server.crt"), []byte("cert"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(control, "certs", "server.key"), []byte("key"), 0o600); err != nil {
		t.Fatal(err)
	}
}

func makeTarGz(t *testing.T, path string, files map[string]string) {
	t.Helper()
	f, err := os.Create(path)
	if err != nil {
		t.Fatal(err)
	}
	gz := gzip.NewWriter(f)
	tw := tar.NewWriter(gz)
	for name, body := range files {
		hdr := &tar.Header{Name: name, Mode: 0o644, Size: int64(len(body)), Typeflag: tar.TypeReg}
		if err := tw.WriteHeader(hdr); err != nil {
			t.Fatal(err)
		}
		if _, err := tw.Write([]byte(body)); err != nil {
			t.Fatal(err)
		}
	}
	if err := tw.Close(); err != nil {
		t.Fatal(err)
	}
	if err := gz.Close(); err != nil {
		t.Fatal(err)
	}
	if err := f.Close(); err != nil {
		t.Fatal(err)
	}
}

func makeAppBundle(t *testing.T, parent, id string) string {
	t.Helper()
	bundle := filepath.Join(parent, "bundle-"+id)
	if err := os.MkdirAll(bundle, 0o755); err != nil {
		t.Fatal(err)
	}
	makeTarGz(t, filepath.Join(bundle, "app.tar.gz"), map[string]string{"images.txt": "app"})
	makeTarGz(t, filepath.Join(bundle, "config.tar.gz"), map[string]string{"config/models/models.yaml": "endpoint: http://old\n"})
	makeTarGz(t, filepath.Join(bundle, "deploy.tar.gz"), map[string]string{
		"deploy/compose.base.yml":     "services: {}\n",
		"deploy/compose.sandbox.yml":  "services: {}\n",
		"deploy/compose.tls-acme.yml": "services: {}\n",
	})
	makeTarGz(t, filepath.Join(bundle, "sandbox.tar.gz"), map[string]string{"images.txt": "sandbox"})
	artifacts := map[string]string{"app": "app.tar.gz", "config": "config.tar.gz", "deploy": "deploy.tar.gz", "sandbox": "sandbox.tar.gz"}
	sandboxImage := "artifactflow-sandbox:sha256-" + strings.Repeat("d", 64)
	images := []string{
		"artifactflow:" + id,
		"artifactflow-frontend:" + id,
		sandboxImage,
		"artifactflow-caddy:sha256-" + strings.Repeat("a", 64),
		"artifactflow-postgres:sha256-" + strings.Repeat("b", 64),
		"artifactflow-redis:sha256-" + strings.Repeat("c", 64),
	}
	if err := WriteManifest(ManifestOptions{Bundle: bundle, ReleaseID: id, Kind: "app", Platform: "linux/" + runtime.GOARCH, Source: "test", SandboxImage: sandboxImage, Images: images, Artifacts: artifacts}); err != nil {
		t.Fatal(err)
	}
	return bundle
}

func newTestController(t *testing.T) (*Controller, *fakeRunner) {
	t.Helper()
	root := t.TempDir()
	writeTestSite(t, root, "runc")
	runner := &fakeRunner{}
	c := NewController(root, &bytes.Buffer{}, &bytes.Buffer{})
	c.Runner = runner
	return c, runner
}

func TestLoadSiteRejectsUnknownAndDuplicateFields(t *testing.T) {
	root := t.TempDir()
	writeTestSite(t, root, "runc")
	path := filepath.Join(root, "control", "site.toml")
	f, err := os.OpenFile(path, os.O_APPEND|os.O_WRONLY, 0)
	if err != nil {
		t.Fatal(err)
	}
	_, _ = f.WriteString("mystery = \"value\"\n")
	_ = f.Close()
	if _, err := LoadSite(path); err == nil || !strings.Contains(err.Error(), "unknown field") {
		t.Fatalf("expected unknown-field error, got %v", err)
	}

	writeTestSite(t, root, "runc")
	f, err = os.OpenFile(path, os.O_APPEND|os.O_WRONLY, 0)
	if err != nil {
		t.Fatal(err)
	}
	_, _ = f.WriteString("tls = \"acme\"\n")
	_ = f.Close()
	if _, err := LoadSite(path); err == nil || !strings.Contains(err.Error(), "duplicate field") {
		t.Fatalf("expected duplicate-field error, got %v", err)
	}
}

func TestAnsibleSiteValidationWarnsThatExecutorIsExperimental(t *testing.T) {
	root := t.TempDir()
	writeTestSite(t, root, "runc")
	path := filepath.Join(root, "control", "site.toml")
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	site := strings.Replace(string(data), `executor = "local"`, `executor = "ansible"`, 1)
	site = strings.Replace(site, "backend_replicas = 2", "backend_replicas = 1", 1)
	site += "inventory = \"control/inventory.ini\"\nansible_ee_image = \"example/ansible@sha256:" + strings.Repeat("0", 64) + "\"\n"
	if err := os.WriteFile(path, []byte(site), 0o644); err != nil {
		t.Fatal(err)
	}
	var errOut bytes.Buffer
	c := NewController(root, &bytes.Buffer{}, &errOut)
	if _, err := c.SiteValidate(); err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(errOut.String(), "experimental") {
		t.Fatalf("missing experimental warning: %s", errOut.String())
	}
}

func TestSiteMigrateV1PreservesSecretsAndDropsOldSandboxSwitch(t *testing.T) {
	root := t.TempDir()
	legacy := filepath.Join(root, "deploy")
	if err := os.MkdirAll(filepath.Join(legacy, "certs"), 0o755); err != nil {
		t.Fatal(err)
	}
	env := "ARTIFACTFLOW_JWT_SECRET=keep-me\nAF_ENABLE_SANDBOX=0\nARTIFACTFLOW_DATABASE_URL=postgres://db\n"
	if err := os.WriteFile(filepath.Join(legacy, ".env"), []byte(env), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(legacy, "certs", "server.crt"), []byte("cert"), 0o644); err != nil {
		t.Fatal(err)
	}
	c := NewController(root, &bytes.Buffer{}, &bytes.Buffer{})
	if err := c.SiteMigrateV1("intranet", "runsc"); err != nil {
		t.Fatal(err)
	}
	got, err := os.ReadFile(c.envPath())
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(got), "keep-me") || strings.Contains(string(got), "AF_ENABLE_SANDBOX") {
		t.Fatalf("unexpected migrated env: %s", got)
	}
	site, err := LoadSite(c.sitePath())
	if err != nil {
		t.Fatal(err)
	}
	if site.SandboxRuntime != "runsc" {
		t.Fatalf("runtime=%s", site.SandboxRuntime)
	}
	if _, err := os.Stat(c.statePath()); !os.IsNotExist(err) {
		t.Fatalf("legacy state must not be imported: %v", err)
	}
}

func TestStrictJSONRejectsUnknownManifestField(t *testing.T) {
	bundle := t.TempDir()
	data := `{"schema":1,"release_id":"v1","kind":"app","platform":"linux/amd64","created_at":"now","source":"test","artifacts":[],"surprise":true}`
	if err := os.WriteFile(filepath.Join(bundle, "manifest.json"), []byte(data), 0o644); err != nil {
		t.Fatal(err)
	}
	if _, err := LoadManifest(bundle); err == nil || !strings.Contains(err.Error(), "unknown field") {
		t.Fatalf("expected strict JSON error, got %v", err)
	}
}

func TestStrictJSONRejectsTrailingInvalidBytes(t *testing.T) {
	path := filepath.Join(t.TempDir(), "state.json")
	if err := os.WriteFile(path, []byte(`{"schema":1} trailing`), 0o600); err != nil {
		t.Fatal(err)
	}
	var value map[string]any
	if err := readStrictJSON(path, &value); err == nil || !strings.Contains(err.Error(), "trailing invalid JSON") {
		t.Fatalf("expected trailing-data error, got %v", err)
	}
}

func TestManifestIdentityIncludesArchiveContentButNotBuildTimestamp(t *testing.T) {
	bundle := t.TempDir()
	file := filepath.Join(bundle, "app.tar.gz")
	if err := os.WriteFile(file, []byte("first"), 0o644); err != nil {
		t.Fatal(err)
	}
	sha1, _ := fileSHA256(file)
	m1 := Manifest{Schema: 1, ReleaseID: "v1", Kind: "app", Platform: "linux/amd64", CreatedAt: "one", Source: "a", SandboxImage: "sandbox:x", Images: []string{"artifactflow:v1", "artifactflow-frontend:v1", "sandbox:x"}, Artifacts: []Artifact{{Role: "app", File: "app.tar.gz", SHA256: sha1}, {Role: "config", File: "config", SHA256: strings.Repeat("a", 64)}, {Role: "deploy", File: "deploy", SHA256: strings.Repeat("b", 64)}, {Role: "sandbox", File: "sandbox", SHA256: strings.Repeat("c", 64)}}}
	m2 := m1
	m2.CreatedAt = "two"
	m2.Source = "b"
	id1, _ := m1.Identity()
	id2, _ := m2.Identity()
	if id1 != id2 {
		t.Fatal("build metadata must not change content identity")
	}
	if err := os.WriteFile(file, []byte("second"), 0o644); err != nil {
		t.Fatal(err)
	}
	sha2, _ := fileSHA256(file)
	m2.Artifacts[0].SHA256 = sha2
	id2, _ = m2.Identity()
	if id1 == id2 {
		t.Fatal("archive content must change identity")
	}
}

func TestManifestRejectsMutableRuntimeImageReferences(t *testing.T) {
	bundle := makeAppBundle(t, t.TempDir(), "v1")
	manifest, err := LoadManifest(bundle)
	if err != nil {
		t.Fatal(err)
	}
	manifest.SandboxImage = "artifactflow-sandbox:latest"
	if err := manifest.Validate(); err == nil || !strings.Contains(err.Error(), "content-addressed sandbox_image") {
		t.Fatalf("expected mutable sandbox rejection, got %v", err)
	}

	manifest, err = LoadManifest(bundle)
	if err != nil {
		t.Fatal(err)
	}
	for i, image := range manifest.Images {
		if strings.HasPrefix(image, "artifactflow-caddy:") {
			manifest.Images[i] = "caddy:2.10-alpine"
		}
	}
	if err := manifest.Validate(); err == nil || !strings.Contains(err.Error(), "content-addressed artifactflow-caddy") {
		t.Fatalf("expected mutable infra rejection, got %v", err)
	}
}

func TestPlanApplyIsReadOnly(t *testing.T) {
	c, _ := newTestController(t)
	bundle := makeAppBundle(t, t.TempDir(), "v1")
	plan, err := c.PlanApply(bundle)
	if err != nil {
		t.Fatal(err)
	}
	if plan.Target != "v1" {
		t.Fatalf("target=%s", plan.Target)
	}
	if _, err := os.Stat(c.releaseDir("v1")); !os.IsNotExist(err) {
		t.Fatalf("plan wrote release dir: %v", err)
	}
	if _, err := os.Stat(c.statePath()); !os.IsNotExist(err) {
		t.Fatalf("plan wrote state: %v", err)
	}
	if _, err := os.Stat(c.lockPath()); !os.IsNotExist(err) {
		t.Fatalf("plan took mutation lock: %v", err)
	}
}

func TestApplyWritesOneStateAndConfigHotfixBindsBase(t *testing.T) {
	c, runner := newTestController(t)
	bundle := makeAppBundle(t, t.TempDir(), "v1")
	if err := c.Apply(context.Background(), bundle); err != nil {
		t.Fatal(err)
	}
	state, err := c.readState()
	if err != nil {
		t.Fatal(err)
	}
	if state.Current != "v1" || state.Previous != "" || state.Generation != 1 {
		t.Fatalf("unexpected state: %+v", state)
	}
	if _, err := os.Lstat(filepath.Join(c.runtimeDir(), "current")); !os.IsNotExist(err) {
		t.Fatalf("legacy current symlink must not exist: %v", err)
	}
	forceRecreate := false
	for _, command := range runner.commands {
		if command.Name == "docker" && len(command.Args) >= 3 && command.Args[0] == "load" {
			if !strings.HasPrefix(command.Args[2], filepath.Join(c.releaseDir("v1"), "artifacts")) {
				t.Fatalf("image load escaped materialized release: %v", command.Args)
			}
		}
		if command.Name == "docker" && slices.Contains(command.Args, "up") && slices.Contains(command.Args, "--force-recreate") {
			forceRecreate = true
		}
	}
	if !forceRecreate {
		t.Fatal("apply must recreate services so stable-path env/certificate changes take effect")
	}

	workspace := filepath.Join(t.TempDir(), "hotfix")
	if err := c.ConfigCheckout(workspace); err != nil {
		t.Fatal(err)
	}
	model := filepath.Join(workspace, "config", "models", "models.yaml")
	if err := os.WriteFile(model, []byte("endpoint: http://new\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := c.ConfigApply(context.Background(), workspace, "hotfix-model-v2"); err != nil {
		t.Fatal(err)
	}
	state, err = c.readState()
	if err != nil {
		t.Fatal(err)
	}
	if state.Current != "hotfix-model-v2" || state.Previous != "v1" || state.Generation != 2 {
		t.Fatalf("unexpected hotfix state: %+v", state)
	}
	meta, err := c.readRelease(state.Current)
	if err != nil {
		t.Fatal(err)
	}
	if meta.BaseRelease != "v1" || meta.AppVersion != "v1" {
		t.Fatalf("hotfix did not inherit base: %+v", meta)
	}
	var manifest Manifest
	if err := readStrictJSON(filepath.Join(c.releaseDir(state.Current), "manifest.json"), &manifest); err != nil {
		t.Fatal(err)
	}
	if manifest.ExpectedBaseRelease != "v1" {
		t.Fatalf("manifest base=%s", manifest.ExpectedBaseRelease)
	}
}

func TestConfigHotfixRejectsStaleCheckout(t *testing.T) {
	c, _ := newTestController(t)
	bundle := makeAppBundle(t, t.TempDir(), "v1")
	if err := c.Apply(context.Background(), bundle); err != nil {
		t.Fatal(err)
	}
	workspace := filepath.Join(t.TempDir(), "hotfix")
	if err := c.ConfigCheckout(workspace); err != nil {
		t.Fatal(err)
	}
	active := filepath.Join(c.releaseDir("v1"), "config", "models", "models.yaml")
	if err := os.WriteFile(active, []byte("endpoint: changed-in-place\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	err := c.ConfigApply(context.Background(), workspace, "stale")
	if err == nil || !strings.Contains(err.Error(), "changed in place") {
		t.Fatalf("expected stale error, got %v", err)
	}
}

func TestRollbackUsesSameApplyPathAndSwapsState(t *testing.T) {
	c, _ := newTestController(t)
	for _, id := range []string{"v1", "v2"} {
		if err := c.Apply(context.Background(), makeAppBundle(t, t.TempDir(), id)); err != nil {
			t.Fatal(err)
		}
	}
	if err := c.Rollback(context.Background()); err != nil {
		t.Fatal(err)
	}
	state, err := c.readState()
	if err != nil {
		t.Fatal(err)
	}
	if state.Current != "v1" || state.Previous != "v2" || state.Generation != 3 {
		t.Fatalf("unexpected rollback state: %+v", state)
	}
}

func TestMutationLockUsesKernelOwnership(t *testing.T) {
	path := filepath.Join(t.TempDir(), "mutation.lock")
	first, err := acquireMutationLock(path)
	if err != nil {
		t.Fatal(err)
	}
	defer first.Close()
	if _, err := acquireMutationLock(path); err == nil || !strings.Contains(err.Error(), "another afctl mutation") {
		t.Fatalf("expected contention, got %v", err)
	}
	if err := first.Close(); err != nil {
		t.Fatal(err)
	}
	second, err := acquireMutationLock(path)
	if err != nil {
		t.Fatalf("kernel lock should release with fd: %v", err)
	}
	_ = second.Close()
}

func TestMaintenanceMutationUsesTheSameKernelLock(t *testing.T) {
	c, _ := newTestController(t)
	lock, err := acquireMutationLock(c.lockPath())
	if err != nil {
		t.Fatal(err)
	}
	defer lock.Close()
	if err := c.Maintenance(context.Background(), "on", "test"); err == nil || !strings.Contains(err.Error(), "another afctl mutation") {
		t.Fatalf("expected maintenance lock contention, got %v", err)
	}
}

func TestCLIRejectsRollbackExtraArguments(t *testing.T) {
	var out, errOut bytes.Buffer
	code := Run([]string{"--root", t.TempDir(), "rollback", "v1", "--dry-run"}, &out, &errOut)
	if code == 0 || !strings.Contains(errOut.String(), "usage: afctl rollback") {
		t.Fatalf("code=%d stderr=%s", code, errOut.String())
	}
}

func TestExtractRejectsTraversal(t *testing.T) {
	archive := filepath.Join(t.TempDir(), "bad.tar.gz")
	makeTarGz(t, archive, map[string]string{"../escape": "bad"})
	err := extractTarGz(archive, t.TempDir())
	if err == nil || !strings.Contains(err.Error(), "unsafe archive path") {
		t.Fatalf("expected traversal rejection, got %v", err)
	}
}
