package afctl

import (
	"context"
	"fmt"
	"io"
	"os"
	"os/exec"
	"strings"
)

type Command struct {
	Name string
	Args []string
	Dir  string
	Env  []string
}

type Runner interface {
	Run(context.Context, Command) error
	Output(context.Context, Command) (string, error)
}

type OSRunner struct{ Out, Err io.Writer }

func (r OSRunner) command(ctx context.Context, command Command) *exec.Cmd {
	cmd := exec.CommandContext(ctx, command.Name, command.Args...)
	cmd.Dir = command.Dir
	if command.Env != nil {
		cmd.Env = append(os.Environ(), command.Env...)
	}
	return cmd
}

func (r OSRunner) Run(ctx context.Context, command Command) error {
	cmd := r.command(ctx, command)
	cmd.Stdout, cmd.Stderr = r.Out, r.Err
	if err := cmd.Run(); err != nil {
		return fmt.Errorf("%s %s: %w", command.Name, strings.Join(command.Args, " "), err)
	}
	return nil
}

func (r OSRunner) Output(ctx context.Context, command Command) (string, error) {
	cmd := r.command(ctx, command)
	data, err := cmd.CombinedOutput()
	if err != nil {
		return string(data), fmt.Errorf("%s %s: %w: %s", command.Name, strings.Join(command.Args, " "), err, strings.TrimSpace(string(data)))
	}
	return string(data), nil
}
