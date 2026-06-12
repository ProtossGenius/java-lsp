package lsp

import (
	"archive/zip"
	"context"
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	"github.com/ProtossGenius/java-lsp/pkg/engine"
	plugincore "github.com/ProtossGenius/java-lsp/pkg/plugin"
	javaplugin "github.com/ProtossGenius/java-lsp/pkg/plugin/java"
	"github.com/ProtossGenius/java-lsp/pkg/storage"
	"github.com/ProtossGenius/java-lsp/pkg/syntax"
	javasyntax "github.com/ProtossGenius/java-lsp/pkg/syntax/java"
)

func TestInitializeAdvertisesRenameAndSignatureHelp(t *testing.T) {
	server := newTestServer(t)

	response := server.handleInitialize(json.RawMessage(`1`), mustRawJSON(t, initializeParams{
		RootURI: "file:///workspace/project",
	}))
	if response == nil || response.Error != nil {
		t.Fatalf("handleInitialize() error = %#v", response)
	}

	result, ok := response.Result.(map[string]any)
	if !ok {
		t.Fatalf("handleInitialize() result type = %T", response.Result)
	}
	capabilities := result["capabilities"].(map[string]any)
	for _, key := range []string{"definitionProvider", "declarationProvider", "implementationProvider", "renameProvider"} {
		if capabilities[key] != true {
			t.Fatalf("%s = %#v, want true", key, capabilities[key])
		}
	}
	if capabilities["renameProvider"] != true {
		t.Fatalf("renameProvider = %#v, want true", capabilities["renameProvider"])
	}
	signatureHelpProvider, ok := capabilities["signatureHelpProvider"].(map[string]any)
	if !ok {
		t.Fatalf("signatureHelpProvider type = %T", capabilities["signatureHelpProvider"])
	}
	triggers, ok := signatureHelpProvider["triggerCharacters"].([]string)
	if !ok {
		t.Fatalf("triggerCharacters type = %T", signatureHelpProvider["triggerCharacters"])
	}
	if len(triggers) != 2 || triggers[0] != "(" || triggers[1] != "," {
		t.Fatalf("triggerCharacters = %#v", triggers)
	}
}

func TestRenameReturnsWorkspaceEditsAcrossFiles(t *testing.T) {
	server := newTestServer(t)
	root := t.TempDir()

	mainPath := filepath.Join(root, "src", "main", "java", "com", "example", "App.java")
	refPath := filepath.Join(root, "src", "main", "java", "com", "example", "UseApp.java")
	writeFile(t, mainPath, `package com.example;

public class App {
}
`)
	writeFile(t, refPath, `package com.example;

public class UseApp {
    private App app;
}
`)

	server.handleInitialize(json.RawMessage(`1`), mustRawJSON(t, initializeParams{
		RootURI: "file://" + root,
	}))

	response := server.handleRename(json.RawMessage(`2`), mustRawJSON(t, renameParams{
		TextDocument: textDocumentIdentifier{URI: "file://" + mainPath},
		Position:     Position{Line: 2, Character: 13},
		NewName:      "RenamedApp",
	}))
	if response == nil || response.Error != nil {
		t.Fatalf("handleRename() error = %#v", response)
	}

	edit, ok := response.Result.(WorkspaceEdit)
	if !ok {
		t.Fatalf("handleRename() result type = %T", response.Result)
	}
	mainEdits := edit.Changes["file://"+mainPath]
	refEdits := edit.Changes["file://"+refPath]
	if len(mainEdits) == 0 {
		t.Fatal("main document edits are empty")
	}
	if len(refEdits) == 0 {
		t.Fatal("reference document edits are empty")
	}
	if mainEdits[len(mainEdits)-1].NewText != "RenamedApp" {
		t.Fatalf("main edit new text = %#v", mainEdits[len(mainEdits)-1].NewText)
	}
}

