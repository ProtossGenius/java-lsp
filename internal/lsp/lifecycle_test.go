package lsp

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestWorkspaceLifecycleAndNavigationCoverage(t *testing.T) {
	server := newTestServer(t)
	root := copyFixtureWorkspace(t, filepath.Join("testdata", "workspaces", "full-java-demo"))

	controllerPath := filepath.Join(root, "src", "main", "java", "com", "example", "demo", "controller", "GreetingController.java")
	greeterPath := filepath.Join(root, "src", "main", "java", "com", "example", "demo", "api", "Greeter.java")
	implPath := filepath.Join(root, "src", "main", "java", "com", "example", "demo", "service", "DefaultGreeter.java")

	server.handleInitialize(json.RawMessage(`1`), mustRawJSON(t, initializeParams{
		RootURI: "file://" + root,
	}))

	openDocument(t, server, controllerPath)
	openDocument(t, server, greeterPath)
	openDocument(t, server, implPath)

	definition := server.handleDefinition(context.Background(), json.RawMessage(`2`), mustRawJSON(t, textDocumentPositionParams{
		TextDocument: textDocumentIdentifier{URI: "file://" + controllerPath},
		Position:     Position{Line: 7, Character: 12},
	}))
	requireLocations(t, "definition", definition, filepath.Base(greeterPath), "Greeter.java")

	declaration := server.handleDeclaration(context.Background(), json.RawMessage(`3`), mustRawJSON(t, textDocumentPositionParams{
		TextDocument: textDocumentIdentifier{URI: "file://" + controllerPath},
		Position:     Position{Line: 7, Character: 12},
	}))
	requireLocations(t, "declaration", declaration, filepath.Base(greeterPath), "Greeter.java")

	implementation := server.handleImplementation(context.Background(), json.RawMessage(`4`), mustRawJSON(t, textDocumentPositionParams{
		TextDocument: textDocumentIdentifier{URI: "file://" + controllerPath},
		Position:     Position{Line: 10, Character: 24},
	}))
	requireLocations(t, "implementation", implementation, filepath.Base(implPath), "DefaultGreeter.java")

	interfaceImpl := server.handleImplementation(context.Background(), json.RawMessage(`4a`), mustRawJSON(t, textDocumentPositionParams{
		TextDocument: textDocumentIdentifier{URI: "file://" + greeterPath},
		Position:     Position{Line: 4, Character: 12},
	}))
	requireLocations(t, "interface implementation", interfaceImpl, filepath.Base(implPath), "DefaultGreeter.java")

	implDecl := server.handleDeclaration(context.Background(), json.RawMessage(`4b`), mustRawJSON(t, textDocumentPositionParams{
		TextDocument: textDocumentIdentifier{URI: "file://" + implPath},
		Position:     Position{Line: 7, Character: 18},
	}))
	requireLocations(t, "implementation declaration", implDecl, filepath.Base(greeterPath), "Greeter.java")

	refs := server.handleReferences(context.Background(), json.RawMessage(`4c`), mustRawJSON(t, referenceParams{
		TextDocument: textDocumentIdentifier{URI: "file://" + greeterPath},
		Position:     Position{Line: 4, Character: 12},
		Context: struct {
			IncludeDeclaration bool `json:"includeDeclaration"`
		}{
			IncludeDeclaration: true,
		},
	}))
	if refs == nil || refs.Error != nil {
		t.Fatalf("references error = %#v", refs)
	}
	refLocations, ok := refs.Result.([]Location)
	if !ok || len(refLocations) < 3 {
		t.Fatalf("references result = %#v", refs.Result)
	}

	signature := server.handleSignatureHelp(json.RawMessage(`5`), mustRawJSON(t, signatureHelpParams{
		TextDocument: textDocumentIdentifier{URI: "file://" + implPath},
		Position:     Position{Line: 8, Character: 30},
	}))
	if signature == nil || signature.Error != nil {
		t.Fatalf("signature help error = %#v", signature)
	}
	result, ok := signature.Result.(*SignatureHelp)
	if !ok || len(result.Signatures) == 0 {
		t.Fatalf("signature result = %#v", signature.Result)
	}
	if got := result.Signatures[0].Label; !strings.Contains(got, "String format") {
		t.Fatalf("signature label = %q", got)
	}

	rename := server.handleRename(json.RawMessage(`6`), mustRawJSON(t, renameParams{
		TextDocument: textDocumentIdentifier{URI: "file://" + greeterPath},
		Position:     Position{Line: 2, Character: 17},
		NewName:      "WelcomeGreeter",
	}))
	if rename == nil || rename.Error != nil {
		t.Fatalf("rename error = %#v", rename)
	}
	edit, ok := rename.Result.(WorkspaceEdit)
	if !ok {
		t.Fatalf("rename result type = %T", rename.Result)
	}
	if len(edit.Changes["file://"+controllerPath]) == 0 || len(edit.Changes["file://"+implPath]) == 0 {
		t.Fatalf("rename changes = %#v", edit.Changes)
	}

	updatedImpl := strings.ReplaceAll(readFile(t, implPath), "hello %s", "welcome %s")
	server.handleDidChange(context.Background(), mustRawJSON(t, didChangeParams{
		TextDocument: struct {
			URI string `json:"uri"`
		}{
			URI: "file://" + implPath,
		},
		ContentChanges: []struct {
			Text string `json:"text"`
		}{
			{Text: updatedImpl},
		},
	}))
	if got, err := server.documentText("file://" + implPath); err != nil || !strings.Contains(got, "welcome %s") {
		t.Fatalf("didChange document text = %q, err = %v", got, err)
	}

	server.handleDidRenameFiles(context.Background(), mustRawJSON(t, didRenameFilesParams{
		Files: []renamedFile{{
			OldURI: "file://" + implPath,
			NewURI: "file://" + filepath.Join(filepath.Dir(implPath), "RenamedGreeter.java"),
		}},
	}))
	if _, ok := server.documents["file://"+implPath]; ok {
		t.Fatal("old renamed document still tracked")
	}
	if _, ok := server.documents["file://"+filepath.Join(filepath.Dir(implPath), "RenamedGreeter.java")]; !ok {
		t.Fatal("new renamed document not tracked")
	}

	server.handleDidClose(mustRawJSON(t, didCloseParams{
		TextDocument: struct {
			URI string `json:"uri"`
		}{
			URI: "file://" + greeterPath,
		},
	}))
	if _, ok := server.documents["file://"+greeterPath]; ok {
		t.Fatal("closed document still tracked")
	}
}

