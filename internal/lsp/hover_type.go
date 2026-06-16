package lsp

import (
	"context"
	"fmt"
	"strings"
)

func (r *navigationResolver) typeDefinition(ctx context.Context, root string, req navigationRequest) ([]Location, error) {
	source := parseSourceContext(req.text)
	workspaceImports, _ := r.workspaceImportMap(root)
	locals := parseLocalVariables(req.text, req.position)

	if !isImportOrPackagePosition(req.text, req.position) {
		if member, ok, err := memberAccessAtPosition(req.text, req.position); err != nil {
			return nil, err
		} else if ok {
			receiverType, _, err := resolveReceiverTypeWithWorkspace(source, locals, workspaceImports, member.receiver)
			if err != nil {
				return nil, err
			}
			if member.focus == "receiver" {
				location, err := r.locationForTypeDeclaration(ctx, root, req.path, receiverType)
				if err != nil {
					return nil, err
				}
				return []Location{location}, nil
			}
			info, err := r.typeInfoForType(ctx, root, req.path, receiverType)
			if err != nil {
				return nil, err
			}
			method, ok := methodByName(info.methods, member.member)
			if !ok {
				return []Location{}, nil
			}
			typeFQCN := resolvePrimaryTypeNameWithWorkspace(info.source, workspaceImports, method.returnType)
			if typeFQCN == "" || typeFQCN == "void" {
				return []Location{}, nil
			}
			location, err := r.locationForTypeDeclaration(ctx, root, req.path, typeFQCN)
			if err != nil {
				return nil, err
			}
			return []Location{location}, nil
		}
	}

	if method, ok := methodSymbolAtPosition(req.text, req.position); ok {
		info, err := r.typeInfoForType(ctx, root, req.path, method.fqcn)
		if err != nil {
			return nil, err
		}
		resolved, ok := methodByName(info.methods, method.methodName)
		if !ok {
			return []Location{}, nil
		}
		typeFQCN := resolvePrimaryTypeNameWithWorkspace(info.source, workspaceImports, resolved.returnType)
		if typeFQCN == "" || typeFQCN == "void" {
			return []Location{}, nil
		}
		location, err := r.locationForTypeDeclaration(ctx, root, req.path, typeFQCN)
		if err != nil {
			return nil, err
		}
		return []Location{location}, nil
	}

	identifier, err := wordAtPosition(req.text, req.position)
	if err != nil {
		return nil, err
	}
	if typeFQCN, ok := resolveValueType(source, locals, workspaceImports, identifier); ok {
		location, err := r.locationForTypeDeclaration(ctx, root, req.path, typeFQCN)
		if err != nil {
			return nil, err
		}
		return []Location{location}, nil
	}

	typeFQCN := resolveTypeNameWithWorkspace(source, workspaceImports, identifier)
	if typeFQCN == "" {
		return []Location{}, nil
	}
	location, err := r.locationForTypeDeclaration(ctx, root, req.path, typeFQCN)
	if err != nil {
		return nil, err
	}
	return []Location{location}, nil
}

func (r *navigationResolver) hover(ctx context.Context, root string, req navigationRequest) (*Hover, error) {
	source := parseSourceContext(req.text)
	workspaceImports, _ := r.workspaceImportMap(root)
	locals := parseLocalVariables(req.text, req.position)

	if !isImportOrPackagePosition(req.text, req.position) {
		if member, ok, err := memberAccessAtPosition(req.text, req.position); err != nil {
			return nil, err
		} else if ok {
			receiverType, _, err := resolveReceiverTypeWithWorkspace(source, locals, workspaceImports, member.receiver)
			if err != nil {
				return nil, err
			}
			if member.focus == "receiver" {
				return r.hoverForType(ctx, root, req.path, receiverType)
			}
			info, err := r.typeInfoForType(ctx, root, req.path, receiverType)
			if err != nil {
				return nil, err
			}
			if method, ok := methodByName(info.methods, member.member); ok {
				return hoverForMethod(method), nil
			}
			return r.hoverForType(ctx, root, req.path, receiverType)
		}
	}

	if method, ok := methodSymbolAtPosition(req.text, req.position); ok {
		info, err := r.typeInfoForType(ctx, root, req.path, method.fqcn)
		if err != nil {
			return nil, err
		}
		if resolved, ok := methodByName(info.methods, method.methodName); ok {
			return hoverForMethod(resolved), nil
		}
	}

	identifier, err := wordAtPosition(req.text, req.position)
	if err != nil {
		return nil, err
	}
	if typeFQCN, ok := resolveValueType(source, locals, workspaceImports, identifier); ok {
		return hoverForValue(typeFQCN, identifier), nil
	}
	typeFQCN := resolveTypeNameWithWorkspace(source, workspaceImports, identifier)
	if typeFQCN == "" {
		return nil, nil
	}
	return r.hoverForType(ctx, root, req.path, typeFQCN)
}

