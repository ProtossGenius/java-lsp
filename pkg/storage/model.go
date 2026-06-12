package storage

import (
	"context"

	"github.com/ProtossGenius/java-lsp/pkg/syntax"
)

type Snapshot struct {
	Classes    []ClassSnapshot `json:"classes"`
	References []Reference     `json:"references"`
}

type ClassSnapshot struct {
	QualifiedName    string              `json:"qualifiedName"`
	Package          string              `json:"package"`
	Name             string              `json:"name"`
	Fields           []syntax.FieldDecl  `json:"fields"`
	Methods          []syntax.MethodDecl `json:"methods"`
	GeneratedMethods []syntax.MethodDecl `json:"generatedMethods"`
}

type Reference struct {
	SourceClass string `json:"sourceClass"`
	MemberName  string `json:"memberName"`
	TargetType  string `json:"targetType"`
	Kind        string `json:"kind"`
}

type Store interface {
	Write(ctx context.Context, snapshot Snapshot) error
	LoadClass(ctx context.Context, qualifiedName string) (ClassSnapshot, bool, error)
	ListClasses(ctx context.Context) ([]ClassSnapshot, error)
	Close() error
}
