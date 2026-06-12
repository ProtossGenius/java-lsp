# Java LSP Support Matrix

This document tracks the Java LSP feature surface this repository is expected to support, which parts are complete, and which parts still need work. Update the status as each round of work finishes.

## Status legend

- **Done**: implemented and covered by automated tests
- **Partial**: implemented, but known gaps remain
- **Todo**: not implemented yet

## Core protocol lifecycle

| Feature | Status | Notes |
| --- | --- | --- |
| `initialize` / capability advertisement | **Done** | Core capabilities are advertised from `internal/lsp/server.go`. |
| `shutdown` / `exit` | **Done** | Covered by lifecycle tests. |
| `didOpen` / `didChange` / `didClose` | **Done** | Covered by lifecycle tests. |
| `workspace/didRenameFiles` | **Done** | Covered by lifecycle tests. |

## Navigation

| Feature | Status | Notes |
| --- | --- | --- |
| `textDocument/definition` | **Done** | Covers workspace types, dependency/JAR types, and JDK runtime types such as `RuntimeException`. |
| `textDocument/declaration` | **Done** | Covers receiver/member navigation and method-level declaration jumps between interfaces and implementations. |
| `textDocument/implementation` | **Done** | Covers receiver/member navigation plus method-level jumps from interfaces to implementations and stable behavior on implementation methods themselves. |
| `textDocument/typeDefinition` | **Todo** | Not implemented yet. |
| `textDocument/references` | **Done** | Workspace references for identifiers and method names are implemented and covered by regression tests. |
| JAR navigation with source jar preference | **Done** | Source jar is preferred when available. |
| JAR navigation fallback to decompiled content | **Done** | Falls back to decompiled/javap output when no sources exist. |
| JDK / `java.lang` navigation | **Done** | JDK types such as `RuntimeException` resolve through `src.zip`, with decompile fallback available when sources are missing. |

## Editing and refactoring

| Feature | Status | Notes |
| --- | --- | --- |
| `textDocument/rename` | **Done** | Workspace rename edits are generated and covered by tests. |
| Java file rename integration | **Done** | Works with `workspace/didRenameFiles`. |

## Assistance

| Feature | Status | Notes |
| --- | --- | --- |
| `textDocument/signatureHelp` | **Done** | Current coverage includes `String.format(...)`. |
| `textDocument/completion` | **Done** | Logger completion, Lombok accessor completion, method snippets, expected-return-type ranking, and imported JDK/JAR type completion such as `List.` are covered. |
| Method completion snippets with parentheses | **Done** | Tested method completions insert parentheses and parameter placeholders. |
| Parameter placeholder ranking | **Partial** | Basic local-variable/name matching is implemented; richer semantic matching still needed. |
| Lombok `@Data` getter/setter completion | **Done** | Tested `@Data` request-object accessors are completed with generated getters/setters. |
| Imported JDK / dependency type completion | **Done** | Current file imports are preferred, with workspace import fallback when needed. |
| Hover | **Todo** | Not implemented yet. |

## Diagnostics

| Feature | Status | Notes |
| --- | --- | --- |
| `textDocument/publishDiagnostics` | **Partial** | Basic syntax/brace and unresolved-type diagnostics exist. |
| Unresolved type diagnostics | **Done** | Tested unresolved-type cases like `User1` are reported, and JDK/runtime lookups no longer create the previous false negatives for `RuntimeException`. |
| Parser/syntax diagnostics | **Partial** | Basic brace errors only; parser-level Java syntax diagnostics remain incomplete. |
| Build/import diagnostics | **Todo** | No Maven/Gradle model diagnostics yet. |

## Test coverage

| Area | Status | Notes |
| --- | --- | --- |
| Go protocol tests for lifecycle/navigation/rename/signature/completion/diagnostics | **Done** | Coverage now includes JDK/runtime resolution, service-interface declaration/implementation, references, imported JDK type completion, Lombok completion, and diagnostics. |
| Neovim regression tests for navigation | **Done** | Logger/dependency navigation, service/interface declaration/implementation, references, and JDK runtime navigation are covered. |
| Neovim regression tests for completion | **Done** | `request.` Lombok accessors and `userService.` ranking are covered. |
| Neovim regression tests for diagnostics | **Done** | Unresolved type diagnostics are covered. |

## Current round focus

- [x] Create this matrix document
- [x] Fix demo-project JDK/runtime resolution (`RuntimeException` and similar types)
- [x] Fix Service / ServiceImpl `SPC l D` and `SPC l i` crashes
- [x] Implement and test `textDocument/references`
- [x] Add regression tests that lock these flows down

## Indexing and cache behavior

| Feature | Status | Notes |
| --- | --- | --- |
| JDK global source index file | **Done** | JDK `src.zip` entries are indexed once into a cache file and reused. |
| Workspace import prewarm/cache | **Done** | Workspace imports are cached so unopened files can still contribute library symbol resolution. |
| Current-file import priority | **Done** | Current buffer imports are resolved before workspace import fallbacks. |
| Opened dependency source/decompiled file origin tracking | **Done** | Extracted source/javap files remember their originating module classpath so follow-up navigation/completion can continue resolving imports. |
| File processed/unprocessed markers | **Done** | Parsed library files, workspace import scans, and JDK index generation are guarded by processing-state markers to avoid duplicate concurrent work. |
