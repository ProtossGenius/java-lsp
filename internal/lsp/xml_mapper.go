package lsp

import (
	"os"
	"path/filepath"
	"regexp"
	"strings"
)

var (
	mapperNamespaceRE = regexp.MustCompile(`<mapper[^>]*namespace\s*=\s*"([^"]+)"`)
	mapperStatementRE = regexp.MustCompile(`<\s*(select|insert|update|delete|sql)\b[^>]*id\s*=\s*"([^"]+)"`)
)

func xmlMapperLocations(root, fqcn, methodName string) ([]Location, error) {
	if root == "" || fqcn == "" || methodName == "" {
		return nil, nil
	}

	locations := make([]Location, 0)
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
		if filepath.Ext(path) != ".xml" {
			return nil
		}
		content := readFileString(path)
		namespace := ""
		if matches := mapperNamespaceRE.FindStringSubmatch(content); len(matches) == 2 {
			namespace = matches[1]
		}
		if namespace != fqcn {
			return nil
		}
		lines := strings.Split(content, "\n")
		for lineIndex, line := range lines {
			if matches := mapperStatementRE.FindStringSubmatch(line); len(matches) == 3 && matches[2] == methodName {
				col := strings.Index(line, matches[2])
				if col < 0 {
					col = 0
				}
				locations = append(locations, Location{
					URI: fileURIFromPath(path),
					Range: Range{
						Start: Position{Line: lineIndex, Character: col},
						End:   Position{Line: lineIndex, Character: col + len(matches[2])},
					},
				})
			}
		}
		return nil
	})
	return locations, err
}
