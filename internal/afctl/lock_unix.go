//go:build unix

package afctl

import (
	"fmt"
	"os"
	"path/filepath"
	"syscall"
)

type mutationLock struct{ file *os.File }

func acquireMutationLock(path string) (*mutationLock, error) {
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return nil, err
	}
	f, err := os.OpenFile(path, os.O_CREATE|os.O_RDWR, 0o600)
	if err != nil {
		return nil, err
	}
	if err := syscall.Flock(int(f.Fd()), syscall.LOCK_EX|syscall.LOCK_NB); err != nil {
		_ = f.Close()
		if err == syscall.EWOULDBLOCK {
			return nil, fmt.Errorf("another afctl mutation is running")
		}
		return nil, err
	}
	if err := f.Truncate(0); err == nil {
		_, _ = fmt.Fprintf(f, "pid=%d\n", os.Getpid())
		_ = f.Sync()
	}
	return &mutationLock{file: f}, nil
}

func (l *mutationLock) Close() error {
	err := syscall.Flock(int(l.file.Fd()), syscall.LOCK_UN)
	closeErr := l.file.Close()
	if err != nil {
		return err
	}
	return closeErr
}
