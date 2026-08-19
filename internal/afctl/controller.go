package afctl

import (
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"slices"
	"strconv"
	"strings"
	"time"
)

const frontendReadinessScript = `const timeout=Number(process.argv[1])*1000;const req=require('http').get('http://localhost:3000/',res=>process.exit(res.statusCode<500?0:1));req.setTimeout(timeout,()=>{req.destroy();process.exit(1)});req.on('error',()=>process.exit(1));`

type Controller struct {
	Root   string
	Runner Runner
	Out    io.Writer
	Err    io.Writer
}

type applyOptions struct {
	KeepMaintenance bool
}

func NewController(root string, out, errOut io.Writer) *Controller {
	return &Controller{Root: root, Runner: OSRunner{Out: out, Err: errOut}, Out: out, Err: errOut}
}

func (c *Controller) controlDir() string          { return filepath.Join(c.Root, "control") }
func (c *Controller) runtimeDir() string          { return filepath.Join(c.Root, ".artifactflow") }
func (c *Controller) releasesDir() string         { return filepath.Join(c.runtimeDir(), "releases") }
func (c *Controller) releaseDir(id string) string { return filepath.Join(c.releasesDir(), id) }
func (c *Controller) sitePath() string            { return filepath.Join(c.controlDir(), "site.toml") }
func (c *Controller) envPath() string             { return filepath.Join(c.controlDir(), ".env") }
func (c *Controller) trustAnchorDir() string {
	return filepath.Join(c.controlDir(), "trust", "ca-certificates")
}
func (c *Controller) authConfigDir() string {
	return filepath.Join(c.controlDir(), "auth")
}

func (c *Controller) statePath() string { return filepath.Join(c.runtimeDir(), "state.json") }
func (c *Controller) lockPath() string  { return filepath.Join(c.runtimeDir(), "mutation.lock") }

func (c *Controller) readState() (State, error) {
	var state State
	err := readStrictJSON(c.statePath(), &state)
	if errors.Is(err, os.ErrNotExist) {
		return State{Schema: StateSchema}, nil
	}
	if err != nil {
		return State{}, err
	}
	if state.Schema != StateSchema {
		return State{}, fmt.Errorf("state schema must be %d", StateSchema)
	}
	if state.Current != "" && !releaseIDPattern.MatchString(state.Current) {
		return State{}, fmt.Errorf("state has invalid current release")
	}
	if state.Previous != "" && !releaseIDPattern.MatchString(state.Previous) {
		return State{}, fmt.Errorf("state has invalid previous release")
	}
	if state.Current == "" {
		if state.Previous != "" || state.Generation != 0 || state.UpdatedAt != "" {
			return State{}, fmt.Errorf("empty state cannot record previous/generation/timestamp")
		}
		return state, nil
	}
	if state.Current == state.Previous {
		return State{}, fmt.Errorf("state current and previous must differ")
	}
	if state.Generation == 0 {
		return State{}, fmt.Errorf("active state requires a non-zero generation")
	}
	if _, err := time.Parse(time.RFC3339, state.UpdatedAt); err != nil {
		return State{}, fmt.Errorf("state updated_at must be RFC3339")
	}
	return state, nil
}

func (c *Controller) writeState(current, previous string, old State) error {
	return writeJSONAtomic(c.statePath(), State{Schema: StateSchema, Current: current, Previous: previous, UpdatedAt: timestamp(), Generation: old.Generation + 1}, 0o600)
}

