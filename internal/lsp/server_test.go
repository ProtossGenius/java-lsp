package lsp

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
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
