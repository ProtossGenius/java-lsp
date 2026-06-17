package lsp

import (
	"context"
	"errors"
	"io/fs"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"unicode"
)

func (s *Server) setWorkspaceRoots(params initializeParams) {
	roots := make([]string, 0, len(params.WorkspaceFolders)+1)
	if params.RootURI != "" {
		if path, ok := filePathFromURI(params.RootURI); ok {
			roots = append(roots, path)
		}
	} else if params.RootPath != "" {
		roots = append(roots, filepath.Clean(params.RootPath))
	}
	for _, folder := range params.WorkspaceFolders {
		if path, ok := filePathFromURI(folder.URI); ok {
			roots = append(roots, path)
		}
	}

	sort.Slice(roots, func(i, j int) bool {
		if len(roots[i]) != len(roots[j]) {
			return len(roots[i]) > len(roots[j])
		}
		return roots[i] < roots[j]
	})

	s.mu.Lock()
	s.workspaceRoots = dedupeStrings(roots)
	s.mu.Unlock()
}

func (s *Server) workspaceRootForURI(uri string) string {
	path, ok := filePathFromURI(uri)
	if !ok {
		return ""
	}

	s.mu.RLock()
	defer s.mu.RUnlock()

	for _, root := range s.workspaceRoots {
		if path == root || strings.HasPrefix(path, root+string(filepath.Separator)) {
			return root
		}
	}
	return ""
}

func (s *Server) documentText(uri string) (string, error) {
	s.mu.RLock()
	document, ok := s.documents[uri]
	s.mu.RUnlock()
	if ok {
		return document.text, nil
	}

	path, ok := filePathFromURI(uri)
	if !ok {
		return "", errors.New("unsupported document URI")
	}
	data, err := os.ReadFile(path)
	if err != nil {
		return "", err
	}
	return string(data), nil
}

func (s *Server) renameWorkspaceDocuments(root, requestURI, oldName, newName string) (map[string][]TextEdit, error) {
	text, err := s.documentText(requestURI)
	if err != nil {
		return nil, err
	}

	documents, err := s.workspaceDocuments(root, requestURI, text)
	if err != nil {
		return nil, err
	}

	changes := make(map[string][]TextEdit)
	for uri, text := range documents {
		edits := renameTextEdits(text, oldName, newName)
		if len(edits) > 0 {
			changes[uri] = edits
		}
	}

	return changes, nil
}

func renameTextEditsInLineRange(text, oldName, newName string, startLine, endLine int) []TextEdit {
	lines := strings.Split(text, "\n")
	oldRunes := []rune(oldName)
	edits := make([]TextEdit, 0)

	if startLine < 0 {
		startLine = 0
	}
	if endLine >= len(lines) {
		endLine = len(lines) - 1
	}

	for lineIndex := startLine; lineIndex <= endLine; lineIndex++ {
		lineRunes := []rune(lines[lineIndex])
		for i := 0; i+len(oldRunes) <= len(lineRunes); i++ {
			if !runesEqual(lineRunes[i:i+len(oldRunes)], oldRunes) {
				continue
			}
			if i > 0 && isJavaIdentifierPart(lineRunes[i-1]) {
				continue
			}
			end := i + len(oldRunes)
			if end < len(lineRunes) && isJavaIdentifierPart(lineRunes[end]) {
				continue
			}
			edits = append(edits, TextEdit{
				Range: Range{
					Start: Position{Line: lineIndex, Character: i},
					End:   Position{Line: lineIndex, Character: end},
				},
				NewText: newName,
			})
			i = end - 1
		}
	}

	sort.SliceStable(edits, func(i, j int) bool {
		if edits[i].Range.Start.Line != edits[j].Range.Start.Line {
			return edits[i].Range.Start.Line > edits[j].Range.Start.Line
		}
		return edits[i].Range.Start.Character > edits[j].Range.Start.Character
	})

	return edits
}

func localRenameScope(text string, pos Position, name string) (int, int, bool) {
	methodRanges := methodLineRanges(text)
	for _, methodRange := range methodRanges {
		if pos.Line < methodRange.start || pos.Line > methodRange.end {
			continue
		}
		if !methodIntroducesName(text, methodRange.start, methodRange.end, name) {
			return 0, 0, false
		}
		return methodRange.start, methodRange.end, true
	}
	return 0, 0, false
}

type lineRange struct {
	start int
	end   int
}

func methodLineRanges(text string) []lineRange {
	lines := strings.Split(text, "\n")
	depth := 0
	pendingStart := -1
	activeStart := -1
	ranges := make([]lineRange, 0)
	for lineIndex, line := range lines {
		trimmed := strings.TrimSpace(line)
		if pendingStart < 0 {
			if _, ok := methodSignatureForCompletion(trimmed); ok {
				pendingStart = lineIndex
			}
		}
		for _, r := range line {
			switch r {
			case '{':
				depth++
				if pendingStart >= 0 && depth == 2 {
					activeStart = pendingStart
					pendingStart = -1
				}
			case '}':
				if activeStart >= 0 && depth == 2 {
					ranges = append(ranges, lineRange{start: activeStart, end: lineIndex})
					activeStart = -1
				}
				depth--
			}
		}
	}
	return ranges
}

