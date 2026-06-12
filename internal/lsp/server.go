package lsp

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"os"
	"strconv"
	"strings"
	"sync"

	"github.com/ProtossGenius/java-lsp/pkg/engine"
	"github.com/ProtossGenius/java-lsp/pkg/syntax"
)

var ErrServerExited = errors.New("server exited")

const textDocumentSyncFull = 1

type Server struct {
	analyzer       *engine.Analyzer
	navigation     *navigationResolver
	logger         *log.Logger
	mu             sync.RWMutex
	documents      map[string]openedDocument
	workspaceRoots []string
	shutdown       bool
}

type openedDocument struct {
	language string
	text     string
}

type incomingMessage struct {
	JSONRPC string          `json:"jsonrpc"`
	ID      json.RawMessage `json:"id,omitempty"`
	Method  string          `json:"method,omitempty"`
	Params  json.RawMessage `json:"params,omitempty"`
}

type responseMessage struct {
	JSONRPC string          `json:"jsonrpc"`
	ID      json.RawMessage `json:"id,omitempty"`
	Result  any             `json:"result,omitempty"`
	Error   *responseError  `json:"error,omitempty"`
}

type responseError struct {
	Code    int    `json:"code"`
	Message string `json:"message"`
}

func NewServer(analyzer *engine.Analyzer) *Server {
	return &Server{
		analyzer:   analyzer,
		navigation: newNavigationResolver(),
		logger:     log.New(os.Stderr, "java-lsp: ", 0),
		documents:  make(map[string]openedDocument),
	}
}

func (s *Server) Serve(ctx context.Context, in io.Reader, out io.Writer) error {
	reader := bufio.NewReader(in)
	for {
		message, err := readMessage(reader)
		if err != nil {
			if errors.Is(err, io.EOF) {
				return nil
			}
			return err
		}

		shouldExit, response := s.handleMessage(ctx, message)
		if response != nil {
			if err := writeMessage(out, response); err != nil {
				return err
			}
		}
		if shouldExit {
			return ErrServerExited
		}
	}
}

func (s *Server) handleMessage(ctx context.Context, message incomingMessage) (bool, *responseMessage) {
	switch message.Method {
	case "initialize":
		return false, s.handleInitialize(message.ID, message.Params)
	case "initialized":
		return false, nil
	case "shutdown":
		s.mu.Lock()
		s.shutdown = true
		s.mu.Unlock()
		return false, &responseMessage{
			JSONRPC: "2.0",
			ID:      message.ID,
			Result:  map[string]any{},
		}
	case "exit":
		return true, nil
	case "textDocument/didOpen":
		return false, s.handleDidOpen(ctx, message.Params)
	case "textDocument/didChange":
		return false, s.handleDidChange(ctx, message.Params)
	case "textDocument/didClose":
		s.handleDidClose(message.Params)
		return false, nil
	case "textDocument/rename":
		return false, s.handleRename(message.ID, message.Params)
	case "textDocument/signatureHelp":
		return false, s.handleSignatureHelp(message.ID, message.Params)
	case "textDocument/definition":
		return false, s.handleDefinition(ctx, message.ID, message.Params)
	case "textDocument/declaration":
		return false, s.handleDeclaration(ctx, message.ID, message.Params)
	case "textDocument/implementation":
		return false, s.handleImplementation(ctx, message.ID, message.Params)
	case "textDocument/completion":
		return false, s.handleCompletion(ctx, message.ID, message.Params)
	case "workspace/didRenameFiles":
		s.handleDidRenameFiles(ctx, message.Params)
		return false, nil
	default:
		if len(message.ID) == 0 {
			return false, nil
		}
		return false, &responseMessage{
			JSONRPC: "2.0",
			ID:      message.ID,
			Error: &responseError{
				Code:    -32601,
				Message: "method not found",
			},
		}
	}
}