func (c *Controller) readRelease(id string) (ReleaseMetadata, error) {
	if !releaseIDPattern.MatchString(id) {
		return ReleaseMetadata{}, fmt.Errorf("invalid release id %q", id)
	}
	var meta ReleaseMetadata
	path := filepath.Join(c.releaseDir(id), ".af-release.json")
	if err := readStrictJSON(path, &meta); err != nil {
		return ReleaseMetadata{}, err
	}
	if meta.Schema != ReleaseSchema || meta.ReleaseID != id {
		return ReleaseMetadata{}, fmt.Errorf("invalid release metadata at %s", path)
	}
	if (meta.Platform != "linux/amd64" && meta.Platform != "linux/arm64") || !shaPattern.MatchString(meta.Identity) || !releaseIDPattern.MatchString(meta.AppVersion) || !sandboxImagePattern.MatchString(meta.SandboxImage) {
		return ReleaseMetadata{}, fmt.Errorf("incomplete release metadata at %s", path)
	}
	if (meta.Kind == "app" && meta.BaseRelease != "") || (meta.Kind == "config" && !releaseIDPattern.MatchString(meta.BaseRelease)) || (meta.Kind != "app" && meta.Kind != "config") {
		return ReleaseMetadata{}, fmt.Errorf("invalid release kind/base at %s", path)
	}
	images := map[string]bool{}
	for _, image := range meta.Images {
		if image == "" || images[image] {
			return ReleaseMetadata{}, fmt.Errorf("invalid release images at %s", path)
		}
		images[image] = true
	}
	for _, image := range []string{"artifactflow:" + meta.AppVersion, "artifactflow-frontend:" + meta.AppVersion, meta.SandboxImage} {
		if !images[image] {
			return ReleaseMetadata{}, fmt.Errorf("release metadata at %s is missing %s", path, image)
		}
	}
	if len(images) != 6 {
		return ReleaseMetadata{}, fmt.Errorf("release metadata at %s must contain six images", path)
	}
	if _, _, _, err := infraImageRefs(meta.Images); err != nil {
		return ReleaseMetadata{}, fmt.Errorf("invalid release metadata at %s: %w", path, err)
	}
	if _, err := time.Parse(time.RFC3339, meta.MaterializedAt); err != nil {
		return ReleaseMetadata{}, fmt.Errorf("release metadata at %s has invalid materialized_at", path)
	}
	return meta, nil
}

func (c *Controller) PlanApply(input string) (Plan, error) {
	return c.planApply(input, applyOptions{})
}

func (c *Controller) planApply(input string, options applyOptions) (Plan, error) {
	site, err := c.SiteValidate()
	if err != nil {
		return Plan{}, err
	}
	state, err := c.readState()
	if err != nil {
		return Plan{}, err
	}
	plan := Plan{Operation: "apply", Current: state.Current}
	var meta ReleaseMetadata
	if input == "current" {
		if state.Current == "" {
			return Plan{}, fmt.Errorf("no current release")
		}
		meta, err = c.readRelease(state.Current)
		if err != nil {
			return Plan{}, err
		}
		plan.Target, plan.AppVersion, plan.ReleaseKind = state.Current, meta.AppVersion, meta.Kind
		plan.Actions = append(plan.Actions, "reuse materialized release "+state.Current)
	} else if releaseIDPattern.MatchString(input) {
		if existing, existingErr := c.readRelease(input); existingErr == nil {
			meta = existing
			plan.Target, plan.AppVersion, plan.ReleaseKind = input, meta.AppVersion, meta.Kind
			plan.Actions = append(plan.Actions, "reuse materialized release "+input)
		} else if !errors.Is(existingErr, os.ErrNotExist) {
			return Plan{}, existingErr
		} else {
			return Plan{}, fmt.Errorf("release %s is not materialized; pass a bundle directory", input)
		}
	} else {
		bundle, err := filepath.Abs(input)
		if err != nil {
			return Plan{}, err
		}
		manifest, err := LoadManifest(bundle)
		if err != nil {
			return Plan{}, err
		}
		if err := VerifyBundle(bundle, manifest); err != nil {
			return Plan{}, err
		}
		if site.Executor == "local" {
			if err := assertHostPlatform(manifest.Platform); err != nil {
				return Plan{}, err
			}
		}
		identity, _ := manifest.Identity()
		if existing, existingErr := c.readRelease(manifest.ReleaseID); existingErr == nil {
			if existing.Identity != identity {
				return Plan{}, fmt.Errorf("immutable release collision: %s already exists with different content", manifest.ReleaseID)
			}
			meta = existing
			plan.Actions = append(plan.Actions, "reuse identical materialized release "+manifest.ReleaseID)
		} else if !errors.Is(existingErr, os.ErrNotExist) {
			return Plan{}, existingErr
		} else {
			plan.Actions = append(plan.Actions, "materialize immutable release "+manifest.ReleaseID)
		}
		if manifest.Kind == "config" {
			if state.Current != manifest.ExpectedBaseRelease {
				return Plan{}, fmt.Errorf("config release expects base %s but current is %s", manifest.ExpectedBaseRelease, emptyLabel(state.Current))
			}
			if meta.ReleaseID == "" {
				base, err := c.readRelease(state.Current)
				if err != nil {
					return Plan{}, err
				}
				meta = ReleaseMetadata{ReleaseID: manifest.ReleaseID, Kind: "config", AppVersion: base.AppVersion, Platform: base.Platform, SandboxImage: base.SandboxImage, Images: base.Images}
			}
		} else if meta.ReleaseID == "" {
			images, err := c.resolveAppImages(manifest, state.Current)
			if err != nil {
				return Plan{}, err
			}
			meta = ReleaseMetadata{ReleaseID: manifest.ReleaseID, Kind: "app", AppVersion: manifest.ReleaseID, Platform: manifest.Platform, SandboxImage: manifest.SandboxImage, Images: images}
			if _, carriesInfra := artifactByRole(manifest, "infra"); !carriesInfra {
				plan.Actions = append(plan.Actions, "inherit infrastructure image references from current release "+state.Current)
			}
		}
		if manifest.Kind == "app" {
			for _, artifact := range manifest.Artifacts {
				if artifact.Role == "app" || artifact.Role == "sandbox" || artifact.Role == "infra" {
					plan.Actions = append(plan.Actions, "load "+artifact.Role+" image archive "+artifact.File)
				}
			}
		}
		plan.Target, plan.AppVersion, plan.ReleaseKind = manifest.ReleaseID, meta.AppVersion, manifest.Kind
	}
	finalMaintenanceAction := "disable maintenance"
	if options.KeepMaintenance {
		finalMaintenanceAction = "leave maintenance enabled for operator verification"
	}
	plan.Actions = append(plan.Actions, "enable maintenance", "compose reconcile via "+site.Executor, "wait for release readiness", "atomically write state.json", finalMaintenanceAction)
	return plan, nil
}

