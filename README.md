# Java LSP (C++ & LLVM)

A high-performance Java Language Server Protocol (LSP) server implemented in **C++** using **LLVM** and **Tree-sitter**, designed for modern Java development with advanced type inference, nested lambda resolution, Lombok/SLF4J support, jar source decompilation, and intelligent chained completion graph search.

---

## Key Features

### 1. Core Architecture (C++ & LLVM)
- **C++17** and **LLVM Support & Core** (`llvm::json`, data structures, raw streams, formatting).
- Ultra-fast incremental AST parsing via **Tree-sitter Java**.
- Real-time diagnostics, symbol table indexing, and lexical scope hierarchy.

### 2. Full LSP & JDTLS Compatibility
- **LSP Protocol 3.17**:
  - `textDocument/completion` (member, type, keyword, snippet, chained graph completion)
  - `textDocument/definition` (jump to local variable, parameter, field, method, class)
  - `textDocument/declaration` (e.g. `@Slf4j` `log` jumps to `org.slf4j.Logger.java`)
  - `textDocument/implementation` (e.g. `log` jumps to Logback `ch.qos.logback.classic.Logger.java`, interface jumps to impl)
  - `textDocument/typeDefinition` (e.g. `userService.listUsers` jumps to `List.java`)
  - `textDocument/hover` (Markdown formatted method signatures and Javadoc documentation)
  - `textDocument/signatureHelp` (parameter hints and active argument index)
  - `textDocument/references` (cross-file reference searching)
  - `textDocument/rename` & `workspace/didRenameFiles` (semantic symbol and file rename)
  - `textDocument/documentSymbol` (hierarchical document outline)
  - `textDocument/publishDiagnostics` (syntax errors, unresolved type errors)
  - `java/overridableMethods` & `java/overrideMethods` (custom JDTLS methods for override generation)

### 3. Nested Lambda Support
- **Robust Scope Hierarchy**:
  Unlike JDTLS which frequently struggles with deeply nested lambdas, `java-lsp` models lexical scopes as a recursive tree with explicit parent pointers.
  ```java
  list.stream()
      .filter(x -> items.stream().anyMatch(y -> y.getId().equals(x.getId())))
      .map(it -> it.toString());
  ```
- **Type Inference**:
  - Outer lambda `x -> ...`: inferred as the element type of `list` (`Integer`).
  - Inner lambda `y -> ...`: inferred as the element type of `items` (`User`).
  - Inside the inner lambda, both `y` and `x` are fully accessible with accurate hover, completion, and definition jump to the corresponding parameter.

### 4. Smart Chained Completion & Type Graph Search
When typing an assignment or return statement with an expected target type:
```java
List<Integer> list = List.of(1);

List<String> strList = list.
```
1. **Smart Filtering**:
   - Methods on `List<Integer>` that cannot possibly produce or lead to `List<String>` (e.g. `add(Integer)` returning boolean, `clear()` returning void, `size()` returning int) are **filtered out**.
2. **Graph Search (Type Transformation Graph)**:
   - Evaluates paths between types:
     - `List<A>` $\to$ `Stream<A>` via `.stream()`
     - `Stream<A>` $\to$ `Stream<B>` via `.map($1-it->$2)`
     - `Stream<B>` $\to$ `List<B>` via `.toList()`
   - Discovers candidate chains:
     - `stream().map($1-it->$2).toList()`
     - `stream().map(Object::toString).toList()`
     - `stream().map(String::valueOf).toList()`
     - `stream().map($1-it->$2).collect(Collectors.toList())`
3. **Multi-level Ranking**:
   - Primary: sorted by chain length ascending (length 1 < length 2 < length 3).
   - Secondary: sorted alphabetically when chain length is equal.

### 5. Lombok & SLF4J Support
- **`@Slf4j`**: Automatically injects `private static final org.slf4j.Logger log`:
  - Completion on `log.` includes `info`, `debug`, `warn`, `error`, `trace`, etc.
  - Hover displays `org.slf4j.Logger log`.
  - Declaration jumps to SLF4J `Logger.java`.
  - Implementation jumps to Logback `Logger.java`.
- **`@Data` / `@Value`**: Generates getters, setters with snippet placeholders (`setEmail(${1:email})`), `equals`, `hashCode`, `toString`.
- **`@RequiredArgsConstructor`**: Generates constructor accepting all `final` fields.
- **`@Getter` / `@Setter` / `@AllArgsConstructor` / `@NoArgsConstructor` / `@Builder`**.

### 6. Jar Source Decompilation & Classpath Resolution
- Supports **Maven** (`pom.xml`) and **Gradle** (`build.gradle`, `build.gradle.kts`) projects, including multi-module setups.
- Extracts JDK source files directly from JDK `src.zip` (`java.base/java/util/List.java`, `java/lang/RuntimeException.java`, etc.).
- Extracts dependency sources from `*-sources.jar` in `~/.m2/repository`.
- Uses embedded **CFR** (`lib/cfr.jar`) to decompile any compiled `.class` bytecode to `.java` into cache.

---

## Building and Running

### Prerequisites
- macOS (arm64/x86_64) or Linux
- CMake $\ge$ 3.20
- Ninja
- LLVM (e.g. Homebrew LLVM: `brew install llvm`)
- Tree-sitter (`brew install tree-sitter`)
- JDK (e.g. OpenJDK 17+)

### Build
```bash
cmake -B build -G Ninja
cmake --build build
```
The compiled executable will be placed in `build/java-lsp` and copied to `bin/java-lsp`.

### Run Unit Tests
```bash
./build/java-lsp-tests
```

---

## Neovim Integration (`java_lsp` Branch)

In `~/.config/nvim`:
A dedicated `java_lsp` branch is configured to run `java-lsp`:
```lua
-- ftplugin/java.lua
local cmd = {
  java_lsp_bin,
  "-storage", storage_path,
}
```
All integration specs pass:
- `test/java_autostart_spec.lua`
- `test/java_double_layer_autostart_spec.lua`
- `test/java_completion_spec.lua`
- `test/java_navigation_spec.lua`
- `test/java_jdk_navigation_spec.lua`
- `test/java_service_navigation_spec.lua`
- `test/java_hover_type_definition_spec.lua`
- `test/java_diagnostics_spec.lua`
- `test/java_signature_help_spec.lua`
- `test/java_file_actions_integration.lua`