func TestSignatureHelpReturnsStringFormatSignature(t *testing.T) {
	server := newTestServer(t)
	path := filepath.Join(t.TempDir(), "App.java")
	text := `package com.example;

public class App {
    public String render(int id) {
        return String.format("hello %d", id);
    }
}
`
	writeFile(t, path, text)

	var openParams didOpenParams
	openParams.TextDocument.URI = "file://" + path
	openParams.TextDocument.LanguageID = "java"
	openParams.TextDocument.Text = text
	server.handleDidOpen(context.Background(), mustRawJSON(t, openParams))

	response := server.handleSignatureHelp(json.RawMessage(`3`), mustRawJSON(t, signatureHelpParams{
		TextDocument: textDocumentIdentifier{URI: "file://" + path},
		Position:     Position{Line: 4, Character: 41},
	}))
	if response == nil || response.Error != nil {
		t.Fatalf("handleSignatureHelp() error = %#v", response)
	}

	result, ok := response.Result.(*SignatureHelp)
	if !ok {
		t.Fatalf("handleSignatureHelp() result type = %T", response.Result)
	}
	if len(result.Signatures) != 1 || result.Signatures[0].Label != "String format(String format, Object... args)" {
		t.Fatalf("signatures = %#v", result.Signatures)
	}
	if result.ActiveParameter != 1 {
		t.Fatalf("active parameter = %d, want 1", result.ActiveParameter)
	}
}

func TestDependencyDeclarationAndImplementationNavigateIntoSourceJars(t *testing.T) {
	server := newTestServer(t)
	root := t.TempDir()
	moduleRoot := filepath.Join(root, "demo")
	sourcePath := filepath.Join(moduleRoot, "src", "main", "java", "com", "example", "demo", "DemoApplication.java")
	writeFile(t, filepath.Join(moduleRoot, "pom.xml"), `<project/>`)
	writeFile(t, sourcePath, `package com.example.demo;

import lombok.extern.slf4j.Slf4j;

@Slf4j
public class DemoApplication {
    void run() {
        log.info("hello");
    }
}
`)

	repoDir := filepath.Join(root, "repo")
	buildFakeDependencyJars(t, repoDir)
	writeFile(t, filepath.Join(moduleRoot, "mvnw"), "#!/bin/sh\nset -eu\noutput=\"${4#-Dmdep.outputFile=}\"\nprintf '%s' \""+filepath.Join(repoDir, "slf4j-api-2.0.13.jar")+":"+filepath.Join(repoDir, "logback-classic-1.5.6.jar")+"\" > \"$output\"\n")
	if err := os.Chmod(filepath.Join(moduleRoot, "mvnw"), 0o755); err != nil {
		t.Fatalf("Chmod() error = %v", err)
	}

	server.handleInitialize(json.RawMessage(`1`), mustRawJSON(t, initializeParams{
		RootURI: "file://" + moduleRoot,
	}))

	var openParams didOpenParams
	openParams.TextDocument.URI = "file://" + sourcePath
	openParams.TextDocument.LanguageID = "java"
	openParams.TextDocument.Text = readFile(t, sourcePath)
	server.handleDidOpen(context.Background(), mustRawJSON(t, openParams))

	declaration := server.handleDeclaration(context.Background(), json.RawMessage(`2`), mustRawJSON(t, textDocumentPositionParams{
		TextDocument: textDocumentIdentifier{URI: "file://" + sourcePath},
		Position:     Position{Line: 7, Character: 12},
	}))
	if declaration == nil || declaration.Error != nil {
		t.Fatalf("handleDeclaration() error = %#v", declaration)
	}
	declLocations, ok := declaration.Result.([]Location)
	if !ok || len(declLocations) == 0 {
		t.Fatalf("declaration locations = %#v", declaration.Result)
	}
	if got := declLocations[0].URI; !strings.HasSuffix(got, "/Logger.java") {
		t.Fatalf("declaration uri = %q, want Logger.java", got)
	}

	implementation := server.handleImplementation(context.Background(), json.RawMessage(`3`), mustRawJSON(t, textDocumentPositionParams{
		TextDocument: textDocumentIdentifier{URI: "file://" + sourcePath},
		Position:     Position{Line: 7, Character: 12},
	}))
	if implementation == nil || implementation.Error != nil {
		t.Fatalf("handleImplementation() error = %#v", implementation)
	}
	implLocations, ok := implementation.Result.([]Location)
	if !ok || len(implLocations) == 0 {
		t.Fatalf("implementation locations = %#v", implementation.Result)
	}
	if got := implLocations[0].URI; !strings.Contains(got, "logback-classic") {
		t.Fatalf("implementation uri = %q, want logback-classic source", got)
	}
}

func newTestServer(t *testing.T) *Server {
	t.Helper()
	return NewServer(engine.NewAnalyzer(
		syntax.NewRegistry(javasyntax.NewParser()),
		plugincore.NewManager(javaplugin.New()),
		storage.NewMemoryStore(),
	))
}

