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
	"time"
)

type fakeRunner struct {
	commands    []Command
	failName    string
	failUpCount int
}

func (r *fakeRunner) Run(_ context.Context, command Command) error {
	r.commands = append(r.commands, command)
	if command.Name == "docker" && slices.Contains(command.Args, "up") && r.failUpCount > 0 {
		r.failUpCount--
		return fmt.Errorf("forced compose up failure")
	}
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

type deadlineRunner struct{}

func (deadlineRunner) Run(context.Context, Command) error { return nil }

func (deadlineRunner) Output(ctx context.Context, _ Command) (string, error) {
	<-ctx.Done()
	return "", ctx.Err()
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
	return makeAppBundleWithInfra(t, parent, id, true)
}

func makeAppOnlyBundle(t *testing.T, parent, id string) string {
	return makeAppBundleWithInfra(t, parent, id, false)
}

func makeAppBundleWithInfra(t *testing.T, parent, id string, withInfra bool) string {
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
	}
	if withInfra {
		makeTarGz(t, filepath.Join(bundle, "infra.tar.gz"), map[string]string{"images.txt": "infra"})
		artifacts["infra"] = "infra.tar.gz"
		images = append(images,
			"artifactflow-caddy:sha256-"+strings.Repeat("a", 64),
			"artifactflow-postgres:sha256-"+strings.Repeat("b", 64),
			"artifactflow-redis:sha256-"+strings.Repeat("c", 64),
		)
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

func TestSiteInitWritesIntranetModelCredentialNames(t *testing.T) {
	root := t.TempDir()
	c := NewController(root, &bytes.Buffer{}, &bytes.Buffer{})
	if err := c.SiteInit("intranet"); err != nil {
		t.Fatal(err)
	}
	data, err := os.ReadFile(c.envPath())
	if err != nil {
		t.Fatal(err)
	}
	env := string(data)
	for _, key := range []string{
		"GPUSTACK_DEEPSEEK_API_KEY=",
		"GPUSTACK_VISION_API_KEY=",
		"ARTIFACTFLOW_COMPACTION_RESERVE_TOKENS=40000",
	} {
		if !strings.Contains(env, key) {
			t.Fatalf("generated intranet environment is missing %s: %s", key, env)
		}
	}
	if strings.Contains(env, "DASHSCOPE_API_KEY=") {
		t.Fatalf("generated intranet environment contains an unused cloud credential: %s", env)
	}
}

func TestSiteInitCreatesOutboundTrustDirectory(t *testing.T) {
	root := t.TempDir()
	c := NewController(root, &bytes.Buffer{}, &bytes.Buffer{})
	if err := c.SiteInit("intranet"); err != nil {
		t.Fatal(err)
	}
	info, err := os.Stat(c.trustAnchorDir())
	if err != nil {
		t.Fatal(err)
	}
	if !info.IsDir() {
		t.Fatalf("outbound trust path is not a directory: %s", c.trustAnchorDir())
	}
}

func TestApplyCreatesMissingOutboundTrustDirectory(t *testing.T) {
	c, _ := newTestController(t)
	if _, err := os.Stat(c.trustAnchorDir()); !os.IsNotExist(err) {
		t.Fatalf("test site unexpectedly has trust directory: %v", err)
	}
	if err := c.Apply(context.Background(), makeAppBundle(t, t.TempDir(), "v1")); err != nil {
		t.Fatal(err)
	}
	if info, err := os.Stat(c.trustAnchorDir()); err != nil || !info.IsDir() {
		t.Fatalf("apply did not create outbound trust directory: info=%v err=%v", info, err)
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
	if !strings.Contains(errOut.String(), "physical multi-host acceptance is incomplete") {
		t.Fatalf("missing multi-host acceptance warning: %s", errOut.String())
	}
}

func TestAnsibleSiteRejectsBundledInfra(t *testing.T) {
	root := t.TempDir()
	writeTestSite(t, root, "runc")
	path := filepath.Join(root, "control", "site.toml")
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	site := strings.Replace(string(data), `executor = "local"`, `executor = "ansible"`, 1)
	site = strings.Replace(site, `infra = "external"`, `infra = "bundled"`, 1)
	site = strings.Replace(site, "backend_replicas = 2", "backend_replicas = 1", 1)
	site += "inventory = \"control/inventory.ini\"\nansible_ee_image = \"example/ansible@sha256:" + strings.Repeat("0", 64) + "\"\n"
	if err := os.WriteFile(path, []byte(site), 0o644); err != nil {
		t.Fatal(err)
	}
	if _, err := LoadSite(path); err == nil || !strings.Contains(err.Error(), "requires infra") {
		t.Fatalf("expected ansible bundled-infra rejection, got %v", err)
	}
}

func TestACMESiteValidationRequiresBareDomain(t *testing.T) {
	tests := []struct {
		name    string
		domain  string
		wantErr bool
	}{
		{name: "domain", domain: "example.com"},
		{name: "localhost", domain: "localhost"},
		{name: "scheme", domain: "https://example.com", wantErr: true},
		{name: "port", domain: "example.com:443", wantErr: true},
		{name: "path", domain: "example.com/service", wantErr: true},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			root := t.TempDir()
			writeTestSite(t, root, "runc")
			sitePath := filepath.Join(root, "control", "site.toml")
			siteData, err := os.ReadFile(sitePath)
			if err != nil {
				t.Fatal(err)
			}
			siteData = []byte(strings.Replace(string(siteData), `tls = "static"`, `tls = "acme"`, 1))
			if err := os.WriteFile(sitePath, siteData, 0o644); err != nil {
				t.Fatal(err)
			}
			envPath := filepath.Join(root, "control", ".env")
			env, err := os.OpenFile(envPath, os.O_APPEND|os.O_WRONLY, 0)
			if err != nil {
				t.Fatal(err)
			}
			_, writeErr := fmt.Fprintf(env, "AF_DOMAIN=%s\nAF_ACME_EMAIL=ops@example.com\n", test.domain)
			closeErr := env.Close()
			if writeErr != nil {
				t.Fatal(writeErr)
			}
			if closeErr != nil {
				t.Fatal(closeErr)
			}

			c := NewController(root, &bytes.Buffer{}, &bytes.Buffer{})
			_, err = c.SiteValidate()
			if test.wantErr {
				if err == nil || !strings.Contains(err.Error(), "AF_DOMAIN to be a bare domain") {
					t.Fatalf("expected bare-domain error for %q, got %v", test.domain, err)
				}
				return
			}
			if err != nil {
				t.Fatalf("expected %q to be valid, got %v", test.domain, err)
			}
		})
	}
}

func TestSiteMigrateV1PreservesSecretsAndDropsOldSandboxSwitch(t *testing.T) {
	root := t.TempDir()
	legacy := filepath.Join(root, "deploy")
	if err := os.MkdirAll(filepath.Join(legacy, "certs"), 0o755); err != nil {
		t.Fatal(err)
	}
	env := "ARTIFACTFLOW_JWT_SECRET=keep-me\nAF_ENABLE_SANDBOX=0\nARTIFACTFLOW_DATABASE_URL=postgres://db\nARTIFACTFLOW_COMPACTION_TOKEN_THRESHOLD=100000\nARTIFACTFLOW_RENDER_TOOL_EXAMPLES=false\n"
	if err := os.WriteFile(filepath.Join(legacy, ".env"), []byte(env), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(legacy, "certs", "server.crt"), []byte("cert"), 0o644); err != nil {
		t.Fatal(err)
	}
	legacySite := filepath.Join(root, ".artifactflow", "current", "config", "site")
	if err := os.MkdirAll(legacySite, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(legacySite, "notifications.json"), []byte("[]\n"), 0o644); err != nil {
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
	if !strings.Contains(string(got), "keep-me") ||
		strings.Contains(string(got), "AF_ENABLE_SANDBOX") ||
		strings.Contains(string(got), "ARTIFACTFLOW_COMPACTION_TOKEN_THRESHOLD") ||
		strings.Contains(string(got), "ARTIFACTFLOW_RENDER_TOOL_EXAMPLES") ||
		!strings.Contains(string(got), "ARTIFACTFLOW_COMPACTION_RESERVE_TOKENS=40000") {
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
	migratedSite, err := os.ReadFile(filepath.Join(c.controlDir(), "site", "notifications.json"))
	if err != nil || string(migratedSite) != "[]\n" {
		t.Fatalf("legacy site config not migrated: %q, %v", migratedSite, err)
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

func TestAppOnlyManifestDeclaresNoInfrastructure(t *testing.T) {
	bundle := makeAppOnlyBundle(t, t.TempDir(), "v2")
	manifest, err := LoadManifest(bundle)
	if err != nil {
		t.Fatal(err)
	}
	if len(manifest.Images) != 3 {
		t.Fatalf("app-only images=%v", manifest.Images)
	}
	if _, ok := artifactByRole(manifest, "infra"); ok {
		t.Fatal("app-only bundle unexpectedly carries an infra artifact")
	}
	for _, image := range manifest.Images {
		if contentImagePattern.MatchString(image) {
			t.Fatalf("app-only bundle unexpectedly declares infra image %s", image)
		}
	}
}

func TestManifestSupportsLegacyAppOnlyAndRequiresRefsForInfraArtifact(t *testing.T) {
	full, err := LoadManifest(makeAppBundle(t, t.TempDir(), "v1"))
	if err != nil {
		t.Fatal(err)
	}
	for i, artifact := range full.Artifacts {
		if artifact.Role == "infra" {
			full.Artifacts = append(full.Artifacts[:i], full.Artifacts[i+1:]...)
			break
		}
	}
	if err := full.Validate(); err != nil {
		t.Fatalf("legacy six-image app-only manifest must remain readable: %v", err)
	}

	appOnly, err := LoadManifest(makeAppOnlyBundle(t, t.TempDir(), "v2"))
	if err != nil {
		t.Fatal(err)
	}
	appOnly.Artifacts = append(appOnly.Artifacts, Artifact{Role: "infra", File: "infra.tar.gz", SHA256: strings.Repeat("e", 64)})
	if err := appOnly.Validate(); err == nil || !strings.Contains(err.Error(), "with infra artifact must declare exactly six") {
		t.Fatalf("expected missing infra image rejection, got %v", err)
	}
}

func TestAppOnlyRequiresCurrentReleaseAndInheritsItsInfra(t *testing.T) {
	c, runner := newTestController(t)
	appOnly := makeAppOnlyBundle(t, t.TempDir(), "v2")
	if _, err := c.PlanApply(appOnly); err == nil || !strings.Contains(err.Error(), "requires an existing current release") {
		t.Fatalf("expected first-apply app-only rejection, got %v", err)
	}

	if err := c.Apply(context.Background(), makeAppBundle(t, t.TempDir(), "v1")); err != nil {
		t.Fatal(err)
	}
	v1, err := c.readRelease("v1")
	if err != nil {
		t.Fatal(err)
	}
	wantCaddy, wantPostgres, wantRedis, err := infraImageRefs(v1.Images)
	if err != nil {
		t.Fatal(err)
	}
	legacy, err := LoadManifest(makeAppBundle(t, t.TempDir(), "legacy"))
	if err != nil {
		t.Fatal(err)
	}
	for i, artifact := range legacy.Artifacts {
		if artifact.Role == "infra" {
			legacy.Artifacts = append(legacy.Artifacts[:i], legacy.Artifacts[i+1:]...)
			break
		}
	}
	for i, image := range legacy.Images {
		switch {
		case strings.HasPrefix(image, "artifactflow-caddy:"):
			legacy.Images[i] = "caddy:2.10-alpine"
		case strings.HasPrefix(image, "artifactflow-postgres:"):
			legacy.Images[i] = "postgres:16-alpine"
		case strings.HasPrefix(image, "artifactflow-redis:"):
			legacy.Images[i] = "redis:7-alpine"
		}
	}
	if err := legacy.Validate(); err != nil {
		t.Fatalf("ignored legacy infra refs must not invalidate app-only: %v", err)
	}
	legacyImages, err := c.resolveAppImages(legacy, "v1")
	if err != nil {
		t.Fatal(err)
	}
	legacyCaddy, legacyPostgres, legacyRedis, err := infraImageRefs(legacyImages)
	if err != nil {
		t.Fatal(err)
	}
	if legacyCaddy != wantCaddy || legacyPostgres != wantPostgres || legacyRedis != wantRedis {
		t.Fatalf("legacy app-only manifest refs were not ignored: %v", legacyImages)
	}

	plan, err := c.PlanApply(appOnly)
	if err != nil {
		t.Fatal(err)
	}
	foundInheritance := false
	for _, action := range plan.Actions {
		if action == "inherit infrastructure image references from current release v1" {
			foundInheritance = true
		}
	}
	if !foundInheritance {
		t.Fatalf("plan did not disclose infra inheritance: %v", plan.Actions)
	}

	runner.commands = nil
	if err := c.Apply(context.Background(), appOnly); err != nil {
		t.Fatal(err)
	}
	v2, err := c.readRelease("v2")
	if err != nil {
		t.Fatal(err)
	}
	gotCaddy, gotPostgres, gotRedis, err := infraImageRefs(v2.Images)
	if err != nil {
		t.Fatal(err)
	}
	if gotCaddy != wantCaddy || gotPostgres != wantPostgres || gotRedis != wantRedis {
		t.Fatalf("app-only infra drifted: got=%v want=%v", []string{gotCaddy, gotPostgres, gotRedis}, []string{wantCaddy, wantPostgres, wantRedis})
	}
	for _, command := range runner.commands {
		if command.Name == "docker" && len(command.Args) >= 3 && command.Args[0] == "load" && strings.Contains(command.Args[2], "infra") {
			t.Fatalf("app-only apply loaded an infra archive: %v", command.Args)
		}
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
	if _, err := os.Stat(c.trustAnchorDir()); !os.IsNotExist(err) {
		t.Fatalf("plan created optional outbound trust directory: %v", err)
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
	deadlineProbe := false
	for _, command := range runner.commands {
		if command.Name == "docker" && len(command.Args) >= 3 && command.Args[0] == "load" {
			if !strings.HasPrefix(command.Args[2], filepath.Join(c.releaseDir("v1"), "artifacts")) {
				t.Fatalf("image load escaped materialized release: %v", command.Args)
			}
		}
		if command.Name == "docker" && slices.Contains(command.Args, "up") && slices.Contains(command.Args, "--force-recreate") {
			forceRecreate = true
		}
		if command.Name == "docker" && slices.Contains(command.Args, "http://localhost:2021/health/ready") && slices.Contains(command.Args, "1") {
			deadlineProbe = true
		}
	}
	if !forceRecreate {
		t.Fatal("apply must recreate services so stable-path env/certificate changes take effect")
	}
	if !deadlineProbe {
		t.Fatal("apply must cap its readiness probe by the remaining deadline")
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

func TestApplyKeepMaintenanceLeavesFlagAfterHealthyStateWrite(t *testing.T) {
	c, _ := newTestController(t)
	bundle := makeAppBundle(t, t.TempDir(), "v1")
	if err := c.applyWithOptions(context.Background(), bundle, applyOptions{KeepMaintenance: true}); err != nil {
		t.Fatal(err)
	}

	flag := filepath.Join(c.controlDir(), "maintenance", "MAINTENANCE_ON")
	if _, err := os.Stat(flag); err != nil {
		t.Fatalf("keep-maintenance did not preserve maintenance flag: %v", err)
	}
	out := c.Out.(*bytes.Buffer).String()
	if !strings.Contains(out, "leave maintenance enabled for operator verification") {
		t.Fatalf("apply plan did not describe retained maintenance: %s", out)
	}
	if !strings.Contains(out, "maintenance remains enabled (--keep-maintenance)") {
		t.Fatalf("apply result did not report retained maintenance: %s", out)
	}
}

func TestCLIApplyKeepMaintenanceFlagAndValidation(t *testing.T) {
	c, _ := newTestController(t)
	bundle := makeAppBundle(t, t.TempDir(), "v1")
	if err := dispatch(context.Background(), c, []string{"apply", bundle, "--keep-maintenance"}); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Join(c.controlDir(), "maintenance", "MAINTENANCE_ON")); err != nil {
		t.Fatalf("CLI keep-maintenance did not preserve flag: %v", err)
	}

	for _, args := range [][]string{
		{"apply", bundle, "--unknown"},
		{"apply", bundle, "--keep-maintenance", "--keep-maintenance"},
		{"apply", bundle, "another-target"},
		{"apply", "--keep-maintenance"},
	} {
		if err := dispatch(context.Background(), c, args); err == nil {
			t.Fatalf("expected apply argument rejection for %v", args)
		}
	}
}

func TestApplyKeepMaintenanceSurvivesSuccessfulRecovery(t *testing.T) {
	c, runner := newTestController(t)
	if err := c.Apply(context.Background(), makeAppBundle(t, t.TempDir(), "v1")); err != nil {
		t.Fatal(err)
	}
	runner.failUpCount = 1

	err := c.applyWithOptions(
		context.Background(),
		makeAppBundle(t, t.TempDir(), "v2"),
		applyOptions{KeepMaintenance: true},
	)
	if err == nil || !strings.Contains(err.Error(), "restored last-known-good release v1; maintenance remains enabled") {
		t.Fatalf("expected recovery with retained maintenance, got %v", err)
	}
	state, stateErr := c.readState()
	if stateErr != nil {
		t.Fatal(stateErr)
	}
	if state.Current != "v1" || state.Generation != 1 {
		t.Fatalf("failed apply changed state: %+v", state)
	}
	if _, statErr := os.Stat(filepath.Join(c.controlDir(), "maintenance", "MAINTENANCE_ON")); statErr != nil {
		t.Fatalf("recovery did not preserve maintenance flag: %v", statErr)
	}
}

func TestLocalReadinessProbeCannotOutliveSiteDeadline(t *testing.T) {
	c := NewController(t.TempDir(), &bytes.Buffer{}, &bytes.Buffer{})
	c.Runner = deadlineRunner{}
	started := time.Now()
	err := c.reconcile(context.Background(), Site{
		Executor:            "local",
		Infra:               "external",
		ReadyTimeoutSeconds: 1,
	}, "v1", ReleaseMetadata{})
	if err == nil || !strings.Contains(err.Error(), "timed out after 1s") {
		t.Fatalf("expected readiness timeout, got %v", err)
	}
	if elapsed := time.Since(started); elapsed > 2*time.Second {
		t.Fatalf("one-second readiness deadline took %s", elapsed)
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

func TestMaintenanceWritesAndRemovesOperatorNote(t *testing.T) {
	c, _ := newTestController(t)
	if err := c.Maintenance(context.Background(), "on", "planned database work"); err != nil {
		t.Fatal(err)
	}
	maintenanceDir := filepath.Join(c.controlDir(), "maintenance")
	note, err := os.ReadFile(filepath.Join(maintenanceDir, "note.txt"))
	if err != nil {
		t.Fatal(err)
	}
	if string(note) != "planned database work\n" {
		t.Fatalf("unexpected maintenance note %q", note)
	}
	if _, err := os.Stat(filepath.Join(maintenanceDir, "MAINTENANCE_ON")); err != nil {
		t.Fatalf("maintenance flag missing: %v", err)
	}

	if err := c.Maintenance(context.Background(), "off", ""); err != nil {
		t.Fatal(err)
	}
	for _, name := range []string{"note.txt", "MAINTENANCE_ON"} {
		if _, err := os.Stat(filepath.Join(maintenanceDir, name)); !os.IsNotExist(err) {
			t.Fatalf("maintenance off retained %s: %v", name, err)
		}
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
