package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"os"

	"github.com/ProtossGenius/java-lsp/internal/lsp"
	"github.com/ProtossGenius/java-lsp/pkg/config"
	"github.com/ProtossGenius/java-lsp/pkg/engine"
	plugincore "github.com/ProtossGenius/java-lsp/pkg/plugin"
	javaplugin "github.com/ProtossGenius/java-lsp/pkg/plugin/java"
	"github.com/ProtossGenius/java-lsp/pkg/storage/pebble"
	"github.com/ProtossGenius/java-lsp/pkg/syntax"
	javasyntax "github.com/ProtossGenius/java-lsp/pkg/syntax/java"
)

func main() {
	var proxyURL string
	var storagePath string

	flag.StringVar(&proxyURL, "proxy", "", "HTTP proxy URL used by plugin dependency and decompile integrations")
	flag.StringVar(&storagePath, "storage", ".java-lsp/index", "path to the embedded index store")
	flag.Parse()

	cfg := config.Config{
		ProxyURL:    proxyURL,
		StoragePath: storagePath,
	}
	if _, err := cfg.HTTPClient(); err != nil {
		exitf("invalid proxy configuration: %v", err)
	}

	store, err := pebble.NewStore(cfg.StoragePath)
	if err != nil {
		exitf("open storage: %v", err)
	}
	defer store.Close()

	parserRegistry := syntax.NewRegistry(javasyntax.NewParser())
	pluginManager := plugincore.NewManager(javaplugin.New())
	analyzer := engine.NewAnalyzer(parserRegistry, pluginManager, store)
	server := lsp.NewServer(analyzer)

	if err := server.Serve(context.Background(), os.Stdin, os.Stdout); err != nil && !errors.Is(err, lsp.ErrServerExited) {
		exitf("server failed: %v", err)
	}
}

func exitf(format string, args ...any) {
	fmt.Fprintf(os.Stderr, format+"\n", args...)
	os.Exit(1)
}
