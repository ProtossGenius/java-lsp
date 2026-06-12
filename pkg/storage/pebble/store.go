package pebble

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/ProtossGenius/java-lsp/pkg/storage"
	"github.com/cockroachdb/pebble"
)

const classPrefix = "class/"

type Store struct {
	db *pebble.DB
}

func NewStore(path string) (*Store, error) {
	if err := os.MkdirAll(path, 0o755); err != nil {
		return nil, fmt.Errorf("create store directory: %w", err)
	}

	db, err := pebble.Open(filepath.Clean(path), &pebble.Options{})
	if err != nil {
		return nil, fmt.Errorf("open pebble: %w", err)
	}

	return &Store{db: db}, nil
}

func (s *Store) Write(_ context.Context, snapshot storage.Snapshot) error {
	batch := s.db.NewBatch()
	defer batch.Close()

	for _, class := range snapshot.Classes {
		payload, err := json.Marshal(class)
		if err != nil {
			return fmt.Errorf("marshal class %s: %w", class.QualifiedName, err)
		}
		if err := batch.Set([]byte(classKey(class.QualifiedName)), payload, pebble.Sync); err != nil {
			return fmt.Errorf("write class %s: %w", class.QualifiedName, err)
		}
	}

	for _, ref := range snapshot.References {
		payload, err := json.Marshal(ref)
		if err != nil {
			return fmt.Errorf("marshal reference %s.%s: %w", ref.SourceClass, ref.MemberName, err)
		}
		if err := batch.Set([]byte(referenceKey(ref)), payload, pebble.Sync); err != nil {
			return fmt.Errorf("write reference %s.%s: %w", ref.SourceClass, ref.MemberName, err)
		}
	}

	if err := batch.Commit(pebble.Sync); err != nil {
		return fmt.Errorf("commit batch: %w", err)
	}

	return nil
}

func (s *Store) LoadClass(_ context.Context, qualifiedName string) (storage.ClassSnapshot, bool, error) {
	value, closer, err := s.db.Get([]byte(classKey(qualifiedName)))
	if err != nil {
		if errors.Is(err, pebble.ErrNotFound) {
			return storage.ClassSnapshot{}, false, nil
		}
		return storage.ClassSnapshot{}, false, fmt.Errorf("read class %s: %w", qualifiedName, err)
	}
	defer closer.Close()

	var snapshot storage.ClassSnapshot
	if err := json.Unmarshal(value, &snapshot); err != nil {
		return storage.ClassSnapshot{}, false, fmt.Errorf("decode class %s: %w", qualifiedName, err)
	}

	return snapshot, true, nil
}

func (s *Store) ListClasses(_ context.Context) ([]storage.ClassSnapshot, error) {
	iter, err := s.db.NewIter(&pebble.IterOptions{
		LowerBound: []byte(classPrefix),
		UpperBound: []byte(nextPrefix(classPrefix)),
	})
	if err != nil {
		return nil, fmt.Errorf("create iterator: %w", err)
	}
	defer iter.Close()

	classes := make([]storage.ClassSnapshot, 0)
	for iter.First(); iter.Valid(); iter.Next() {
		var snapshot storage.ClassSnapshot
		if err := json.Unmarshal(iter.Value(), &snapshot); err != nil {
			return nil, fmt.Errorf("decode class snapshot: %w", err)
		}
		classes = append(classes, snapshot)
	}
	if err := iter.Error(); err != nil {
		return nil, fmt.Errorf("iterate classes: %w", err)
	}

	return classes, nil
}

func (s *Store) Close() error {
	return s.db.Close()
}

func classKey(qualifiedName string) string {
	return classPrefix + qualifiedName
}

func referenceKey(ref storage.Reference) string {
	return fmt.Sprintf(
		"ref/%s/%s/%s",
		sanitizeKeySegment(ref.SourceClass),
		sanitizeKeySegment(ref.Kind),
		sanitizeKeySegment(ref.MemberName),
	)
}

func sanitizeKeySegment(value string) string {
	return strings.NewReplacer("/", "_", " ", "_").Replace(value)
}

func nextPrefix(prefix string) string {
	if prefix == "" {
		return ""
	}
	last := prefix[len(prefix)-1]
	return prefix[:len(prefix)-1] + string(last+1)
}
