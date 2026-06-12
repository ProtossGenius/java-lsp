package syntax

import (
	"context"
	"errors"
	"fmt"
	"path/filepath"
	"strings"
)

var ErrParserNotFound = errors.New("syntax parser not found")

type Parser interface {
	Language() string
	CanParse(doc Document) bool
	Parse(ctx context.Context, doc Document) (ParseResult, error)
}

type Registry struct {
	parsers map[string]Parser
}

func NewRegistry(parsers ...Parser) *Registry {
	registry := &Registry{
		parsers: make(map[string]Parser, len(parsers)),
	}
	for _, parser := range parsers {
		registry.parsers[parser.Language()] = parser
	}
	return registry
}

func (r *Registry) Register(parser Parser) {
	r.parsers[parser.Language()] = parser
}

func (r *Registry) ParserFor(doc Document) (Parser, error) {
	language := doc.Language
	if language == "" {
		language = languageFromURI(doc.URI)
	}
	if parser, ok := r.parsers[language]; ok && parser.CanParse(doc) {
		return parser, nil
	}

	for _, parser := range r.parsers {
		if parser.CanParse(doc) {
			return parser, nil
		}
	}

	return nil, fmt.Errorf("%w: %s", ErrParserNotFound, doc.URI)
}

func languageFromURI(uri string) string {
	switch strings.ToLower(filepath.Ext(uri)) {
	case ".java":
		return "java"
	default:
		return ""
	}
}
