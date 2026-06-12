package lsp

import (
	"archive/zip"
	"bytes"
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"sync"
)

type navigationResolver struct {
	mu        sync.Mutex
	cacheDir  string
	classpath map[string]*moduleClasspath
}

type moduleClasspath struct {
	moduleRoot string
	jars       []jarArtifact
}

type jarArtifact struct {
	jarPath       string
	sourceJarPath string
}

type sourceContext struct {
	packageName      string
	imports          map[string]string
	classAnnotations []string
	fields           map[string]string
}

type navigationRequest struct {
	uri      string
	text     string
	path     string
	position Position
}

type memberAccess struct {
	receiver string
	member   string
}

var fieldDeclRE = regexp.MustCompile(`^\s*(?:public|protected|private|static|final|transient|volatile|\s)+([A-Za-z_][A-Za-z0-9_<>\[\].?]*)\s+([A-Za-z_][A-Za-z0-9_]*)\s*(?:=[^;]*)?;`)

func newNavigationResolver() *navigationResolver {
	cacheDir := filepath.Join(os.TempDir(), "java-lsp-navigation")
	return &navigationResolver{
		cacheDir:  cacheDir,
		classpath: make(map[string]*moduleClasspath),
	}
}

func (r *navigationResolver) definition(ctx context.Context, root string, req navigationRequest) ([]Location, error) {
	contextInfo := parseSourceContext(req.text)

	if member, ok, err := memberAccessAtPosition(req.text, req.position); err != nil {
		return nil, err
	} else if ok {
		receiverType, err := resolveReceiverType(req.text, contextInfo, member.receiver)
		if err != nil {
			return nil, err
		}
		return r.locationsForTypeMember(ctx, req.path, receiverType, member.member)
	}

	identifier, err := wordAtPosition(req.text, req.position)
	if err != nil {
		return nil, err
	}
	fqcn := resolveTypeName(contextInfo, identifier)
	if fqcn == "" {
		return nil, errors.New("could not resolve type at cursor")
	}
	location, err := r.locationForTypeDeclaration(ctx, req.path, fqcn)
	if err != nil {
		return nil, err
	}
	return []Location{location}, nil
}

func (r *navigationResolver) declaration(ctx context.Context, root string, req navigationRequest) ([]Location, error) {
	return r.definition(ctx, root, req)
}

func (r *navigationResolver) implementation(ctx context.Context, root string, req navigationRequest) ([]Location, error) {
	contextInfo := parseSourceContext(req.text)

	member, ok, err := memberAccessAtPosition(req.text, req.position)
	if err != nil {
		return nil, err
	}
	if !ok {
		return nil, errors.New("implementation lookup requires a member access")
	}

	receiverType, err := resolveReceiverType(req.text, contextInfo, member.receiver)
	if err != nil {
		return nil, err
	}

	module, err := r.moduleForPath(ctx, req.path)
	if err != nil {
		return nil, err
	}

	implementations, err := r.implementationsForTypeMember(ctx, module, receiverType, member.member)
	if err != nil {
		return nil, err
	}
	if len(implementations) > 0 {
		return implementations, nil
	}

	return r.locationsForTypeMember(ctx, req.path, receiverType, member.member)
}

func (r *navigationResolver) locationsForTypeMember(ctx context.Context, path, fqcn, member string) ([]Location, error) {
	location, err := r.locationForTypeMember(ctx, path, fqcn, member)
	if err != nil {
		return nil, err
	}
	return []Location{location}, nil
}

func (r *navigationResolver) locationForTypeDeclaration(ctx context.Context, path, fqcn string) (Location, error) {
	sourcePath, err := r.materializeTypeSource(ctx, path, fqcn)
	if err != nil {
		return Location{}, err
	}
	line := findClassLine(readFileString(sourcePath), simpleName(fqcn))
	return locationForLine(sourcePath, line), nil
}