func emptyLabel(value string) string {
	if value == "" {
		return "<none>"
	}
	return value
}

func (c *Controller) PrintPlan(plan Plan) {
	_, _ = fmt.Fprintf(c.Out, "plan: %s current=%s target=%s app=%s kind=%s\n", plan.Operation, emptyLabel(plan.Current), plan.Target, plan.AppVersion, plan.ReleaseKind)
	for i, action := range plan.Actions {
		_, _ = fmt.Fprintf(c.Out, "  %d. %s\n", i+1, action)
	}
}

func (c *Controller) PlanRollback() (Plan, error) {
	if _, err := c.SiteValidate(); err != nil {
		return Plan{}, err
	}
	state, err := c.readState()
	if err != nil {
		return Plan{}, err
	}
	if state.Previous == "" {
		return Plan{}, fmt.Errorf("no previous release recorded")
	}
	meta, err := c.readRelease(state.Previous)
	if err != nil {
		return Plan{}, err
	}
	return Plan{Operation: "rollback", Current: state.Current, Target: state.Previous, AppVersion: meta.AppVersion, ReleaseKind: meta.Kind, Actions: []string{"enable maintenance", "compose reconcile the previous immutable release", "wait for release readiness", "atomically swap current and previous in state.json", "disable maintenance"}}, nil
}

func (c *Controller) Apply(ctx context.Context, input string) error {
	return c.applyWithOptions(ctx, input, applyOptions{})
}

func (c *Controller) applyWithOptions(ctx context.Context, input string, options applyOptions) error {
	lock, err := acquireMutationLock(c.lockPath())
	if err != nil {
		return err
	}
	defer lock.Close()
	return c.applyLocked(ctx, input, options)
}

func (c *Controller) Rollback(ctx context.Context) error {
	lock, err := acquireMutationLock(c.lockPath())
	if err != nil {
		return err
	}
	defer lock.Close()
	state, err := c.readState()
	if err != nil {
		return err
	}
	if state.Previous == "" {
		return fmt.Errorf("no previous release recorded")
	}
	return c.applyLocked(ctx, state.Previous, applyOptions{})
}

