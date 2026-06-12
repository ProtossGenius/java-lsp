package java

import (
	"strings"
	"unicode"

	"github.com/ProtossGenius/java-lsp/pkg/syntax"
)

type Plugin struct{}

func New() *Plugin {
	return &Plugin{}
}

func (p *Plugin) Language() string {
	return "java"
}

func (p *Plugin) GeneratedMethods(class syntax.ClassDecl) []syntax.MethodDecl {
	existing := make(map[string]struct{}, len(class.Methods)+len(class.GeneratedMethods))
	for _, method := range class.Methods {
		existing[method.Name] = struct{}{}
	}
	for _, method := range class.GeneratedMethods {
		existing[method.Name] = struct{}{}
	}

	methods := make([]syntax.MethodDecl, 0)
	for _, field := range class.Fields {
		if !hasGetterAnnotation(field.Annotations) {
			continue
		}

		name := "get" + upperFirst(field.Name)
		if _, ok := existing[name]; ok {
			continue
		}
		existing[name] = struct{}{}
		methods = append(methods, syntax.MethodDecl{
			Name:       name,
			ReturnType: field.Type,
			Generated:  true,
		})
	}

	return methods
}

func (p *Plugin) InferBinaryExprType(expr syntax.BinaryExpr) (string, bool) {
	left := syntax.NormalizeTypeName(expr.LeftType)
	right := syntax.NormalizeTypeName(expr.RightType)

	if expr.Operator == "+" && (left == "String" || right == "String") {
		return "String", true
	}

	if !isNumeric(left) || !isNumeric(right) {
		return "", false
	}

	if rank(left) >= rank(right) {
		return left, true
	}
	return right, true
}

func hasGetterAnnotation(annotations []string) bool {
	for _, annotation := range annotations {
		if annotation == "Getter" || annotation == "lombok.Getter" {
			return true
		}
	}
	return false
}

func upperFirst(input string) string {
	if input == "" {
		return ""
	}
	runes := []rune(input)
	runes[0] = unicode.ToUpper(runes[0])
	return string(runes)
}

func isNumeric(typeName string) bool {
	switch strings.TrimSpace(typeName) {
	case "byte", "short", "int", "long", "float", "double":
		return true
	default:
		return false
	}
}

func rank(typeName string) int {
	switch strings.TrimSpace(typeName) {
	case "byte":
		return 1
	case "short":
		return 2
	case "int":
		return 3
	case "long":
		return 4
	case "float":
		return 5
	case "double":
		return 6
	default:
		return 0
	}
}
