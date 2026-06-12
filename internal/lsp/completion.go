package lsp

import (
	"context"
	"sort"
	"strings"
)

type CompletionList struct {
	IsIncomplete bool             `json:"isIncomplete"`
	Items        []CompletionItem `json:"items"`
}

type CompletionItem struct {
	Label  string `json:"label"`
	Kind   int    `json:"kind,omitempty"`
	Detail string `json:"detail,omitempty"`
}

type completionContext struct {
	source sourceContext
	text   string
	locals map[string]string
}

type completionTarget struct {
	receiver string
	prefix   string
}

func completionAtPosition(ctx context.Context, resolver *navigationResolver, root string, req navigationRequest) (*CompletionList, error) {
	target, memberCompletion, err := completionTargetAtPosition(req.text, req.position)
	if err != nil {
		return nil, err
	}

	parsed := completionContext{
		source: parseSourceContext(req.text),
		text:   req.text,
		locals: parseLocalVariables(req.text, req.position),
	}

	items := make([]CompletionItem, 0)
	if memberCompletion {
		receiverType, err := resolveCompletionReceiverType(parsed, target.receiver)
		if err != nil {
			return &CompletionList{Items: nil}, nil
		}
		members, err := completionItemsForType(ctx, resolver, root, req.path, receiverType, target.prefix)
		if err != nil {
			return nil, err
		}
		items = append(items, members...)
	} else {
		items = append(items, completionItemsForCurrentScope(parsed, target.prefix)...)
	}

	sort.Slice(items, func(i, j int) bool {
		if items[i].Label != items[j].Label {
			return items[i].Label < items[j].Label
		}
		return items[i].Detail < items[j].Detail
	})
	items = dedupeCompletionItems(items)

	return &CompletionList{
		IsIncomplete: false,
		Items:        items,
	}, nil
}

func completionTargetAtPosition(text string, pos Position) (completionTarget, bool, error) {
	lines := strings.Split(text, "\n")
	if pos.Line < 0 || pos.Line >= len(lines) {
		return completionTarget{}, false, nil
	}
	line := []rune(lines[pos.Line])
	if len(line) == 0 {
		return completionTarget{}, false, nil
	}

	cursor := pos.Character
	if cursor < 0 {
		cursor = 0
	}
	if cursor > len(line) {
		cursor = len(line)
	}

	memberEnd := cursor
	memberStart := cursor
	for memberStart > 0 && isJavaIdentifierPart(line[memberStart-1]) {
		memberStart--
	}
	for memberEnd < len(line) && isJavaIdentifierPart(line[memberEnd]) {
		memberEnd++
	}
	prefix := string(line[memberStart:memberEnd])

	dotIndex := memberStart - 1
	if dotIndex >= 0 && line[dotIndex] == '.' {
		receiverEnd := dotIndex
		receiverStart := receiverEnd
		for receiverStart > 0 && isJavaIdentifierPart(line[receiverStart-1]) {
			receiverStart--
		}
		if receiverStart < receiverEnd {
			return completionTarget{
				receiver: string(line[receiverStart:receiverEnd]),
				prefix:   prefix,
			}, true, nil
		}
	}

	if cursor < len(line) && line[cursor] == '.' {
		receiverEnd := cursor
		receiverStart := receiverEnd
		for receiverStart > 0 && isJavaIdentifierPart(line[receiverStart-1]) {
			receiverStart--
		}
		if receiverStart < receiverEnd {
			return completionTarget{
				receiver: string(line[receiverStart:receiverEnd]),
				prefix:   "",
			}, true, nil
		}
	}

	if cursor > 0 && cursor <= len(line) && line[cursor-1] == '.' {
		receiverEnd := cursor - 1
		receiverStart := receiverEnd
		for receiverStart > 0 && isJavaIdentifierPart(line[receiverStart-1]) {
			receiverStart--
		}
		if receiverStart < receiverEnd {
			return completionTarget{
				receiver: string(line[receiverStart:receiverEnd]),
				prefix:   "",
			}, true, nil
		}
	}

	return completionTarget{prefix: prefix}, false, nil
}

func resolveCompletionReceiverType(ctx completionContext, receiver string) (string, error) {
	if typeName, ok := ctx.locals[receiver]; ok {
		return resolveTypeName(ctx.source, typeName), nil
	}
	if typeName, ok := ctx.source.fields[receiver]; ok {
		return resolveTypeName(ctx.source, typeName), nil
	}
	if receiver == "log" && hasLoggerAnnotation(ctx.source.classAnnotations) {
		return "org.slf4j.Logger", nil
	}
	return "", errUnresolvedReceiver(receiver)
}

