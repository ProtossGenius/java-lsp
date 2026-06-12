package lsp

import (
	"context"
	"fmt"
	"regexp"
	"strings"
)

var importRE = regexp.MustCompile(`^\s*import\s+([A-Za-z_][A-Za-z0-9_.]*)\s*;`)
var newTypeRE = regexp.MustCompile(`\bnew\s+([A-Za-z_][A-Za-z0-9_<>\[\].?]*)\s*\(`)

func diagnosticsForDocument(ctx context.Context, resolver *navigationResolver, root, path, text string) []Diagnostic {
	diagnostics := make([]Diagnostic, 0)
	diagnostics = append(diagnostics, braceDiagnostics(text)...)
	diagnostics = append(diagnostics, unresolvedTypeDiagnostics(ctx, resolver, root, path, text)...)
	return diagnostics
}

func braceDiagnostics(text string) []Diagnostic {
	lines := strings.Split(text, "\n")
	depth := 0
	for lineIndex, line := range lines {
		for col, r := range []rune(line) {
			switch r {
			case '{':
				depth++
			case '}':
				depth--
				if depth < 0 {
					return []Diagnostic{{
						Range: Range{
							Start: Position{Line: lineIndex, Character: col},
							End:   Position{Line: lineIndex, Character: col + 1},
						},
						Severity: 1,
						Source:   "java-lsp",
						Message:  "unexpected closing brace",
					}}
				}
			}
		}
	}
	if depth > 0 && len(lines) > 0 {
		lastLine := len(lines) - 1
		lastCol := len([]rune(lines[lastLine]))
		return []Diagnostic{{
			Range: Range{
				Start: Position{Line: lastLine, Character: lastCol},
				End:   Position{Line: lastLine, Character: lastCol},
			},
			Severity: 1,
			Source:   "java-lsp",
			Message:  "missing closing brace",
		}}
	}
	return nil
}

func unresolvedTypeDiagnostics(ctx context.Context, resolver *navigationResolver, root, path, text string) []Diagnostic {
	diags := make([]Diagnostic, 0)
	source := parseSourceContext(text)
	lines := strings.Split(text, "\n")
	seen := map[string]struct{}{}

	addTypeDiag := func(typeName string, lineIndex int, column int) {
		for _, candidate := range extractTypeNames(typeName) {
			if candidate == "" || isBuiltinJavaType(candidate) {
				continue
			}
			fqcn := resolveTypeName(source, candidate)
			if fqcn == "" {
				continue
			}
			if typeExists(ctx, resolver, root, path, fqcn) {
				continue
			}
			key := fmt.Sprintf("%s:%d:%d", candidate, lineIndex, column)
			if _, ok := seen[key]; ok {
				continue
			}
			seen[key] = struct{}{}
			diags = append(diags, Diagnostic{
				Range: Range{
					Start: Position{Line: lineIndex, Character: column},
					End:   Position{Line: lineIndex, Character: column + len(candidate)},
				},
				Severity: 1,
				Source:   "java-lsp",
				Message:  fmt.Sprintf("unresolved type: %s", candidate),
			})
		}
	}

	for lineIndex, line := range lines {
		if matches := importRE.FindStringSubmatchIndex(line); len(matches) == 4 {
			importPath := line[matches[2]:matches[3]]
			if !typeExists(ctx, resolver, root, path, importPath) {
				simple := simpleName(importPath)
				column := strings.Index(line, simple)
				addTypeDiag(simple, lineIndex, max(column, 0))
			}
		}
		if matches := fieldDeclRE.FindStringSubmatchIndex(line); len(matches) == 6 {
			typeName := line[matches[2]:matches[3]]
			addTypeDiag(typeName, lineIndex, matches[2])
		}
		if matches := methodDeclRE.FindStringSubmatchIndex(line); len(matches) >= 8 {
			returnType := line[matches[2]:matches[3]]
			addTypeDiag(returnType, lineIndex, matches[2])
			params := line[matches[6]:matches[7]]
			for _, param := range parseCompletionParams(params) {
				column := strings.Index(line, param.typeName)
				addTypeDiag(param.typeName, lineIndex, max(column, 0))
			}
		}
		for _, match := range newTypeRE.FindAllStringSubmatchIndex(line, -1) {
			typeName := line[match[2]:match[3]]
			addTypeDiag(typeName, lineIndex, match[2])
		}
	}

	return diags
}

func extractTypeNames(typeName string) []string {
	cleaned := strings.NewReplacer(
		"<", " ",
		">", " ",
		",", " ",
		"[", " ",
		"]", " ",
		"?", " ",
		"extends", " ",
		"super", " ",
	).Replace(typeName)
	fields := strings.Fields(cleaned)
	result := make([]string, 0, len(fields))
	for _, field := range fields {
		field = strings.TrimSpace(field)
		if field != "" {
			result = append(result, field)
		}
	}
	return result
}

func isBuiltinJavaType(typeName string) bool {
	switch typeName {
	case "String", "Object", "Class", "Long", "Integer", "Short", "Byte", "Double", "Float", "Boolean", "Character", "Void",
		"byte", "short", "int", "long", "float", "double", "boolean", "char", "void",
		"List", "Map", "Set", "Optional":
		return true
	default:
		return false
	}
}

func typeExists(ctx context.Context, resolver *navigationResolver, root, path, fqcn string) bool {
	if fqcn == "" {
		return false
	}
	if _, ok, _ := workspaceLocationForTypeDeclaration(root, fqcn); ok {
		return true
	}
	_, err := resolver.sourcePathForType(ctx, root, path, fqcn)
	return err == nil
}

func max(left, right int) int {
	if left > right {
		return left
	}
	return right
}