func (s *Server) handleInitialize(id json.RawMessage, raw json.RawMessage) *responseMessage {
	var params initializeParams
	if len(raw) > 0 {
		if err := json.Unmarshal(raw, &params); err != nil {
			return &responseMessage{
				JSONRPC: "2.0",
				ID:      id,
				Error: &responseError{
					Code:    -32602,
					Message: fmt.Sprintf("invalid initialize params: %v", err),
				},
			}
		}
	}

	s.setWorkspaceRoots(params)

	return &responseMessage{
		JSONRPC: "2.0",
		ID:      id,
		Result: map[string]any{
			"capabilities": map[string]any{
				"textDocumentSync":       textDocumentSyncFull,
				"definitionProvider":     true,
				"declarationProvider":    true,
				"implementationProvider": true,
				"completionProvider": map[string]any{
					"triggerCharacters": []string{"."},
				},
				"renameProvider": true,
				"signatureHelpProvider": map[string]any{
					"triggerCharacters": []string{"(", ","},
				},
				"workspace": map[string]any{
					"fileOperations": map[string]any{
						"didRename": map[string]any{
							"filters": []map[string]any{
								{
									"scheme": "file",
									"pattern": map[string]any{
										"glob": "**/*.java",
									},
								},
							},
						},
					},
				},
			},
			"serverInfo": map[string]any{
				"name":    "java-lsp",
				"version": "0.2.0",
			},
		},
	}
}

func (s *Server) handleDefinition(ctx context.Context, id json.RawMessage, raw json.RawMessage) *responseMessage {
	return s.handleNavigation(ctx, id, raw, s.navigation.definition)
}

func (s *Server) handleDeclaration(ctx context.Context, id json.RawMessage, raw json.RawMessage) *responseMessage {
	return s.handleNavigation(ctx, id, raw, s.navigation.declaration)
}

func (s *Server) handleImplementation(ctx context.Context, id json.RawMessage, raw json.RawMessage) *responseMessage {
	return s.handleNavigation(ctx, id, raw, s.navigation.implementation)
}

func (s *Server) handleCompletion(ctx context.Context, id json.RawMessage, raw json.RawMessage) *responseMessage {
	var params textDocumentPositionParams
	if err := json.Unmarshal(raw, &params); err != nil {
		return invalidParamsResponse(id, err)
	}

	text, err := s.documentText(params.TextDocument.URI)
	if err != nil {
		return internalErrorResponse(id, err)
	}
	path, ok := filePathFromURI(params.TextDocument.URI)
	if !ok {
		return internalErrorResponse(id, errors.New("unsupported document URI"))
	}
	root := s.workspaceRootForURI(params.TextDocument.URI)
	result, err := completionAtPosition(ctx, s.navigation, root, navigationRequest{
		uri:      params.TextDocument.URI,
		text:     text,
		path:     path,
		position: params.Position,
	})
	if err != nil {
		return internalErrorResponse(id, err)
	}
	return &responseMessage{
		JSONRPC: "2.0",
		ID:      id,
		Result:  result,
	}
}

func (s *Server) handleNavigation(
	ctx context.Context,
	id json.RawMessage,
	raw json.RawMessage,
	resolve func(context.Context, string, navigationRequest) ([]Location, error),
) *responseMessage {
	var params textDocumentPositionParams
	if err := json.Unmarshal(raw, &params); err != nil {
		return invalidParamsResponse(id, err)
	}

	text, err := s.documentText(params.TextDocument.URI)
	if err != nil {
		return internalErrorResponse(id, err)
	}
	path, ok := filePathFromURI(params.TextDocument.URI)
	if !ok {
		return internalErrorResponse(id, errors.New("unsupported document URI"))
	}
	root := s.workspaceRootForURI(params.TextDocument.URI)
	locations, err := resolve(ctx, root, navigationRequest{
		uri:      params.TextDocument.URI,
		text:     text,
		path:     path,
		position: params.Position,
	})
	if err != nil {
		return internalErrorResponse(id, err)
	}

	return &responseMessage{
		JSONRPC: "2.0",
		ID:      id,
		Result:  locations,
	}
}