func (r *navigationResolver) hoverForType(ctx context.Context, root, path, fqcn string) (*Hover, error) {
	info, err := r.typeInfoForType(ctx, root, path, fqcn)
	if err != nil {
		return nil, err
	}
	header := info.typeHeader
	if header == "" {
		header = simpleName(fqcn)
	}
	value := fmt.Sprintf("```java\npackage %s;\n%s\n```", info.source.packageName, header)
	if info.source.packageName == "" {
		value = fmt.Sprintf("```java\n%s\n```", header)
	}
	if info.typeDoc != "" {
		value = info.typeDoc + "\n\n" + value
	}
	return &Hover{
		Contents: MarkupContent{
			Kind:  "markdown",
			Value: value,
		},
	}, nil
}

func hoverForMethod(method completionMethod) *Hover {
	params := make([]string, 0, len(method.params))
	for _, param := range method.params {
		params = append(params, param.typeName+" "+param.name)
	}
	signature := fmt.Sprintf("%s %s(%s)", method.returnType, method.name, strings.Join(params, ", "))
	if method.generated {
		signature += " // generated"
	}
	value := "```java\n" + signature + "\n```"
	if method.documentation != "" {
		value = method.documentation + "\n\n" + value
	}
	return &Hover{
		Contents: MarkupContent{
			Kind:  "markdown",
			Value: value,
		},
	}
}

func hoverForValue(typeFQCN, identifier string) *Hover {
	return &Hover{
		Contents: MarkupContent{
			Kind:  "markdown",
			Value: "```java\n" + typeFQCN + " " + identifier + "\n```",
		},
	}
}

func resolveReceiverTypeWithWorkspace(source sourceContext, locals map[string]string, workspaceImports map[string]string, receiver string) (string, receiverResolutionKind, error) {
	if typeName, ok := locals[receiver]; ok {
		return resolveTypeNameWithWorkspace(source, workspaceImports, typeName), receiverValue, nil
	}
	if typeName, ok := source.fields[receiver]; ok {
		return resolveTypeNameWithWorkspace(source, workspaceImports, typeName), receiverValue, nil
	}
	if receiver == "log" && hasLoggerAnnotation(source.classAnnotations) {
		return "org.slf4j.Logger", receiverValue, nil
	}
	if fqcn, ok := source.imports[receiver]; ok {
		return fqcn, receiverType, nil
	}
	if fqcn, ok := workspaceImports[receiver]; ok {
		return fqcn, receiverType, nil
	}
	if isJavaLangType(receiver) {
		return "java.lang." + receiver, receiverType, nil
	}
	return "", receiverValue, errUnresolvedReceiver(receiver)
}

func resolveValueType(source sourceContext, locals map[string]string, workspaceImports map[string]string, identifier string) (string, bool) {
	if typeName, ok := locals[identifier]; ok {
		return resolveTypeNameWithWorkspace(source, workspaceImports, typeName), true
	}
	if typeName, ok := source.fields[identifier]; ok {
		return resolveTypeNameWithWorkspace(source, workspaceImports, typeName), true
	}
	return "", false
}

func resolveTypeNameWithWorkspace(source sourceContext, workspaceImports map[string]string, name string) string {
	name = strings.TrimSpace(name)
	for _, candidate := range extractTypeNames(name) {
		if candidate == "" {
			continue
		}
		if strings.Contains(candidate, ".") {
			return candidate
		}
		if fqcn, ok := source.imports[candidate]; ok {
			return fqcn
		}
		if fqcn, ok := workspaceImports[candidate]; ok {
			return fqcn
		}
		if isJavaLangType(candidate) {
			return "java.lang." + candidate
		}
		if source.packageName != "" {
			return source.packageName + "." + candidate
		}
		return candidate
	}
	return ""
}

func resolvePrimaryTypeNameWithWorkspace(source sourceContext, workspaceImports map[string]string, name string) string {
	return resolveTypeNameWithWorkspace(source, workspaceImports, name)
}

func methodByName(methods []completionMethod, name string) (completionMethod, bool) {
	for _, method := range methods {
		if method.name == name {
			return method, true
		}
	}
	return completionMethod{}, false
}
