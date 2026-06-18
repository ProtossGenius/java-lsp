package java

import (
	"context"
	"strings"

	"github.com/ProtossGenius/java-lsp/pkg/syntax"
)

type Parser struct{}

func NewParser() *Parser {
	return &Parser{}
}

func (p *Parser) Language() string {
	return "java"
}

func (p *Parser) CanParse(doc syntax.Document) bool {
	return doc.Language == "java" || strings.HasSuffix(strings.ToLower(doc.URI), ".java")
}

func (p *Parser) Parse(_ context.Context, doc syntax.Document) (syntax.ParseResult, error) {
	file := Analyze(doc.Text)
	classes := make([]syntax.ClassDecl, 0, len(file.Types))
	for _, typ := range file.Types {
		if typ.Kind != "class" && typ.Kind != "interface" && typ.Kind != "record" && typ.Kind != "enum" {
			continue
		}
		fields := make([]syntax.FieldDecl, 0, len(typ.Fields))
		for _, field := range typ.Fields {
			fields = append(fields, syntax.FieldDecl{
				Name:        field.Name,
				Type:        field.Type,
				Annotations: cloneStrings(field.Annotations),
			})
		}
		methods := make([]syntax.MethodDecl, 0, len(typ.Methods))
		for _, method := range typ.Methods {
			methods = append(methods, syntax.MethodDecl{
				Name:       method.Name,
				ReturnType: method.ReturnType,
			})
		}
		classes = append(classes, syntax.ClassDecl{
			Package: file.PackageName,
			Name:    typ.Name,
			Fields:  fields,
			Methods: methods,
		})
	}

	return syntax.ParseResult{
		Language: "java",
		Classes:  classes,
	}, nil
}

func cloneStrings(values []string) []string {
	if len(values) == 0 {
		return nil
	}
	cloned := make([]string, len(values))
	copy(cloned, values)
	return cloned
}