func (s *Server) handleDidOpen(ctx context.Context, raw json.RawMessage) *responseMessage {
	var params didOpenParams
	if err := json.Unmarshal(raw, &params); err != nil {
		s.logger.Printf("decode didOpen: %v", err)
		return nil
	}

	s.mu.Lock()
	s.documents[params.TextDocument.URI] = openedDocument{
		language: params.TextDocument.LanguageID,
		text:     params.TextDocument.Text,
	}
	s.mu.Unlock()

	if err := s.analyze(ctx, params.TextDocument.URI, params.TextDocument.LanguageID, params.TextDocument.Text); err != nil {
		s.logger.Printf("analyze didOpen %s: %v", params.TextDocument.URI, err)
	}
	return nil
}

func (s *Server) handleDidChange(ctx context.Context, raw json.RawMessage) *responseMessage {
	var params didChangeParams
	if err := json.Unmarshal(raw, &params); err != nil {
		s.logger.Printf("decode didChange: %v", err)
		return nil
	}
	if len(params.ContentChanges) == 0 {
		return nil
	}

	latestText := params.ContentChanges[len(params.ContentChanges)-1].Text

	s.mu.Lock()
	document := s.documents[params.TextDocument.URI]
	document.text = latestText
	s.documents[params.TextDocument.URI] = document
	s.mu.Unlock()

	if err := s.analyze(ctx, params.TextDocument.URI, document.language, latestText); err != nil {
		s.logger.Printf("analyze didChange %s: %v", params.TextDocument.URI, err)
	}
	return nil
}

func (s *Server) handleDidClose(raw json.RawMessage) {
	var params didCloseParams
	if err := json.Unmarshal(raw, &params); err != nil {
		s.logger.Printf("decode didClose: %v", err)
		return
	}

	s.mu.Lock()
	delete(s.documents, params.TextDocument.URI)
	s.mu.Unlock()
}

func (s *Server) handleRename(id json.RawMessage, raw json.RawMessage) *responseMessage {
	var params renameParams
	if err := json.Unmarshal(raw, &params); err != nil {
		return invalidParamsResponse(id, err)
	}
	if !isJavaIdentifier(params.NewName) {
		return &responseMessage{
			JSONRPC: "2.0",
			ID:      id,
			Error: &responseError{
				Code:    -32602,
				Message: "newName must be a valid Java identifier",
			},
		}
	}

	text, err := s.documentText(params.TextDocument.URI)
	if err != nil {
		return internalErrorResponse(id, err)
	}
	oldName, err := wordAtPosition(text, params.Position)
	if err != nil {
		return &responseMessage{
			JSONRPC: "2.0",
			ID:      id,
			Error: &responseError{
				Code:    -32602,
				Message: err.Error(),
			},
		}
	}
	if oldName == params.NewName {
		return &responseMessage{
			JSONRPC: "2.0",
			ID:      id,
			Result:  WorkspaceEdit{Changes: map[string][]TextEdit{}},
		}
	}

	root := s.workspaceRootForURI(params.TextDocument.URI)
	changes, err := s.renameWorkspaceDocuments(root, params.TextDocument.URI, oldName, params.NewName)
	if err != nil {
		return internalErrorResponse(id, err)
	}

	return &responseMessage{
		JSONRPC: "2.0",
		ID:      id,
		Result: WorkspaceEdit{
			Changes: changes,
		},
	}
}

