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

func workspaceReferences(root, symbol string, includeDeclaration bool) []Location {
	if root == "" || symbol == "" {
		return nil
	}
	documents, err := workspaceDocumentsForRoot(root, "", "")
	if err != nil {
		return nil
	}
	locations := make([]Location, 0)
	for uri, content := range documents {
		path, ok := filePathFromURI(uri)
		if !ok {
			continue
		}
		for _, location := range findIdentifierLocations(path, content, symbol) {
			if !includeDeclaration && looksLikeDeclarationLine(content, location.Range.Start.Line, symbol) {
				continue
			}
			locations = append(locations, location)
		}
	}
	sort.Slice(locations, func(i, j int) bool {
		if locations[i].URI != locations[j].URI {
			return locations[i].URI < locations[j].URI
		}
		if locations[i].Range.Start.Line != locations[j].Range.Start.Line {
			return locations[i].Range.Start.Line < locations[j].Range.Start.Line
		}
		return locations[i].Range.Start.Character < locations[j].Range.Start.Character
	})
	return locations
}

type moduleClasspath struct {
	moduleRoot  string
	reactorRoot string
	jars        []jarArtifact
}

func methodSymbolAtPosition(text string, pos Position) (methodSymbol, bool) {
	lines := strings.Split(text, "\n")
	if pos.Line < 0 || pos.Line >= len(lines) {
		return methodSymbol{}, false
	}
	line := lines[pos.Line]
	method, ok := methodSignatureForCompletion(strings.TrimSpace(line))
	if !ok {
		return methodSymbol{}, false
	}
	identifier, err := wordAtPosition(text, pos)
	if err != nil || identifier != method.name {
		return methodSymbol{}, false
	}
	ctx := parseSourceContext(text)
	typeName := parseDeclaredTypeName(text)
	if typeName == "" {
		return methodSymbol{}, false
	}
	fqcn := typeName
	if ctx.packageName != "" {
		fqcn = ctx.packageName + "." + typeName
	}
	isInterface := strings.Contains(firstTypeHeader(text), "interface ")
	return methodSymbol{
		fqcn:        fqcn,
		typeName:    typeName,
		methodName:  method.name,
		isInterface: isInterface,
	}, true
}

func (r *navigationResolver) declarationsForMethod(ctx context.Context, root, path string, method methodSymbol) ([]Location, error) {
	header := firstTypeHeader(readFileString(path))
	for _, candidate := range splitTypeList(header) {
		target := resolveTypeName(parseSourceContext(readFileString(path)), candidate)
		location, err := r.locationForTypeMember(ctx, root, path, target, method.methodName)
		if err == nil {
			return []Location{location}, nil
		}
	}
	location, err := r.locationForTypeMember(ctx, root, path, method.fqcn, method.methodName)
	if err != nil {
		return nil, nil
	}
	return []Location{location}, nil
}

func findIdentifierLocations(path, content, symbol string) []Location {
	lines := strings.Split(content, "\n")
	needle := []rune(symbol)
	locations := make([]Location, 0)
	for lineIndex, line := range lines {
		runes := []rune(line)
		for i := 0; i+len(needle) <= len(runes); i++ {
			if !runesEqual(runes[i:i+len(needle)], needle) {
				continue
			}
			if i > 0 && isJavaIdentifierPart(runes[i-1]) {
				continue
			}
			end := i + len(needle)
			if end < len(runes) && isJavaIdentifierPart(runes[end]) {
				continue
			}
			locations = append(locations, Location{
				URI: fileURIFromPath(path),
				Range: Range{
					Start: Position{Line: lineIndex, Character: i},
					End:   Position{Line: lineIndex, Character: end},
				},
			})
			i = end - 1
		}
	}
	return locations
}

