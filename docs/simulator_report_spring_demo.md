# Black-Box Project Simulator Report

- **Target Project**: `/home/fakename/workspace/java-lsp/java17-spring-demo`
- **Generated At**: 2026-11-20T17:32:27
- **Server**: `java-lsp 1.0.0`
- **Modules**: `api`, `core`
- **Java Files Discovered**: 11
- **Harness Errors / Crashes**: 0
- **Server Alive At End**: True

## Server Capabilities Audit (LSP 3.17)

- **Advertised**: `textDocumentSync`, `completionProvider`, `hoverProvider`, `signatureHelpProvider`, `declarationProvider`, `definitionProvider`, `typeDefinitionProvider`, `implementationProvider`, `referencesProvider`, `documentSymbolProvider`, `codeActionProvider`, `documentFormattingProvider`, `renameProvider`
- **Unadvertised / Missing**: `documentHighlightProvider`, `workspaceSymbolProvider`, `codeLensProvider`, `documentLinkProvider`, `documentRangeFormattingProvider`, `documentOnTypeFormattingProvider`, `foldingRangeProvider`, `selectionRangeProvider`, `linkedEditingRangeProvider`, `callHierarchyProvider`, `typeHierarchyProvider`, `semanticTokensProvider`, `inlayHintProvider`, `diagnosticProvider`
- **Incremental Sync (`textDocumentSync == 2`)**: False (actual=1)
- **`completionItem/resolve` (`resolveProvider`)**: False

## Mode 1: Automated Project Browser

- **Total LSP Diagnostic Errors**: 0
- **Total LSP Diagnostic Warnings**: 0
- **Unsupported Methods (`-32601`)**: None
- **Total Navigation Probes**: 83

### Per-File Diagnostics & Structural Endpoints

| File | Module | Errors | Warns | `documentSymbol` | `foldingRange` | `semanticTokens` | `inlayHint` | `codeLens` |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| `api/src/main/java/com/example/demo/api/UserFeignClient.java` | `api` | 0 | 0 | ok:2 | ok:0 | ok:0 | ok:0 | ok:0 |
| `core/src/main/java/com/example/demo/DemoApplication.java` | `core` | 0 | 0 | ok:3 | ok:0 | ok:0 | ok:0 | ok:0 |
| `core/src/main/java/com/example/demo/controller/UserController.java` | `core` | 0 | 0 | ok:7 | ok:0 | ok:0 | ok:0 | ok:0 |
| `core/src/main/java/com/example/demo/es/EsInitconstant.java` | `core` | 0 | 0 | ok:3 | ok:0 | ok:0 | ok:0 | ok:0 |
| `core/src/main/java/com/example/demo/mapper/UserMapper.java` | `core` | 0 | 0 | ok:6 | ok:0 | ok:0 | ok:0 | ok:0 |
| `core/src/main/java/com/example/demo/model/CreateUserRequest.java` | `core` | 0 | 0 | ok:11 | ok:0 | ok:0 | ok:0 | ok:0 |
| `core/src/main/java/com/example/demo/model/User.java` | `core` | 0 | 0 | ok:17 | ok:0 | ok:0 | ok:0 | ok:0 |
| `core/src/main/java/com/example/demo/model/UserEnum.java` | `core` | 0 | 0 | ok:1 | ok:0 | ok:0 | ok:0 | ok:0 |
| `core/src/main/java/com/example/demo/service/UserService.java` | `core` | 0 | 0 | ok:4 | ok:0 | ok:0 | ok:0 | ok:0 |
| `core/src/main/java/com/example/demo/service/impl/UserServiceImpl.java` | `core` | 0 | 0 | ok:7 | ok:0 | ok:0 | ok:0 | ok:0 |
| `core/src/test/java/com/example/demo/service/UserServiceSmokeTest.java` | `core` | 0 | 0 | ok:3 | ok:0 | ok:0 | ok:0 | ok:0 |

### Position Probe Summary by Endpoint

| Endpoint | Hit | Self-Echo / `(0,0)` | Empty | Unsupported | Invalid URI | Error | Crash/Timeout |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| `hover` | 25 | 0 | 58 | 0 | 0 | 0 | 0 |
| `definition` | 19 | 7 | 64 | 0 | 0 | 0 | 0 |
| `declaration` | 25 | 18 | 58 | 0 | 0 | 0 | 0 |
| `typeDefinition` | 0 | 0 | 83 | 0 | 0 | 0 | 0 |
| `implementation` | 19 | 18 | 64 | 0 | 0 | 0 | 0 |
| `references` | 83 | 0 | 0 | 0 | 0 | 0 | 0 |
| `documentHighlight` | 0 | 0 | 83 | 0 | 0 | 0 | 0 |
| `prepareRename` | 83 | 0 | 0 | 0 | 0 | 0 | 0 |