func mustRawJSON(t *testing.T, value any) json.RawMessage {
	t.Helper()
	data, err := json.Marshal(value)
	if err != nil {
		t.Fatalf("json.Marshal() error = %v", err)
	}
	return data
}

func writeFile(t *testing.T, path, content string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatalf("MkdirAll() error = %v", err)
	}
	if err := os.WriteFile(path, []byte(content), 0o644); err != nil {
		t.Fatalf("WriteFile() error = %v", err)
	}
}

func readFile(t *testing.T, path string) string {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("ReadFile() error = %v", err)
	}
	return string(data)
}

func buildFakeDependencyJars(t *testing.T, repoDir string) {
	t.Helper()
	writeZipFile(t, filepath.Join(repoDir, "slf4j-api-2.0.13.jar"), map[string]string{
		"org/slf4j/Logger.class": "class bytes ignored",
	})
	writeZipFile(t, filepath.Join(repoDir, "slf4j-api-2.0.13-sources.jar"), map[string]string{
		"org/slf4j/Logger.java": `package org.slf4j;

public interface Logger {
    void info(String message);
}
`,
	})
	writeZipFile(t, filepath.Join(repoDir, "logback-classic-1.5.6.jar"), map[string]string{
		"ch/qos/logback/classic/Logger.class": "class bytes ignored",
	})
	writeZipFile(t, filepath.Join(repoDir, "logback-classic-1.5.6-sources.jar"), map[string]string{
		"ch/qos/logback/classic/Logger.java": `package ch.qos.logback.classic;

import org.slf4j.Logger;

public class Logger implements Logger {
    public void info(String message) {
    }
}
`,
	})
}

func writeZipFile(t *testing.T, path string, files map[string]string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatalf("MkdirAll() error = %v", err)
	}
	file, err := os.Create(path)
	if err != nil {
		t.Fatalf("Create() error = %v", err)
	}
	defer file.Close()

	writer := zip.NewWriter(file)
	for name, content := range files {
		entry, err := writer.Create(name)
		if err != nil {
			t.Fatalf("zip Create() error = %v", err)
		}
		if _, err := entry.Write([]byte(content)); err != nil {
			t.Fatalf("zip Write() error = %v", err)
		}
	}
	if err := writer.Close(); err != nil {
		t.Fatalf("zip Close() error = %v", err)
	}
}

func buildNoSourceDependencyJar(t *testing.T, repoDir string) {
	t.Helper()
	sourceDir := filepath.Join(repoDir, "src", "com", "acme")
	writeFile(t, filepath.Join(sourceDir, "NoSourceGreeter.java"), `package com.acme;

public class NoSourceGreeter {
    public String greet(String name) {
        return "hello " + name;
    }
}
`)

	classesDir := filepath.Join(repoDir, "classes")
	if err := os.MkdirAll(classesDir, 0o755); err != nil {
		t.Fatalf("MkdirAll() error = %v", err)
	}
	cmd := exec.Command("javac", "-d", classesDir, filepath.Join(sourceDir, "NoSourceGreeter.java"))
	output, err := cmd.CombinedOutput()
	if err != nil {
		t.Fatalf("javac error = %v (%s)", err, strings.TrimSpace(string(output)))
	}

	writeJarFromDirectory(t, filepath.Join(repoDir, "nosource-1.0.0.jar"), classesDir)
}

func writeJarFromDirectory(t *testing.T, jarPath, sourceDir string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(jarPath), 0o755); err != nil {
		t.Fatalf("MkdirAll() error = %v", err)
	}
	file, err := os.Create(jarPath)
	if err != nil {
		t.Fatalf("Create() error = %v", err)
	}
	defer file.Close()

	writer := zip.NewWriter(file)
	err = filepath.Walk(sourceDir, func(path string, info os.FileInfo, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		if info.IsDir() {
			return nil
		}
		rel, err := filepath.Rel(sourceDir, path)
		if err != nil {
			return err
		}
		entry, err := writer.Create(filepath.ToSlash(rel))
		if err != nil {
			return err
		}
		data, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		_, err = entry.Write(data)
		return err
	})
	if err != nil {
		t.Fatalf("Walk() error = %v", err)
	}
	if err := writer.Close(); err != nil {
		t.Fatalf("zip Close() error = %v", err)
	}
}