func (c *Controller) applyLocked(ctx context.Context, input string, options applyOptions) error {
	plan, err := c.planApply(input, options)
	if err != nil {
		return err
	}
	c.PrintPlan(plan)
	site, err := LoadSite(c.sitePath())
	if err != nil {
		return err
	}
	state, err := c.readState()
	if err != nil {
		return err
	}
	target := plan.Target
	var bundle string
	if input != "current" && input != target {
		bundle, _ = filepath.Abs(input)
		manifest, err := LoadManifest(bundle)
		if err != nil {
			return err
		}
		if err := c.materializeRelease(bundle, manifest); err != nil {
			return err
		}
		if site.Executor == "local" {
			if err := c.loadReleaseImages(ctx, target, manifest); err != nil {
				return err
			}
		}
	}
	meta, err := c.readRelease(target)
	if err != nil {
		return err
	}
	if err := c.verifyRuntime(ctx, site, meta); err != nil {
		return err
	}
	if site.Executor == "local" {
		if err := c.Runner.Run(ctx, Command{Name: "docker", Args: []string{"run", "--rm", "--runtime=" + site.SandboxRuntime, "--network=none", meta.SandboxImage, "true"}}); err != nil {
			return fmt.Errorf("sandbox runtime smoke failed: %w", err)
		}
	}
	services, err := c.reconcileServices(state.Current, target, meta, input == "current")
	if err != nil {
		return err
	}
	_, _ = fmt.Fprintf(c.Out, "reconcile services: %s\n", strings.Join(services, ", "))
	if err := c.setMaintenance(true); err != nil {
		return err
	}
	if err := c.reconcile(ctx, site, target, meta, services); err != nil {
		recovered := false
		if state.Current != "" && state.Current != target {
			_, _ = fmt.Fprintf(c.Err, "apply failed; attempting last-known-good release %s\n", state.Current)
			if previous, previousErr := c.readRelease(state.Current); previousErr == nil {
				if recoveryServices, serviceErr := c.reconcileServices(target, state.Current, previous, false); serviceErr == nil {
					recovered = c.reconcile(ctx, site, state.Current, previous, recoveryServices) == nil
				}
			}
		}
		if recovered {
			if !options.KeepMaintenance {
				if maintenanceErr := c.disableMaintenanceAfterApply(ctx, site); maintenanceErr != nil {
					return fmt.Errorf("apply failed and restored last-known-good release %s, but maintenance could not be disabled: %v (original apply error: %w)", state.Current, maintenanceErr, err)
				}
				return fmt.Errorf("apply failed; restored last-known-good release %s: %w", state.Current, err)
			}
			return fmt.Errorf("apply failed; restored last-known-good release %s; maintenance remains enabled: %w", state.Current, err)
		}
		return fmt.Errorf("apply failed; maintenance remains enabled: %w", err)
	}
	previous := state.Current
	if target == state.Current {
		previous = state.Previous
	}
	if err := c.writeState(target, previous, state); err != nil {
		return fmt.Errorf("release is healthy but state write failed; maintenance remains enabled: %w", err)
	}
	if !options.KeepMaintenance {
		if err := c.disableMaintenanceAfterApply(ctx, site); err != nil {
			return fmt.Errorf("release is healthy and recorded but maintenance could not be disabled: %w", err)
		}
	} else {
		_, _ = fmt.Fprintln(c.Out, "maintenance remains enabled (--keep-maintenance)")
	}
	_, _ = fmt.Fprintf(c.Out, "applied release %s (app=%s)\n", target, meta.AppVersion)
	return nil
}

