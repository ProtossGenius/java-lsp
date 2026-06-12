package lsp

import (
	"context"
	"fmt"
	"regexp"
	"sort"
	"strings"
	"unicode"
)

const insertTextFormatSnippet = 2

var methodDeclRE = regexp.MustCompile(`^\s*(?:(?:public|protected|private|static|final|abstract|synchronized|native|default)\s+)*([A-Za-z_][A-Za-z0-9_<>\[\].?, ]*)\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(([^)]*)\)\s*(?:\{|;)`)

type CompletionList struct {
	IsIncomplete bool             `json:"isIncomplete"`
	Items        []CompletionItem `json:"items"`
}

type CompletionItem struct {
	Label            string `json:"label"`
	Kind             int    `json:"kind,omitempty"`
	Detail           string `json:"detail,omitempty"`
	InsertText       string `json:"insertText,omitempty"`
	InsertTextFormat int    `json:"insertTextFormat,omitempty"`
	SortText         string `json:"sortText,omitempty"`
	FilterText       string `json:"filterText,omitempty"`
}

type completionContext struct {
	source       sourceContext
	text         string
	locals       map[string]string
	expectedType string
	targetName   string
}

type completionTarget struct {
	receiver string
	prefix   string
}

type completionMethod struct {
	name       string
	returnType string
	params     []completionParam
	generated  bool
}

type completionParam struct {
	typeName string
	name     string
}

func completionAtPosition(ctx context.Context, resolver *navigationResolver, root string, req navigationRequest) (*CompletionList, error) {
	target, memberCompletion, err := completionTargetAtPosition(req.text, req.position)
	if err != nil {
		return nil, err
	}

	expectedType, targetName := parseCompletionExpectation(req.text, req.position)
	parsed := completionContext{
		source:       parseSourceContext(req.text),
		text:         req.text,
		locals:       parseLocalVariables(req.text, req.position),
		expectedType: expectedType,
		targetName:   targetName,
	}

	items := make([]CompletionItem, 0)
	if memberCompletion {
		receiverType, err := resolveCompletionReceiverType(parsed, target.receiver)
		if err != nil {
			return &CompletionList{IsIncomplete: false, Items: []CompletionItem{}}, nil
		}
		members, err := completionItemsForType(ctx, resolver, parsed, root, req.path, receiverType, target.prefix)
		if err != nil {
			return nil, err
		}
		items = append(items, members...)
	} else {
		items = append(items, completionItemsForCurrentScope(parsed, target.prefix)...)
	}

	items = dedupeCompletionItems(items)
	sort.SliceStable(items, func(i, j int) bool {
		if items[i].SortText != items[j].SortText {
			return items[i].SortText < items[j].SortText
		}
		if items[i].Label != items[j].Label {
			return items[i].Label < items[j].Label
		}
		return items[i].Detail < items[j].Detail
	})

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
			items = append(items, CompletionItem{
				Label:      label,
				Detail:     detail,
				Kind:       kind,
				FilterText: label,
				SortText:   sortKey(0, label),
			})
		}
	}

	for name, typeName := range ctx.source.fields {
		addIfMatches(name, typeName, 5)
	}
	for name, typeName := range ctx.locals {
		addIfMatches(name, typeName, 6)
	}

	methods := parseMethodCompletions(ctx.text)
	for _, method := range methods {
		if prefix != "" && !strings.HasPrefix(method.name, prefix) {
			continue
		}
		items = append(items, buildMethodCompletionItem(ctx, method))
	}
	return items
}

func completionItemsForType(ctx context.Context, resolver *navigationResolver, requestCtx completionContext, root, path, fqcn, prefix string) ([]CompletionItem, error) {
	sourcePath, err := resolver.sourcePathForType(ctx, root, path, fqcn)
	if err != nil {
		return nil, err
	}
	content := readFileString(sourcePath)
	methods := parseMethodCompletions(content)
	filtered := make([]CompletionItem, 0, len(methods))
	for _, method := range methods {
		if prefix != "" && !strings.HasPrefix(method.name, prefix) {
			continue
		}
		filtered = append(filtered, buildMethodCompletionItem(requestCtx, method))
	}
	return filtered, nil
}

func parseMethodCompletions(content string) []completionMethod {
	lines := strings.Split(content, "\n")
	methods := make([]completionMethod, 0)
	existing := make(map[string]struct{})
	ctx := parseSourceContext(content)

	for _, line := range lines {
		trimmed := strings.TrimSpace(line)
		if strings.HasPrefix(trimmed, "//") || strings.HasPrefix(trimmed, "*") {
			continue
		}
		method, ok := methodSignatureForCompletion(trimmed)
		if !ok {
			continue
		}
		key := method.name + "\x00" + method.returnType + "\x00" + fmt.Sprint(len(method.params))
		if _, seen := existing[key]; seen {
			continue
		}
		existing[key] = struct{}{}
		methods = append(methods, method)
	}

	for _, method := range generatedAccessorMethods(ctx) {
		key := method.name + "\x00" + method.returnType + "\x00" + fmt.Sprint(len(method.params))
		if _, seen := existing[key]; seen {
			continue
		}
		existing[key] = struct{}{}
		methods = append(methods, method)
	}

	return methods
}

