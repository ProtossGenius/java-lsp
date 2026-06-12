# Copilot Instructions

## Build, test, and lint

- Build the server with `go build ./...`.
- Run the full test suite with `go test ./...`.
- Run a single package with `go test ./pkg/engine`.
- Run a single test with `go test ./pkg/plugin/java -run TestGeneratedMethodsCreatesGetterForAnnotatedField`.
- Run the LSP capability tests with `go test ./internal/lsp -run 'TestInitializeAdvertisesRenameAndSignatureHelp|TestRenameReturnsWorkspaceEditsAcrossFiles|TestSignatureHelpReturnsStringFormatSignature|TestDependencyDeclarationAndImplementationNavigateIntoSourceJars|TestCompletionReturnsLoggerMembers|TestCompletionIncludesLombokDataAccessorsAndSnippets|TestCompletionRanksMethodsByExpectedType|TestDiagnosticsReportUnresolvedTypes'`.
- Run the full LSP lifecycle fixture tests with `go test ./internal/lsp -run 'TestWorkspaceLifecycleAndNavigationCoverage|TestDependencyDefinitionFallsBackToDecompiledClassWithoutSources|TestServeHandlesShutdownAndExit'`.
- Run the Spring Boot fixture coverage with `go test ./pkg/engine -run TestAnalyzerIndexesSpringBootFixtures`.
- Sync the upstream Java fixtures and regenerate the JDTLS unit-test manifest with `./scripts/sync_upstreams.sh`.
- Verify compile-preserving rename refactors against the large Spring Petclinic project with `./scripts/verify_petclinic_refactor_compile.sh`.
- No project-specific linter is checked in yet. Do not assume `golangci-lint` or another linter is a repository requirement until config is added.

## High-level architecture

- `DESIGN.md` is still the architectural source of truth, and the current code now mirrors it with a first runnable slice.
- `cmd/java-lsp` wires the application together and starts a minimal stdio LSP server.
- `internal/lsp` implements the transport loop and currently handles `initialize`, `shutdown`, `exit`, `textDocument/didOpen`, `textDocument/didChange`, `textDocument/didClose`, `textDocument/definition`, `textDocument/declaration`, `textDocument/implementation`, `textDocument/completion`, `textDocument/rename`, `textDocument/signatureHelp`, and `workspace/didRenameFiles`. It also publishes `textDocument/publishDiagnostics`.
- `pkg/syntax` defines the parser abstractions; `pkg/syntax/java` provides the first Java parser focused on packages, classes, fields, methods, and annotations.
- `pkg/plugin` defines language plugin hooks; `pkg/plugin/java` owns Java-only semantics such as generated getters and binary-expression type inference.
- `pkg/engine` is the orchestration layer: it parses a document, applies the language plugin, turns the result into persistent class/reference snapshots, and can walk an entire workspace tree to index project fixtures.
- `pkg/storage` defines the storage model and interface. `pkg/storage/pebble` is the embedded on-disk index store used by the CLI, while `pkg/storage` also includes an in-memory store for tests.
- Proxy configuration is exposed as the `--proxy` flag and normalized in `pkg/config`.
- `testdata/workspaces` contains realistic Maven- and Gradle-managed Spring Boot projects used as indexing fixtures.
- `internal/lsp/testdata/workspaces/full-java-demo` is the protocol-level Java fixture used to cover definition/declaration/implementation, rename, signature help, change/close, and rename-notification flows.
- `third_party/spring-petclinic` is the large upstream compile/regression fixture. `scripts/verify_petclinic_refactor_compile.sh` copies it to a temp workspace, applies deterministic rename refactors, then recompiles it.
- `third_party/eclipse.jdt.ls` is the upstream JDTLS source tree. `testdata/manifests/jdtls-ut-files.txt` is the generated inventory of all imported JDTLS unit-test source files from the pinned upstream commit.

## Key conventions

- Preserve the **engine vs. plugin** boundary: parsing/indexing orchestration belongs in `pkg/engine`, while Java-specific rules stay in `pkg/plugin/java`.
- Keep syntax parsers language-focused and structural. The current Java parser intentionally extracts declarations and annotations without embedding semantic Java rules into the parser itself.
- Treat generated members as first-class indexed output. The engine persists plugin-generated methods alongside declared methods and emits separate reference records for them.
- Keep on-disk indexing behind the `storage.Store` interface so tests can stay fast with the in-memory store while the CLI uses the Pebble-backed store.
- Keep proxy-aware external integration code flowing through `pkg/config` instead of scattering proxy parsing through plugins or transport code.
- Prefer realistic project-shaped fixtures in `testdata/workspaces` when extending indexing behavior, especially for build-tool-aware or multi-file scenarios.
- Keep large upstream codebases pinned as submodules under `third_party` rather than copying snapshots into the main tree; regenerate any derived manifests after updating them.
- Fix editor integrations by adding real server capabilities instead of client-side fallbacks. Neovim currently depends on the server's actual `textDocument/definition`, `textDocument/declaration`, `textDocument/implementation`, `textDocument/rename`, `textDocument/signatureHelp`, and file-rename notification support.
- Dependency navigation must prefer `-sources.jar` content when it exists and fall back to decompiled class output only when no source jar is available.
- Java completion should return stable LSP completion lists (never `null` items), support snippet-style insert text for methods, include Lombok-generated accessors for `@Data`-style classes, and publish unresolved-type diagnostics for demo-project errors such as `User1`.
