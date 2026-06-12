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
	analyzer  *engine.Analyzer
	logger    *log.Logger
	mu        sync.Mutex
	documents map[string]openedDocument
	shutdown  bool
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

type didOpenParams struct {
	TextDocument struct {
		URI        string `json:"uri"`
		LanguageID string `json:"languageId"`
		Text       string `json:"text"`
	} `json:"textDocument"`
}

type didChangeParams struct {
	TextDocument struct {
		URI string `json:"uri"`
	} `json:"textDocument"`
	ContentChanges []struct {
		Text string `json:"text"`
	} `json:"contentChanges"`
}

type didCloseParams struct {
	TextDocument struct {
		URI string `json:"uri"`
	} `json:"textDocument"`
}

func NewServer(analyzer *engine.Analyzer) *Server {
	return &Server{
		analyzer:  analyzer,
		logger:    log.New(os.Stderr, "java-lsp: ", 0),
		documents: make(map[string]openedDocument),
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
		return false, &responseMessage{
			JSONRPC: "2.0",
			ID:      message.ID,
			Result: map[string]any{
				"capabilities": map[string]any{
					"textDocumentSync": textDocumentSyncFull,
				},
				"serverInfo": map[string]any{
					"name":    "java-lsp",
					"version": "0.1.0",
				},
			},
		}
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

func (s *Server) analyze(ctx context.Context, uri, language, text string) error {
	_, err := s.analyzer.Analyze(ctx, syntax.Document{
		URI:      uri,
		Language: language,
		Text:     text,
	})
	return err
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