func TestDependencyDefinitionFallsBackToDecompiledClassWithoutSources(t *testing.T) {
	server := newTestServer(t)
	root := t.TempDir()
	moduleRoot := filepath.Join(root, "demo")
	repoDir := filepath.Join(root, "repo")
	sourcePath := filepath.Join(moduleRoot, "src", "main", "java", "com", "example", "demo", "DemoApplication.java")

	writeFile(t, filepath.Join(moduleRoot, "pom.xml"), `<project/>`)
	writeFile(t, sourcePath, `package com.example.demo;

import com.acme.NoSourceGreeter;

public class DemoApplication {
    private NoSourceGreeter greeter = new NoSourceGreeter();
}
`)
	buildNoSourceDependencyJar(t, repoDir)
	writeMavenWrapper(t, moduleRoot, []string{filepath.Join(repoDir, "nosource-1.0.0.jar")})

	server.handleInitialize(json.RawMessage(`1`), mustRawJSON(t, initializeParams{
		RootURI: "file://" + moduleRoot,
	}))
	openDocument(t, server, sourcePath)

	definition := server.handleDefinition(context.Background(), json.RawMessage(`2`), mustRawJSON(t, textDocumentPositionParams{
		TextDocument: textDocumentIdentifier{URI: "file://" + sourcePath},
		Position:     Position{Line: 5, Character: 20},
	}))
	if definition == nil || definition.Error != nil {
		t.Fatalf("definition error = %#v", definition)
	}
	locations, ok := definition.Result.([]Location)
	if !ok || len(locations) == 0 {
		t.Fatalf("definition locations = %#v", definition.Result)
	}
	if got := locations[0].URI; !strings.Contains(got, "/javap/") {
		t.Fatalf("definition uri = %q, want javap cache path", got)
	}
	content := readFile(t, strings.TrimPrefix(locations[0].URI, "file://"))
	if !strings.Contains(content, "public class com.acme.NoSourceGreeter") {
		t.Fatalf("decompiled content = %q", content)
	}
}

