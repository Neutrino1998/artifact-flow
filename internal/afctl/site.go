package afctl

import (
	"bufio"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"
)

var siteKeys = map[string]func(*Site, string) error{
	"schema": func(s *Site, value string) error {
		v, err := strconv.Atoi(value)
		if err != nil {
			return fmt.Errorf("schema must be an integer")
		}
		s.Schema = v
		return nil
	},
	"executor":        func(s *Site, value string) error { s.Executor = value; return nil },
	"tls":             func(s *Site, value string) error { s.TLS = value; return nil },
	"infra":           func(s *Site, value string) error { s.Infra = value; return nil },
	"sandbox_runtime": func(s *Site, value string) error { s.SandboxRuntime = value; return nil },
	"scratch_root":    func(s *Site, value string) error { s.ScratchRoot = value; return nil },
	"backend_replicas": func(s *Site, value string) error {
		v, err := strconv.Atoi(value)
		if err != nil {
			return fmt.Errorf("backend_replicas must be an integer")
		}
		s.BackendReplicas = v
		return nil
	},
	"ready_timeout_seconds": func(s *Site, value string) error {
		v, err := strconv.Atoi(value)
		if err != nil {
			return fmt.Errorf("ready_timeout_seconds must be an integer")
		}
		s.ReadyTimeoutSeconds = v
		return nil
	},
	"inventory":        func(s *Site, value string) error { s.Inventory = value; return nil },
	"ansible_ee_image": func(s *Site, value string) error { s.AnsibleEEImage = value; return nil },
}

func LoadSite(path string) (Site, error) {
	f, err := os.Open(path)
	if err != nil {
		return Site{}, err
	}
	defer f.Close()

	var site Site
	seen := map[string]bool{}
	scanner := bufio.NewScanner(f)
	lineNo := 0
	for scanner.Scan() {
		lineNo++
		line := strings.TrimSpace(stripTOMLComment(scanner.Text()))
		if line == "" {
			continue
		}
		if strings.HasPrefix(line, "[") {
			return Site{}, fmt.Errorf("%s:%d: tables are not supported; site.toml is intentionally flat", path, lineNo)
		}
		key, raw, ok := strings.Cut(line, "=")
		if !ok {
			return Site{}, fmt.Errorf("%s:%d: expected key = value", path, lineNo)
		}
		key, raw = strings.TrimSpace(key), strings.TrimSpace(raw)
		set, ok := siteKeys[key]
		if !ok {
			return Site{}, fmt.Errorf("%s:%d: unknown field %q", path, lineNo, key)
		}
		if seen[key] {
			return Site{}, fmt.Errorf("%s:%d: duplicate field %q", path, lineNo, key)
		}
		seen[key] = true
		value, err := parseTOMLScalar(raw)
		if err != nil {
			return Site{}, fmt.Errorf("%s:%d: %w", path, lineNo, err)
		}
		if err := set(&site, value); err != nil {
			return Site{}, fmt.Errorf("%s:%d: %w", path, lineNo, err)
		}
	}
	if err := scanner.Err(); err != nil {
		return Site{}, err
	}
	root := filepath.Dir(filepath.Dir(path))
	if site.Executor == "ansible" && site.Inventory != "" && !filepath.IsAbs(site.Inventory) {
		site.Inventory = filepath.Join(root, site.Inventory)
	}
	if err := site.Validate(root); err != nil {
		return Site{}, err
	}
	return site, nil
}

func stripTOMLComment(line string) string {
	quoted, escaped := false, false
	for i, r := range line {
		if escaped {
			escaped = false
			continue
		}
		if r == '\\' && quoted {
			escaped = true
			continue
		}
		if r == '"' {
			quoted = !quoted
			continue
		}
		if r == '#' && !quoted {
			return line[:i]
		}
	}
	return line
}

func parseTOMLScalar(raw string) (string, error) {
	if strings.HasPrefix(raw, "\"") {
		value, err := strconv.Unquote(raw)
		if err != nil {
			return "", fmt.Errorf("invalid quoted string")
		}
		return value, nil
	}
	if raw == "" || strings.ContainsAny(raw, " \t") {
		return "", fmt.Errorf("strings must be quoted")
	}
	return raw, nil
}

func (s Site) Validate(root string) error {
	if s.Schema != SiteSchema {
		return fmt.Errorf("site schema must be %d", SiteSchema)
	}
	if s.Executor != "local" && s.Executor != "ansible" {
		return fmt.Errorf("executor must be local or ansible")
	}
	if s.TLS != "static" && s.TLS != "acme" {
		return fmt.Errorf("tls must be static or acme")
	}
	if s.Infra != "bundled" && s.Infra != "external" {
		return fmt.Errorf("infra must be bundled or external")
	}
	if s.SandboxRuntime != "runsc" && s.SandboxRuntime != "runc" {
		return fmt.Errorf("sandbox_runtime must be runsc or runc")
	}
	if !filepath.IsAbs(s.ScratchRoot) || filepath.Clean(s.ScratchRoot) != s.ScratchRoot || filepath.Dir(s.ScratchRoot) == "/" || strings.ContainsAny(s.ScratchRoot, " \t\r\n") {
		return fmt.Errorf("scratch_root must be a clean absolute path below a parent directory")
	}
	if s.BackendReplicas < 1 {
		return fmt.Errorf("backend_replicas must be at least 1")
	}
	if s.ReadyTimeoutSeconds < 1 {
		return fmt.Errorf("ready_timeout_seconds must be at least 1")
	}
	if s.Executor == "local" {
		if s.Inventory != "" || s.AnsibleEEImage != "" {
			return fmt.Errorf("inventory and ansible_ee_image are only valid with executor = \"ansible\"")
		}
	} else {
		if s.Infra != "external" {
			return fmt.Errorf("experimental ansible executor currently requires infra = \"external\"; provision PostgreSQL and Redis separately")
		}
		if s.BackendReplicas != 1 {
			return fmt.Errorf("ansible executor requires backend_replicas = 1; scale is the number of app inventory hosts")
		}
		if s.Inventory == "" {
			return fmt.Errorf("inventory is required with executor = \"ansible\"")
		}
		if !filepath.IsAbs(s.Inventory) {
			return fmt.Errorf("inventory could not be resolved to an absolute path under %s", root)
		}
		rel, err := filepath.Rel(root, s.Inventory)
		if err != nil || rel == ".." || strings.HasPrefix(rel, ".."+string(filepath.Separator)) {
			return fmt.Errorf("inventory must be inside install root %s", root)
		}
		parts := strings.Split(s.AnsibleEEImage, "@sha256:")
		if len(parts) != 2 || parts[0] == "" || !shaPattern.MatchString(parts[1]) {
			return fmt.Errorf("ansible_ee_image must be pinned by @sha256 digest")
		}
	}
	return nil
}

func siteTOML(preset string) (string, error) {
	tls := "static"
	if preset == "public" {
		tls = "acme"
	}
	if preset != "intranet" && preset != "public" {
		return "", fmt.Errorf("preset must be intranet or public")
	}
	return fmt.Sprintf(`schema = 1
executor = "local"
tls = %q
infra = "bundled"
sandbox_runtime = "runsc"
scratch_root = "/data/artifactflow/sandbox"
backend_replicas = 2
ready_timeout_seconds = 120
`, tls), nil
}