func looksLikeDeclarationLine(content string, lineIndex int, symbol string) bool {
	lines := strings.Split(content, "\n")
	if lineIndex < 0 || lineIndex >= len(lines) {
		return false
	}
	line := strings.TrimSpace(lines[lineIndex])
	if strings.Contains(line, "class "+symbol) || strings.Contains(line, "interface "+symbol) || strings.Contains(line, "record "+symbol) {
		return true
	}
	if method, ok := methodSignatureForCompletion(line); ok && method.name == symbol {
		return true
	}
	return false
}

func (r *navigationResolver) jdkSourcePathForType(fqcn string) (string, bool, error) {
	if !strings.HasPrefix(fqcn, "java.") && !strings.HasPrefix(fqcn, "javax.") && !strings.HasPrefix(fqcn, "jdk.") && !strings.HasPrefix(fqcn, "sun.") {
		return "", false, nil
	}

	srcZip := detectJDKSrcZip()
	if srcZip != "" {
		modulePath := modulePathForJDKType(fqcn)
		if modulePath != "" {
			found, err := zipContains(srcZip, modulePath)
			if err != nil {
				return "", false, err
			}
			if found {
				sourcePath, err := r.extractSourceFile(srcZip, modulePath)
				if err != nil {
					return "", false, err
				}
				return sourcePath, true, nil
			}
		}
	}

	jmod := detectJavaBaseJmod()
	if jmod != "" {
		outputPath, err := r.decompileClass(jmod, fqcn)
		if err != nil {
			return "", false, err
		}
		return outputPath, true, nil
	}
	return "", false, nil
}

func detectJDKSrcZip() string {
	if javaHome := currentJavaHome(); javaHome != "" {
		srcZip := filepath.Join(javaHome, "lib", "src.zip")
		if _, err := os.Stat(srcZip); err == nil {
			return srcZip
		}
	}
	return ""
}

func detectJavaBaseJmod() string {
	if javaHome := currentJavaHome(); javaHome != "" {
		jmod := filepath.Join(javaHome, "jmods", "java.base.jmod")
		if _, err := os.Stat(jmod); err == nil {
			return jmod
		}
	}
	return ""
}

func currentJavaHome() string {
	if home := os.Getenv("JAVA_HOME"); home != "" {
		return home
	}
	output, err := exec.Command("/usr/libexec/java_home").Output()
	if err == nil {
		return strings.TrimSpace(string(output))
	}
	return ""
}

func modulePathForJDKType(fqcn string) string {
	switch {
	case strings.HasPrefix(fqcn, "java."):
		return "java.base/" + strings.ReplaceAll(fqcn, ".", "/") + ".java"
	case strings.HasPrefix(fqcn, "javax."):
		return "java.management/" + strings.ReplaceAll(fqcn, ".", "/") + ".java"
	default:
		return "java.base/" + strings.ReplaceAll(fqcn, ".", "/") + ".java"
	}
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
	focus    string
}

type methodSymbol struct {
	fqcn        string
	typeName    string
	methodName  string
	isInterface bool
}

type implementationCandidate struct {
	location Location
	score    int
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
		if member.focus == "receiver" {
			location, err := r.locationForTypeDeclaration(ctx, root, req.path, receiverType)
			if err != nil {
				return nil, err
			}
			return []Location{location}, nil
		}
		return r.locationsForTypeMember(ctx, root, req.path, receiverType, member.member)
	}

	identifier, err := wordAtPosition(req.text, req.position)
	if err != nil {
		return nil, err
	}
	if method, ok := methodSymbolAtPosition(req.text, req.position); ok {
		location, err := r.locationForTypeMember(ctx, root, req.path, method.fqcn, method.methodName)
		if err != nil {
			return nil, err
		}
		return []Location{location}, nil
	}
	fqcn := resolveTypeName(contextInfo, identifier)
	if fqcn == "" {
		return nil, errors.New("could not resolve type at cursor")
	}
	location, err := r.locationForTypeDeclaration(ctx, root, req.path, fqcn)
	if err != nil {
		return nil, err
	}
	return []Location{location}, nil
}