func (r *navigationResolver) locationForTypeMember(ctx context.Context, path, fqcn, member string) (Location, error) {
	sourcePath, err := r.materializeTypeSource(ctx, path, fqcn)
	if err != nil {
		return Location{}, err
	}
	content := readFileString(sourcePath)
	line := findMemberLine(content, member)
	if line == 0 {
		line = findClassLine(content, simpleName(fqcn))
	}
	return locationForLine(sourcePath, line), nil
}

func (r *navigationResolver) implementationsForTypeMember(ctx context.Context, module *moduleClasspath, fqcn, member string) ([]Location, error) {
	locations := make([]Location, 0)
	seen := map[string]struct{}{}
	targetSimple := simpleName(fqcn)

	for _, jar := range module.jars {
		if jar.sourceJarPath == "" {
			continue
		}

		reader, err := zip.OpenReader(jar.sourceJarPath)
		if err != nil {
			return nil, err
		}

		for _, file := range reader.File {
			if !strings.HasSuffix(file.Name, ".java") {
				continue
			}
			content, err := readZipFile(file)
			if err != nil {
				reader.Close()
				return nil, err
			}
			if !strings.Contains(content, member+"(") {
				continue
			}
			implementsTarget, resolvedFQCN := sourceImplementsType(content, fqcn, targetSimple)
			if !implementsTarget {
				continue
			}
			sourcePath, err := r.extractSourceFile(jar.sourceJarPath, file.Name)
			if err != nil {
				reader.Close()
				return nil, err
			}
			key := sourcePath + "#" + member
			if _, ok := seen[key]; ok {
				continue
			}
			seen[key] = struct{}{}
			line := findMemberLine(content, member)
			if line == 0 {
				line = findClassLine(content, simpleName(resolvedFQCN))
			}
			locations = append(locations, locationForLine(sourcePath, line))
		}

		reader.Close()
	}

	sort.Slice(locations, func(i, j int) bool {
		if locations[i].URI != locations[j].URI {
			return locations[i].URI < locations[j].URI
		}
		return locations[i].Range.Start.Line < locations[j].Range.Start.Line
	})

	return locations, nil
}

func (r *navigationResolver) materializeTypeSource(ctx context.Context, path, fqcn string) (string, error) {
	module, err := r.moduleForPath(ctx, path)
	if err != nil {
		return "", err
	}

	relativeSourcePath := strings.ReplaceAll(fqcn, ".", "/") + ".java"
	for _, jar := range module.jars {
		if jar.sourceJarPath == "" {
			continue
		}
		found, err := zipContains(jar.sourceJarPath, relativeSourcePath)
		if err != nil {
			return "", err
		}
		if found {
			return r.extractSourceFile(jar.sourceJarPath, relativeSourcePath)
		}
	}

	for _, jar := range module.jars {
		relativeClassPath := strings.ReplaceAll(fqcn, ".", "/") + ".class"
		found, err := zipContains(jar.jarPath, relativeClassPath)
		if err != nil {
			return "", err
		}
		if found {
			return r.decompileClass(jar.jarPath, fqcn)
		}
	}

	return "", fmt.Errorf("type %s not found on dependency classpath", fqcn)
}

func (r *navigationResolver) moduleForPath(ctx context.Context, path string) (*moduleClasspath, error) {
	moduleRoot := findModuleRoot(path)
	if moduleRoot == "" {
		return nil, errors.New("could not find Maven module root for file")
	}

	r.mu.Lock()
	defer r.mu.Unlock()

	if module, ok := r.classpath[moduleRoot]; ok {
		return module, nil
	}

	jars, err := buildMavenClasspath(ctx, moduleRoot)
	if err != nil {
		return nil, err
	}
	module := &moduleClasspath{
		moduleRoot: moduleRoot,
		jars:       jars,
	}
	r.classpath[moduleRoot] = module
	return module, nil
}

