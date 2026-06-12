package java

import (
	"context"
	"regexp"
	"strings"

	"github.com/ProtossGenius/java-lsp/pkg/syntax"
)

var (
	packageRE = regexp.MustCompile(`(?m)^\s*package\s+([A-Za-z_][A-Za-z0-9_.]*)\s*;`)
	classRE   = regexp.MustCompile(`(?m)^\s*(?:public|protected|private|abstract|final|static|sealed|non-sealed|\s)*class\s+([A-Za-z_][A-Za-z0-9_]*)`)
	fieldRE   = regexp.MustCompile(`^\s*(?:public|protected|private|static|final|transient|volatile|\s)+([A-Za-z_][A-Za-z0-9_<>\[\].?]*)\s+([A-Za-z_][A-Za-z0-9_]*)\s*(?:=[^;]*)?;`)
	methodRE  = regexp.MustCompile(`^\s*(?:public|protected|private|static|final|abstract|synchronized|native|\s)+([A-Za-z_][A-Za-z0-9_<>\[\].?]*)\s+([A-Za-z_][A-Za-z0-9_]*)\s*\([^;{)]*\)\s*(?:\{|;)`)
	annoRE    = regexp.MustCompile(`^\s*@([A-Za-z_][A-Za-z0-9_.]*)`)
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
	packageName := ""
	if matches := packageRE.FindStringSubmatch(doc.Text); len(matches) == 2 {
		packageName = matches[1]
	}

	classIndexes := classRE.FindAllStringSubmatchIndex(doc.Text, -1)
	classes := make([]syntax.ClassDecl, 0, len(classIndexes))
	for _, idx := range classIndexes {
		className := doc.Text[idx[2]:idx[3]]
		bodyStart := strings.Index(doc.Text[idx[0]:], "{")
		if bodyStart < 0 {
			continue
		}
		absoluteBodyStart := idx[0] + bodyStart
		bodyEnd := matchBrace(doc.Text, absoluteBodyStart)
		if bodyEnd < 0 {
			continue
		}
		body := doc.Text[absoluteBodyStart+1 : bodyEnd]
		fields, methods := parseMembers(body)
		classes = append(classes, syntax.ClassDecl{
			Package: packageName,
			Name:    className,
			Fields:  fields,
			Methods: methods,
		})
	}

	return syntax.ParseResult{
		Language: "java",
		Classes:  classes,
	}, nil
}

func parseMembers(body string) ([]syntax.FieldDecl, []syntax.MethodDecl) {
	lines := strings.Split(body, "\n")
	fields := make([]syntax.FieldDecl, 0)
	methods := make([]syntax.MethodDecl, 0)
	pendingAnnotations := make([]string, 0)
	depth := 0

	for _, line := range lines {
		trimmed := strings.TrimSpace(line)
		if trimmed == "" {
			depth += braceDelta(line)
			continue
		}
		if depth == 0 {
			if matches := annoRE.FindStringSubmatch(trimmed); len(matches) == 2 {
				pendingAnnotations = append(pendingAnnotations, matches[1])
				depth += braceDelta(line)
				continue
			}
			if matches := fieldRE.FindStringSubmatch(line); len(matches) == 3 {
				fields = append(fields, syntax.FieldDecl{
					Name:        matches[2],
					Type:        syntax.NormalizeTypeName(matches[1]),
					Annotations: cloneStrings(pendingAnnotations),
				})
				pendingAnnotations = pendingAnnotations[:0]
				depth += braceDelta(line)
				continue
			}
			if matches := methodRE.FindStringSubmatch(line); len(matches) == 3 {
				methods = append(methods, syntax.MethodDecl{
					Name:       matches[2],
					ReturnType: syntax.NormalizeTypeName(matches[1]),
				})
				pendingAnnotations = pendingAnnotations[:0]
				depth += braceDelta(line)
				continue
			}
			pendingAnnotations = pendingAnnotations[:0]
		} else {
			pendingAnnotations = pendingAnnotations[:0]
		}
		depth += braceDelta(line)
	}

	return fields, methods
}

func braceDelta(line string) int {
	delta := 0
	for _, ch := range line {
		switch ch {
		case '{':
			delta++
		case '}':
			delta--
		}
	}
	return delta
}

func matchBrace(input string, openIndex int) int {
	depth := 0
	for i := openIndex; i < len(input); i++ {
		switch input[i] {
		case '{':
			depth++
		case '}':
			depth--
			if depth == 0 {
				return i
			}
		}
	}
	return -1
}

func cloneStrings(values []string) []string {
	if len(values) == 0 {
		return nil
	}
	cloned := make([]string, len(values))
	copy(cloned, values)
	return cloned
}