func (r *navigationResolver) references(ctx context.Context, root string, req navigationRequest, includeDeclaration bool) ([]Location, error) {
	if member, ok, err := memberAccessAtPosition(req.text, req.position); err != nil {
		return nil, err
	} else if ok {
		return workspaceReferences(root, member.member, includeDeclaration), nil
	}

	if method, ok := methodSymbolAtPosition(req.text, req.position); ok {
		return workspaceReferences(root, method.methodName, includeDeclaration), nil
	}

	identifier, err := wordAtPosition(req.text, req.position)
	if err != nil {
		return nil, err
	}
	return workspaceReferences(root, identifier, includeDeclaration), nil
}

func (r *navigationResolver) declaration(ctx context.Context, root string, req navigationRequest) ([]Location, error) {
	if method, ok := methodSymbolAtPosition(req.text, req.position); ok {
		decls, err := r.declarationsForMethod(ctx, root, req.path, method)
		if err != nil {
			return nil, err
		}
		if len(decls) > 0 {
			return decls, nil
		}
	}
	return r.definition(ctx, root, req)
}

func (r *navigationResolver) implementation(ctx context.Context, root string, req navigationRequest) ([]Location, error) {
	contextInfo := parseSourceContext(req.text)

	if method, ok := methodSymbolAtPosition(req.text, req.position); ok {
		workspaceDocs, err := workspaceDocumentsForRoot(root, req.uri, req.text)
		if err != nil {
			return nil, err
		}
		if method.isInterface {
			workspaceImplementations := workspaceImplementationsForTypeMember(workspaceDocs, method.fqcn, method.methodName)
			if len(workspaceImplementations) > 0 {
				return workspaceImplementations, nil
			}
		}
		location, err := r.locationForTypeMember(ctx, root, req.path, method.fqcn, method.methodName)
		if err != nil {
			return nil, err
		}
		return []Location{location}, nil
	}

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

	workspaceDocs, err := workspaceDocumentsForRoot(root, req.uri, req.text)
	if err != nil {
		return nil, err
	}
	workspaceImplementations := workspaceImplementationsForTypeMember(workspaceDocs, receiverType, member.member)
	if len(workspaceImplementations) > 0 {
		return workspaceImplementations, nil
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

	return r.locationsForTypeMember(ctx, root, req.path, receiverType, member.member)
}

func (r *navigationResolver) locationsForTypeMember(ctx context.Context, root, path, fqcn, member string) ([]Location, error) {
	location, err := r.locationForTypeMember(ctx, root, path, fqcn, member)
	if err != nil {
		return nil, err
	}
	return []Location{location}, nil
}

func (r *navigationResolver) locationForTypeDeclaration(ctx context.Context, root, path, fqcn string) (Location, error) {
	if location, ok, err := workspaceLocationForTypeDeclaration(root, fqcn); err != nil {
		return Location{}, err
	} else if ok {
		return location, nil
	}
	sourcePath, err := r.materializeTypeSource(ctx, path, fqcn)
	if err != nil {
		return Location{}, err
	}
	line := findClassLine(readFileString(sourcePath), simpleName(fqcn))
	return locationForLine(sourcePath, line), nil
}

func (r *navigationResolver) locationForTypeMember(ctx context.Context, root, path, fqcn, member string) (Location, error) {
	if location, ok, err := workspaceLocationForTypeMember(root, fqcn, member); err != nil {
		return Location{}, err
	} else if ok {
		return location, nil
	}
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
	candidates := make([]implementationCandidate, 0)
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
			candidates = append(candidates, implementationCandidate{
				location: locationForLine(sourcePath, line),
				score:    implementationScore(content),
			})
		}

		reader.Close()
	}

	sort.Slice(candidates, func(i, j int) bool {
		if candidates[i].score != candidates[j].score {
			return candidates[i].score > candidates[j].score
		}
		if candidates[i].location.URI != candidates[j].location.URI {
			return candidates[i].location.URI < candidates[j].location.URI
		}
		return candidates[i].location.Range.Start.Line < candidates[j].location.Range.Start.Line
	})

	locations := make([]Location, 0, len(candidates))
	for _, candidate := range candidates {
		locations = append(locations, candidate.location)
	}
	return locations, nil
}

