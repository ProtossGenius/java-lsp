package java

import (
	"context"
	"testing"

	"github.com/ProtossGenius/java-lsp/pkg/syntax"
)

func TestParserParsesClassFieldsMethodsAndAnnotations(t *testing.T) {
	parser := NewParser()
	doc := syntax.Document{
		URI:      "file:///workspace/User.java",
		Language: "java",
		Text: `package demo.user;

public class User {
    @Getter
    private String name;

    private int age;

    public int age() {
        return age;
    }
}
`,
	}

	result, err := parser.Parse(context.Background(), doc)
	if err != nil {
		t.Fatalf("Parse() error = %v", err)
	}
	if result.Language != "java" {
		t.Fatalf("Parse() language = %q, want java", result.Language)
	}
	if len(result.Classes) != 1 {
		t.Fatalf("Parse() classes = %d, want 1", len(result.Classes))
	}

	class := result.Classes[0]
	if class.QualifiedName() != "demo.user.User" {
		t.Fatalf("QualifiedName() = %q, want demo.user.User", class.QualifiedName())
	}
	if len(class.Fields) != 2 {
		t.Fatalf("fields = %d, want 2", len(class.Fields))
	}
	if got := class.Fields[0].Annotations; len(got) != 1 || got[0] != "Getter" {
		t.Fatalf("first field annotations = %#v, want [Getter]", got)
	}
	if len(class.Methods) != 1 || class.Methods[0].Name != "age" {
		t.Fatalf("methods = %#v, want explicit age method", class.Methods)
	}
}
