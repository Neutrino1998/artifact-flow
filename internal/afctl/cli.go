package afctl

import (
	"context"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
)

func Run(args []string, out, errOut io.Writer) int {
	root := os.Getenv("AF_ROOT")
	if root == "" {
		var err error
		root, err = os.Getwd()
		if err != nil {
			_, _ = fmt.Fprintln(errOut, err)
			return 1
		}
	}
	if len(args) >= 2 && args[0] == "--root" {
		root = args[1]
		args = args[2:]
	}
	if len(args) > 0 && strings.HasPrefix(args[0], "--root=") {
		root = strings.TrimPrefix(args[0], "--root=")
		args = args[1:]
	}
	abs, err := filepath.Abs(root)
	if err != nil {
		_, _ = fmt.Fprintln(errOut, err)
		return 1
	}
	c := NewController(abs, out, errOut)
	if len(args) == 0 {
		printUsage(out)
		return 0
	}
	ctx := context.Background()
	err = dispatch(ctx, c, args)
	if err != nil {
		_, _ = fmt.Fprintf(errOut, "error: %v\n", err)
		return 1
	}
	return 0
}

func dispatch(ctx context.Context, c *Controller, args []string) error {
	switch args[0] {
	case "site":
		if len(args) < 2 {
			return fmt.Errorf("usage: afctl site init|validate")
		}
		switch args[1] {
		case "init":
			preset := "intranet"
			if len(args) == 4 && args[2] == "--preset" {
				preset = args[3]
			} else if len(args) == 3 && strings.HasPrefix(args[2], "--preset=") {
				preset = strings.TrimPrefix(args[2], "--preset=")
			} else if len(args) != 2 {
				return fmt.Errorf("usage: afctl site init [--preset intranet|public]")
			}
			return c.SiteInit(preset)
		case "validate":
			if len(args) != 2 {
				return fmt.Errorf("usage: afctl site validate")
			}
			_, err := c.SiteValidate()
			return err
		case "migrate-v1":
			preset, runtimeName := "", ""
			for i := 2; i < len(args); i++ {
				if i+1 >= len(args) {
					return fmt.Errorf("%s requires a value", args[i])
				}
				value := args[i+1]
				switch args[i] {
				case "--preset":
					preset = value
				case "--sandbox-runtime":
					runtimeName = value
				default:
					return fmt.Errorf("unknown migrate-v1 flag %q", args[i])
				}
				i++
			}
			if preset == "" || runtimeName == "" {
				return fmt.Errorf("usage: afctl site migrate-v1 --preset intranet|public --sandbox-runtime runsc|runc")
			}
			return c.SiteMigrateV1(preset, runtimeName)
		default:
			return fmt.Errorf("unknown site command %q", args[1])
		}
	case "doctor":
		if len(args) != 1 {
			return fmt.Errorf("usage: afctl doctor")
		}
		return c.Doctor(ctx)
	case "plan":
		if len(args) < 2 {
			return fmt.Errorf("usage: afctl plan apply TARGET | afctl plan rollback")
		}
		switch args[1] {
		case "apply":
			if len(args) != 3 {
				return fmt.Errorf("usage: afctl plan apply <bundle-dir|release-id|current>")
			}
			plan, err := c.PlanApply(args[2])
			if err == nil {
				c.PrintPlan(plan)
			}
			return err
		case "rollback":
			if len(args) != 2 {
				return fmt.Errorf("usage: afctl plan rollback")
			}
			plan, err := c.PlanRollback()
			if err == nil {
				c.PrintPlan(plan)
			}
			return err
		default:
			return fmt.Errorf("unknown plan operation %q", args[1])
		}
	case "apply":
		target, options, err := parseApplyArgs(args[1:])
		if err != nil {
			return err
		}
		return c.applyWithOptions(ctx, target, options)
	case "rollback":
		if len(args) != 1 {
			return fmt.Errorf("usage: afctl rollback")
		}
		return c.Rollback(ctx)
	case "status":
		if len(args) != 1 {
			return fmt.Errorf("usage: afctl status")
		}
		return c.Status(ctx)
	case "maintenance":
		if len(args) < 2 || len(args) > 3 {
			return fmt.Errorf("usage: afctl maintenance on [NOTE] | off | status")
		}
		note := ""
		if len(args) == 3 {
			note = args[2]
		}
		return c.Maintenance(ctx, args[1], note)
	case "config":
		return dispatchConfig(ctx, c, args[1:])
	case "release":
		return dispatchRelease(args[1:])
	case "help", "-h", "--help":
		printUsage(c.Out)
		return nil
	default:
		return fmt.Errorf("unknown command %q", args[0])
	}
}