func methodSignatureForCompletion(line string) (completionMethod, bool) {
	matches := methodDeclRE.FindStringSubmatch(line)
	if len(matches) != 4 {
		return completionMethod{}, false
	}
	name := matches[2]
	if strings.Contains(name, ".") || !isJavaIdentifier(name) {
		return completionMethod{}, false
	}
	returnType := compactType(matches[1])
	forbidden := map[string]struct{}{
		"throw": {}, "return": {}, "new": {}, "if": {}, "for": {}, "while": {}, "switch": {}, "catch": {},
	}
	for _, token := range strings.Fields(returnType) {
		if _, ok := forbidden[token]; ok {
			return completionMethod{}, false
		}
	}
	return completionMethod{
		name:       name,
		returnType: returnType,
		params:     parseCompletionParams(matches[3]),
	}, true
}

func parseCompletionParams(raw string) []completionParam {
	raw = strings.TrimSpace(raw)
	if raw == "" {
		return nil
	}
	parts := splitCSV(raw)
	params := make([]completionParam, 0, len(parts))
	for _, part := range parts {
		fields := strings.Fields(strings.TrimSpace(part))
		if len(fields) < 2 {
			continue
		}
		filtered := make([]string, 0, len(fields))
		for _, field := range fields {
			if strings.HasPrefix(field, "@") {
				continue
			}
			switch field {
			case "final":
				continue
			}
			filtered = append(filtered, field)
		}
		if len(filtered) < 2 {
			continue
		}
		name := filtered[len(filtered)-1]
		typeName := compactType(strings.Join(filtered[:len(filtered)-1], " "))
		params = append(params, completionParam{
			typeName: typeName,
			name:     name,
		})
	}
	return params
}

func splitCSV(raw string) []string {
	parts := make([]string, 0)
	depth := 0
	start := 0
	for i, r := range raw {
		switch r {
		case '<':
			depth++
		case '>':
			if depth > 0 {
				depth--
			}
		case ',':
			if depth == 0 {
				parts = append(parts, raw[start:i])
				start = i + 1
			}
		}
	}
	parts = append(parts, raw[start:])
	return parts
}

func generatedAccessorMethods(ctx sourceContext) []completionMethod {
	methods := make([]completionMethod, 0)
	classGetter := hasAnnotation(ctx.classAnnotations, "Data", "lombok.Data", "Getter", "lombok.Getter")
	classSetter := hasAnnotation(ctx.classAnnotations, "Data", "lombok.Data", "Setter", "lombok.Setter")

	for fieldName, fieldType := range ctx.fields {
		getterEnabled := classGetter || fieldHasGeneratedAccessor(ctx, fieldName, "Getter", "lombok.Getter")
		setterEnabled := classSetter || fieldHasGeneratedAccessor(ctx, fieldName, "Setter", "lombok.Setter")
		if getterEnabled {
			methods = append(methods, completionMethod{
				name:       "get" + upperFirst(fieldName),
				returnType: fieldType,
				generated:  true,
			})
		}
		if setterEnabled {
			methods = append(methods, completionMethod{
				name:       "set" + upperFirst(fieldName),
				returnType: "void",
				params: []completionParam{{
					typeName: fieldType,
					name:     fieldName,
				}},
				generated: true,
			})
		}
	}
	return methods
}

func fieldHasGeneratedAccessor(_ sourceContext, _ string, _ ...string) bool {
	return false
}

func hasAnnotation(annotations []string, names ...string) bool {
	for _, annotation := range annotations {
		for _, name := range names {
			if annotation == name {
				return true
			}
		}
	}
	return false
}

func buildMethodCompletionItem(ctx completionContext, method completionMethod) CompletionItem {
	detail := method.returnType + "()"
	if len(method.params) > 0 {
		params := make([]string, 0, len(method.params))
		for _, param := range method.params {
			params = append(params, param.typeName+" "+param.name)
		}
		detail = method.returnType + "(" + strings.Join(params, ", ") + ")"
	}

	insertText := method.name + "()"
	insertFormat := 0
	if len(method.params) > 0 {
		insertFormat = insertTextFormatSnippet
		parts := make([]string, 0, len(method.params))
		for index, param := range method.params {
			placeholder := bestArgumentPlaceholder(ctx, param, index+1)
			parts = append(parts, placeholder)
		}
		insertText = method.name + "(" + strings.Join(parts, ", ") + ")"
	}

	score := methodCompletionScore(ctx, method)
	return CompletionItem{
		Label:            method.name,
		Kind:             2,
		Detail:           detail,
		InsertText:       insertText,
		InsertTextFormat: insertFormat,
		SortText:         sortKey(score, method.name),
		FilterText:       method.name,
	}
}