func methodIntroducesName(text string, methodStartLine, methodEndLine int, name string) bool {
	lines := strings.Split(text, "\n")
	if methodStartLine < 0 || methodStartLine >= len(lines) {
		return false
	}
	signature := strings.TrimSpace(lines[methodStartLine])
	for lineIndex := methodStartLine + 1; lineIndex < len(lines) && !looksLikeFinishedMethodSignature(signature); lineIndex++ {
		signature += " " + strings.TrimSpace(lines[lineIndex])
	}
	if method, ok := methodSignatureForCompletion(compactWhitespace(signature)); ok {
		for _, param := range method.params {
			if param.name == name {
				return true
			}
		}
	}
	if methodEndLine >= len(lines) {
		methodEndLine = len(lines) - 1
	}
	for lineIndex := methodStartLine; lineIndex <= methodEndLine; lineIndex++ {
		matches := fieldDeclRE.FindStringSubmatch(lines[lineIndex])
		if len(matches) == 3 && matches[2] == name {
			return true
		}
	}
	return false
}

func (s *Server) workspaceDocuments(root, requestURI, requestText string) (map[string]string, error) {
	documents := make(map[string]string)

	if root == "" {
		documents[requestURI] = requestText
		return documents, nil
	}

	err := filepath.WalkDir(root, func(path string, d fs.DirEntry, walkErr error) error {
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
		text, err := s.documentText(uri)
		if err != nil {
			return err
		}
		documents[uri] = text
		return nil
	})
	if err != nil {
		return nil, err
	}

	return documents, nil
}

func filePathFromURI(uri string) (string, bool) {
	if strings.HasPrefix(uri, "file://") {
		path := strings.TrimPrefix(uri, "file://")
		if path == "" {
			return "", false
		}
		return filepath.Clean(path), true
	}
	if uri == "" {
		return "", false
	}
	return filepath.Clean(uri), true
}

func fileURIFromPath(path string) string {
	cleaned := filepath.Clean(path)
	if strings.HasPrefix(cleaned, "/") {
		return "file://" + cleaned
	}
	return cleaned
}

func dedupeStrings(values []string) []string {
	seen := make(map[string]struct{}, len(values))
	deduped := make([]string, 0, len(values))
	for _, value := range values {
		if _, ok := seen[value]; ok {
			continue
		}
		seen[value] = struct{}{}
		deduped = append(deduped, value)
	}
	return deduped
}

func isJavaIdentifier(name string) bool {
	if name == "" {
		return false
	}
	for i, r := range name {
		if i == 0 {
			if !isJavaIdentifierStart(r) {
				return false
			}
			continue
		}
		if !isJavaIdentifierPart(r) {
			return false
		}
	}
	return true
}

func isJavaIdentifierStart(r rune) bool {
	return r == '_' || r == '$' || unicode.IsLetter(r)
}

func isJavaIdentifierPart(r rune) bool {
	return isJavaIdentifierStart(r) || unicode.IsDigit(r)
}

func wordAtPosition(text string, pos Position) (string, error) {
	lines := strings.Split(text, "\n")
	if pos.Line < 0 || pos.Line >= len(lines) {
		return "", errors.New("position line is out of range")
	}

	runes := []rune(lines[pos.Line])
	if len(runes) == 0 {
		return "", errors.New("position is on an empty line")
	}

	index := pos.Character
	if index < 0 {
		return "", errors.New("position character is out of range")
	}
	if index >= len(runes) {
		index = len(runes) - 1
	}
	if !isJavaIdentifierPart(runes[index]) && index > 0 && isJavaIdentifierPart(runes[index-1]) {
		index--
	}
	if !isJavaIdentifierPart(runes[index]) {
		return "", errors.New("position does not point to a Java identifier")
	}

	start := index
	for start > 0 && isJavaIdentifierPart(runes[start-1]) {
		start--
	}
	end := index + 1
	for end < len(runes) && isJavaIdentifierPart(runes[end]) {
		end++
	}
	return string(runes[start:end]), nil
}

func renameTextEdits(text, oldName, newName string) []TextEdit {
	lines := strings.Split(text, "\n")
	oldRunes := []rune(oldName)
	if len(oldRunes) == 0 {
		return nil
	}

	edits := make([]TextEdit, 0)
	for lineIndex, line := range lines {
		lineRunes := []rune(line)
		for i := 0; i+len(oldRunes) <= len(lineRunes); i++ {
			if !runesEqual(lineRunes[i:i+len(oldRunes)], oldRunes) {
				continue
			}
			if i > 0 && isJavaIdentifierPart(lineRunes[i-1]) {
				continue
			}
			end := i + len(oldRunes)
			if end < len(lineRunes) && isJavaIdentifierPart(lineRunes[end]) {
				continue
			}

			edits = append(edits, TextEdit{
				Range: Range{
					Start: Position{Line: lineIndex, Character: i},
					End:   Position{Line: lineIndex, Character: end},
				},
				NewText: newName,
			})
			i = end - 1
		}
	}

	sort.SliceStable(edits, func(i, j int) bool {
		if edits[i].Range.Start.Line != edits[j].Range.Start.Line {
			return edits[i].Range.Start.Line > edits[j].Range.Start.Line
		}
		return edits[i].Range.Start.Character > edits[j].Range.Start.Character
	})

	return edits
}

func runesEqual(left, right []rune) bool {
	if len(left) != len(right) {
		return false
	}
	for i := range left {
		if left[i] != right[i] {
			return false
		}
	}
	return true
}

func (s *Server) reindexDocument(ctx context.Context, uri string) error {
	s.mu.RLock()
	document, ok := s.documents[uri]
	s.mu.RUnlock()
	if !ok {
		return nil
	}
	return s.analyze(ctx, uri, document.language, document.text)
}