func parseApplyArgs(args []string) (string, applyOptions, error) {
	target := ""
	options := applyOptions{}
	for _, arg := range args {
		switch {
		case arg == "--keep-maintenance":
			if options.KeepMaintenance {
				return "", applyOptions{}, fmt.Errorf("duplicate apply flag %q", arg)
			}
			options.KeepMaintenance = true
		case strings.HasPrefix(arg, "-"):
			return "", applyOptions{}, fmt.Errorf("unknown apply flag %q", arg)
		case target != "":
			return "", applyOptions{}, fmt.Errorf("multiple apply targets supplied")
		default:
			target = arg
		}
	}
	if target == "" {
		return "", applyOptions{}, fmt.Errorf("usage: afctl apply <bundle-dir|release-id|current> [--keep-maintenance]")
	}
	return target, options, nil
}

func dispatchConfig(ctx context.Context, c *Controller, args []string) error {
	if len(args) < 1 {
		return fmt.Errorf("usage: afctl config checkout DIR | afctl config apply [--id ID] DIR")
	}
	switch args[0] {
	case "checkout":
		if len(args) != 2 {
			return fmt.Errorf("usage: afctl config checkout DIR")
		}
		return c.ConfigCheckout(args[1])
	case "apply":
		id, workspace := "", ""
		for i := 1; i < len(args); i++ {
			if args[i] == "--id" {
				if i+1 >= len(args) {
					return fmt.Errorf("--id requires a value")
				}
				id = args[i+1]
				i++
				continue
			}
			if strings.HasPrefix(args[i], "-") {
				return fmt.Errorf("unknown config apply flag %q", args[i])
			}
			if workspace != "" {
				return fmt.Errorf("multiple config workspaces supplied")
			}
			workspace = args[i]
		}
		if workspace == "" {
			return fmt.Errorf("usage: afctl config apply [--id ID] DIR")
		}
		return c.ConfigApply(ctx, workspace, id)
	default:
		return fmt.Errorf("unknown config command %q", args[0])
	}
}

func dispatchRelease(args []string) error {
	if len(args) < 1 || args[0] != "manifest" {
		return fmt.Errorf("usage: afctl release manifest [flags]")
	}
	var options ManifestOptions
	options.Artifacts = map[string]string{}
	for i := 1; i < len(args); i++ {
		flag := args[i]
		if i+1 >= len(args) {
			return fmt.Errorf("%s requires a value", flag)
		}
		value := args[i+1]
		i++
		switch flag {
		case "--bundle":
			options.Bundle = value
		case "--id":
			options.ReleaseID = value
		case "--kind":
			options.Kind = value
		case "--platform":
			options.Platform = value
		case "--source":
			options.Source = value
		case "--expected-base":
			options.ExpectedBase = value
		case "--sandbox-image":
			options.SandboxImage = value
		case "--image":
			options.Images = append(options.Images, value)
		case "--artifact":
			role, file, ok := strings.Cut(value, "=")
			if !ok || role == "" || file == "" {
				return fmt.Errorf("--artifact expects role=filename")
			}
			if _, exists := options.Artifacts[role]; exists {
				return fmt.Errorf("duplicate artifact role %s", role)
			}
			options.Artifacts[role] = file
		default:
			return fmt.Errorf("unknown release manifest flag %q", flag)
		}
	}
	return WriteManifest(options)
}

func printUsage(out io.Writer) {
	_, _ = fmt.Fprint(out, `afctl — ArtifactFlow deployment controller

Usage:
  afctl [--root PATH] site init [--preset intranet|public]
  afctl [--root PATH] site validate
  afctl [--root PATH] site migrate-v1 --preset intranet|public --sandbox-runtime runsc|runc
  afctl [--root PATH] doctor
  afctl [--root PATH] plan apply <bundle-dir|release-id|current>
  afctl [--root PATH] apply <bundle-dir|release-id|current> [--keep-maintenance]
  afctl [--root PATH] plan rollback
  afctl [--root PATH] rollback
  afctl [--root PATH] status
  afctl [--root PATH] maintenance on [NOTE] | off | status
  afctl [--root PATH] config checkout DIR
  afctl [--root PATH] config apply [--id ID] DIR
`)
}
