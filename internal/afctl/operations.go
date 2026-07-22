package afctl

import (
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"
)

func (c *Controller) setMaintenance(enabled bool) error {
	dir := filepath.Join(c.controlDir(), "maintenance")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return err
	}
	flag := filepath.Join(dir, "MAINTENANCE_ON")
	if enabled {
		return os.WriteFile(flag, []byte{}, 0o644)
	}
	for _, path := range []string{flag, filepath.Join(dir, "note.txt")} {
		if err := os.Remove(path); err != nil && !errors.Is(err, os.ErrNotExist) {
			return err
		}
	}
	return nil
}

func (c *Controller) disableMaintenanceAfterApply(ctx context.Context, site Site) error {
	if site.Executor == "ansible" {
		if err := c.maintenanceAnsible(ctx, site, "off", ""); err != nil {
			return err
		}
	}
	return c.setMaintenance(false)
}

func (c *Controller) Maintenance(ctx context.Context, action, note string) error {
	if action != "on" && action != "off" && action != "status" {
		return fmt.Errorf("maintenance action must be on, off, or status")
	}
	if note != "" && action != "on" {
		return fmt.Errorf("maintenance note is only valid with on")
	}
	if action != "status" {
		lock, err := acquireMutationLock(c.lockPath())
		if err != nil {
			return err
		}
		defer lock.Close()
	}
	site, err := c.SiteValidate()
	if err != nil {
		return err
	}
	flag := filepath.Join(c.controlDir(), "maintenance", "MAINTENANCE_ON")
	notePath := filepath.Join(c.controlDir(), "maintenance", "note.txt")
	if action == "status" {
		if site.Executor == "ansible" {
			return c.maintenanceAnsible(ctx, site, action, note)
		}
		if _, err := os.Stat(flag); err == nil {
			_, _ = fmt.Fprintln(c.Out, "maintenance=on")
			return nil
		} else if errors.Is(err, os.ErrNotExist) {
			_, _ = fmt.Fprintln(c.Out, "maintenance=off")
			return nil
		} else {
			return err
		}
	}
	if site.Executor == "ansible" {
		if err := c.maintenanceAnsible(ctx, site, action, note); err != nil {
			return err
		}
	}
	if action == "on" {
		if err := os.MkdirAll(filepath.Dir(flag), 0o755); err != nil {
			return err
		}
		if err := os.WriteFile(notePath, []byte(note+"\n"), 0o644); err != nil {
			return err
		}
		if err := os.WriteFile(flag, []byte{}, 0o644); err != nil {
			return err
		}
	} else {
		if err := c.setMaintenance(false); err != nil {
			return err
		}
	}
	_, _ = fmt.Fprintf(c.Out, "maintenance=%s\n", action)
	return nil
}

func (c *Controller) Status(ctx context.Context) error {
	site, err := c.SiteValidate()
	if err != nil {
		return err
	}
	state, err := c.readState()
	if err != nil {
		return err
	}
	if state.Current == "" {
		return fmt.Errorf("no release has been applied")
	}
	meta, err := c.readRelease(state.Current)
	if err != nil {
		return err
	}
	_, _ = fmt.Fprintf(c.Out, "current=%s previous=%s generation=%d updated_at=%s\n", state.Current, emptyLabel(state.Previous), state.Generation, state.UpdatedAt)
	if site.Executor == "ansible" {
		return c.statusAnsible(ctx, site, state.Current, meta)
	}
	if err := c.Runner.Run(ctx, c.composeCommand(site, state.Current, meta, "ps")); err != nil {
		return err
	}
	if _, err := c.Runner.Output(ctx, c.composeCommand(site, state.Current, meta, "exec", "-T", "caddy", "wget", "-q", "--spider", "-T", "8", "http://localhost:2021/health/ready")); err != nil {
		return fmt.Errorf("load balancer /health/ready is not green")
	}
	_, _ = fmt.Fprintln(c.Out, "load balancer ready")
	return nil
}

func (c *Controller) Doctor(ctx context.Context) error {
	site, err := c.SiteValidate()
	if err != nil {
		return err
	}
	state, err := c.readState()
	if err != nil {
		return err
	}
	meta := ReleaseMetadata{}
	if state.Current != "" {
		meta, err = c.readRelease(state.Current)
		if err != nil {
			return err
		}
	}
	if site.Executor == "local" && meta.ReleaseID == "" {
		meta.Images = nil
	}
	if err := c.verifyRuntime(ctx, site, meta); err != nil {
		return err
	}
	if site.Executor == "ansible" {
		_, _ = fmt.Fprintln(c.Out, "doctor: inventory and pinned Ansible EE are present; remote host capabilities are verified at apply")
		return nil
	}
	_, _ = fmt.Fprintln(c.Out, "doctor: all required capabilities are present")
	return nil
}
