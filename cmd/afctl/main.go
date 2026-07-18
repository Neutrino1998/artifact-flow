package main

import (
	"os"

	"github.com/Neutrino1998/artifact-flow/internal/afctl"
)

func main() {
	os.Exit(afctl.Run(os.Args[1:], os.Stdout, os.Stderr))
}
