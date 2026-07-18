package afctl

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

type ansibleVars struct {
	InstallRoot     string            `json:"af_install_root"`
	ReleaseSource   string            `json:"af_release_source"`
	ReleaseID       string            `json:"af_release_id"`
	ReleaseIdentity string            `json:"af_release_identity"`
	AppVersion      string            `json:"af_app_version"`
	Platform        string            `json:"af_platform"`
	Infra           string            `json:"af_infra"`
	TLS             string            `json:"af_tls"`
	SandboxRuntime  string            `json:"af_sandbox_runtime"`
	ScratchRoot     string            `json:"af_scratch_root"`
	AppImages       []string          `json:"af_app_images"`
	CaddyImages     []string          `json:"af_caddy_images"`
	DataImages      []string          `json:"af_data_images"`
	CaddyImage      string            `json:"af_caddy_image"`
	PostgresImage   string            `json:"af_postgres_image"`
	RedisImage      string            `json:"af_redis_image"`
	SandboxImage    string            `json:"af_sandbox_image"`
	Artifacts       map[string]string `json:"af_artifacts"`
}

func (c *Controller) reconcileAnsible(ctx context.Context, site Site, release string, meta ReleaseMetadata) error {
	return c.runAnsible(ctx, site, release, meta, "apply.yml")
}

func (c *Controller) statusAnsible(ctx context.Context, site Site, release string, meta ReleaseMetadata) error {
	return c.runAnsible(ctx, site, release, meta, "status.yml")
}

func (c *Controller) runAnsible(ctx context.Context, site Site, release string, meta ReleaseMetadata, playbook string) error {
	relInventory, err := filepath.Rel(c.Root, site.Inventory)
	if err != nil || relInventory == ".." || strings.HasPrefix(relInventory, ".."+string(filepath.Separator)) {
		return fmt.Errorf("ansible inventory must be inside install root %s", c.Root)
	}
	appImages, caddyImages, dataImages := classifyImages(meta)
	caddyImage, postgresImage, redisImage, err := infraImageRefs(meta.Images)
	if err != nil {
		return err
	}
	artifacts := map[string]string{}
	var manifest Manifest
	if err := readStrictJSON(filepath.Join(c.releaseDir(release), "manifest.json"), &manifest); err != nil {
		return err
	}
	if err := manifest.Validate(); err != nil {
		return err
	}
	for _, artifact := range manifest.Artifacts {
		if artifact.Role != "config" && artifact.Role != "deploy" {
			artifacts[artifact.Role] = "/work/.artifactflow/releases/" + release + "/artifacts/" + artifact.File
		}
	}
	vars := ansibleVars{InstallRoot: c.Root, ReleaseSource: "/work/.artifactflow/releases/" + release, ReleaseID: release, ReleaseIdentity: meta.Identity, AppVersion: meta.AppVersion, Platform: meta.Platform, Infra: site.Infra, TLS: site.TLS, SandboxRuntime: site.SandboxRuntime, ScratchRoot: site.ScratchRoot, AppImages: appImages, CaddyImages: caddyImages, DataImages: dataImages, CaddyImage: caddyImage, PostgresImage: postgresImage, RedisImage: redisImage, SandboxImage: meta.SandboxImage, Artifacts: artifacts}
	data, err := json.MarshalIndent(vars, "", "  ")
	if err != nil {
		return err
	}
	_, varsContainerPath, cleanup, err := c.writeAnsibleVars(data)
	if err != nil {
		return err
	}
	defer cleanup()
	home, _ := os.UserHomeDir()
	args := []string{"run", "--rm", "-v", c.Root + ":/work", "-w", "/work"}
	if info, statErr := os.Stat(filepath.Join(home, ".ssh")); home != "" && statErr == nil && info.IsDir() {
		args = append(args, "-v", filepath.Join(home, ".ssh")+":/root/.ssh:ro")
	}
	args = append(args, site.AnsibleEEImage, "ansible-playbook", "-i", "/work/"+filepath.ToSlash(relInventory), "/work/.artifactflow/releases/"+release+"/deploy/ansible/"+playbook, "-e", "@"+varsContainerPath)
	return c.Runner.Run(ctx, Command{Name: "docker", Args: args})
}