func workspaceLocationForTypeDeclaration(root, fqcn string) (Location, bool, error) {
	if root == "" {
		return Location{}, false, nil
	}
	documents, err := workspaceDocumentsForRoot(root, "", "")
	if err != nil {
		return Location{}, false, err
	}
	for uri, content := range documents {
		ctx := parseSourceContext(content)
		className := parseDeclaredTypeName(content)
		if className == "" {
			continue
		}
		resolved := className
		if ctx.packageName != "" {
			resolved = ctx.packageName + "." + className
		}
		if resolved != fqcn {
			continue
		}
		path, ok := filePathFromURI(uri)
		if !ok {
			continue
		}
		return locationForLine(path, findClassLine(content, className)), true, nil
	}
	return Location{}, false, nil
}

func workspaceLocationForTypeMember(root, fqcn, member string) (Location, bool, error) {
	if root == "" {
		return Location{}, false, nil
	}
	documents, err := workspaceDocumentsForRoot(root, "", "")
	if err != nil {
		return Location{}, false, err
	}
	for uri, content := range documents {
		ctx := parseSourceContext(content)
		className := parseDeclaredTypeName(content)
		if className == "" {
			continue
		}
		resolved := className
		if ctx.packageName != "" {
			resolved = ctx.packageName + "." + className
		}
		if resolved != fqcn {
			continue
		}
		line := findMemberLine(content, member)
		if line == 0 {
			line = findClassLine(content, className)
		}
		path, ok := filePathFromURI(uri)
		if !ok {
			continue
		}
		return locationForLine(path, line), true, nil
	}
	return Location{}, false, nil
}

func workspaceDocumentsForRoot(root, requestURI, requestText string) (map[string]string, error) {
	documents := make(map[string]string)

	if root == "" {
		documents[requestURI] = requestText
		return documents, nil
	}

	err := filepath.WalkDir(root, func(path string, d os.DirEntry, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		if d.IsDir() {
			switch d.Name() {
			case ".git", ".gradle", "build", "target":
				return filepath.SkipDir
			}
			return nil
		}
		if filepath.Ext(path) != ".java" {
			return nil
		}

		uri := fileURIFromPath(path)
		if uri == requestURI {
			documents[uri] = requestText
			return nil
		}
		data, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		documents[uri] = string(data)
		return nil
	})
	if err != nil {
		return nil, err
	}

	return documents, nil
}

func workspaceImplementationsForTypeMember(documents map[string]string, fqcn, member string) []Location {
	candidates := make([]implementationCandidate, 0)
	targetSimple := simpleName(fqcn)

	for uri, content := range documents {
		implementsTarget, resolvedFQCN := sourceImplementsType(content, fqcn, targetSimple)
		if !implementsTarget {
			continue
		}
		line := findMemberLine(content, member)
		if line == 0 {
			line = findClassLine(content, simpleName(resolvedFQCN))
		}
		path, ok := filePathFromURI(uri)
		if !ok {
			continue
		}
		candidates = append(candidates, implementationCandidate{
			location: locationForLine(path, line),
			score:    implementationScore(content),
		})
	}

	sort.Slice(candidates, func(i, j int) bool {
		if candidates[i].score != candidates[j].score {
			return candidates[i].score > candidates[j].score
		}
		if candidates[i].location.URI != candidates[j].location.URI {
			return candidates[i].location.URI < candidates[j].location.URI
		}
		return candidates[i].location.Range.Start.Line < candidates[j].location.Range.Start.Line
	})
	locations := make([]Location, 0, len(candidates))
	for _, candidate := range candidates {
		locations = append(locations, candidate.location)
	}
	return locations
}