func TestJDKDefinitionPrefersRuntimeSources(t *testing.T) {
	server := newTestServer(t)
	moduleRoot := t.TempDir()
	sourcePath := filepath.Join(moduleRoot, "src", "main", "java", "com", "example", "demo", "DemoApplication.java")

	writeFile(t, filepath.Join(moduleRoot, "pom.xml"), `<project/>`)
	writeFile(t, sourcePath, `package com.example.demo;

public class DemoApplication {
    public void run() {
        throw new RuntimeException("boom");
    }
}
`)

	server.handleInitialize(json.RawMessage(`1`), mustRawJSON(t, initializeParams{
		RootURI: "file://" + moduleRoot,
	}))
	openDocument(t, server, sourcePath)
	sourceText := readFile(t, sourcePath)

	response := server.handleDefinition(context.Background(), json.RawMessage(`2`), mustRawJSON(t, textDocumentPositionParams{
		TextDocument: textDocumentIdentifier{URI: "file://" + sourcePath},
		Position: Position{
			Line:      4,
			Character: lineColumnAtSubstring(sourceText, 4, "RuntimeException") + 2,
		},
	}))
	if response == nil || response.Error != nil {
		t.Fatalf("jdk definition error = %#v", response)
	}
	locations, ok := response.Result.([]Location)
	if !ok || len(locations) == 0 {
		t.Fatalf("jdk definition result = %#v", response.Result)
	}
	if got := locations[0].URI; !strings.HasSuffix(got, "/RuntimeException.java") {
		t.Fatalf("jdk definition uri = %q, want RuntimeException.java", got)
	}
}

func TestServeHandlesShutdownAndExit(t *testing.T) {
	server := newTestServer(t)
	exit, shutdown := server.handleMessage(context.Background(), incomingMessage{
		Method:  "shutdown",
		ID:      json.RawMessage(`1`),
		JSONRPC: "2.0",
	})
	if exit {
		t.Fatal("shutdown should not exit immediately")
	}
	if responseID := string(shutdown.ID); responseID != "1" {
		t.Fatalf("shutdown id = %q", responseID)
	}
	exit, response := server.handleMessage(context.Background(), incomingMessage{
		Method:  "exit",
		JSONRPC: "2.0",
	})
	if !exit || response != nil {
		t.Fatalf("exit = %v, response = %#v", exit, response)
	}
}

func openDocument(t *testing.T, server *Server, path string) {
	t.Helper()
	var openParams didOpenParams
	openParams.TextDocument.URI = "file://" + path
	openParams.TextDocument.LanguageID = "java"
	openParams.TextDocument.Text = readFile(t, path)
	server.handleDidOpen(context.Background(), mustRawJSON(t, openParams))
}

func requireLocations(t *testing.T, name string, response *responseMessage, wantBase string, wantSuffix string) {
	t.Helper()
	if response == nil || response.Error != nil {
		t.Fatalf("%s error = %#v", name, response)
	}
	locations, ok := response.Result.([]Location)
	if !ok || len(locations) == 0 {
		t.Fatalf("%s locations = %#v", name, response.Result)
	}
	if got := filepath.Base(strings.TrimPrefix(locations[0].URI, "file://")); got != wantBase {
		t.Fatalf("%s base = %q, want %q", name, got, wantBase)
	}
	if got := locations[0].URI; !strings.HasSuffix(got, wantSuffix) {
		t.Fatalf("%s uri = %q, want suffix %q", name, got, wantSuffix)
	}
}

func copyFixtureWorkspace(t *testing.T, relative string) string {
	t.Helper()
	sourceRoot := filepath.Join(relative)
	targetRoot := t.TempDir()
	if err := filepath.Walk(sourceRoot, func(path string, info os.FileInfo, err error) error {
		if err != nil {
			return err
		}
		rel, err := filepath.Rel(sourceRoot, path)
		if err != nil {
			return err
		}
		target := filepath.Join(targetRoot, rel)
		if info.IsDir() {
			return os.MkdirAll(target, 0o755)
		}
		data, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		return os.WriteFile(target, data, 0o644)
	}); err != nil {
		t.Fatalf("copyFixtureWorkspace() error = %v", err)
	}
	return targetRoot
}

func writeMavenWrapper(t *testing.T, moduleRoot string, jars []string) {
	t.Helper()
	output := strings.Join(jars, string(os.PathListSeparator))
	content := "#!/bin/sh\nset -eu\nfor arg in \"$@\"; do\n  case \"$arg\" in\n    -Dmdep.outputFile=*) output_file=\"${arg#-Dmdep.outputFile=}\" ;;\n  esac\ndone\nprintf '%s' \"" + output + "\" > \"$output_file\"\n"
	writeFile(t, filepath.Join(moduleRoot, "mvnw"), content)
	if err := os.Chmod(filepath.Join(moduleRoot, "mvnw"), 0o755); err != nil {
		t.Fatalf("Chmod() error = %v", err)
	}
}