func (c *Controller) maintenanceAnsible(ctx context.Context, site Site, action, note string) error {
	relInventory, err := filepath.Rel(c.Root, site.Inventory)
	if err != nil || relInventory == ".." || strings.HasPrefix(relInventory, ".."+string(filepath.Separator)) {
		return fmt.Errorf("ansible inventory must be inside install root %s", c.Root)
	}
	state, err := c.readState()
	if err != nil {
		return err
	}
	if state.Current == "" {
		return fmt.Errorf("no current release")
	}
	data, err := json.MarshalIndent(map[string]string{"af_install_root": c.Root, "af_maintenance_action": action, "af_maintenance_note": note}, "", "  ")
	if err != nil {
		return err
	}
	_, varsContainerPath, cleanup, err := c.writeAnsibleVars(data)
	if err != nil {
		return err
	}
	defer cleanup()
	home, _ := os.UserHomeDir()
	args := []string{"run", "--rm", "-v", c.Root + ":/work", "-w", "/work"}
	if info, statErr := os.Stat(filepath.Join(home, ".ssh")); home != "" && statErr == nil && info.IsDir() {
		args = append(args, "-v", filepath.Join(home, ".ssh")+":/root/.ssh:ro")
	}
	args = append(args, site.AnsibleEEImage, "ansible-playbook", "-i", "/work/"+filepath.ToSlash(relInventory), "/work/.artifactflow/releases/"+state.Current+"/deploy/ansible/maintenance.yml", "-e", "@"+varsContainerPath)
	return c.Runner.Run(ctx, Command{Name: "docker", Args: args})
}

func (c *Controller) writeAnsibleVars(data []byte) (hostPath, containerPath string, cleanup func(), err error) {
	if err := os.MkdirAll(c.runtimeDir(), 0o755); err != nil {
		return "", "", nil, err
	}
	f, err := os.CreateTemp(c.runtimeDir(), "ansible-vars-*.json")
	if err != nil {
		return "", "", nil, err
	}
	hostPath = f.Name()
	cleanup = func() { _ = os.Remove(hostPath) }
	fail := func(cause error) (string, string, func(), error) {
		_ = f.Close()
		cleanup()
		return "", "", nil, cause
	}
	if err := f.Chmod(0o600); err != nil {
		return fail(err)
	}
	if _, err := f.Write(append(data, '\n')); err != nil {
		return fail(err)
	}
	if err := f.Sync(); err != nil {
		return fail(err)
	}
	if err := f.Close(); err != nil {
		cleanup()
		return "", "", nil, err
	}
	rel, err := filepath.Rel(c.Root, hostPath)
	if err != nil || rel == ".." || strings.HasPrefix(rel, ".."+string(filepath.Separator)) {
		cleanup()
		return "", "", nil, fmt.Errorf("ansible vars must be inside install root")
	}
	return hostPath, "/work/" + filepath.ToSlash(rel), cleanup, nil
}

func classifyImages(meta ReleaseMetadata) (app, caddy, data []string) {
	for _, image := range meta.Images {
		if image == meta.SandboxImage {
			continue
		}
		if strings.HasPrefix(image, "artifactflow:") || strings.HasPrefix(image, "artifactflow-frontend:") {
			app = append(app, image)
		} else if strings.HasPrefix(image, "artifactflow-caddy:") {
			caddy = append(caddy, image)
		} else if strings.HasPrefix(image, "artifactflow-postgres:") || strings.HasPrefix(image, "artifactflow-redis:") {
			data = append(data, image)
		}
	}
	return app, caddy, data
}
