package syntax

import "strings"

type Document struct {
	URI      string
	Language string
	Text     string
}

type ParseResult struct {
	Language string
	Classes  []ClassDecl
}

type ClassDecl struct {
	Package          string
	Name             string
	Fields           []FieldDecl
	Methods          []MethodDecl
	GeneratedMethods []MethodDecl
}

func (c ClassDecl) QualifiedName() string {
	if c.Package == "" {
		return c.Name
	}
	return c.Package + "." + c.Name
}

type FieldDecl struct {
	Name        string
	Type        string
	Annotations []string
}

func (f FieldDecl) HasAnnotation(name string) bool {
	for _, annotation := range f.Annotations {
		if annotation == name {
			return true
		}
	}
	return false
}

type MethodDecl struct {
	Name       string
	ReturnType string
	Generated  bool
}

type BinaryExpr struct {
	Operator  string
	LeftType  string
	RightType string
}

func NormalizeTypeName(name string) string {
	name = strings.TrimSpace(name)
	name = strings.TrimPrefix(name, "java.lang.")
	return name
}