func buildMavenClasspath(ctx context.Context, moduleRoot string) ([]jarArtifact, error) {
	tempDir, err := os.MkdirTemp("", "java-lsp-classpath-*")
	if err != nil {
		return nil, err
	}
	defer os.RemoveAll(tempDir)

	outputFile := filepath.Join(tempDir, "classpath.txt")
	command := "mvn"
	if wrapper := findMavenWrapper(moduleRoot); wrapper != "" {
		command = wrapper
	}

	args := []string{
		"-q",
		"-DskipTests",
		"dependency:build-classpath",
		"-Dmdep.outputFile=" + outputFile,
	}
	cmd := exec.CommandContext(ctx, command, args...)
	cmd.Dir = moduleRoot
	var stderr bytes.Buffer
	cmd.Stderr = &stderr
	if err := cmd.Run(); err != nil {
		return nil, fmt.Errorf("build Maven classpath: %w (%s)", err, strings.TrimSpace(stderr.String()))
	}

	data, err := os.ReadFile(outputFile)
	if err != nil {
		return nil, err
	}
	entries := strings.Split(strings.TrimSpace(string(data)), string(os.PathListSeparator))
	jars := make([]jarArtifact, 0, len(entries))
	for _, entry := range entries {
		entry = strings.TrimSpace(entry)
		if entry == "" || !strings.HasSuffix(entry, ".jar") {
			continue
		}
		jars = append(jars, jarArtifact{
			jarPath:       entry,
			sourceJarPath: sourceJarPathFor(entry),
		})
	}
	return jars, nil
}

func findModuleRoot(path string) string {
	current := filepath.Dir(path)
	for {
		if stat, err := os.Stat(filepath.Join(current, "pom.xml")); err == nil && !stat.IsDir() {
			return current
		}
		parent := filepath.Dir(current)
		if parent == current {
			return ""
		}
		current = parent
	}
}

func findMavenWrapper(moduleRoot string) string {
	current := moduleRoot
	for {
		wrapper := filepath.Join(current, "mvnw")
		if stat, err := os.Stat(wrapper); err == nil && !stat.IsDir() {
			return wrapper
		}
		parent := filepath.Dir(current)
		if parent == current {
			return ""
		}
		current = parent
	}
}

func sourceJarPathFor(jarPath string) string {
	if !strings.HasSuffix(jarPath, ".jar") {
		return ""
	}
	sourceJar := strings.TrimSuffix(jarPath, ".jar") + "-sources.jar"
	if _, err := os.Stat(sourceJar); err == nil {
		return sourceJar
	}
	return ""
}

func zipContains(jarPath, entryName string) (bool, error) {
	reader, err := zip.OpenReader(jarPath)
	if err != nil {
		return false, err
	}
	defer reader.Close()
	for _, file := range reader.File {
		if file.Name == entryName {
			return true, nil
		}
	}
	return false, nil
}

func (r *navigationResolver) extractSourceFile(sourceJarPath, entryName string) (string, error) {
	reader, err := zip.OpenReader(sourceJarPath)
	if err != nil {
		return "", err
	}
	defer reader.Close()

	for _, file := range reader.File {
		if file.Name != entryName {
			continue
		}
		content, err := readZipFile(file)
		if err != nil {
			return "", err
		}
		outputPath := filepath.Join(r.cacheDir, "sources", filepath.Base(sourceJarPath), entryName)
		if err := os.MkdirAll(filepath.Dir(outputPath), 0o755); err != nil {
			return "", err
		}
		if err := os.WriteFile(outputPath, []byte(content), 0o644); err != nil {
			return "", err
		}
		return outputPath, nil
	}

	return "", fmt.Errorf("source entry %s not found in %s", entryName, sourceJarPath)
}

func (r *navigationResolver) decompileClass(jarPath, fqcn string) (string, error) {
	outputPath := filepath.Join(r.cacheDir, "javap", filepath.Base(jarPath), strings.ReplaceAll(fqcn, ".", "_")+".java")
	if data, err := os.ReadFile(outputPath); err == nil && len(data) > 0 {
		return outputPath, nil
	}

	if err := os.MkdirAll(filepath.Dir(outputPath), 0o755); err != nil {
		return "", err
	}
	cmd := exec.Command("javap", "-classpath", jarPath, "-public", fqcn)
	output, err := cmd.CombinedOutput()
	if err != nil {
		return "", fmt.Errorf("javap %s: %w (%s)", fqcn, err, strings.TrimSpace(string(output)))
	}
	if err := os.WriteFile(outputPath, output, 0o644); err != nil {
		return "", err
	}
	return outputPath, nil
}

