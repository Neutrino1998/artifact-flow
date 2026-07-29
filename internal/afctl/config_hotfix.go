package afctl

import (
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"time"
)

const checkoutMetadataName = ".af-checkout.json"

func (c *Controller) ConfigCheckout(destination string) error {
	state, err := c.readState()
	if err != nil {
		return err
	}
	if state.Current == "" {
		return fmt.Errorf("no current release")
	}
	if _, err := os.Stat(destination); err == nil {
		return fmt.Errorf("destination already exists: %s", destination)
	} else if !errors.Is(err, os.ErrNotExist) {
		return err
	}
	source := filepath.Join(c.releaseDir(state.Current), "config")
	digest, err := treeDigest(source)
	if err != nil {
		return err
	}
	if err := copyTree(source, filepath.Join(destination, "config")); err != nil {
		return err
	}
	meta := CheckoutMetadata{Schema: 1, BaseRelease: state.Current, ConfigDigest: digest, CreatedAt: timestamp()}
	if err := writeJSONAtomic(filepath.Join(destination, checkoutMetadataName), meta, 0o600); err != nil {
		_ = os.RemoveAll(destination)
		return err
	}
	_, _ = fmt.Fprintf(c.Out, "checked out config from %s to %s\n", state.Current, destination)
	return nil
}

func (c *Controller) ConfigApply(ctx context.Context, workspace, id string) error {
	lock, err := acquireMutationLock(c.lockPath())
	if err != nil {
		return err
	}
	defer lock.Close()
	var checkout CheckoutMetadata
	if err := readStrictJSON(filepath.Join(workspace, checkoutMetadataName), &checkout); err != nil {
		return err
	}
	if checkout.Schema != 1 || !releaseIDPattern.MatchString(checkout.BaseRelease) || !shaPattern.MatchString(checkout.ConfigDigest) {
		return fmt.Errorf("invalid checkout metadata")
	}
	if _, err := time.Parse(time.RFC3339, checkout.CreatedAt); err != nil {
		return fmt.Errorf("invalid checkout metadata timestamp")
	}
	state, err := c.readState()
	if err != nil {
		return err
	}
	if state.Current != checkout.BaseRelease {
		return fmt.Errorf("checkout expects base %s but current is %s", checkout.BaseRelease, emptyLabel(state.Current))
	}
	activeDigest, err := treeDigest(filepath.Join(c.releaseDir(state.Current), "config"))
	if err != nil {
		return err
	}
	if activeDigest != checkout.ConfigDigest {
		return fmt.Errorf("active config changed in place after checkout; make a fresh checkout")
	}
	configDir := filepath.Join(workspace, "config")
	if info, err := os.Stat(configDir); err != nil || !info.IsDir() {
		return fmt.Errorf("workspace has no config directory")
	}
	if id == "" {
		id = "hotfix-config-" + time.Now().UTC().Format("20060102-150405")
	}
	if !releaseIDPattern.MatchString(id) {
		return fmt.Errorf("invalid hotfix id %q", id)
	}
	bundleParent := filepath.Join(c.runtimeDir(), "hotfix-bundles")
	bundle := filepath.Join(bundleParent, id)
	if _, err := os.Stat(bundle); err == nil {
		return fmt.Errorf("hotfix bundle already exists: %s", bundle)
	} else if !errors.Is(err, os.ErrNotExist) {
		return err
	}
	if err := os.MkdirAll(bundleParent, 0o700); err != nil {
		return err
	}
	staging, err := os.MkdirTemp(bundleParent, "."+id+".tmp-")
	if err != nil {
		return err
	}
	defer os.RemoveAll(staging)
	archiveName := "artifactflow-config-" + id + ".tar.gz"
	archive := filepath.Join(staging, archiveName)
	if err := createConfigTar(configDir, archive); err != nil {
		return err
	}
	sha, err := fileSHA256(archive)
	if err != nil {
		return err
	}
	base, err := c.readRelease(checkout.BaseRelease)
	if err != nil {
		return err
	}
	manifest := Manifest{Schema: ManifestSchema, ReleaseID: id, Kind: "config", Platform: base.Platform, CreatedAt: timestamp(), Source: "production config checkout", ExpectedBaseRelease: checkout.BaseRelease, Artifacts: []Artifact{{Role: "config", File: archiveName, SHA256: sha}}}
	if err := manifest.Validate(); err != nil {
		return err
	}
	if err := writeJSONAtomic(filepath.Join(staging, "manifest.json"), manifest, 0o600); err != nil {
		return err
	}
	if err := os.Rename(staging, bundle); err != nil {
		return err
	}
	if err := syncDir(bundleParent); err != nil {
		return err
	}
	_, _ = fmt.Fprintf(c.Out, "retained hotfix bundle: %s\n", bundle)
	return c.applyLocked(ctx, bundle, applyOptions{})
}