func (r *navigationResolver) materializeTypeSource(ctx context.Context, path, fqcn string) (string, error) {
	if jdkPath, ok, err := r.jdkSourcePathForType(fqcn); err != nil {
		return "", err
	} else if ok {
		return jdkPath, nil
	}
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

func (r *navigationResolver) sourcePathForType(ctx context.Context, root, path, fqcn string) (string, error) {
	if location, ok, err := workspaceLocationForTypeDeclaration(root, fqcn); err != nil {
		return "", err
	} else if ok {
		sourcePath, ok := filePathFromURI(location.URI)
		if !ok {
			return "", fmt.Errorf("unsupported workspace URI for %s", fqcn)
		}
		return sourcePath, nil
	}
	return r.materializeTypeSource(ctx, path, fqcn)
}

func (r *navigationResolver) moduleForPath(ctx context.Context, path string) (*moduleClasspath, error) {
	moduleRoot, reactorRoot := findMavenRoots(path)
	if moduleRoot == "" {
		return nil, errors.New("could not find Maven module root for file")
	}
	if reactorRoot == "" {
		reactorRoot = moduleRoot
	}

	r.mu.Lock()
	defer r.mu.Unlock()

	if module, ok := r.classpath[moduleRoot]; ok {
		return module, nil
	}

	jars, err := buildMavenClasspath(ctx, moduleRoot, reactorRoot)
	if err != nil {
		return nil, err
	}
	module := &moduleClasspath{
		moduleRoot:  moduleRoot,
		reactorRoot: reactorRoot,
		jars:        jars,
	}
	r.classpath[moduleRoot] = module
	return module, nil
}

func buildMavenClasspath(ctx context.Context, moduleRoot, reactorRoot string) ([]jarArtifact, error) {
	tempDir, err := os.MkdirTemp("", "java-lsp-classpath-*")
	if err != nil {
		return nil, err
	}
	defer os.RemoveAll(tempDir)

	outputFile := filepath.Join(tempDir, "classpath.txt")
	command := "mvn"
	if wrapper := findMavenWrapper(reactorRoot); wrapper != "" {
		command = wrapper
	}

	args := make([]string, 0, 8)
	if reactorRoot != moduleRoot {
		relativeModule, err := filepath.Rel(reactorRoot, moduleRoot)
		if err != nil {
			return nil, err
		}
		args = append(args, "-pl", filepath.ToSlash(relativeModule), "-am")
	}
	args = append(args,
		"-q",
		"-DskipTests",
		"dependency:build-classpath",
		"-Dmdep.outputFile="+outputFile,
	)
	cmd := exec.CommandContext(ctx, command, args...)
	cmd.Dir = reactorRoot
	var stdout bytes.Buffer
	var stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr
	if err := cmd.Run(); err != nil {
		if data, readErr := os.ReadFile(outputFile); readErr == nil && strings.TrimSpace(string(data)) != "" {
			return parseClasspathArtifacts(string(data)), nil
		}
		output := strings.TrimSpace(strings.Join([]string{stdout.String(), stderr.String()}, "\n"))
		return nil, fmt.Errorf("build Maven classpath: %w (%s)", err, output)
	}

	data, err := os.ReadFile(outputFile)
	if err != nil {
		return nil, err
	}
	return parseClasspathArtifacts(string(data)), nil
}

func parseClasspathArtifacts(classpath string) []jarArtifact {
	entries := strings.Split(strings.TrimSpace(classpath), string(os.PathListSeparator))
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
	return jars
}

func findMavenRoots(path string) (string, string) {
	current := filepath.Dir(path)
	moduleRoot := ""
	reactorRoot := ""
	for {
		if stat, err := os.Stat(filepath.Join(current, "pom.xml")); err == nil && !stat.IsDir() {
			if moduleRoot == "" {
				moduleRoot = current
			}
			reactorRoot = current
		}
		parent := filepath.Dir(current)
		if parent == current {
			return moduleRoot, reactorRoot
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
	if isJavaLangType(name) {
		return "java.lang." + name
	}
	if ctx.packageName != "" {
		return ctx.packageName + "." + name
	}
	return name
}

func isJavaLangType(name string) bool {
	switch name {
	case "String", "Object", "Class", "Throwable", "Exception", "RuntimeException", "IllegalArgumentException",
		"IllegalStateException", "NullPointerException", "System", "Long", "Integer", "Short", "Byte", "Double",
		"Float", "Boolean", "Character", "Void", "Number", "Math", "Enum", "Iterable":
		return true
	default:
		return false
	}
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
	tokenStart := index
	for tokenStart > 0 && isJavaIdentifierPart(lineRunes[tokenStart-1]) {
		tokenStart--
	}
	tokenEnd := index + 1
	for tokenEnd < len(lineRunes) && isJavaIdentifierPart(lineRunes[tokenEnd]) {
		tokenEnd++
	}
	if tokenStart == tokenEnd {
		return memberAccess{}, false, nil
	}

	receiverStart, receiverEnd, memberStart, memberEnd, ok := surroundingMemberAccess(lineRunes, tokenStart, tokenEnd)
	if !ok {
		return memberAccess{}, false, nil
	}
	focus := "member"
	if tokenStart == receiverStart && tokenEnd == receiverEnd {
		focus = "receiver"
	}

	return memberAccess{
		receiver: string(lineRunes[receiverStart:receiverEnd]),
		member:   string(lineRunes[memberStart:memberEnd]),
		focus:    focus,
	}, true, nil
}

func surroundingMemberAccess(lineRunes []rune, tokenStart, tokenEnd int) (int, int, int, int, bool) {
	if tokenEnd < len(lineRunes) && lineRunes[tokenEnd] == '.' {
		receiverStart := tokenStart
		receiverEnd := tokenEnd
		memberStart := tokenEnd + 1
		memberEnd := memberStart
		for memberEnd < len(lineRunes) && isJavaIdentifierPart(lineRunes[memberEnd]) {
			memberEnd++
		}
		if memberStart < memberEnd {
			return receiverStart, receiverEnd, memberStart, memberEnd, true
		}
	}

	if tokenStart > 0 && lineRunes[tokenStart-1] == '.' {
		memberStart := tokenStart
		memberEnd := tokenEnd
		receiverEnd := tokenStart - 1
		receiverStart := receiverEnd
		for receiverStart > 0 && isJavaIdentifierPart(lineRunes[receiverStart-1]) {
			receiverStart--
		}
		if receiverStart < receiverEnd {
			return receiverStart, receiverEnd, memberStart, memberEnd, true
		}
	}

	return 0, 0, 0, 0, false
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
	for _, header := range typeHeaders(content) {
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

func implementationScore(content string) int {
	score := 0
	header := firstTypeHeader(content)
	if strings.Contains(header, "abstract class") {
		score -= 100
	} else if strings.Contains(header, " class ") || strings.HasPrefix(strings.TrimSpace(header), "class ") {
		score += 100
	}
	ctx := parseSourceContext(content)
	if strings.Contains(ctx.packageName, ".helpers") {
		score -= 40
	}
	if strings.Contains(ctx.packageName, "logback") {
		score += 40
	}
	return score
}

func firstTypeHeader(content string) string {
	headers := typeHeaders(content)
	if len(headers) > 0 {
		return headers[0]
	}
	return ""
}

func typeHeaders(content string) []string {
	lines := strings.Split(content, "\n")
	headers := make([]string, 0)
	for i := 0; i < len(lines); i++ {
		trimmed := strings.TrimSpace(lines[i])
		if !strings.Contains(trimmed, "class ") && !strings.Contains(trimmed, "interface ") && !strings.Contains(trimmed, "record ") && !strings.HasPrefix(trimmed, "abstract class ") {
			continue
		}
		header := trimmed
		for !strings.Contains(header, "{") && i+1 < len(lines) {
			i++
			header += " " + strings.TrimSpace(lines[i])
		}
		headers = append(headers, compactWhitespace(header))
	}
	return headers
}

func compactWhitespace(value string) string {
	return strings.Join(strings.Fields(value), " ")
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