### Resolution Target Origins

| Endpoint | Same File | Cross-File | Cross-Module | JDK | Maven Dep | MyBatis XML | Missing File |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| `references` | 176 | 294 | 9 | 0 | 0 | 0 | 0 |
| `definition` | 12 | 0 | 0 | 0 | 0 | 7 | 0 |
| `declaration` | 8 | 12 | 0 | 0 | 3 | 2 | 0 |
| `implementation` | 13 | 5 | 0 | 0 | 0 | 1 | 0 |

### Probe Hits by Symbol Category (`definition` / `hover` / `references`)

| Category | `hover` Hit/Total | `definition` Hit/Total | `typeDefinition` Hit/Total | `implementation` Hit/Total | `references` Hit/Total |
| :--- | :---: | :---: | :---: | :---: | :---: |
| `annotation` | 0/7 | 0/7 | 0/7 | 1/7 | 7/7 |
| `cross_file_type` | 4/5 | 1/5 | 0/5 | 2/5 | 5/5 |
| `jdk_import` | 0/9 | 0/9 | 0/9 | 2/9 | 9/9 |
| `jdk_method_call` | 2/2 | 0/2 | 0/2 | 0/2 | 2/2 |
| `lambda_param` | 1/1 | 0/1 | 0/1 | 0/1 | 1/1 |
| `lombok_or_method_call` | 2/3 | 1/3 | 0/3 | 1/3 | 3/3 |
| `lombok_slf4j` | 0/3 | 3/3 | 0/3 | 1/3 | 3/3 |
| `lombok_slf4j_method` | 3/3 | 0/3 | 0/3 | 1/3 | 3/3 |
| `maven_import` | 0/24 | 1/24 | 0/24 | 2/24 | 24/24 |
| `method_call` | 4/7 | 3/7 | 0/7 | 2/7 | 7/7 |
| `project_import` | 3/4 | 1/4 | 0/4 | 3/4 | 4/4 |
| `type_declaration` | 5/11 | 6/11 | 0/11 | 3/11 | 11/11 |
| `variable_or_field` | 1/4 | 3/4 | 0/4 | 1/4 | 4/4 |

### `workspace/symbol` Probes

- Query `CreateUserRequest`: status=`ok`, matches=0
- Query `DemoApplication`: status=`ok`, matches=0
- Query `EsInitconstant`: status=`ok`, matches=0
- Query `User`: status=`ok`, matches=0
- Query `UserController`: status=`ok`, matches=0

## Mode 2: Simulated Human Coding

### 2.1 Progressive Typing & Completion

- **Total Scenarios**: 48
- **Hit Rate**: 28/48 (58.3%)
- **Top-5 Rate**: 19/48 (39.6%)
- **Scenarios Returning Snippets**: 18
- **Auto-Import `additionalTextEdits`**: 0/1

| Category | Total | Hits | Top-5 |
| :--- | :---: | :---: | :---: |
| `annotation` | 7 | 5 | 3 |
| `auto_import` | 1 | 0 | 0 |
| `chained_call` | 1 | 0 | 0 |
| `chained_call_prefix` | 1 | 0 | 0 |
| `chained_type_graph` | 1 | 1 | 1 |
| `jdk_or_static` | 3 | 1 | 1 |
| `jdk_or_static_prefix` | 1 | 0 | 0 |
| `lambda_param` | 1 | 0 | 0 |
| `lombok_log` | 6 | 5 | 1 |
| `lombok_log_prefix` | 6 | 5 | 5 |
| `member_dot` | 8 | 4 | 4 |
| `member_dot_prefix` | 8 | 4 | 4 |
| `new_constructor` | 3 | 3 | 0 |
| `static_import` | 1 | 0 | 0 |

#### Missed Completion Scenarios

