package engine

import (
	"context"
	"io/fs"
	"os"
	"path/filepath"
	"sort"

	"github.com/ProtossGenius/java-lsp/pkg/storage"
	"github.com/ProtossGenius/java-lsp/pkg/syntax"
)

// AnalyzeWorkspace walks a project tree, indexes Java sources, and returns the
// merged snapshot that was persisted to the configured store.
func (a *Analyzer) AnalyzeWorkspace(ctx context.Context, root string) (storage.Snapshot, error) {
	javaFiles := make([]string, 0)
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
		if filepath.Ext(path) == ".java" {
			javaFiles = append(javaFiles, path)
		}
		return nil
	})
	if err != nil {
		return storage.Snapshot{}, err
	}

	sort.Strings(javaFiles)

	merged := storage.Snapshot{
		Classes:    make([]storage.ClassSnapshot, 0),
		References: make([]storage.Reference, 0),
	}
	for _, path := range javaFiles {
		contents, err := os.ReadFile(path)
		if err != nil {
			return storage.Snapshot{}, err
		}
		snapshot, err := a.Analyze(ctx, syntax.Document{
			URI:  path,
			Text: string(contents),
		})
		if err != nil {
			return storage.Snapshot{}, err
		}
		merged.Classes = append(merged.Classes, snapshot.Classes...)
		merged.References = append(merged.References, snapshot.References...)
	}

	return merged, nil
}