func bestArgumentPlaceholder(ctx completionContext, param completionParam, index int) string {
	best := param.name
	bestScore := 0
	for name, typeName := range ctx.locals {
		score := argumentMatchScore(param, name, typeName)
		if score > bestScore {
			best = name
			bestScore = score
		}
	}
	return fmt.Sprintf("${%d:%s}", index, best)
}

func argumentMatchScore(param completionParam, candidateName, candidateType string) int {
	score := 0
	if normalizeTypeForMatch(param.typeName) == normalizeTypeForMatch(candidateType) {
		score += 100
	}
	if candidateName == param.name {
		score += 80
	} else if strings.Contains(candidateName, param.name) || strings.Contains(param.name, candidateName) {
		score += 30
	}
	return score
}

func methodCompletionScore(ctx completionContext, method completionMethod) int {
	score := 0
	if ctx.expectedType != "" {
		expected := normalizeTypeForMatch(ctx.expectedType)
		returnType := normalizeTypeForMatch(method.returnType)
		if expected == returnType {
			score += 200
		} else if outerTypeName(expected) == outerTypeName(returnType) {
			score += 120
		}
	}
	if ctx.targetName != "" {
		target := strings.ToLower(ctx.targetName)
		name := strings.ToLower(method.name)
		if name == target || strings.Contains(name, target) {
			score += 40
		} else if strings.Contains(name, singularize(target)) || strings.Contains(name, pluralize(target)) {
			score += 20
		}
	}
	if method.generated {
		score += 10
	}
	return score
}

func parseCompletionExpectation(text string, pos Position) (string, string) {
	lines := strings.Split(text, "\n")
	if pos.Line < 0 || pos.Line >= len(lines) {
		return "", ""
	}
	prefix := lines[pos.Line]
	if pos.Character >= 0 && pos.Character < len([]rune(prefix)) {
		prefix = string([]rune(prefix)[:pos.Character])
	}
	eq := strings.LastIndex(prefix, "=")
	if eq < 0 {
		return "", ""
	}
	left := strings.TrimSpace(prefix[:eq])
	fields := strings.Fields(left)
	if len(fields) < 2 {
		return "", ""
	}
	typeName := compactType(strings.Join(fields[:len(fields)-1], " "))
	varName := fields[len(fields)-1]
	return typeName, varName
}

func parseLocalVariables(text string, pos Position) map[string]string {
	lines := strings.Split(text, "\n")
	limit := pos.Line
	if limit >= len(lines) {
		limit = len(lines) - 1
	}
	locals := make(map[string]string)
	lastMethodParams := []completionParam{}
	for i := 0; i <= limit; i++ {
		line := strings.TrimSpace(lines[i])
		if line == "" || strings.HasPrefix(line, "@") {
			continue
		}
		if method, ok := methodSignatureForCompletion(line); ok {
			lastMethodParams = method.params
			continue
		}
		if strings.HasPrefix(line, "for ") || strings.HasPrefix(line, "if ") || strings.HasPrefix(line, "return ") {
			continue
		}
		if matches := fieldDeclRE.FindStringSubmatch(line); len(matches) == 3 {
			locals[matches[2]] = compactType(matches[1])
		}
	}
	for _, param := range lastMethodParams {
		locals[param.name] = param.typeName
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

func compactType(value string) string {
	return strings.Join(strings.Fields(strings.TrimSpace(value)), " ")
}

func normalizeTypeForMatch(value string) string {
	value = strings.ReplaceAll(compactType(value), " ", "")
	value = strings.TrimPrefix(value, "java.lang.")
	return value
}

func outerTypeName(value string) string {
	if index := strings.Index(value, "<"); index >= 0 {
		return value[:index]
	}
	return value
}

func singularize(value string) string {
	if strings.HasSuffix(value, "s") && len(value) > 1 {
		return strings.TrimSuffix(value, "s")
	}
	return value
}

func pluralize(value string) string {
	if strings.HasSuffix(value, "s") {
		return value
	}
	return value + "s"
}

func sortKey(score int, label string) string {
	return fmt.Sprintf("%04d:%s", 9999-score, label)
}

func upperFirst(value string) string {
	if value == "" {
		return ""
	}
	runes := []rune(value)
	runes[0] = unicode.ToUpper(runes[0])
	return string(runes)
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