func parseSourceContext(text string) sourceContext {
	ctx := sourceContext{
		imports: make(map[string]string),
		fields:  make(map[string]string),
	}
	for _, line := range strings.Split(text, "\n") {
		trimmed := strings.TrimSpace(line)
		if strings.HasPrefix(trimmed, "package ") {
			ctx.packageName = strings.TrimSuffix(strings.TrimSpace(strings.TrimPrefix(trimmed, "package ")), ";")
			continue
		}
		if strings.HasPrefix(trimmed, "import ") {
			importPath := strings.TrimSuffix(strings.TrimSpace(strings.TrimPrefix(trimmed, "import ")), ";")
			importPath = strings.TrimPrefix(importPath, "static ")
			ctx.imports[simpleName(importPath)] = importPath
			continue
		}
		if strings.HasPrefix(trimmed, "@") {
			ctx.classAnnotations = append(ctx.classAnnotations, strings.TrimPrefix(strings.Fields(trimmed)[0], "@"))
			continue
		}
		if strings.Contains(trimmed, "class ") || strings.Contains(trimmed, "interface ") || strings.Contains(trimmed, "record ") {
			break
		}
	}
	for _, line := range strings.Split(extractFirstClassBody(text), "\n") {
		matches := fieldDeclRE.FindStringSubmatch(line)
		if len(matches) == 3 {
			ctx.fields[matches[2]] = matches[1]
		}
	}
	return ctx
}

func extractFirstClassBody(text string) string {
	index := strings.Index(text, "{")
	if index < 0 {
		return text
	}
	end := index + 1
	depth := 1
	for end < len(text) {
		switch text[end] {
		case '{':
			depth++
		case '}':
			depth--
			if depth == 0 {
				return text[index+1 : end]
			}
		}
		end++
	}
	return text[index+1:]
}

func resolveReceiverType(text string, ctx sourceContext, receiver string) (string, error) {
	if receiver == "log" && hasLoggerAnnotation(ctx.classAnnotations) {
		return "org.slf4j.Logger", nil
	}
	if typeName, ok := ctx.fields[receiver]; ok {
		fqcn := resolveTypeName(ctx, typeName)
		if fqcn == "" {
			return "", fmt.Errorf("could not resolve field type %s", typeName)
		}
		return fqcn, nil
	}
	return "", fmt.Errorf("could not resolve receiver type for %s", receiver)
}

func hasLoggerAnnotation(annotations []string) bool {
	for _, annotation := range annotations {
		switch annotation {
		case "Slf4j", "lombok.extern.slf4j.Slf4j":
			return true
		}
	}
	return false
}

func resolveTypeName(ctx sourceContext, name string) string {
	name = strings.TrimSpace(name)
	name = strings.TrimSuffix(name, "[]")
	if strings.Contains(name, ".") {
		return name
	}
	if fqcn, ok := ctx.imports[name]; ok {
		return fqcn
	}
	if name == "String" {
		return "java.lang.String"
	}
	if ctx.packageName != "" {
		return ctx.packageName + "." + name
	}
	return name
}

