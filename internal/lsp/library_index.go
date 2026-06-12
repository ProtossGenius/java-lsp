package lsp

import (
	"archive/zip"
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"sort"
	"strings"
)

type processingStatus int

const (
	statusIdle processingStatus = iota
	statusProcessing
	statusDone
	statusFailed
)

type parsedTypeInfo struct {
	sourcePath string
	source     sourceContext
	methods    []completionMethod
}

type parsedTypeState struct {
	status processingStatus
	wait   chan struct{}
	info   *parsedTypeInfo
	err    error
}

type workspaceImportState struct {
	status processingStatus
	wait   chan struct{}
	value  map[string]string
	err    error
}

type jdkIndexState struct {
	status  processingStatus
	wait    chan struct{}
	entries map[string]string
	srcZip  string
	err     error
}

type persistedJDKIndex struct {
	SrcZip  string            `json:"srcZip"`
	Entries map[string]string `json:"entries"`
}

func (r *navigationResolver) typeInfoForType(ctx context.Context, root, path, fqcn string) (*parsedTypeInfo, error) {
	sourcePath, err := r.sourcePathForType(ctx, root, path, fqcn)
	if err != nil {
		return nil, err
	}

	for {
		r.mu.Lock()
		if state, ok := r.parsedTypes[sourcePath]; ok {
			switch state.status {
			case statusDone:
				info := state.info
				err := state.err
				r.mu.Unlock()
				return info, err
			case statusFailed:
				err := state.err
				r.mu.Unlock()
				return nil, err
			case statusProcessing:
				wait := state.wait
				r.mu.Unlock()
				<-wait
				continue
			}
		}

		state := &parsedTypeState{
			status: statusProcessing,
			wait:   make(chan struct{}),
		}
		r.parsedTypes[sourcePath] = state
		r.mu.Unlock()

		info, err := parseTypeInfoFromSource(sourcePath)

		r.mu.Lock()
		if err != nil {
			state.status = statusFailed
			state.err = err
		} else {
			state.status = statusDone
			state.info = info
		}
		close(state.wait)
		r.mu.Unlock()
		return info, err
	}
}

func parseTypeInfoFromSource(sourcePath string) (*parsedTypeInfo, error) {
	content := readFileString(sourcePath)
	return &parsedTypeInfo{
		sourcePath: sourcePath,
		source:     parseSourceContext(content),
		methods:    parseMethodCompletions(content),
	}, nil
}

func (r *navigationResolver) workspaceImportMap(root string) (map[string]string, error) {
	if root == "" {
		return map[string]string{}, nil
	}

	for {
		r.mu.Lock()
		if state, ok := r.workspaceImport[root]; ok {
			switch state.status {
			case statusDone:
				value := state.value
				err := state.err
				r.mu.Unlock()
				return value, err
			case statusFailed:
				err := state.err
				r.mu.Unlock()
				return nil, err
			case statusProcessing:
				wait := state.wait
				r.mu.Unlock()
				<-wait
				continue
			}
		}

		state := &workspaceImportState{
			status: statusProcessing,
			wait:   make(chan struct{}),
		}
		r.workspaceImport[root] = state
		r.mu.Unlock()

		value, err := collectWorkspaceImports(root)

		r.mu.Lock()
		if err != nil {
			state.status = statusFailed
			state.err = err
		} else {
			state.status = statusDone
			state.value = value
		}
		close(state.wait)
		r.mu.Unlock()
		return value, err
	}
}

func collectWorkspaceImports(root string) (map[string]string, error) {
	imports := make(map[string]string)
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
		ctx := parseSourceContext(readFileString(path))
		for simple, fqcn := range ctx.imports {
			if _, exists := imports[simple]; !exists {
				imports[simple] = fqcn
			}
		}
		return nil
	})
	return imports, err
}

func (r *navigationResolver) registerSourceOrigin(sourcePath string, module *moduleClasspath) {
	if module == nil {
		return
	}
	r.mu.Lock()
	defer r.mu.Unlock()
	r.sourceOrigins[filepath.Clean(sourcePath)] = module
}

func (r *navigationResolver) originModuleForPath(path string) *moduleClasspath {
	r.mu.Lock()
	defer r.mu.Unlock()
	return r.sourceOrigins[filepath.Clean(path)]
}

func (r *navigationResolver) ensureJDKIndex() (string, map[string]string, error) {
	for {
		r.mu.Lock()
		state := r.jdkIndex
		switch state.status {
		case statusDone:
			srcZip := state.srcZip
			entries := state.entries
			err := state.err
			r.mu.Unlock()
			return srcZip, entries, err
		case statusFailed:
			err := state.err
			r.mu.Unlock()
			return "", nil, err
		case statusProcessing:
			wait := state.wait
			r.mu.Unlock()
			<-wait
			continue
		default:
			state.status = statusProcessing
			state.wait = make(chan struct{})
			r.mu.Unlock()

			srcZip, entries, err := r.buildJDKIndex()

			r.mu.Lock()
			if err != nil {
				state.status = statusFailed
				state.err = err
			} else {
				state.status = statusDone
				state.srcZip = srcZip
				state.entries = entries
			}
			close(state.wait)
			r.mu.Unlock()
			return srcZip, entries, err
		}
	}
}

func (r *navigationResolver) buildJDKIndex() (string, map[string]string, error) {
	srcZip := detectJDKSrcZip()
	if srcZip == "" {
		return "", nil, os.ErrNotExist
	}

	indexFile := filepath.Join(r.cacheDir, "jdk-index.json")
	if data, err := os.ReadFile(indexFile); err == nil {
		var cached persistedJDKIndex
		if json.Unmarshal(data, &cached) == nil && cached.SrcZip == srcZip && len(cached.Entries) > 0 {
			return srcZip, cached.Entries, nil
		}
	}

	reader, err := zip.OpenReader(srcZip)
	if err != nil {
		return "", nil, err
	}
	defer reader.Close()

	entries := make(map[string]string)
	for _, file := range reader.File {
		if !strings.HasSuffix(file.Name, ".java") {
			continue
		}
		if strings.Count(file.Name, "/") < 2 {
			continue
		}
		entry := strings.TrimSuffix(file.Name, ".java")
		parts := strings.SplitN(entry, "/", 2)
		if len(parts) != 2 {
			continue
		}
		fqcn := strings.ReplaceAll(parts[1], "/", ".")
		entries[fqcn] = file.Name
	}

	if err := os.MkdirAll(filepath.Dir(indexFile), 0o755); err == nil {
		payload, _ := json.MarshalIndent(persistedJDKIndex{
			SrcZip:  srcZip,
			Entries: entries,
		}, "", "  ")
		_ = os.WriteFile(indexFile, payload, 0o644)
	}

	return srcZip, entries, nil
}

func sortImportedEntries(entries map[string]string) []string {
	keys := make([]string, 0, len(entries))
	for key := range entries {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	return keys
}