func completionItemsForCurrentScope(ctx completionContext, prefix string) []CompletionItem {
	items := make([]CompletionItem, 0)
	addIfMatches := func(label, detail string, kind int) {
		if prefix == "" || strings.HasPrefix(label, prefix) {
			items = append(items, CompletionItem{Label: label, Detail: detail, Kind: kind})
		}
	}

	for name, typeName := range ctx.source.fields {
		addIfMatches(name, typeName, 5)
	}
	for name, typeName := range ctx.locals {
		addIfMatches(name, typeName, 6)
	}
	for _, method := range parseMethodItems(extractFirstClassBody(ctx.text)) {
		addIfMatches(method.Label, method.Detail, method.Kind)
	}
	return items
}

func completionItemsForType(ctx context.Context, resolver *navigationResolver, root, path, fqcn, prefix string) ([]CompletionItem, error) {
	sourcePath, err := resolver.sourcePathForType(ctx, root, path, fqcn)
	if err != nil {
		return nil, err
	}
	content := readFileString(sourcePath)
	items := parseMethodItems(content)
	filtered := make([]CompletionItem, 0, len(items))
	for _, item := range items {
		if prefix == "" || strings.HasPrefix(item.Label, prefix) {
			filtered = append(filtered, item)
		}
	}
	return filtered, nil
}

func parseMethodItems(content string) []CompletionItem {
	lines := strings.Split(content, "\n")
	items := make([]CompletionItem, 0)
	for _, line := range lines {
		trimmed := strings.TrimSpace(line)
		if strings.HasPrefix(trimmed, "//") || strings.HasPrefix(trimmed, "*") {
			continue
		}
		matches := methodSignatureForCompletion(trimmed)
		if matches.label == "" {
			continue
		}
		items = append(items, CompletionItem{
			Label:  matches.label,
			Detail: matches.detail,
			Kind:   2,
		})
	}
	return items
}

type completionMethodMatch struct {
	label  string
	detail string
}

func methodSignatureForCompletion(line string) completionMethodMatch {
	if strings.Contains(line, " class ") || strings.Contains(line, " interface ") || strings.Contains(line, " record ") {
		return completionMethodMatch{}
	}
	if !strings.Contains(line, "(") || !strings.Contains(line, ")") {
		return completionMethodMatch{}
	}
	open := strings.Index(line, "(")
	before := strings.TrimSpace(line[:open])
	fields := strings.Fields(before)
	if len(fields) < 2 {
		return completionMethodMatch{}
	}
	name := fields[len(fields)-1]
	if strings.Contains(name, ".") || !isJavaIdentifier(name) {
		return completionMethodMatch{}
	}
	returnType := fields[len(fields)-2]
	params := line[open+1:]
	if close := strings.Index(params, ")"); close >= 0 {
		params = params[:close]
	}
	return completionMethodMatch{
		label:  name,
		detail: strings.TrimSpace(returnType + "(" + params + ")"),
	}
}

func parseLocalVariables(text string, pos Position) map[string]string {
	lines := strings.Split(text, "\n")
	limit := pos.Line
	if limit >= len(lines) {
		limit = len(lines) - 1
	}
	locals := make(map[string]string)
	for i := 0; i <= limit; i++ {
		line := strings.TrimSpace(lines[i])
		if strings.HasPrefix(line, "for ") || strings.HasPrefix(line, "if ") || strings.HasPrefix(line, "return ") {
			continue
		}
		if matches := fieldDeclRE.FindStringSubmatch(line); len(matches) == 3 {
			locals[matches[2]] = matches[1]
		}
	}
	return locals
}

func dedupeCompletionItems(items []CompletionItem) []CompletionItem {
	seen := make(map[string]struct{}, len(items))
	deduped := make([]CompletionItem, 0, len(items))
	for _, item := range items {
		key := item.Label + "\x00" + item.Detail
		if _, ok := seen[key]; ok {
			continue
		}
		seen[key] = struct{}{}
		deduped = append(deduped, item)
	}
	return deduped
}

func errUnresolvedReceiver(receiver string) error {
	return &unresolvedReceiverError{receiver: receiver}
}

type unresolvedReceiverError struct {
	receiver string
}

func (e *unresolvedReceiverError) Error() string {
	return "could not resolve receiver type for " + e.receiver
}
