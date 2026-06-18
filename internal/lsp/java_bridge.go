package lsp

import (
	"strings"

	javasyntax "github.com/ProtossGenius/java-lsp/pkg/syntax/java"
)

func parseSourceContext(text string) sourceContext {
	file := javasyntax.Analyze(text)
	ctx := sourceContext{
		packageName: file.PackageName,
		imports:     make(map[string]string, len(file.Imports)),
		fields:      make(map[string]string),
	}
	for key, value := range file.Imports {
		ctx.imports[key] = value
	}
	if len(file.Types) == 0 {
		return ctx
	}
	typ := file.Types[0]
	ctx.typeName = typ.Name
	ctx.classAnnotations = cloneStrings(typ.Annotations)
	for _, field := range typ.Fields {
		ctx.fields[field.Name] = field.Type
	}
	return ctx
}

func parseMethodCompletions(content string) []completionMethod {
	file := javasyntax.Analyze(content)
	if len(file.Types) == 0 {
		return nil
	}
	methods := convertTypeMethods(file.Types[0])
	methods = append(methods, generatedSyntheticMethods(parseSourceContext(content))...)
	return dedupeCompletionMethods(methods)
}

func extractTypeHeaderAndDoc(content string) (string, string) {
	file := javasyntax.Analyze(content)
	if len(file.Types) == 0 {
		return "", ""
	}
	return file.Types[0].Header, file.Types[0].Documentation
}

func convertTypeMethods(typ javasyntax.TypeInfo) []completionMethod {
	methods := make([]completionMethod, 0, len(typ.Methods))
	for _, method := range typ.Methods {
		params := make([]completionParam, 0, len(method.Params))
		for _, param := range method.Params {
			params = append(params, completionParam{
				typeName: param.Type,
				name:     param.Name,
			})
		}
		methods = append(methods, completionMethod{
			name:          method.Name,
			returnType:    method.ReturnType,
			params:        params,
			isStatic:      method.IsStatic,
			documentation: method.Documentation,
		})
	}
	return methods
}

func dedupeCompletionMethods(methods []completionMethod) []completionMethod {
	seen := make(map[string]struct{}, len(methods))
	deduped := make([]completionMethod, 0, len(methods))
	for _, method := range methods {
		key := method.name + "\x00" + method.returnType + "\x00" + strings.Join(methodParamNames(method.params), ",")
		if _, ok := seen[key]; ok {
			continue
		}
		seen[key] = struct{}{}
		deduped = append(deduped, method)
	}
	return deduped
}

func methodParamNames(params []completionParam) []string {
	names := make([]string, 0, len(params))
	for _, param := range params {
		names = append(names, param.typeName+":"+param.name)
	}
	return names
}

func cloneStrings(values []string) []string {
	if len(values) == 0 {
		return nil
	}
	cloned := make([]string, len(values))
	copy(cloned, values)
	return cloned
}