func (c *Controller) materializeRelease(bundle string, manifest Manifest) error {
	identity, err := manifest.Identity()
	if err != nil {
		return err
	}
	target := c.releaseDir(manifest.ReleaseID)
	if current, err := c.readRelease(manifest.ReleaseID); err == nil {
		if current.Identity != identity {
			return fmt.Errorf("immutable release collision for %s", manifest.ReleaseID)
		}
		return nil
	} else if !errors.Is(err, os.ErrNotExist) {
		return err
	}
	var appImages []string
	if manifest.Kind == "app" {
		state, err := c.readState()
		if err != nil {
			return err
		}
		appImages, err = c.resolveAppImages(manifest, state.Current)
		if err != nil {
			return err
		}
	}
	if err := os.MkdirAll(c.releasesDir(), 0o755); err != nil {
		return err
	}
	tmp, err := os.MkdirTemp(c.releasesDir(), "."+manifest.ReleaseID+".tmp-")
	if err != nil {
		return err
	}
	ok := false
	defer func() {
		if !ok {
			_ = os.RemoveAll(tmp)
		}
	}()
	meta := ReleaseMetadata{Schema: ReleaseSchema, ReleaseID: manifest.ReleaseID, Kind: manifest.Kind, Identity: identity, MaterializedAt: timestamp()}
	artifactsDir := filepath.Join(tmp, "artifacts")
	if err := os.MkdirAll(artifactsDir, 0o755); err != nil {
		return err
	}
	for _, artifact := range manifest.Artifacts {
		staged := filepath.Join(artifactsDir, artifact.File)
		if err := copyFile(filepath.Join(bundle, artifact.File), staged, 0o644); err != nil {
			return err
		}
		sha, err := fileSHA256(staged)
		if err != nil {
			return err
		}
		if sha != artifact.SHA256 {
			return fmt.Errorf("bundle changed while materializing %s", artifact.File)
		}
	}
	if manifest.Kind == "app" {
		for _, role := range []string{"deploy", "config"} {
			artifact, _ := artifactByRole(manifest, role)
			if err := extractTarGz(filepath.Join(artifactsDir, artifact.File), tmp); err != nil {
				return fmt.Errorf("extract %s: %w", artifact.File, err)
			}
		}
		meta.AppVersion, meta.Platform, meta.SandboxImage, meta.Images = manifest.ReleaseID, manifest.Platform, manifest.SandboxImage, appImages
	} else {
		base, err := c.readRelease(manifest.ExpectedBaseRelease)
		if err != nil {
			return err
		}
		if err := copyTree(filepath.Join(c.releaseDir(base.ReleaseID), "deploy"), filepath.Join(tmp, "deploy")); err != nil {
			return err
		}
		artifact, _ := artifactByRole(manifest, "config")
		if err := extractTarGz(filepath.Join(artifactsDir, artifact.File), tmp); err != nil {
			return err
		}
		meta.AppVersion, meta.Platform, meta.BaseRelease, meta.SandboxImage, meta.Images = base.AppVersion, base.Platform, base.ReleaseID, base.SandboxImage, append([]string(nil), base.Images...)
	}
	if _, err := os.Stat(filepath.Join(tmp, "deploy", "compose.base.yml")); err != nil {
		return fmt.Errorf("release deploy unit missing compose.base.yml")
	}
	if info, err := os.Stat(filepath.Join(tmp, "config")); err != nil || !info.IsDir() {
		return fmt.Errorf("release config unit missing")
	}
	if err := writeJSONAtomic(filepath.Join(tmp, ".af-release.json"), meta, 0o644); err != nil {
		return err
	}
	if err := writeJSONAtomic(filepath.Join(tmp, "manifest.json"), manifest, 0o644); err != nil {
		return err
	}
	if err := os.Rename(tmp, target); err != nil {
		return err
	}
	if err := syncDir(c.releasesDir()); err != nil {
		return err
	}
	ok = true
	return nil
}

func (c *Controller) resolveAppImages(manifest Manifest, currentRelease string) ([]string, error) {
	if _, carriesInfra := artifactByRole(manifest, "infra"); carriesInfra {
		return append([]string(nil), manifest.Images...), nil
	}
	images := []string{"artifactflow:" + manifest.ReleaseID, "artifactflow-frontend:" + manifest.ReleaseID, manifest.SandboxImage}
	if currentRelease == "" {
		return nil, fmt.Errorf("app-only release %s requires an existing current release; use a --with-infra bundle for the first apply", manifest.ReleaseID)
	}
	current, err := c.readRelease(currentRelease)
	if err != nil {
		return nil, fmt.Errorf("read current release for app-only infrastructure inheritance: %w", err)
	}
	if current.Platform != manifest.Platform {
		return nil, fmt.Errorf("app-only release platform %s does not match current release platform %s", manifest.Platform, current.Platform)
	}
	caddy, postgres, redis, err := infraImageRefs(current.Images)
	if err != nil {
		return nil, fmt.Errorf("current release cannot provide app-only infrastructure images: %w", err)
	}
	return append(images, caddy, postgres, redis), nil
}

func (c *Controller) loadReleaseImages(ctx context.Context, release string, manifest Manifest) error {
	for _, artifact := range manifest.Artifacts {
		if artifact.Role != "app" && artifact.Role != "sandbox" && artifact.Role != "infra" {
			continue
		}
		if err := c.Runner.Run(ctx, Command{Name: "docker", Args: []string{"load", "-i", filepath.Join(c.releaseDir(release), "artifacts", artifact.File)}}); err != nil {
			return err
		}
	}
	return nil
}

