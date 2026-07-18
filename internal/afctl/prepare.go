package afctl

import (
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"strings"
)

func (c *Controller) Prepare(ctx context.Context, gvisorPackage string) error {
	lock, err := acquireMutationLock(c.lockPath())
	if err != nil {
		return err
	}
	defer lock.Close()
	site, err := c.SiteValidate()
	if err != nil {
		return err
	}
	if site.Executor == "ansible" {
		return fmt.Errorf("ansible apply only verifies host capabilities; provision runsc and the scratch mount on each app host before apply")
	}
	if runtime.GOOS != "linux" {
		return fmt.Errorf("host preparation is supported only on Linux targets")
	}
	if os.Geteuid() != 0 {
		return fmt.Errorf("prepare mutates host runtime/mount state and must run as root")
	}
	if site.SandboxRuntime == "runsc" {
		if _, err := c.Runner.Output(ctx, Command{Name: "runsc", Args: []string{"--version"}}); err != nil {
			if gvisorPackage == "" {
				return fmt.Errorf("runsc is missing; pass --gvisor-package <offline-package>")
			}
			if err := c.installGVisor(ctx, gvisorPackage); err != nil {
				return err
			}
		}
	}
	if _, err := c.Runner.Output(ctx, Command{Name: "findmnt", Args: []string{"-rn", site.ScratchRoot}}); err == nil {
		_, _ = fmt.Fprintf(c.Out, "scratch root already mounted: %s\n", site.ScratchRoot)
		return nil
	}
	pool := filepath.Join(filepath.Dir(site.ScratchRoot), "sandbox-pool.img")
	if _, err := os.Stat(pool); err == nil {
		return fmt.Errorf("%s exists but %s is not mounted; inspect manually", pool, site.ScratchRoot)
	} else if !errors.Is(err, os.ErrNotExist) {
		return err
	}
	if err := os.MkdirAll(site.ScratchRoot, 0o755); err != nil {
		return err
	}
	entries, err := os.ReadDir(site.ScratchRoot)
	if err != nil {
		return err
	}
	if len(entries) != 0 {
		return fmt.Errorf("scratch mountpoint %s is not empty; refusing to hide existing data", site.ScratchRoot)
	}
	if err := c.Runner.Run(ctx, Command{Name: "fallocate", Args: []string{"-l", site.ScratchSize, pool}}); err != nil {
		return err
	}
	if err := c.Runner.Run(ctx, Command{Name: "mkfs.ext4", Args: []string{"-m", "0", "-F", pool}}); err != nil {
		return err
	}
	fstabLine := fmt.Sprintf("%s %s ext4 loop,nosuid,nodev 0 0\n", pool, site.ScratchRoot)
	fstab, err := os.ReadFile("/etc/fstab")
	if err != nil {
		return err
	}
	if !strings.Contains(string(fstab), strings.TrimSpace(fstabLine)) {
		f, err := os.OpenFile("/etc/fstab", os.O_APPEND|os.O_WRONLY, 0)
		if err != nil {
			return err
		}
		if _, err := f.WriteString(fstabLine); err != nil {
			_ = f.Close()
			return err
		}
		if err := f.Close(); err != nil {
			return err
		}
	}
	if err := c.Runner.Run(ctx, Command{Name: "mount", Args: []string{site.ScratchRoot}}); err != nil {
		return err
	}
	_, _ = fmt.Fprintf(c.Out, "prepared sandbox scratch filesystem %s at %s\n", site.ScratchSize, site.ScratchRoot)
	return nil
}

func (c *Controller) installGVisor(ctx context.Context, archive string) error {
	if err := verifyChecksumSidecar(archive); err != nil {
		return fmt.Errorf("gVisor package checksum: %w", err)
	}
	tmp, err := os.MkdirTemp("", "afctl-gvisor-")
	if err != nil {
		return err
	}
	defer os.RemoveAll(tmp)
	if err := extractTarGz(archive, tmp); err != nil {
		return err
	}
	var installer string
	_ = filepath.WalkDir(tmp, func(path string, entry os.DirEntry, err error) error {
		if err == nil && entry.Name() == "install.sh" {
			installer = path
		}
		return nil
	})
	if installer == "" {
		return fmt.Errorf("gVisor package has no install.sh")
	}
	if err := c.Runner.Run(ctx, Command{Name: "bash", Args: []string{installer}}); err != nil {
		return err
	}
	return c.Runner.Run(ctx, Command{Name: "systemctl", Args: []string{"reload", "docker"}})
}

func verifyChecksumSidecar(path string) error {
	data, err := os.ReadFile(path + ".sha256")
	if err != nil {
		return err
	}
	fields := strings.Fields(string(data))
	if len(fields) != 2 {
		return fmt.Errorf("%s.sha256 must contain one SHA-256 and basename", path)
	}
	if fields[1] != filepath.Base(path) && fields[1] != "*"+filepath.Base(path) {
		return fmt.Errorf("checksum names %q, expected %q", fields[1], filepath.Base(path))
	}
	if !shaPattern.MatchString(fields[0]) {
		return fmt.Errorf("invalid SHA-256 in sidecar")
	}
	actual, err := fileSHA256(path)
	if err != nil {
		return err
	}
	if actual != fields[0] {
		return fmt.Errorf("checksum mismatch")
	}
	return nil
}