- `core/src/main/java/com/example/demo/DemoApplication.java` [`jdk_or_static`] expected `getProperty` (prefix=``, items=0, top5=[])
- `core/src/main/java/com/example/demo/DemoApplication.java` [`jdk_or_static_prefix`] expected `getProperty` (prefix=`ge`, items=0, top5=[])
- `core/src/main/java/com/example/demo/DemoApplication.java` [`member_dot`] expected `run` (prefix=``, items=0, top5=[])
- `core/src/main/java/com/example/demo/DemoApplication.java` [`member_dot_prefix`] expected `run` (prefix=`ru`, items=0, top5=[])
- `core/src/main/java/com/example/demo/es/EsInitconstant.java` [`member_dot`] expected `Builder` (prefix=``, items=0, top5=[])
- `core/src/main/java/com/example/demo/es/EsInitconstant.java` [`member_dot_prefix`] expected `Builder` (prefix=`Bu`, items=0, top5=[])
- `core/src/main/java/com/example/demo/es/EsInitconstant.java` [`member_dot`] expected `properties` (prefix=``, items=45, top5=['MEtA_OBJECT_MAPPING', 'abstract', 'boolean', 'break', 'byte'])
- `core/src/main/java/com/example/demo/es/EsInitconstant.java` [`member_dot_prefix`] expected `properties` (prefix=`pr`, items=2, top5=['private', 'protected'])
- `core/src/main/java/com/example/demo/es/EsInitconstant.java` [`member_dot`] expected `keyword` (prefix=``, items=0, top5=[])
- `core/src/main/java/com/example/demo/es/EsInitconstant.java` [`member_dot_prefix`] expected `keyword` (prefix=`ke`, items=0, top5=[])
- `core/src/main/java/com/example/demo/es/EsInitconstant.java` [`jdk_or_static`] expected `of` (prefix=``, items=0, top5=[])
- `core/src/main/java/com/example/demo/model/CreateUserRequest.java` [`annotation`] expected `Data` (prefix=`Dat`, items=50, top5=['DataBindingException', 'DataBufferDecoder', 'DataBufferFactory', 'DataBufferInputStream', 'DataBufferUtils'])
- `core/src/main/java/com/example/demo/model/User.java` [`annotation`] expected `Data` (prefix=`Dat`, items=50, top5=['DataBindingException', 'DataBufferDecoder', 'DataBufferFactory', 'DataBufferInputStream', 'DataBufferUtils'])
- `core/src/main/java/com/example/demo/service/impl/UserServiceImpl.java` [`lombok_log`] expected `info` (prefix=``, items=0, top5=[])
- `core/src/main/java/com/example/demo/service/impl/UserServiceImpl.java` [`lombok_log_prefix`] expected `info` (prefix=`in`, items=0, top5=[])
- `core/src/test/java/com/example/demo/service/UserServiceSmokeTest.java` [`chained_call`] expected `extracting` (prefix=``, items=45, top5=['abstract', 'boolean', 'break', 'byte', 'case'])
- `core/src/test/java/com/example/demo/service/UserServiceSmokeTest.java` [`chained_call_prefix`] expected `extracting` (prefix=`ex`, items=1, top5=['extends'])
- `core/src/test/java/com/example/demo/service/UserServiceSmokeTest.java` [`static_import`] expected `assertThat` (prefix=`asse`, items=0, top5=[])
- `core/src/main/java/com/example/demo/controller/UserController.java` [`lambda_param`] expected `length` (prefix=``, items=0, top5=[])
- `core/src/main/java/com/example/demo/controller/UserController.java` [`auto_import`] expected `BigDecimal` (prefix=`BigDec`, items=17, top5=['BigDecimalAsStringSerializer', 'BigDecimalAssert', 'BigDecimalCloseTo', 'BigDecimalComparator', 'BigDecimalConversion'])

### 2.2 Live Signature Help

- **Scenarios**: 2/24 returned signatures, 2/24 matched `activeParameter`

