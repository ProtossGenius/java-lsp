package storage

import (
	"context"
	"sync"
)

type MemoryStore struct {
	mu         sync.RWMutex
	classes    map[string]ClassSnapshot
	references map[string]Reference
}

func NewMemoryStore() *MemoryStore {
	return &MemoryStore{
		classes:    make(map[string]ClassSnapshot),
		references: make(map[string]Reference),
	}
}

func (s *MemoryStore) Write(_ context.Context, snapshot Snapshot) error {
	s.mu.Lock()
	defer s.mu.Unlock()

	for _, class := range snapshot.Classes {
		s.classes[class.QualifiedName] = class
	}
	for _, ref := range snapshot.References {
		key := ref.SourceClass + ":" + ref.Kind + ":" + ref.MemberName
		s.references[key] = ref
	}

	return nil
}

func (s *MemoryStore) LoadClass(_ context.Context, qualifiedName string) (ClassSnapshot, bool, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()

	class, ok := s.classes[qualifiedName]
	return class, ok, nil
}

func (s *MemoryStore) ListClasses(_ context.Context) ([]ClassSnapshot, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()

	classes := make([]ClassSnapshot, 0, len(s.classes))
	for _, class := range s.classes {
		classes = append(classes, class)
	}
	return classes, nil
}

func (s *MemoryStore) Close() error {
	return nil
}