func (c *Controller) verifyRuntime(ctx context.Context, site Site, meta ReleaseMetadata) error {
	if site.Executor == "ansible" {
		return c.verifyAnsibleControl(ctx, site)
	}
	if _, err := c.Runner.Output(ctx, Command{Name: "docker", Args: []string{"compose", "version"}}); err != nil {
		return fmt.Errorf("Docker Compose v2 is required: %w", err)
	}
	if _, err := c.Runner.Output(ctx, Command{Name: "docker", Args: []string{"info"}}); err != nil {
		return fmt.Errorf("Docker daemon is unreachable: %w", err)
	}
	if site.SandboxRuntime == "runsc" {
		if _, err := c.Runner.Output(ctx, Command{Name: "runsc", Args: []string{"--version"}}); err != nil {
			return fmt.Errorf("site requires runsc but it is unavailable: %w", err)
		}
		info, err := c.Runner.Output(ctx, Command{Name: "docker", Args: []string{"info", "--format", "{{json .Runtimes}}"}})
		if err != nil || !strings.Contains(info, "runsc") {
			return fmt.Errorf("site requires Docker runtime runsc to be registered")
		}
	} else {
		_, _ = fmt.Fprintln(c.Err, "warning: sandbox_runtime=runc is explicit reduced isolation; production support requires runsc")
	}
	if _, err := c.Runner.Output(ctx, Command{Name: "findmnt", Args: []string{"-rn", site.ScratchRoot}}); err != nil {
		return fmt.Errorf("sandbox scratch root is not a mounted filesystem: %s", site.ScratchRoot)
	}
	for _, image := range meta.Images {
		if site.Infra == "external" && (strings.HasPrefix(image, "artifactflow-postgres:") || strings.HasPrefix(image, "artifactflow-redis:")) {
			continue
		}
		if _, err := c.Runner.Output(ctx, Command{Name: "docker", Args: []string{"image", "inspect", image}}); err != nil {
			return fmt.Errorf("required immutable image is not loaded: %s", image)
		}
	}
	if site.TLS == "static" {
		for _, name := range []string{"server.crt", "server.key"} {
			info, err := os.Stat(filepath.Join(c.controlDir(), "certs", name))
			if err != nil {
				return fmt.Errorf("static TLS requires control/certs/%s; no self-signed fallback is generated", name)
			}
			if name == "server.key" && info.Mode().Perm()&0o077 != 0 {
				return fmt.Errorf("control/certs/server.key must not be group/world readable; chmod 600 it")
			}
		}
	}
	return nil
}

func (c *Controller) verifyAnsibleControl(ctx context.Context, site Site) error {
	if _, err := os.Stat(site.Inventory); err != nil {
		return fmt.Errorf("inventory: %w", err)
	}
	if _, err := c.Runner.Output(ctx, Command{Name: "docker", Args: []string{"image", "inspect", site.AnsibleEEImage}}); err != nil {
		return fmt.Errorf("pinned Ansible execution environment is not loaded: %s", site.AnsibleEEImage)
	}
	return nil
}

func (c *Controller) composeCommand(site Site, release string, meta ReleaseMetadata, args ...string) Command {
	releaseRoot := c.releaseDir(release)
	composeArgs := []string{"compose", "--project-name", "artifactflow", "--env-file", c.envPath(), "-f", filepath.Join(releaseRoot, "deploy", "compose.base.yml")}
	if site.TLS == "acme" {
		composeArgs = append(composeArgs, "-f", filepath.Join(releaseRoot, "deploy", "compose.tls-acme.yml"))
	}
	composeArgs = append(composeArgs, "-f", filepath.Join(releaseRoot, "deploy", "compose.sandbox.yml"))
	composeArgs = append(composeArgs, args...)
	caddyImage, postgresImage, redisImage, _ := infraImageRefs(meta.Images)
	env := []string{"AF_VERSION=" + meta.AppVersion, "AF_CADDY_IMAGE=" + caddyImage, "AF_POSTGRES_IMAGE=" + postgresImage, "AF_REDIS_IMAGE=" + redisImage, "AF_RUNTIME_DEPLOY_DIR=" + c.controlDir(), "ARTIFACTFLOW_SANDBOX_RUNTIME=" + site.SandboxRuntime, "ARTIFACTFLOW_SANDBOX_SCRATCH_ROOT=" + site.ScratchRoot}
	return Command{Name: "docker", Args: composeArgs, Dir: filepath.Join(releaseRoot, "deploy"), Env: env}
}

