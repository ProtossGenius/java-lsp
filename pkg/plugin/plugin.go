package plugin

import (
	"errors"
	"fmt"

	"github.com/ProtossGenius/java-lsp/pkg/syntax"
)

var ErrPluginNotFound = errors.New("language plugin not found")

type LanguagePlugin interface {
	Language() string
	GeneratedMethods(class syntax.ClassDecl) []syntax.MethodDecl
	InferBinaryExprType(expr syntax.BinaryExpr) (string, bool)
}

type Manager struct {
	plugins map[string]LanguagePlugin
}

func NewManager(plugins ...LanguagePlugin) *Manager {
	manager := &Manager{
		plugins: make(map[string]LanguagePlugin, len(plugins)),
	}
	for _, plugin := range plugins {
		manager.plugins[plugin.Language()] = plugin
	}
	return manager
}

func (m *Manager) Register(plugin LanguagePlugin) {
	m.plugins[plugin.Language()] = plugin
}

func (m *Manager) Plugin(language string) (LanguagePlugin, error) {
	plugin, ok := m.plugins[language]
	if !ok {
		return nil, fmt.Errorf("%w: %s", ErrPluginNotFound, language)
	}
	return plugin, nil
}