func (s *Server) handleSignatureHelp(id json.RawMessage, raw json.RawMessage) *responseMessage {
	var params signatureHelpParams
	if err := json.Unmarshal(raw, &params); err != nil {
		return invalidParamsResponse(id, err)
	}

	text, err := s.documentText(params.TextDocument.URI)
	if err != nil {
		return internalErrorResponse(id, err)
	}

	result, err := signatureHelpAtPosition(text, params.Position)
	if err != nil {
		return &responseMessage{
			JSONRPC: "2.0",
			ID:      id,
			Error: &responseError{
				Code:    -32602,
				Message: err.Error(),
			},
		}
	}

	return &responseMessage{
		JSONRPC: "2.0",
		ID:      id,
		Result:  result,
	}
}

func (s *Server) handleDidRenameFiles(ctx context.Context, raw json.RawMessage) {
	var params didRenameFilesParams
	if err := json.Unmarshal(raw, &params); err != nil {
		s.logger.Printf("decode didRenameFiles: %v", err)
		return
	}

	s.mu.Lock()
	renamedDocs := make([]struct {
		uri string
		doc openedDocument
	}, 0, len(params.Files))
	for _, file := range params.Files {
		doc, ok := s.documents[file.OldURI]
		if ok {
			delete(s.documents, file.OldURI)
			s.documents[file.NewURI] = doc
			renamedDocs = append(renamedDocs, struct {
				uri string
				doc openedDocument
			}{uri: file.NewURI, doc: doc})
		}
	}
	s.mu.Unlock()

	for _, renamed := range renamedDocs {
		if err := s.analyze(ctx, renamed.uri, renamed.doc.language, renamed.doc.text); err != nil {
			s.logger.Printf("analyze didRenameFiles %s: %v", renamed.uri, err)
		}
	}
}

func (s *Server) analyze(ctx context.Context, uri, language, text string) error {
	_, err := s.analyzer.Analyze(ctx, syntax.Document{
		URI:      uri,
		Language: language,
		Text:     text,
	})
	return err
}

func invalidParamsResponse(id json.RawMessage, err error) *responseMessage {
	return &responseMessage{
		JSONRPC: "2.0",
		ID:      id,
		Error: &responseError{
			Code:    -32602,
			Message: fmt.Sprintf("invalid params: %v", err),
		},
	}
}

func internalErrorResponse(id json.RawMessage, err error) *responseMessage {
	return &responseMessage{
		JSONRPC: "2.0",
		ID:      id,
		Error: &responseError{
			Code:    -32603,
			Message: err.Error(),
		},
	}
}

func readMessage(reader *bufio.Reader) (incomingMessage, error) {
	contentLength := 0
	for {
		line, err := reader.ReadString('\n')
		if err != nil {
			return incomingMessage{}, err
		}
		trimmed := strings.TrimSpace(line)
		if trimmed == "" {
			break
		}
		if strings.HasPrefix(strings.ToLower(trimmed), "content-length:") {
			value := strings.TrimSpace(trimmed[len("content-length:"):])
			length, err := strconv.Atoi(value)
			if err != nil {
				return incomingMessage{}, fmt.Errorf("invalid content length %q: %w", value, err)
			}
			contentLength = length
		}
	}

	if contentLength <= 0 {
		return incomingMessage{}, errors.New("missing content length")
	}

	payload := make([]byte, contentLength)
	if _, err := io.ReadFull(reader, payload); err != nil {
		return incomingMessage{}, fmt.Errorf("read payload: %w", err)
	}

	var message incomingMessage
	if err := json.Unmarshal(payload, &message); err != nil {
		return incomingMessage{}, fmt.Errorf("decode message: %w", err)
	}
	return message, nil
}

func writeMessage(out io.Writer, message *responseMessage) error {
	payload, err := json.Marshal(message)
	if err != nil {
		return fmt.Errorf("encode response: %w", err)
	}

	if _, err := fmt.Fprintf(out, "Content-Length: %d\r\n\r\n", len(payload)); err != nil {
		return fmt.Errorf("write header: %w", err)
	}
	if _, err := out.Write(payload); err != nil {
		return fmt.Errorf("write payload: %w", err)
	}
	return nil
}
