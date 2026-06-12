package engine

import (
	"context"

	"github.com/ProtossGenius/java-lsp/pkg/plugin"
	"github.com/ProtossGenius/java-lsp/pkg/storage"
	"github.com/ProtossGenius/java-lsp/pkg/syntax"
)

type Analyzer struct {
	parsers *syntax.Registry
	plugins *plugin.Manager
	store   storage.Store
}

func NewAnalyzer(parsers *syntax.Registry, plugins *plugin.Manager, store storage.Store) *Analyzer {
	return &Analyzer{
		parsers: parsers,
		plugins: plugins,
		store:   store,
	}
}

func (a *Analyzer) Analyze(ctx context.Context, doc syntax.Document) (storage.Snapshot, error) {
	parser, err := a.parsers.ParserFor(doc)
	if err != nil {
		return storage.Snapshot{}, err
	}

	parseResult, err := parser.Parse(ctx, doc)
	if err != nil {
		return storage.Snapshot{}, err
	}

	langPlugin, err := a.plugins.Plugin(parseResult.Language)
	if err != nil {
		return storage.Snapshot{}, err
	}

	snapshot := storage.Snapshot{
		Classes:    make([]storage.ClassSnapshot, 0, len(parseResult.Classes)),
		References: make([]storage.Reference, 0),
	}

	for _, class := range parseResult.Classes {
		class.GeneratedMethods = langPlugin.GeneratedMethods(class)
		snapshot.Classes = append(snapshot.Classes, storage.ClassSnapshot{
			QualifiedName:    class.QualifiedName(),
			Package:          class.Package,
			Name:             class.Name,
			Fields:           class.Fields,
			Methods:          class.Methods,
			GeneratedMethods: class.GeneratedMethods,
		})
		snapshot.References = append(snapshot.References, collectReferences(class)...)
	}

	if err := a.store.Write(ctx, snapshot); err != nil {
		return storage.Snapshot{}, err
	}

	return snapshot, nil
}

func (a *Analyzer) InferBinaryExprType(language string, expr syntax.BinaryExpr) (string, bool, error) {
	langPlugin, err := a.plugins.Plugin(language)
	if err != nil {
		return "", false, err
	}
	result, ok := langPlugin.InferBinaryExprType(expr)
	return result, ok, nil
}

func collectReferences(class syntax.ClassDecl) []storage.Reference {
	refs := make([]storage.Reference, 0, len(class.Fields)+len(class.Methods)+len(class.GeneratedMethods))
	for _, field := range class.Fields {
		refs = append(refs, storage.Reference{
			SourceClass: class.QualifiedName(),
			MemberName:  field.Name,
			TargetType:  field.Type,
			Kind:        "field-type",
		})
	}
	for _, method := range class.Methods {
		refs = append(refs, storage.Reference{
			SourceClass: class.QualifiedName(),
			MemberName:  method.Name,
			TargetType:  method.ReturnType,
			Kind:        "method-return",
		})
	}
	for _, method := range class.GeneratedMethods {
		refs = append(refs, storage.Reference{
			SourceClass: class.QualifiedName(),
			MemberName:  method.Name,
			TargetType:  method.ReturnType,
			Kind:        "generated-method-return",
		})
	}
	return refs
}