func (c *Controller) reconcileServices(currentRelease, targetRelease string, target ReleaseMetadata, refreshSite bool) ([]string, error) {
	services := []string{"release", "backend"}
	if currentRelease == "" {
		return append(services, "frontend", "caddy"), nil
	}

	current, err := c.readRelease(currentRelease)
	if err != nil {
		return nil, err
	}
	if current.AppVersion != target.AppVersion {
		services = append(services, "frontend")
	}

	restartCaddy := refreshSite
	if !restartCaddy {
		currentCaddy, _, _, err := infraImageRefs(current.Images)
		if err != nil {
			return nil, err
		}
		targetCaddy, _, _, err := infraImageRefs(target.Images)
		if err != nil {
			return nil, err
		}
		restartCaddy = currentCaddy != targetCaddy
	}
	if !restartCaddy {
		currentDeploy, err := treeDigest(filepath.Join(c.releaseDir(currentRelease), "deploy"))
		if err != nil {
			return nil, err
		}
		targetDeploy, err := treeDigest(filepath.Join(c.releaseDir(targetRelease), "deploy"))
		if err != nil {
			return nil, err
		}
		restartCaddy = currentDeploy != targetDeploy
	}
	if restartCaddy {
		services = append(services, "caddy")
	}
	return services, nil
}

func (c *Controller) reconcile(ctx context.Context, site Site, release string, meta ReleaseMetadata, services []string) error {
	// Existing v2 sites predate outbound CA support. Create the optional stable
	// bind source only in the mutating Apply path; site validation and plan stay
	// read-only. Ansible consumes the same controller-side directory via /work.
	if err := os.MkdirAll(c.trustAnchorDir(), 0o755); err != nil {
		return fmt.Errorf("create outbound CA directory: %w", err)
	}
	if err := os.MkdirAll(c.authConfigDir(), 0o755); err != nil {
		return fmt.Errorf("create authentication config directory: %w", err)
	}
	if site.Executor == "ansible" {
		return c.reconcileAnsible(ctx, site, release, meta)
	}
	args := []string{}
	if site.Infra == "bundled" {
		if err := c.Runner.Run(ctx, c.composeCommand(site, release, meta, "--profile", "infra", "up", "-d", "postgres", "redis")); err != nil {
			return err
		}
		args = append(args, "--profile", "infra")
	}
	args = append(args, "up", "-d", "--remove-orphans", "--force-recreate", "--scale", "backend="+strconv.Itoa(site.BackendReplicas))
	args = append(args, services...)
	if err := c.Runner.Run(ctx, c.composeCommand(site, release, meta, args...)); err != nil {
		return err
	}
	deadline := time.Now().Add(time.Duration(site.ReadyTimeoutSeconds) * time.Second)
	last := error(context.DeadlineExceeded)
	probe := func(command func(timeoutSeconds int) Command) error {
		remaining := time.Until(deadline)
		if remaining <= 0 {
			return context.DeadlineExceeded
		}
		probeSeconds := int((remaining + time.Second - 1) / time.Second)
		if probeSeconds > 8 {
			probeSeconds = 8
		}
		probeCtx, cancel := context.WithTimeout(ctx, remaining)
		defer cancel()
		_, err := c.Runner.Output(probeCtx, command(probeSeconds))
		return err
	}
	waitForFrontend := slices.Contains(services, "frontend")
	for {
		if time.Until(deadline) <= 0 {
			break
		}
		last = probe(func(probeSeconds int) Command {
			return c.composeCommand(site, release, meta, "exec", "-T", "caddy", "wget", "-q", "--spider", "-T", strconv.Itoa(probeSeconds), "http://localhost:2021/health/ready")
		})
		if last != nil {
			last = fmt.Errorf("backend readiness through Caddy: %w", last)
		} else if waitForFrontend {
			last = probe(func(probeSeconds int) Command {
				return c.composeCommand(site, release, meta, "exec", "-T", "frontend", "node", "-e", frontendReadinessScript, strconv.Itoa(probeSeconds))
			})
			if last != nil {
				last = fmt.Errorf("frontend readiness: %w", last)
			}
		}
		if last == nil {
			return nil
		}
		if err := ctx.Err(); err != nil {
			return err
		}
		remaining := time.Until(deadline)
		if remaining <= 0 {
			break
		}
		delay := min(2*time.Second, remaining)
		timer := time.NewTimer(delay)
		select {
		case <-ctx.Done():
			timer.Stop()
			return ctx.Err()
		case <-timer.C:
		}
	}
	return fmt.Errorf("release readiness timed out after %ds: %w", site.ReadyTimeoutSeconds, last)
}
