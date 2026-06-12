package java

import (
	"testing"

	"github.com/ProtossGenius/java-lsp/pkg/syntax"
)

func TestGeneratedMethodsCreatesGetterForAnnotatedField(t *testing.T) {
	plugin := New()
	class := syntax.ClassDecl{
		Name: "User",
		Fields: []syntax.FieldDecl{
			{Name: "name", Type: "String", Annotations: []string{"Getter"}},
			{Name: "age", Type: "int"},
		},
	}

	methods := plugin.GeneratedMethods(class)
	if len(methods) != 1 {
		t.Fatalf("GeneratedMethods() count = %d, want 1", len(methods))
	}
	if methods[0].Name != "getName" || methods[0].ReturnType != "String" || !methods[0].Generated {
		t.Fatalf("GeneratedMethods() = %#v, want generated getName(): String", methods[0])
	}
}

func TestInferBinaryExprTypePrefersStringConcatAndNumericPromotion(t *testing.T) {
	plugin := New()

	if got, ok := plugin.InferBinaryExprType(syntax.BinaryExpr{
		Operator:  "+",
		LeftType:  "String",
		RightType: "Integer",
	}); !ok || got != "String" {
		t.Fatalf("InferBinaryExprType() string concat = (%q, %v), want (String, true)", got, ok)
	}

	if got, ok := plugin.InferBinaryExprType(syntax.BinaryExpr{
		Operator:  "+",
		LeftType:  "int",
		RightType: "double",
	}); !ok || got != "double" {
		t.Fatalf("InferBinaryExprType() numeric promotion = (%q, %v), want (double, true)", got, ok)
	}
}
