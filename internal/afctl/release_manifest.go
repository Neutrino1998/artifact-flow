package afctl

import (
	"fmt"
	"path/filepath"
	"sort"
)

type ManifestOptions struct {
	Bundle, ReleaseID, Kind, Platform, Source, ExpectedBase, SandboxImage string
	Images                                                                []string
	Artifacts                                                             map[string]string
}

func WriteManifest(options ManifestOptions) error {
	if options.Bundle == "" {
		return fmt.Errorf("bundle is required")
	}
	manifest := Manifest{Schema: ManifestSchema, ReleaseID: options.ReleaseID, Kind: options.Kind, Platform: options.Platform, CreatedAt: timestamp(), Source: options.Source, ExpectedBaseRelease: options.ExpectedBase, SandboxImage: options.SandboxImage, Images: append([]string(nil), options.Images...)}
	roles := make([]string, 0, len(options.Artifacts))
	for role := range options.Artifacts {
		roles = append(roles, role)
	}
	sort.Strings(roles)
	for _, role := range roles {
		name := options.Artifacts[role]
		if filepath.Base(name) != name {
			return fmt.Errorf("artifact %s path must be a bundle basename", name)
		}
		sha, err := fileSHA256(filepath.Join(options.Bundle, name))
		if err != nil {
			return err
		}
		manifest.Artifacts = append(manifest.Artifacts, Artifact{Role: role, File: name, SHA256: sha})
	}
	if err := manifest.Validate(); err != nil {
		return err
	}
	return writeJSONAtomic(filepath.Join(options.Bundle, "manifest.json"), manifest, 0o644)
}