func memberAccessAtPosition(text string, pos Position) (memberAccess, bool, error) {
	lines := strings.Split(text, "\n")
	if pos.Line < 0 || pos.Line >= len(lines) {
		return memberAccess{}, false, errors.New("position line is out of range")
	}

	lineRunes := []rune(lines[pos.Line])
	if len(lineRunes) == 0 {
		return memberAccess{}, false, nil
	}

	index := pos.Character
	if index >= len(lineRunes) {
		index = len(lineRunes) - 1
	}
	if index < 0 {
		return memberAccess{}, false, errors.New("position character is out of range")
	}
	if !isJavaIdentifierPart(lineRunes[index]) && index > 0 && isJavaIdentifierPart(lineRunes[index-1]) {
		index--
	}
	start := index
	for start > 0 && isJavaIdentifierPart(lineRunes[start-1]) {
		start--
	}
	end := index + 1
	for end < len(lineRunes) && isJavaIdentifierPart(lineRunes[end]) {
		end++
	}

	if start == end || start == 0 || lineRunes[start-1] != '.' {
		return memberAccess{}, false, nil
	}

	receiverEnd := start - 1
	receiverStart := receiverEnd
	for receiverStart > 0 && isJavaIdentifierPart(lineRunes[receiverStart-1]) {
		receiverStart--
	}
	if receiverStart == receiverEnd {
		return memberAccess{}, false, nil
	}

	return memberAccess{
		receiver: string(lineRunes[receiverStart:receiverEnd]),
		member:   string(lineRunes[start:end]),
	}, true, nil
}

func readZipFile(file *zip.File) (string, error) {
	reader, err := file.Open()
	if err != nil {
		return "", err
	}
	defer reader.Close()
	data, err := io.ReadAll(reader)
	if err != nil {
		return "", err
	}
	return string(data), nil
}

func sourceImplementsType(content, targetFQCN, targetSimple string) (bool, string) {
	ctx := parseSourceContext(content)
	lines := strings.Split(content, "\n")
	for _, line := range lines {
		if !strings.Contains(line, "class ") && !strings.Contains(line, "interface ") && !strings.Contains(line, "record ") {
			continue
		}
		header := line
		if !strings.Contains(header, "implements") && !strings.Contains(header, "extends") {
			continue
		}

		className := parseDeclaredTypeName(header)
		if className == "" {
			continue
		}
		for _, candidate := range splitTypeList(header) {
			resolved := resolveTypeName(ctx, candidate)
			if resolved == targetFQCN || candidate == targetSimple {
				fqcn := className
				if ctx.packageName != "" {
					fqcn = ctx.packageName + "." + className
				}
				return true, fqcn
			}
		}
	}
	return false, ""
}

func splitTypeList(header string) []string {
	index := strings.Index(header, "implements")
	prefixLen := len("implements")
	if index < 0 {
		index = strings.Index(header, "extends")
		prefixLen = len("extends")
	}
	if index < 0 {
		return nil
	}
	types := strings.Split(header[index+prefixLen:], ",")
	result := make([]string, 0, len(types))
	for _, part := range types {
		part = strings.TrimSpace(strings.TrimSuffix(part, "{"))
		if part != "" {
			result = append(result, strings.Fields(part)[0])
		}
	}
	return result
}

func parseDeclaredTypeName(header string) string {
	for _, keyword := range []string{"class", "interface", "record"} {
		if index := strings.Index(header, keyword+" "); index >= 0 {
			rest := strings.TrimSpace(header[index+len(keyword)+1:])
			if rest == "" {
				return ""
			}
			return strings.Fields(rest)[0]
		}
	}
	return ""
}

func findClassLine(content, className string) int {
	lines := strings.Split(content, "\n")
	for index, line := range lines {
		if strings.Contains(line, "class "+className) || strings.Contains(line, "interface "+className) || strings.Contains(line, "record "+className) {
			return index + 1
		}
	}
	return 1
}

func findMemberLine(content, member string) int {
	lines := strings.Split(content, "\n")
	pattern := member + "("
	for index, line := range lines {
		if strings.Contains(line, pattern) {
			return index + 1
		}
	}
	return 0
}

func locationForLine(path string, line int) Location {
	if line <= 0 {
		line = 1
	}
	return Location{
		URI: fileURIFromPath(path),
		Range: Range{
			Start: Position{Line: line - 1, Character: 0},
			End:   Position{Line: line - 1, Character: 0},
		},
	}
}

func readFileString(path string) string {
	data, err := os.ReadFile(path)
	if err != nil {
		return ""
	}
	return string(data)
}

func simpleName(fqcn string) string {
	if index := strings.LastIndex(fqcn, "."); index >= 0 {
		return fqcn[index+1:]
	}
	return fqcn
}