| File | Callee | Expected `activeParameter` | Hit | Actual `activeParameter` | Top Signature |
| :--- | :--- | :---: | :---: | :---: | :--- |
| `core/src/main/java/com/example/demo/DemoApplication.java` | `System.getProperty` | 0 | NO | 0 | `` |
| `core/src/main/java/com/example/demo/DemoApplication.java` | `SpringApplication.run` | 0 | NO | 0 | `` |
| `core/src/main/java/com/example/demo/DemoApplication.java` | `SpringApplication.run` | 1 | NO | 0 | `` |
| `core/src/main/java/com/example/demo/controller/UserController.java` | `RuntimeException` | 0 | NO | 0 | `` |
| `core/src/main/java/com/example/demo/controller/UserController.java` | `log.info` | 0 | NO | 0 | `` |
| `core/src/main/java/com/example/demo/controller/UserController.java` | `log.info` | 1 | NO | 0 | `` |
| `core/src/main/java/com/example/demo/controller/UserController.java` | `String.format` | 0 | YES | 0 | `String format(String format, Object... args)` |
| `core/src/main/java/com/example/demo/controller/UserController.java` | `String.format` | 1 | YES | 1 | `String format(String format, Object... args)` |
| `core/src/main/java/com/example/demo/controller/UserController.java` | `userService.getUser` | 0 | NO | 0 | `` |
| `core/src/main/java/com/example/demo/controller/UserController.java` | `userService.createUser` | 0 | NO | 0 | `` |
| `core/src/main/java/com/example/demo/es/EsInitconstant.java` | `property.keyword` | 0 | NO | 0 | `` |
| `core/src/main/java/com/example/demo/es/EsInitconstant.java` | `pathMatch` | 0 | NO | 0 | `` |
| `core/src/main/java/com/example/demo/es/EsInitconstant.java` | `matchMappingType` | 0 | NO | 0 | `` |
| `core/src/main/java/com/example/demo/es/EsInitconstant.java` | `number.scalingFactor` | 0 | NO | 0 | `` |
| `core/src/main/java/com/example/demo/mapper/UserMapper.java` | `updateStatus` | 0 | NO | 0 | `` |
| `core/src/main/java/com/example/demo/mapper/UserMapper.java` | `updateStatus` | 1 | NO | 0 | `` |
| `core/src/main/java/com/example/demo/service/UserService.java` | `getUser` | 0 | NO | 0 | `` |
| `core/src/main/java/com/example/demo/service/UserService.java` | `createUser` | 0 | NO | 0 | `` |
| `core/src/main/java/com/example/demo/service/impl/UserServiceImpl.java` | `log.info` | 0 | NO | 0 | `` |
| `core/src/main/java/com/example/demo/service/impl/UserServiceImpl.java` | `userMapper.findById` | 0 | NO | 0 | `` |
| `core/src/main/java/com/example/demo/service/impl/UserServiceImpl.java` | `IllegalArgumentException` | 0 | NO | 0 | `` |
| `core/src/main/java/com/example/demo/service/impl/UserServiceImpl.java` | `userMapper.insert` | 0 | NO | 0 | `` |
| `core/src/test/java/com/example/demo/service/UserServiceSmokeTest.java` | `assertThat` | 0 | NO | 0 | `` |
| `core/src/test/java/com/example/demo/service/UserServiceSmokeTest.java` | `extracting` | 0 | NO | 0 | `` |

### 2.3 Error Injection & Live Diagnostics

- **Target File**: `core/src/main/java/com/example/demo/controller/UserController.java` (baseline errors: 0)
- **Detected**: 1/8, **Reverted Cleanly**: 8/8

| Error Kind | Injected Line | Detected | Severity=Error | Line Accurate | Reverted Clean | Sample Diagnostic |
| :--- | :---: | :---: | :---: | :---: | :---: | :--- |
| `syntax_error` | 32 | NO | NO | NO | YES | `` |
| `undefined_identifier` | 32 | NO | NO | NO | YES | `` |
| `unknown_method` | 32 | NO | NO | NO | YES | `` |
| `wrong_arg_count` | 32 | NO | NO | NO | YES | `` |
| `wrong_arg_type` | 32 | NO | NO | NO | YES | `` |
| `incompatible_return_or_assign` | 32 | NO | NO | NO | YES | `` |
| `missing_import` | 32 | YES | YES | YES | YES | `BigDecimal cannot be resolved to a type` |
| `unimplemented_interface_method` | 32 | NO | NO | NO | YES | `` |

### 2.4 Code Actions

- **Unsupported (`-32601`)**: False
- **Any-Action Hits**: 2/2
- **Error-Specific Quick-Fix Hits**: 0/2
- `missing_import_fix` (`core/src/main/java/com/example/demo/controller/UserController.java`): count=2, quickfix=False, titles=['Organize Imports', "Extract to method 'extractedMethod'"]
- `unimplemented_method_fix` (`core/src/main/java/com/example/demo/controller/UserController.java`): count=2, quickfix=False, titles=['Organize Imports', "Extract to method 'extractedMethod'"]

### 2.5 Rename Workflows

| Scope | Symbol | File | `prepareRename` | Edited Files | Total Edits | Files Touched |
| :--- | :--- | :--- | :---: | :---: | :---: | :--- |
| `class` | `User` | `core/src/main/java/com/example/demo/model/User.java` | YES | 1 | 1 | `core/src/main/java/com/example/demo/model/User.java` |
| `method` | `listUsers` | `core/src/main/java/com/example/demo/service/UserService.java` | YES | 1 | 1 | `core/src/main/java/com/example/demo/service/UserService.java` |
| `field` | `userService` | `core/src/main/java/com/example/demo/controller/UserController.java` | YES | 1 | 4 | `core/src/main/java/com/example/demo/controller/UserController.java` |
| `local` | `user` | `core/src/main/java/com/example/demo/service/impl/UserServiceImpl.java` | YES | 1 | 4 | `core/src/main/java/com/example/demo/service/impl/UserServiceImpl.java` |
