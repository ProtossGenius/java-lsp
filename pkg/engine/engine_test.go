package engine

import (
	"context"
	"path/filepath"
	"testing"

	plugincore "github.com/ProtossGenius/java-lsp/pkg/plugin"
	javaplugin "github.com/ProtossGenius/java-lsp/pkg/plugin/java"
	"github.com/ProtossGenius/java-lsp/pkg/storage"
	"github.com/ProtossGenius/java-lsp/pkg/syntax"
	javasyntax "github.com/ProtossGenius/java-lsp/pkg/syntax/java"
)

func TestAnalyzerPersistsGeneratedMembersAndReferences(t *testing.T) {
	store := storage.NewMemoryStore()
	analyzer := newTestAnalyzer(store)

	snapshot, err := analyzer.Analyze(context.Background(), syntax.Document{
		URI:      "file:///workspace/User.java",
		Language: "java",
		Text: `package demo.user;

public class User {
    @Getter
    private String name;

    public int age() {
        return 42;
    }
}
`,
	})
	if err != nil {
		t.Fatalf("Analyze() error = %v", err)
	}

	if len(snapshot.Classes) != 1 {
		t.Fatalf("snapshot classes = %d, want 1", len(snapshot.Classes))
	}
	if len(snapshot.Classes[0].GeneratedMethods) != 1 || snapshot.Classes[0].GeneratedMethods[0].Name != "getName" {
		t.Fatalf("generated methods = %#v, want getName", snapshot.Classes[0].GeneratedMethods)
	}
	if len(snapshot.References) != 3 {
		t.Fatalf("references = %d, want 3", len(snapshot.References))
	}

	persisted, ok, err := store.LoadClass(context.Background(), "demo.user.User")
	if err != nil {
		t.Fatalf("LoadClass() error = %v", err)
	}
	if !ok {
		t.Fatal("LoadClass() ok = false, want true")
	}
	if len(persisted.GeneratedMethods) != 1 || persisted.GeneratedMethods[0].Name != "getName" {
		t.Fatalf("persisted generated methods = %#v, want getName", persisted.GeneratedMethods)
	}
}

func TestAnalyzerIndexesSpringBootFixtures(t *testing.T) {
	t.Parallel()

	testCases := []struct {
		name               string
		fixtureDir         string
		expectedClassCount int
		checkClass         string
		checkGetter        string
	}{
		{
			name:               "maven",
			fixtureDir:         filepath.Join("..", "..", "testdata", "workspaces", "maven-springboot"),
			expectedClassCount: 4,
			checkClass:         "com.example.demo.user.UserProfile",
			checkGetter:        "getName",
		},
		{
			name:               "gradle",
			fixtureDir:         filepath.Join("..", "..", "testdata", "workspaces", "gradle-springboot"),
			expectedClassCount: 4,
			checkClass:         "com.example.gradledemo.order.OrderSummary",
			checkGetter:        "getOrderNumber",
		},
	}

	for _, tc := range testCases {
		tc := tc
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()

			store := storage.NewMemoryStore()
			analyzer := newTestAnalyzer(store)

			snapshot, err := analyzer.AnalyzeWorkspace(context.Background(), tc.fixtureDir)
			if err != nil {
				t.Fatalf("AnalyzeWorkspace() error = %v", err)
			}
			if len(snapshot.Classes) != tc.expectedClassCount {
				t.Fatalf("AnalyzeWorkspace() class count = %d, want %d", len(snapshot.Classes), tc.expectedClassCount)
			}

			indexed, ok, err := store.LoadClass(context.Background(), tc.checkClass)
			if err != nil {
				t.Fatalf("LoadClass(%q) error = %v", tc.checkClass, err)
			}
			if !ok {
				t.Fatalf("LoadClass(%q) ok = false, want true", tc.checkClass)
			}
			if len(indexed.GeneratedMethods) != 1 || indexed.GeneratedMethods[0].Name != tc.checkGetter {
				t.Fatalf("generated methods = %#v, want [%s]", indexed.GeneratedMethods, tc.checkGetter)
			}
		})
	}
}

func newTestAnalyzer(store storage.Store) *Analyzer {
	return NewAnalyzer(
		syntax.NewRegistry(javasyntax.NewParser()),
		plugincore.NewManager(javaplugin.New()),
		store,
	)
}
