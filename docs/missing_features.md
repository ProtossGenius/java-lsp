# `java-lsp` 黑盒能力评估与功能缺失/缺陷清单

> **评估基准**：`java-lsp 1.0.0`（Linux x86_64 ELF 构建版本），纯黑盒 JSON-RPC 2.0 stdio 测试。  
> **核心结论**：在全量 **253** 个黑盒测试用例中，通过 **49** 项（**19.4%**），暴露真实功能缺失或行为缺陷 **204** 项（`GAP_FAIL`，覆盖 **207** 个失败标签），测试底座异常 **0** 项（`HARNESS_ERROR == 0`），总耗时 `136.583s`。在真实多模块项目 `java17-spring-demo` 的模拟编程评估中，自动化浏览 83 个位置探针仅实现部分同文件/MyBatis 跳转，代码补全命中率 **58.3%**（28/48），`signatureHelp` 命中率 **8.3%**（2/24），错误注入实时诊断检出率 **12.5%**（1/8），且存在负列号引发 `SIGABRT` 崩溃、UTF-8 字节偏移混用、忽略增量编辑与跨文件重命名失效等关键缺陷。

---

## 1. 黑盒测试方法与环境说明

### 1.1 纯黑盒协议测试方法与运行特征

本次评估完全遵循**纯黑盒测试原则**，不依赖任何内部源码或进程内测试桩，所有验证均通过标准 LSP 3.17 JSON-RPC 2.0 协议经由操作系统标准输入输出管道（stdio）驱动可执行文件 `./bin/java-lsp` 完成：

- **启动命令与 CLI 参数**：
  - 语法：`./bin/java-lsp -storage <tempdir>`（执行 `./bin/java-lsp --help` 返回 `Usage: java-lsp [-storage <path>]` 且退出码为 `0`）。
  - 每个独立测试类或测试夹具均使用独立的临时目录作为 `-storage` 参数，避免缓存污染。
- **JSON-RPC 传输与进程生命周期**：
  - 使用严格按字节长度计算的 `Content-Length: <bytes>\r\n\r\n<utf8-json>` 帧协议读写 `stdin`/`stdout`。
  - 通过独立异步读取线程解析服务端响应与推送通知（如 `textDocument/publishDiagnostics`、`window/logMessage`、`$/progress`），并显式校验未捕获的 JSON-RPC 错误码（如 `-32600`、`-32601`、`-32602`）、超时或子进程异常退出。
- **实测启动特征与集成注意事项**：
  1. **`initialize` 阶段索引延迟**：在 `java17-spring-demo` 上首次冷启动 `initialize` 耗时约 **~13.8s**（扫描并索引 JDK jmods 与本地 `~/.m2/repository` 归档），缓存命中后二次启动耗时约 **~1.1s**。客户端需将 `initialize` 超时预算设置至 `>= 45s`（测试套件使用 `45s–60s`）。
  2. **海量 `stderr` 日志与管道死锁风险**：服务端启动期间向 `stderr` 输出约 **709 行 / 128,809 字节（~128KB）** `[INFO] Indexing archive:` 日志，已超过 Linux 常见的 `64KB` 内核管道缓冲区上限。LSP 客户端必须使用独立后台线程持续排空 `stderr`（或重定向到文件），否则进程会在 `initialize` 阶段因写 `stderr` 阻塞而死锁。
  3. **工作区缓存副产物**：在打开包含 `pom.xml` 的工作区时，`java-lsp` 会在根目录及子模块目录下生成 `.classpath_m2.txt`（例如 `java17-spring-demo/.classpath_m2.txt`、`api/.classpath_m2.txt`、`core/.classpath_m2.txt`）。为保证 Git 工作区绝对干净并隔离并发测试，所有基于磁盘项目目录的测试均通过 `TempJavaProject` 将工程复制到 `/tmp/` 临时目录中运行，且不修改仓库内原始文件。
- **基础测试环境（Linux x86_64 / Ubuntu 24.04）**：
  - 服务端二进制：`bin/java-lsp`（Linux x86_64 ELF），依赖 `lib/tree-sitter-java.so` 与 `lib/cfr.jar`。
  - JDK 运行环境：服务端在启动日志中解析 `JAVA_HOME` 为 `/usr/lib/jvm/default-java`（OpenJDK 21）；系统默认 `java`/`javac` 为 OpenJDK `25.0.5.0.3`；所有合成 Java 17 夹具均通过 `javac --release 17` 校验合法性（或负向测试用例预期编译失败），`mvn -f java17-spring-demo/pom.xml -o test-compile` 在默认 JDK 下可在 `2.221s` 内编译通过。

### 1.2 测试目标与用例覆盖范围

1. **真实多模块 Maven 项目：`java17-spring-demo/`**
   - 包含 `api` 与 `core` 两个 Maven 子模块，共 **11** 个 `.java` 源文件与 1 个 MyBatis XML 映射文件（`core/src/main/resources/mapper/UserMapper.xml`）。
   - 涵盖主流企业级技术栈：Java 17、Spring Boot `3.3.2`、Spring Web MVC、Spring Cloud OpenFeign `2023.0.3`（`api` 模块 `UserFeignClient` 跨模块引用）、Lombok `1.18.38`（`@Data`、`@Builder`、`@Slf4j`、`@RequiredArgsConstructor`、`@NoArgsConstructor`、`@AllArgsConstructor`）、MyBatis `3.0.3`（`UserMapper` <-> `UserMapper.xml`）、Elasticsearch Java Client `8.7.1`（`EsInitconstant` 深层嵌套泛型 Lambda 构造链）、JUnit 5 与 AssertJ（`UserServiceSmokeTest`）。
2. **隔离合成夹具（Synthetic Fixtures）**
   - 覆盖标准单模块 Maven、多模块 Parent POM / `<dependencyManagement>` BOM / `${property}` 占位符 / 传递依赖、Gradle Groovy DSL（`build.gradle`）与 Kotlin DSL（`build.gradle.kts`）、无构建描述符的独立 `.java` 文件，以及专项 Java 8–17+ 语言特性、LSP 3.17 协议边界（UTF-16 代理对、CRLF 换行、增量同步、越界输入）、诊断准确率、CodeAction、重命名与格式化夹具。

### 1.3 测试套件与自动化模拟工具运行命令

```bash
# 1. 运行全量 10 个黑盒测试模块（253 个用例）并输出结构化 JSON 结果
python3 tests/blackbox/run_blackbox_tests.py --json-out /tmp/blackbox_results.json

# 2. 按正则筛选特定模块或 Gap ID 标签并打印实时详细日志
python3 tests/blackbox/run_blackbox_tests.py --filter "spring_demo|PROTO-26|NAV-11" -v

# 3. CI/底座健康检查：当 HARNESS_ERROR == 0 时返回退出码 0（忽略已知 GAP_FAIL）
python3 tests/blackbox/run_blackbox_tests.py --check-harness-only --summary-only

# 4. 运行自动化项目浏览与模拟编程工具（默认复制至临时隔离目录，绝不污染原项目）
python3 tests/blackbox/project_simulator.py java17-spring-demo \
  --md-out docs/simulator_report_spring_demo.md \
  --json-out /tmp/simulator_report_spring_demo.json
```

---

## 2. 测试总览与 LSP 3.17 能力矩阵

### 2.1 黑盒回归套件分模块统计（全量 253 用例）

数据来源：`python3 tests/blackbox/run_blackbox_tests.py --json-out /tmp/blackbox_results.json`（总耗时 `136.583s`）。

| 测试模块 (`tests/blackbox/`) | 用例总数 (`Total`) | 通过 (`PASS`) | 缺陷失败 (`GAP_FAIL`) | 底座错误 (`HARNESS_ERROR`) | 跳过 (`SKIP`) | 通过率 |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| `test_protocol_and_lifecycle.py` | 30 | 7 | 23 | 0 | 0 | 23.3% |
| `test_navigation_and_symbols.py` | 31 | 3 | 28 | 0 | 0 | 9.7% |
| `test_hover_and_signature_help.py` | 28 | 5 | 23 | 0 | 0 | 17.9% |
| `test_completion_and_chains.py` | 29 | 11 | 18 | 0 | 0 | 37.9% |
| `test_diagnostics_accuracy.py` | 32 | 10 | 22 | 0 | 0 | 31.3% |
| `test_modern_java_features.py` | 28 | 0 | 28 | 0 | 0 | 0.0% |
| `test_lombok_and_frameworks.py` | 22 | 7 | 15 | 0 | 0 | 31.8% |
| `test_refactoring_and_code_actions.py` | 29 | 3 | 26 | 0 | 0 | 10.3% |
| `test_project_and_dependencies.py` | 12 | 1 | 11 | 0 | 0 | 8.3% |
| `test_spring_demo_e2e.py` | 12 | 2 | 10 | 0 | 0 | 16.7% |
| **总计 (`TOTAL`)** | **253** | **49** | **204** | **0** | **0** | **19.4%** |

**已验证通过（49 项 `PASS`）的核心能力基线**：
- **协议与健壮性**：`initialize` 响应结构（`[PROTO-01]`）、核心能力字段声明（`[PROTO-04]`）、`shutdown` 返回 `null` 且 `exit` 退出码为 `0`（`[PROTO-16]`）、`$/cancelRequest` 并发取消不挂死（`[PROTO-18]`）、未知通知安全忽略（`[PROTO-20]`）、空文件处理不崩溃（`[PROTO-27]`）、CRLF 行尾换行行号映射准确（`[PROTO-30]`）。
- **基础补全与部分链式类型图**：同文件实例成员 dot 补全（`[COMP-01]`）、前缀与 CamelCase 驼峰模糊匹配 `gUBI`（`[COMP-05]`, `[COMP-06]`）、接口/重写去重与重载保留（`[COMP-07]`）、`List<Integer>` -> `List<String>` 链式图推导与赋值上下文过滤及多跳成员链 `user.getAddress().getCity().` 与排序（`[COMP-19]`, `[COMP-20]`, `[COMP-25]`, `[COMP-26]`）、部分未完成语句恢复（分号缺失、`if (`、函数参数内：`[COMP-27]`, `[COMP-28]`, `[COMP-29]`）。
- **基础导航、悬停与框架集成**：同方法局部/形参跳转（`[NAV-01]`, `[NAV-02]`）、`UserService.getUser` 跨文件引用搜索（`[NAV-19]`）、类方法与构造函数 Javadoc 悬停与 Markdown 格式（`[HOVER-03]`, `[HOVER-05]`, `[HOVER-12]`, `[HOVER-13]`）、`@Data` getter 悬停（`[HOVER-14]`）、Maven `*-sources.jar` 源码提取跳转（`[PROJ-09]`）、MyBatis Mapper <-> XML 双向跳转与补全（`[E2E-06]`, `[FW-15]`, `[FW-16]`, `[FW-17]`）、`@UtilityClass` 静态补全（`[FW-09]`）、`@Autowired` 字段注入补全（`[FW-12]`）、作用域内局部变量/参数重命名（`[REF-01]`, `[REF-02]`）、提取方法重构（`[ACT-12]`）、以及对 `java17-spring-demo` 和部分合法 Java 语法在静默打开时的 0 误报诊断（`[E2E-01]`, `[DIAG-01]`, `[DIAG-02]`, `[DIAG-04]`..`[DIAG-09]`, `[DIAG-11]`, `[DIAG-16]`, `[FW-19]`, `[FW-22]`）。

### 2.2 `project_simulator.py` 在 `java17-spring-demo` 上的实测摘要

基于 [simulator_report_spring_demo.md](file:///home/fakename/workspace/java-lsp/docs/simulator_report_spring_demo.md)（测试对象：`java17-spring-demo`，11 个 Java 文件，`api` + `core` 模块，0 次测试底座崩溃）：

| 测试模式与维度 | 核心实测指标 | 关键发现 |
| :--- | :--- | :--- |
| **Mode 1：项目自动浏览** | `0` 错误 / `0` 警告；`documentSymbol` 11/11 文件有大纲 | 未实现端点 `foldingRange`/`semanticTokens/full`/`inlayHint`/`codeLens` 全部返回 `ok:0`（不返回 `-32601`）；`workspace/symbol` 5 次查询均返回 `0` 匹配 |
| **Mode 1：83 个位置探针** | `hover` 25/83；`definition` 19/83；`declaration` 25/83；`typeDefinition` 0/83；`implementation` 19/83；`references` 83/83；`documentHighlight` 0/83；`prepareRename` 83/83 | `definition` 命中中 **7 次为自回显或 `(0,0)`**、7 次为 MyBatis XML、12 次为同文件，**0 次跨文件/跨模块/JDK/Maven**；`declaration` 中 **18/25 为自回显或 `(0,0)`**；`implementation` 中 **18/19 为自回显或 `(0,0)`**；`prepareRename` 对全部 83 处包括外部符号均无脑放行 |
| **Mode 2.1：渐进输入补全** | **28/48 (58.3%)** 命中；**19/48 (39.6%)** Top-5；18 个返回 Snippet；自动导包 `0/1` | `chained_type_graph` 1/1、`lombok_log` 5/6；`chained_call` 0/1、`lambda_param` 0/1、`static_import` 0/1、`auto_import` 0/1 |
| **Mode 2.2：签名帮助** | **2/24 (8.3%)** 返回签名，2/24 匹配 `activeParameter` | 仅 JDK `String.format`（arg 0、arg 1）命中；其余 22 个项目方法、`log.info`、AssertJ、ES Builder 调用全部返回空 |
| **Mode 2.3：错误注入诊断** | **1/8 (12.5%)** 检出；8/8 还原后恢复 0 误报 | 仅 `missing_import`（`BigDecimal`）检出；其余语法错误、未定义变量、未知方法、参数错误、不兼容赋值、未实现接口均漏报（0 诊断） |
| **Mode 2.4：CodeAction** | `2/2` 返回通用动作，**0/2** 返回错误快速修复 | 仅返回 `'Organize Imports'` 和 `"Extract to method 'extractedMethod'"`，无导入修复或实现接口方法修复 |
| **Mode 2.5：Rename 工作流** | `4/4` `prepareRename=YES`；所有重命名均仅编辑 **1 个文件** | 重命名类 `User`（1 文件/1 处）、接口方法 `listUsers`（1 文件/1 处），未同步更新任何跨文件调用与实现 |

### 2.3 `initialize` 能力声明与 LSP 3.17 实际支持矩阵

实测 `bin/java-lsp` 在 `initialize` 响应中返回的 `serverInfo` 为 `{"name": "java-lsp", "version": "1.0.0"}`，共声明 **13** 个 `capabilities` 键：
- `textDocumentSync`: `1`（Full 全量同步）
- `completionProvider`: `{"resolveProvider": false, "triggerCharacters": [".", "@"]}`
- `hoverProvider`: `true`
- `signatureHelpProvider`: `{"triggerCharacters": ["(", ","]}`
- `declarationProvider`: `true`
- `definitionProvider`: `true`
- `typeDefinitionProvider`: `true`
- `implementationProvider`: `true`
- `referencesProvider`: `true`
- `documentSymbolProvider`: `true`
- `codeActionProvider`: `true`
- `documentFormattingProvider`: `true`
- `renameProvider`: `{"prepareProvider": true}`

下表将 `initialize` 声明状态与黑盒实测行为逐项对照：

| LSP 3.17 能力 / 方法 | `initialize` 声明 | 黑盒实测状态 | 实际行为与关键问题说明 |
| :--- | :---: | :---: | :--- |
| `initialize` / `shutdown` / `exit` / `$/cancelRequest` | 核心生命周期 | **部分支持** | 正常启动与取消可用（`[PROTO-01]`, `[PROTO-16]`, `[PROTO-18]`），但 `shutdown` 后仍处理请求而非返回 `-32600`（`[PROTO-17]`）；未知方法静默返回 `null` 而非 `-32601`（`[PROTO-19]`） |
| `textDocumentSync`（增量同步与文档生命周期） | `1` (Full) | **存在严重缺陷** | 不支持增量同步（`[PROTO-02]`），显式发送增量 `range` 编辑无效（`[PROTO-21]`）；`didClose` 会清空磁盘回退导致悬停失效（`[PROTO-22]`） |
| 位置坐标与边界防护（UTF-16 / 负数坐标） | 隐式协议规范 | **存在严重缺陷** | 负列号触发 `SIGABRT`（`returncode=-6`）崩溃（`[PROTO-26]`）；行列偏移按 UTF-8 字节而非 UTF-16 code unit 计算（`[PROTO-29]`） |
| `textDocument/completion` | `triggerCharacters: [".", "@"]` | **部分支持** | 基础点补全与部分链式图可用（11/29 PASS），但缺少继承/`Object` 成员、访问控制过滤、自动导包、`@` 注解/属性、静态导入、`import` 补全及现代 Java 推导（18/29 GAP_FAIL） |
| `completionItem/resolve` | `resolveProvider: false` | **未实现** | 返回 `null`，不提供惰性 Javadoc/详情解析（`[PROTO-03]`, `[COMP-18]`） |
| `textDocument/hover` | `true` | **存在严重缺陷** | 仅对方法/构造/`@Data` getter 等有效（5/28 PASS）；局部变量、字段常量、注解、`var`、Lambda、泛型替换、多数外部库失效，类误显示构造器文档，`EsInitconstant` 误匹配 `UserBuilder` |
| `textDocument/signatureHelp` | `triggerCharacters: ["(", ","]` | **存在严重缺陷** | 几乎仅对 `String.format` 等内置条目有效（2/24），项目方法/构造/Maven 调用全空（`[SIG-01]`..`[SIG-12]`），且将同名 `format` 错配为 `String.format`、字符串/嵌套调用逗号计数越界（`[SIG-13]`） |
| `textDocument/definition` | `true` | **存在严重缺陷** | 仅同文件局部/形参及 MyBatis Mapper <-> XML 稳定；跨文件接口方法常跳回当前 Controller 行、不支持重载与 `super.`、跨模块与 JDK/CFR 跳转失效（`[NAV-03]`..`[NAV-16]`） |
| `textDocument/declaration` | `true` | **部分支持** | `@Slf4j log` 可跳至 `org/slf4j/Logger.java`，但模拟器 25 次命中中有 18 次为自回显或 `(0,0)` |
| `textDocument/typeDefinition` | `true` | **未实现 / 全部为空** | 声明了能力但全部测试与 83 个模拟器探针均返回 `[]`（`[NAV-13]`, `[E2E-04]`） |
| `textDocument/implementation` | `true` | **存在严重缺陷** | `UserService.listUsers` 方法可跳至实现类，但 `interface UserService` 返回 `(0,0)`、`@Slf4j log` 无法跳至 Logback、`sealed` 接口返回 `[]`（`[NAV-14]`, `[NAV-20]`, `[JAVA-17]`） |
| `textDocument/references` | `true` | **部分支持** | `UserService.getUser` 跨文件引用可用（`[NAV-19]`），但忽略 `includeDeclaration=false`、混淆重载且局部变量泄漏跨方法（`[NAV-17]`, `[NAV-18]`） |
| `textDocument/documentSymbol` | `true` | **部分支持** | 11 个 Spring Demo 文件均可返回基础大纲（`[E2E-01]`），但遗漏内部类、嵌套类、枚举常量与 Record 组件（`[SYM-01]`, `[SYM-02]`） |
| `textDocument/publishDiagnostics` | Push 通知 | **存在严重缺陷** | 对已知合法文件偶有误报（`try (var ...)`、局部类、嵌套注解、`SQLException`），对 22 类编译错误中的 21 类均漏报（仅能检出部分顶级未解析类型），且跨文件编辑后未更新 |
| `diagnosticProvider`（Pull 诊断） | 未声明 | **未实现** | 未声明且未实现 LSP 3.17 Pull Diagnostics（`[PROTO-14]`） |
| `textDocument/codeAction` & `codeAction/resolve` | `true` | **存在严重缺陷** | 无任何错误快速修复（QuickFix）；仅返回通用 `'Organize Imports'`（不删未用导入）及有语法破坏的 `"Extract to method"`；`codeAction/resolve` 返回 `None`（`[ACT-01]`..`[ACT-14]`） |
| `textDocument/prepareRename` & `rename` | `prepareProvider: true` | **存在严重缺陷** | 仅局部变量/参数同文件作用域重命名通过；字段/方法/接口/类重命名无法跨文件与联动 `RenameFile`，无命名冲突检查且允许重命名字面量与 JDK 符号（`[REF-03]`..`[REF-10]`） |
| `textDocument/formatting` | `true` | **存在严重缺陷** | 返回越界行号 `line: 4`（3 行文本），未拆分单行代码或遵守 `tabSize`（`[FMT-01]`） |
| `documentRangeFormattingProvider` / `documentOnTypeFormattingProvider` | 未声明 | **未实现** | 返回 `None` 且不返回 `-32601`（`[PROTO-13]`, `[FMT-02]`, `[FMT-03]`） |
| `workspace/symbol` | 未声明 | **未实现** | 返回 `None` / 空结果（`[PROTO-05]`, `[SYM-03]`） |
| `documentHighlight` / `foldingRange` / `selectionRange` / `linkedEditingRange` | 未声明 | **未实现** | 返回 `None` 且不返回 `-32601`（`[PROTO-08]`, `[PROTO-09]`, `[PROTO-12]`, `[SYM-04]`..`[SYM-06]`） |
| `semanticTokens` / `inlayHint` / `callHierarchy` / `typeHierarchy` / `codeLens` / `documentLink` | 未声明 | **未实现** | 返回 `None` 且不返回 `-32601`（`[PROTO-06]`, `[PROTO-07]`, `[PROTO-10]`, `[PROTO-11]`, `[SYM-07]`..`[SYM-11]`） |
| `workspace.workspaceFolders` / `workspace.fileOperations` / 监听通知 | 未声明 | **未实现** | `didChangeWorkspaceFolders`、`didChangeWatchedFiles`、`didCreateFiles`/`willRenameFiles`/`didRenameFiles`/`didDeleteFiles` 均未更新索引或返回 `None`（`[PROTO-15]`, `[PROTO-23]`..`[PROTO-25]`, `[REF-09]`, `[REF-10]`） |
| `java/overridableMethods` & `java/overrideMethods`（JDTLS 扩展） | 自定义方法 | **存在严重缺陷** | 忽略真实类继承结构，返回硬编码 Mock 数据 `UserService.listUsers`（`[JDT-01]`, `[JDT-02]`） |

> **关键协议级缺陷汇总**：  
> 1. **进程崩溃（P0）**：`textDocument/completion` 传入负数 `character: -5` 导致服务端未捕获异常 `std::out_of_range` 并以 `SIGABRT`（`returncode=-6`）崩溃。  
> 2. **编码错位（P1）**：全服务端将 UTF-8 字节偏移当作 LSP UTF-16 `character` 偏移使用，导致任何包含中文或四字节 Emoji 代理对的同行代码出现悬停失效与导航/大纲范围错位。  
> 3. **静默空响应替代协议错误（P2）**：对未知 JSON-RPC 方法及所有未实现的 LSP 3.17 标准方法，服务端均返回 `result: null`（或 `None`）而非标准 `-32601 Method not found`；且在 `shutdown` 之后继续响应请求而不返回 `-32600 InvalidRequest`。  
> 4. **增量编辑与关闭回退失效（P1）**：客户端发送增量 `range` 修改或关闭已修改文件时，服务端内存视图与磁盘状态失去同步。

---

## 3. 功能缺失与缺陷详细清单 (`GAP-001` .. `GAP-061`)

以下按 9 个功能域对全量 **204** 个 `GAP_FAIL` 测试（覆盖 **207** 个失败标签）进行去重归类与根因/现象级记录。每条 `GAP` 均对应经过黑盒实测复现的真实问题。

### 3.1 协议与生命周期 (Protocol & Lifecycle)

#### GAP-001：负列号位置参数导致服务端 `SIGABRT` 崩溃
- **ID 与严重级别**：`GAP-001` | `P0 阻塞/崩溃`
- **涉及 LSP 方法 / 特性**：`textDocument/completion`、`Position.character` 边界校验
- **复现场景与黑盒实测对比**：
  - 场景：打开合法 `Oob.java` 后，依次发送正值越界位置（`9999:9999`、`2:9999`）、负行号（`-1:0`）及负列号（`{'line': 0, 'character': -5}`）。
  - 预期行为：返回空结果或 JSON-RPC `-32602` (`InvalidParams`)，服务端保持存活。
  - 实际输出：前三种越界输入未崩溃，但在 `textDocument/completion` 收到 `{'line': 0, 'character': -5}` 时，服务端抛出 `std::out_of_range: basic_string::substr: __pos (which is 18446744073709551611) > this->size() (which is 17)`，触发 **`SIGABRT`（`returncode=-6`）** 进程直接退出。
- **在 `java17-spring-demo` 及日常开发中的影响**：若编辑器插件或包装脚本在光标计算边界偶发传入负列号，会导致整座 LSP 后台进程闪退，丢失全部已打开文件状态与编译缓存。
- **关联测试用例**：
  - `tests/blackbox/test_protocol_and_lifecycle.py::TestProtocolEdgeCasesAndUtf16::test_proto_26_out_of_bounds_positions_do_not_crash` `[PROTO-26]`

#### GAP-002：行列坐标使用 UTF-8 字节偏移而非 UTF-16 Code Units
- **ID 与严重级别**：`GAP-002` | `P1 核心缺失`
- **涉及 LSP 方法 / 特性**：`textDocument/hover`、`textDocument/definition`、`textDocument/documentSymbol`（LSP 3.17 `PositionEncodingKind.UTF16` 默认规范）
- **复现场景与黑盒实测对比**：
  - 场景：在 `Utf16Offset.java` 同行前缀包含中文与代理对 Emoji：`String msg = "你好😀🚀"; int targetVal = msg.length();` 与 `/* 中文😀🚀 */ int afterComment = afterEmojiMethod() + targetVal;` 以及 `/* 注释😀 */ public int afterEmojiMethod() { return 1; }`。
  - 预期行为：`hover(afterEmojiMethod)` 在 UTF-16 列 `36` 返回签名；`definition(targetVal)` 返回第 4 行 UTF-16 列 `31..40`；`documentSymbol` 中 `afterEmojiMethod` `selectionRange` 为第 8 行 UTF-16 列 `24..40`。
  - 实际输出：`hover` 返回 `None`；`documentSymbol` `selectionRange` 返回 `30..46`（多出 UTF-8 字节差值 6）；`definition(targetVal)` 错位命中第 8 行 `30..46`。
- **在 `java17-spring-demo` 及日常开发中的影响**：只要源码同行的字符串字面量或注释中包含中文（每个 UTF-8 占 3 字节、UTF-16 占 1 unit）或 Emoji（UTF-8 4 字节、UTF-16 2 units），光标后的补全、悬停、跳转和符号高亮位置会全部向右偏移。
- **关联测试用例**：
  - `tests/blackbox/test_protocol_and_lifecycle.py::TestProtocolEdgeCasesAndUtf16::test_proto_29_utf16_surrogate_pairs_and_cjk_column_offsets` `[PROTO-29]`

#### GAP-003：不支持 `textDocumentSync=2` 增量文档同步，忽略 `didChange` `range` 编辑
- **ID 与严重级别**：`GAP-003` | `P1 核心缺失`
- **涉及 LSP 方法 / 特性**：`initialize` (`serverCapabilities.textDocumentSync`)、`textDocument/didChange` (`TextDocumentContentChangeEvent` 增量范围)
- **复现场景与黑盒实测对比**：
  - 场景：审计 `initialize` 能力，并向已打开的 `IncEdit.java` 发送仅包含 `range` 与 `text` 的增量变更数组（将 `alpha` 替换为 `beta`），随后请求 `definition(beta)`。
  - 预期行为：`textDocumentSync` 支持增量同步（`2`），且增量编辑后 `definition(beta)` 准确跳转至第 4 行 `int beta = 10;`。
  - 实际输出：`serverCapabilities.textDocumentSync` 仅返回 `1`（Full）；发送增量 `range` 编辑后 `textDocument/definition` 返回 `[]`。
- **在 `java17-spring-demo` 及日常开发中的影响**：大文件编辑时客户端必须每次全量传输整份文件文本，且若客户端按增量模式发送变更会导致服务端 AST 彻底脱节。
- **关联测试用例**：
  - `tests/blackbox/test_protocol_and_lifecycle.py::TestServerCapabilitiesAudit::test_proto_02_incremental_text_document_sync_capability` `[PROTO-02]`
  - `tests/blackbox/test_protocol_and_lifecycle.py::TestLifecycleAndProtocolHandling::test_proto_21_did_change_incremental_range_edits` `[PROTO-21]`

#### GAP-004：`textDocument/didClose` 销毁磁盘回退而非恢复磁盘版本
- **ID 与严重级别**：`GAP-004` | `P1 核心缺失`
- **涉及 LSP 方法 / 特性**：`textDocument/didOpen`、`textDocument/didChange`、`textDocument/didClose`
- **复现场景与黑盒实测对比**：
  - 场景：打开磁盘上存在 `diskMethod()` 的 `Target.java` 并通过 `didChange` 在内存中改为 `memMethod()`；随后关闭 `Target.java`（`didClose`），让 `Caller.java` 调用磁盘原有 `t.diskMethod()` 并请求 `hover`。
  - 预期行为：关闭内存缓冲区后，服务端应回退读取磁盘上的 `Target.java`，`hover(t.diskMethod())` 返回有效签名。
  - 实际输出：`hover` 返回 `None`，表明 `didClose` 清空了目标文件在内存/索引中的符号信息而未从磁盘重载。
- **在 `java17-spring-demo` 及日常开发中的影响**：开发者在编辑器中浏览并关闭某个文件（如 `UserService.java` 或 `User.java`）后，其它打开文件中的跳转和悬停能力会随之退化或失效。
- **关联测试用例**：
  - `tests/blackbox/test_protocol_and_lifecycle.py::TestLifecycleAndProtocolHandling::test_proto_22_multi_file_open_close_reopen_lifecycle` `[PROTO-22]`

#### GAP-005：未知方法未返回 `-32601` 且 `shutdown` 后未拦截请求返回 `-32600`
- **ID 与严重级别**：`GAP-005` | `P2 体验缺陷`
- **涉及 LSP 方法 / 特性**：JSON-RPC 2.0 协议状态机、`shutdown`、未知方法分发
- **复现场景与黑盒实测对比**：
  - 场景：(1) 发送 `custom/nonExistentLspMethod` 请求；(2) 发送 `shutdown` 请求成功后、发送 `exit` 通知之前，再发送 `textDocument/documentSymbol`。
  - 预期行为：(1) 返回 JSON-RPC 错误 `-32601` (`Method not found`)；(2) 返回 JSON-RPC 错误 `-32600` (`InvalidRequest`)。
  - 实际输出：(1) 返回 `{"result": null}`（无 `error` 对象）；(2) `shutdown` 后仍正常执行并返回 `documentSymbol` 列表。
- **在 `java17-spring-demo` 及日常开发中的影响**：编辑器的 LSP 客户端无法通过 `-32601` 探测未实现扩展端点，容易把静默 `null` 误判为“当前位置无结果”并阻塞降级提示。
- **关联测试用例**：
  - `tests/blackbox/test_protocol_and_lifecycle.py::TestLifecycleAndProtocolHandling::test_proto_17_request_after_shutdown_returns_invalid_request` `[PROTO-17]`
  - `tests/blackbox/test_protocol_and_lifecycle.py::TestLifecycleAndProtocolHandling::test_proto_19_unknown_method_returns_32601` `[PROTO-19]`

#### GAP-006：未声明且未响应工作区目录及文件动态变更通知
- **ID 与严重级别**：`GAP-006` | `P1 核心缺失`
- **涉及 LSP 方法 / 特性**：`serverCapabilities.workspace`、`workspace/didChangeWorkspaceFolders`、`workspace/didChangeWatchedFiles`、`workspace/didCreateFiles` / `didRenameFiles` / `didDeleteFiles`
- **复现场景与黑盒实测对比**：
  - 场景：检查 `initialize` 中 `workspace.fileOperations` 与 `workspaceFolders`；运行时分别通过 `didChangeWorkspaceFolders` 添加含 `ExtraHelper.java` 的工作区、创建 `WatchedBean.java` 并发送 `didChangeWatchedFiles(type=1)`、创建 `CreatedBean.java` 并发送 `didCreateFiles`。
  - 预期行为：`initialize` 声明工作区文件操作能力，且新建/新增文件夹内的类在已有打开文件中立即可 `hover` 解析。
  - 实际输出：`capabilities.workspace` 为 `None`；上述三种场景下请求 `hover` 均返回 `None`，新文件及新目录未被索引。
- **在 `java17-spring-demo` 及日常开发中的影响**：IDE 中新建类、切换 Git 分支、拉取新模块或增加工作区文件夹后，不重启整个 LSP 服务端就无法识别新增类型。
- **关联测试用例**：
  - `tests/blackbox/test_protocol_and_lifecycle.py::TestServerCapabilitiesAudit::test_proto_15_workspace_file_operations_and_folders_capabilities` `[PROTO-15]`
  - `tests/blackbox/test_protocol_and_lifecycle.py::TestLifecycleAndProtocolHandling::test_proto_23_workspace_did_change_workspace_folders` `[PROTO-23]`
  - `tests/blackbox/test_protocol_and_lifecycle.py::TestLifecycleAndProtocolHandling::test_proto_24_workspace_did_change_watched_files` `[PROTO-24]`
  - `tests/blackbox/test_protocol_and_lifecycle.py::TestLifecycleAndProtocolHandling::test_proto_25_workspace_file_operation_notifications` `[PROTO-25]`

#### GAP-007：严重语法破损文件不触发语法错误诊断
- **ID 与严重级别**：`GAP-007` | `P2 体验缺陷`
- **涉及 LSP 方法 / 特性**：`textDocument/publishDiagnostics`、错误恢复后诊断发布
- **复现场景与黑盒实测对比**：
  - 场景：打开未闭合大括号且包含行尾悬空 `text.` 的 `Broken.java`。
  - 预期行为：虽然补全/悬停/大纲需安全容错，但 `publishDiagnostics` 应产出至少 1 条语法错误诊断。
  - 实际输出：未崩溃，但 `publishDiagnostics` 发布 `[]`（0 条诊断）。
- **在 `java17-spring-demo` 及日常开发中的影响**：用户漏写 `}` 或误删半截语句时，编辑器不会显示任何红色语法报错提示。
- **关联测试用例**：
  - `tests/blackbox/test_protocol_and_lifecycle.py::TestProtocolEdgeCasesAndUtf16::test_proto_28_unclosed_brace_syntax_broken_file` `[PROTO-28]`

#### GAP-008：未声明且未实现 `diagnosticProvider` 与 `linkedEditingRangeProvider` 等 LSP 3.17 协议扩展
- **ID 与严重级别**：`GAP-008` | `P3 增强缺失`
- **涉及 LSP 方法 / 特性**：`serverCapabilities.diagnosticProvider`（Pull Diagnostics）、`serverCapabilities.linkedEditingRangeProvider`
- **复现场景与黑盒实测对比**：
  - 场景：检查 `initialize` 返回的 `capabilities`。
  - 预期行为：遵循 LSP 3.17 声明 Pull 诊断与联动编辑范围能力。
  - 实际输出：`diagnosticProvider` 与 `linkedEditingRangeProvider` 均为 `None`。
- **在 `java17-spring-demo` 及日常开发中的影响**：现代客户端（如 VS Code / Neovim 新版插件）无法按需拉取诊断状态或进行同步标记编辑。
- **关联测试用例**：
  - `tests/blackbox/test_protocol_and_lifecycle.py::TestServerCapabilitiesAudit::test_proto_12_linked_editing_range_capability` `[PROTO-12]`
  - `tests/blackbox/test_protocol_and_lifecycle.py::TestServerCapabilitiesAudit::test_proto_14_pull_diagnostic_provider_capability` `[PROTO-14]`

---

### 3.2 代码导航与符号浏览 (Navigation & Symbols)

#### GAP-009：`textDocument/definition` 跨文件接口调用与 `this.` 字段解析错误
- **ID 与严重级别**：`GAP-009` | `P1 核心缺失`
- **涉及 LSP 方法 / 特性**：`textDocument/definition`
- **复现场景与黑盒实测对比**：
  - 场景：(1) 在 `java17-spring-demo` 的 `UserController.java` 中对 `userService.listUsers()` 和 `userService.getUser(id)` 请求 `definition`；(2) 在 `Widget.java` 构造函数 `Widget(String title, int count)` 内对 `this.title` 请求 `definition`。
  - 预期行为：(1) 分别跳转至 `UserService.java`（或 `UserServiceImpl.java`）对应方法声明；(2) 跳转至 `Widget.java` 第 3 行字段 `private final String title;`。
  - 实际输出：(1) **按同名符号错跳回 `UserController.java` 自身**（第 30 行 `listUsers()` 和第 36 行 `getUser()`）；(2) `this.title` 错跳至第 10 行构造函数形参 `String title`。
- **在 `java17-spring-demo` 及日常开发中的影响**：Spring Boot 分层架构中 Controller 与 Service 方法名通常一致，导致开发时点击 `userService.xxx()` 总是原地跳转至当前 Controller 方法，严重妨碍跨层阅读代码；构造函数赋值 `this.field = field` 也无法跳至字段定义。
- **关联测试用例**：
  - `tests/blackbox/test_navigation_and_symbols.py::TestSyntheticNavigationAndSymbols::test_nav_03_field_definition_this_and_unqualified` `[NAV-03]`
  - `tests/blackbox/test_navigation_and_symbols.py::TestSpringDemoNavigationAndDependencies::test_nav_11_cross_file_interface_definition` `[NAV-11]`
  - `tests/blackbox/test_spring_demo_e2e.py::TestSpringDemoE2E::test_e2e_04_cross_file_service_navigation_and_type_definition` `[E2E-04]`
  - `tests/blackbox/test_lombok_and_frameworks.py::TestSpringDemoLombokSpringMyBatisAssertJ::test_spring_constructor_di_and_implementation` `[FW-11]`

#### GAP-010：`textDocument/definition` 无法解析构造方法及方法重载
- **ID 与严重级别**：`GAP-010` | `P1 核心缺失`
- **涉及 LSP 方法 / 特性**：`textDocument/definition`
- **复现场景与黑盒实测对比**：
  - 场景：在 `NavRunner.java` 中分别点击 `new Widget(inputName)`、`new Widget(inputName, doubled)` 以及重载调用 `w1.compute(10)`、`w1.compute("ten")`、`w1.compute(1, 2)`。
  - 预期行为：根据参数个数与实参类型分别准确跳至 `Widget.java` 中对应的构造函数（第 6 行 vs 第 9 行）及重载方法（第 30/34/38 行）。
  - 实际输出：全部返回 `[]`。
- **在 `java17-spring-demo` 及日常开发中的影响**：面对任意含多个构造函数或重载方法的 Java 类（如工具类、实体构造器、Builder 等），均无法从调用点跳转到目标声明。
- **关联测试用例**：
  - `tests/blackbox/test_navigation_and_symbols.py::TestSyntheticNavigationAndSymbols::test_nav_04_constructor_definition_and_overload_selection` `[NAV-04]`
  - `tests/blackbox/test_navigation_and_symbols.py::TestSyntheticNavigationAndSymbols::test_nav_05_overloaded_method_definition_distinguishes_arg_types` `[NAV-05]`

#### GAP-011：`super.` 父类方法调用跳转到子类自身或返回空
- **ID 与严重级别**：`GAP-011` | `P1 核心缺失`
- **涉及 LSP 方法 / 特性**：`textDocument/definition`
- **复现场景与黑盒实测对比**：
  - 场景：在 `Widget.describe()` 内对 `super.describe()`（以及 `CoreConstructs.Child.describe()` 内 `super.describe()`）请求 `definition`。
  - 预期行为：跳转至父类 `BaseComponent.describe()`（第 9 行）或 `Base.describe()`（第 38 行）。
  - 实际输出：在 `Widget.java` 中误跳至子类自身 `Widget.describe()`（第 17 行）；在 `CoreConstructs.java` 中返回 `[]`。
- **在 `java17-spring-demo` 及日常开发中的影响**：重写方法时点击 `super.xxx()` 无法回看父类原实现。
- **关联测试用例**：
  - `tests/blackbox/test_navigation_and_symbols.py::TestSyntheticNavigationAndSymbols::test_nav_06_this_and_super_method_navigation` `[NAV-06]`
  - `tests/blackbox/test_modern_java_features.py::TestJava14To17AndConstructs::test_this_vs_super_disambiguation` `[JAVA-29]`

#### GAP-012：链式调用、静态导入、枚举常量及嵌套/内部/匿名类跳转失效
- **ID 与严重级别**：`GAP-012` | `P1 核心缺失`
- **涉及 LSP 方法 / 特性**：`textDocument/definition`
- **复现场景与黑盒实测对比**：
  - 场景：分别测试链式调用 `w2.withCount(5).asBase().describe()` 每一段、静态导入 `computeMagic`/`MAGIC_FACTOR`/`CRIMSON`、枚举 `Color.RED.getCode()`、静态嵌套类 `StaticNested.tag()`、内部类 `InnerHelper.readTitle()` 及匿名类捕获外部字段 `prefix`。
  - 预期行为：每一段均精准跳回对应声明位置。
  - 实际输出：上述场景全部返回 `[]`。
- **在 `java17-spring-demo` 及日常开发中的影响**：链式 Stream/Builder 代码与测试文件静态导入断言（如 `UserServiceSmokeTest.java` 中的 `assertThat`）均无法使用 Go to Definition。
- **关联测试用例**：
  - `tests/blackbox/test_navigation_and_symbols.py::TestSyntheticNavigationAndSymbols::test_nav_07_chained_method_calls_navigation` `[NAV-07]`
  - `tests/blackbox/test_navigation_and_symbols.py::TestSyntheticNavigationAndSymbols::test_nav_08_static_import_navigation` `[NAV-08]`
  - `tests/blackbox/test_navigation_and_symbols.py::TestSyntheticNavigationAndSymbols::test_nav_09_enum_constant_and_method_navigation` `[NAV-09]`
  - `tests/blackbox/test_navigation_and_symbols.py::TestSyntheticNavigationAndSymbols::test_nav_10_inner_nested_and_anonymous_classes` `[NAV-10]`

#### GAP-013：`textDocument/typeDefinition` 全局返回空
- **ID 与严重级别**：`GAP-013` | `P1 核心缺失`
- **涉及 LSP 方法 / 特性**：`textDocument/typeDefinition`
- **复现场景与黑盒实测对比**：
  - 场景：在 `UserController.java` 中对 `userService.listUsers()`、`request.getEmail()` 与 `UserServiceSmokeTest.java` 中对 `users` 请求 `typeDefinition`；并在 `project_simulator.py` 对 83 个符号探针全量测试。
  - 预期行为：`userService.listUsers()` 跳至 `List.java`、`request` 跳至 `CreateUserRequest.java`、`users` 跳至 `List.java`/`User.java`。
  - 实际输出：所有用例及模拟器 83/83 探针均返回 `[]`（`empty`）。
- **在 `java17-spring-demo` 及日常开发中的影响**：虽然 `initialize` 声明了 `typeDefinitionProvider: true`，但实际上任何变量或表达式都无法跳转至其返回值/类型定义。
- **关联测试用例**：
  - `tests/blackbox/test_navigation_and_symbols.py::TestSpringDemoNavigationAndDependencies::test_nav_13_type_definition_on_variables_and_methods` `[NAV-13]`
  - `tests/blackbox/test_spring_demo_e2e.py::TestSpringDemoE2E::test_e2e_04_cross_file_service_navigation_and_type_definition` `[E2E-04]`

#### GAP-014：`textDocument/implementation` 无法定位接口实现类、`sealed permits` 与 Logback 实现
- **ID 与严重级别**：`GAP-014` | `P1 核心缺失`
- **涉及 LSP 方法 / 特性**：`textDocument/implementation`
- **复现场景与黑盒实测对比**：
  - 场景：(1) 在 `UserService.java` 的 `interface UserService` 声明处请求 `implementation`；(2) 在 `UserController.java` / `UserServiceImpl.java` 的 `@Slf4j log` 上请求 `implementation`；(3) 在 `sealed interface Shape` 上请求 `implementation`。
  - 预期行为：(1) 定位到 `UserServiceImpl.java` 第 18 行类声明；(2) 定位到 `ch.qos.logback.classic.Logger.java`；(3) 定位到 `Circle` 与 `Rect`。
  - 实际输出：(1) 虽然方法级 `UserService.listUsers` 通过，但接口级 `interface UserService` 仅返回退化坐标 `line=0, character=0`；(2) `@Slf4j log` 返回 `[]` 或自回显当前文件；(3) `sealed Shape` 返回 `[]`。
- **在 `java17-spring-demo` 及日常开发中的影响**：从接口定义处难以直接跳转至实现类声明行，也无法按 README 描述从 `@Slf4j log` 跳转到运行时日志实现。
- **关联测试用例**：
  - `tests/blackbox/test_navigation_and_symbols.py::TestSpringDemoNavigationAndDependencies::test_nav_14_lombok_slf4j_declaration_and_implementation` `[NAV-14]`
  - `tests/blackbox/test_navigation_and_symbols.py::TestSpringDemoNavigationAndDependencies::test_nav_20_interface_implementation_navigation` `[NAV-20]`
  - `tests/blackbox/test_lombok_and_frameworks.py::TestSpringDemoLombokSpringMyBatisAssertJ::test_lombok_slf4j_and_log4j2` `[FW-05]`
  - `tests/blackbox/test_modern_java_features.py::TestJava14To17AndConstructs::test_sealed_interface_and_permits` `[JAVA-17]`

#### GAP-015：`textDocument/references` 忽略 `includeDeclaration=false`、混淆重载且局部变量跨方法泄漏
- **ID 与严重级别**：`GAP-015` | `P1 核心缺失`
- **涉及 LSP 方法 / 特性**：`textDocument/references`
- **复现场景与黑盒实测对比**：
  - 场景：(1) 对 `Widget.compute(int n)` 传 `context: {"includeDeclaration": false}`；(2) 对 `NavRunner.methodA()` 中的局部变量 `shadow` 请求 `references`（`methodB()` 存在同名局部变量 `shadow`）。
  - 预期行为：(1) 仅返回 `w1.compute(10)` 调用处，不含声明行与其它重载；(2) 仅返回 `methodA()` 内的声明与使用。
  - 实际输出：(1) 不仅仍返回第 30 行声明，还将第 34、38 行的 `compute(String)`、`compute(int, int)` 重载全部返回；(2) `methodA` 的 `shadow` 引用泄漏到了 `methodB()` 的第 41、42 行。
- **在 `java17-spring-demo` 及日常开发中的影响**：同名局部变量（如常见的 `user`、`id`、`result`）和重载方法的引用查找会产生大量跨作用域/跨重载噪音。
- **关联测试用例**：
  - `tests/blackbox/test_navigation_and_symbols.py::TestSyntheticNavigationAndSymbols::test_nav_17_references_include_declaration_true_vs_false` `[NAV-17]`
  - `tests/blackbox/test_navigation_and_symbols.py::TestSyntheticNavigationAndSymbols::test_nav_18_references_scoped_local_shadow_and_overloads` `[NAV-18]`

#### GAP-016：`textDocument/documentSymbol` 遗漏内部类、静态嵌套类与枚举常量
- **ID 与严重级别**：`GAP-016` | `P2 体验缺陷`
- **涉及 LSP 方法 / 特性**：`textDocument/documentSymbol`
- **复现场景与黑盒实测对比**：
  - 场景：对含 `StaticNested`、`InnerHelper` 的 `Widget.java` 与含枚举常量 `RED`、`GREEN`、`CRIMSON` 及 `getCode()` 的 `Color.java` 请求文档大纲。
  - 预期行为：返回完整的树状 `DocumentSymbol` 结构（包含嵌套类及其子方法、枚举常量及枚举方法）。
  - 实际输出：`Widget.java` 仅返回 14 个顶级成员，完全缺失 `StaticNested`、`InnerHelper`；`Color.java` 仅返回 `['Color']`，缺失全部枚举常量与方法。
- **在 `java17-spring-demo` 及日常开发中的影响**：如 `UserEnum.java`（含 `AVAILABLE`、`DELETED`）或包含内部 Builder/DTO 的文件在编辑器的 Outline/Breadcrumb 视图中无法展示内部结构。
- **关联测试用例**：
  - `tests/blackbox/test_navigation_and_symbols.py::TestSyntheticNavigationAndSymbols::test_sym_01_document_symbol_hierarchy` `[SYM-01]`
  - `tests/blackbox/test_navigation_and_symbols.py::TestSyntheticNavigationAndSymbols::test_sym_02_document_symbol_record_and_enum` `[SYM-02]`

#### GAP-017：`workspace/symbol` 全局符号搜索未实现
- **ID 与严重级别**：`GAP-017` | `P2 体验缺陷`
- **涉及 LSP 方法 / 特性**：`workspace/symbol`、`serverCapabilities.workspaceSymbolProvider`
- **复现场景与黑盒实测对比**：
  - 场景：查询 `{"query": "Widget"}` 以及模拟器中查询 `CreateUserRequest`、`DemoApplication`、`EsInitconstant`、`User`、`UserController`。
  - 预期行为：返回匹配的 `SymbolInformation[]` / `WorkspaceSymbol[]` 列表。
  - 实际输出：`workspaceSymbolProvider` 未声明；单测中返回 `None`；模拟器中返回空列表（0 匹配）。
- **在 `java17-spring-demo` 及日常开发中的影响**：无法通过 IDE 的“全局符号跳转”（Go to Symbol in Workspace）快速搜索类名或方法名。
- **关联测试用例**：
  - `tests/blackbox/test_protocol_and_lifecycle.py::TestServerCapabilitiesAudit::test_proto_05_workspace_symbol_provider_capability` `[PROTO-05]`
  - `tests/blackbox/test_navigation_and_symbols.py::TestSyntheticNavigationAndSymbols::test_sym_03_workspace_symbol_search` `[SYM-03]`

#### GAP-018：`documentHighlight`、`foldingRange` 与 `selectionRange` 编辑器交互能力未实现
- **ID 与严重级别**：`GAP-018` | `P2 体验缺陷`
- **涉及 LSP 方法 / 特性**：`textDocument/documentHighlight`、`textDocument/foldingRange`、`textDocument/selectionRange`
- **复现场景与黑盒实测对比**：
  - 场景：检查 `initialize` 能力并调用上述三个方法。
  - 预期行为：声明对应 Provider，并返回光标下同文件符号高亮范围、类/方法代码折叠区间及递增选区链。
  - 实际输出：能力均未声明，且请求全部返回 `None`（未返回 `-32601`）。
- **在 `java17-spring-demo` 及日常开发中的影响**：编辑器无法基于 LSP 高亮当前光标变量的所有读写位置、无法使用 LSP 智能语法折叠或 AST 选区扩展。
- **关联测试用例**：
  - `tests/blackbox/test_protocol_and_lifecycle.py::TestServerCapabilitiesAudit::test_proto_08_folding_and_selection_range_capabilities` `[PROTO-08]`
  - `tests/blackbox/test_protocol_and_lifecycle.py::TestServerCapabilitiesAudit::test_proto_09_document_highlight_provider_capability` `[PROTO-09]`
  - `tests/blackbox/test_navigation_and_symbols.py::TestSyntheticNavigationAndSymbols::test_sym_04_document_highlight` `[SYM-04]`
  - `tests/blackbox/test_navigation_and_symbols.py::TestSyntheticNavigationAndSymbols::test_sym_05_folding_range` `[SYM-05]`
  - `tests/blackbox/test_navigation_and_symbols.py::TestSyntheticNavigationAndSymbols::test_sym_06_selection_range` `[SYM-06]`

#### GAP-019：`semanticTokens`、`inlayHint`、`callHierarchy`、`typeHierarchy`、`codeLens` 与 `documentLink` 未实现
- **ID 与严重级别**：`GAP-019` | `P3 增强缺失`
- **涉及 LSP 方法 / 特性**：`textDocument/semanticTokens/full`、`textDocument/inlayHint`、`textDocument/prepareCallHierarchy` (+ `incomingCalls`/`outgoingCalls`)、`textDocument/prepareTypeHierarchy` (+ `supertypes`/`subtypes`)、`textDocument/codeLens`、`documentLinkProvider`
- **复现场景与黑盒实测对比**：
  - 场景：审计 `initialize` 能力并请求上述端点。
  - 预期行为：返回语义高亮 token 5 元组、参数/推导类型 Inlay Hints、调用/继承层次结构与 CodeLens。
  - 实际输出：对应能力全部未声明，全部请求均返回 `None`。
- **在 `java17-spring-demo` 及日常开发中的影响**：缺少语义着色、参数名内联提示以及服务/接口的调用链与继承树浏览器。
- **关联测试用例**：
  - `tests/blackbox/test_protocol_and_lifecycle.py::TestServerCapabilitiesAudit::test_proto_06_semantic_tokens_provider_capability` `[PROTO-06]`
  - `tests/blackbox/test_protocol_and_lifecycle.py::TestServerCapabilitiesAudit::test_proto_07_inlay_hint_provider_capability` `[PROTO-07]`
  - `tests/blackbox/test_protocol_and_lifecycle.py::TestServerCapabilitiesAudit::test_proto_10_call_and_type_hierarchy_capabilities` `[PROTO-10]`
  - `tests/blackbox/test_protocol_and_lifecycle.py::TestServerCapabilitiesAudit::test_proto_11_code_lens_and_document_link_capabilities` `[PROTO-11]`
  - `tests/blackbox/test_navigation_and_symbols.py::TestSyntheticNavigationAndSymbols::test_sym_07_semantic_tokens_full` `[SYM-07]`
  - `tests/blackbox/test_navigation_and_symbols.py::TestSyntheticNavigationAndSymbols::test_sym_08_inlay_hint` `[SYM-08]`
  - `tests/blackbox/test_navigation_and_symbols.py::TestSyntheticNavigationAndSymbols::test_sym_09_call_hierarchy` `[SYM-09]`
  - `tests/blackbox/test_navigation_and_symbols.py::TestSyntheticNavigationAndSymbols::test_sym_10_type_hierarchy` `[SYM-10]`
  - `tests/blackbox/test_navigation_and_symbols.py::TestSyntheticNavigationAndSymbols::test_sym_11_code_lens` `[SYM-11]`

---

### 3.3 代码补全与链式类型图搜索 (Completion & Chained Type Graph)

#### GAP-020：成员补全遗漏父类、接口 `default` 方法与 `Object` 基类方法，且排序混淆构造器与实例方法
- **ID 与严重级别**：`GAP-020` | `P1 核心缺失`
- **涉及 LSP 方法 / 特性**：`textDocument/completion`（成员继承解析与 `sortText` 排序）
- **复现场景与黑盒实测对比**：
  - 场景：(1) 在 `acc.`（`UserAccount extends BaseEntity`）触发点补全；(2) 在实现接口 `Greeter` 的 `Child c.` 上触发补全。
  - 预期行为：(1) 补全包含继承的 `getBaseId`、`describe` 及 `Object` 方法 `equals`、`hashCode`、`getClass`，且直属方法排在前面；(2) 包含接口默认方法 `defaultGreet`。
  - 实际输出：(1) `acc.` 仅返回 16 项本类成员与重写的 `toString()`，缺失全部 `BaseEntity` 与其他 `Object` 成员，且把构造函数 `UserAccount()`/`UserAccount(String)`（`sortText: '020_UserAccount'`）当作方法混排在 `getUser` 之前；(2) `Child c.` 仅返回 `['childOnly', 'describe', 'toString()']`，缺失 `defaultGreet`。
- **在 `java17-spring-demo` 及日常开发中的影响**：继承树与接口默认方法在补全列表中大面积消失，且构造器混入实例方法补全列表造成误导。
- **关联测试用例**：
  - `tests/blackbox/test_completion_and_chains.py::TestCompletionBasicsAndVisibility::test_inherited_superclass_and_object_methods` `[COMP-02]`
  - `tests/blackbox/test_completion_and_chains.py::TestCompletionBasicsAndVisibility::test_sorting_and_sort_text` `[COMP-08]`
  - `tests/blackbox/test_modern_java_features.py::TestJava14To17AndConstructs::test_interface_default_and_static_methods` `[JAVA-28]`

#### GAP-021：静态类补全混入实例成员，且 `private`/跨包可见性过滤失效
- **ID 与严重级别**：`GAP-021` | `P1 核心缺失`
- **涉及 LSP 方法 / 特性**：`textDocument/completion`（静态上下文识别与访问修饰符过滤）
- **复现场景与黑盒实测对比**：
  - 场景：(1) 在 `UserAccount.` 静态访问点触发补全；(2) 在同包类 `SamePackageCaller` 中对 `acc.` 触发补全。
  - 预期行为：(1) `UserAccount.` 仅出现 `DEFAULT_ROLE`、`createDefault` 等静态成员；(2) `acc.` 不出现 `private` 字段 `privateToken`。
  - 实际输出：(1) `UserAccount.` 混入了实例方法 `getUser()`；(2) 同包调用点 `acc.` 暴露了 `privateToken`。
- **在 `java17-spring-demo` 及日常开发中的影响**：在静态工具类（如 `EsInitconstant.`）或普通类上补全会推荐无法编译的实例方法，或误用私有成员。
- **关联测试用例**：
  - `tests/blackbox/test_completion_and_chains.py::TestCompletionBasicsAndVisibility::test_static_members_on_class_name` `[COMP-03]`
  - `tests/blackbox/test_completion_and_chains.py::TestCompletionBasicsAndVisibility::test_visibility_filtering_same_and_cross_package` `[COMP-04]`

#### GAP-022：未导入类补全缺少 `additionalTextEdits` 自动导包，且 `import` 与静态导入补全缺失
- **ID 与严重级别**：`GAP-022` | `P1 核心缺失`
- **涉及 LSP 方法 / 特性**：`textDocument/completion`（`additionalTextEdits`、包路径补全、静态导入符号）
- **复现场景与黑盒实测对比**：
  - 场景：(1) 输入未导入 JDK 类 `ArrayList`/`Collectors`、跨包类 `RemoteOrder`、Maven 类 `ResponseEntity`（`UserController` 中）及 `BigDec`（模拟器中）；(2) 输入 `import java.util.`；(3) 输入静态导入的 `emptyL` 或 `UserServiceSmokeTest` 中的 `assertTh`。
  - 预期行为：(1) 补全项带 `additionalTextEdits` 自动插入 `import ...;`；(2) `import java.util.` 列出 `Map`/`ArrayList`；(3) 补全静态导入符号 `emptyList`/`assertThat`。
  - 实际输出：(1) 返回了对应的 `kind: 7` 类条目（如 `ResponseEntity`），但 `additionalTextEdits` 为空，用户选中后不会自动导包；(2) `import java.util.` 返回 0 项；(3) `emptyL`、`assertTh` 均返回 0 项。
- **在 `java17-spring-demo` 及日常开发中的影响**：在编写 Spring 控制器（返回 `ResponseEntity`）或单元测试（调用 AssertJ `assertThat`）时无法完成自动导包与静态导入补全。
- **关联测试用例**：
  - `tests/blackbox/test_completion_and_chains.py::TestAutoImportsKeywordsAnnotationsAndResolve::test_unimported_jdk_class_completion_with_auto_import` `[COMP-09]`
  - `tests/blackbox/test_completion_and_chains.py::TestAutoImportsKeywordsAnnotationsAndResolve::test_unimported_project_class_completion_with_auto_import` `[COMP-10]`
  - `tests/blackbox/test_completion_and_chains.py::TestMavenUnimportedCompletion::test_unimported_maven_dependency_completion` `[COMP-11]`
  - `tests/blackbox/test_completion_and_chains.py::TestAutoImportsKeywordsAnnotationsAndResolve::test_static_import_completion` `[COMP-12]`
  - `tests/blackbox/test_completion_and_chains.py::TestAutoImportsKeywordsAnnotationsAndResolve::test_import_statement_completion` `[COMP-16]`
  - `tests/blackbox/test_spring_demo_e2e.py::TestSpringDemoE2E::test_e2e_07_stream_optional_and_assertj_static_import` `[E2E-07]`
  - `tests/blackbox/test_lombok_and_frameworks.py::TestSpringDemoLombokSpringMyBatisAssertJ::test_assertj_static_import_definition_and_completion` `[FW-21]`

#### GAP-023：`new ` 构造函数补全仅返回单条类符号，缺失重载构造方法签名
- **ID 与严重级别**：`GAP-023` | `P2 体验缺陷`
- **涉及 LSP 方法 / 特性**：`textDocument/completion`（构造器补全）
- **复现场景与黑盒实测对比**：
  - 场景：在 `ImportPlayground.java` 输入 `Person p = new Person`（`Person` 具有 `Person()` 和 `Person(String name)` 两个构造函数）。
  - 预期行为：提示两个重载构造方法条目及参数占位符。
  - 实际输出：仅返回 1 个普通类条目 `[{'detail': 'com.acme.app.Person', 'kind': 7, 'label': 'Person', 'sortText': '0_Person'}]`。
- **在 `java17-spring-demo` 及日常开发中的影响**：`new ` 实例化对象时无法在补全阶段预览多个重载构造参数。
- **关联测试用例**：
  - `tests/blackbox/test_completion_and_chains.py::TestAutoImportsKeywordsAnnotationsAndResolve::test_new_constructor_completion_diamond_and_overloads` `[COMP-13]`

#### GAP-024：`@` 注解名、注解属性与类体内方法重写（Override）补全缺失
- **ID 与严重级别**：`GAP-024` | `P2 体验缺陷`
- **涉及 LSP 方法 / 特性**：`textDocument/completion`（`@` 触发词、注解属性上下文、类体方法重写生成）
- **复现场景与黑盒实测对比**：
  - 场景：(1) 在方法上输入 `@MyT` 及 `@MyTag(`；(2) 在继承 `BaseWorker` 的类体中输入父类方法前缀 `lab`。
  - 预期行为：(1) 补全注解 `MyTag` 及注解属性 `value`、`priority`、`enabled`；(2) 提供 `BaseWorker.label()` 的 `@Override` 方法桩补全。
  - 实际输出：上述场景均返回 0 个补全项（`[]`）。
- **在 `java17-spring-demo` 及日常开发中的影响**：虽然 `completionProvider` 声明了 `"@"` 作为 `triggerCharacters`，但在写 Spring/Lombok 注解（如 `@Data` 模拟器命中也跌出 Top-5）和注解参数或覆盖父接口方法时基本不可用。
- **关联测试用例**：
  - `tests/blackbox/test_completion_and_chains.py::TestAutoImportsKeywordsAnnotationsAndResolve::test_annotation_and_attribute_completion` `[COMP-14]` `[COMP-15]`
  - `tests/blackbox/test_completion_and_chains.py::TestAutoImportsKeywordsAnnotationsAndResolve::test_override_method_completion_in_class_body` `[COMP-17]`

#### GAP-025：未提供 `completionItem/resolve` 惰性解析能力
- **ID 与严重级别**：`GAP-025` | `P2 体验缺陷`
- **涉及 LSP 方法 / 特性**：`completionProvider.resolveProvider`、`completionItem/resolve`
- **复现场景与黑盒实测对比**：
  - 场景：审计 `resolveProvider`，并对 `Person.getName` 补全项发送 `completionItem/resolve`。
  - 预期行为：`resolveProvider: true` 且 `completionItem/resolve` 返回填充了完整 `documentation`/`detail` 的 `CompletionItem`。
  - 实际输出：`resolveProvider` 为 `False`，且直接调用 `completionItem/resolve` 返回 `null`（`None`）。
- **在 `java17-spring-demo` 及日常开发中的影响**：大量补全项在未全部预计算文档的情况下无法实现客户端按选中项惰性加载 Javadoc 或自动导包编辑。
- **关联测试用例**：
  - `tests/blackbox/test_protocol_and_lifecycle.py::TestServerCapabilitiesAudit::test_proto_03_completion_resolve_provider_capability` `[PROTO-03]`
  - `tests/blackbox/test_completion_and_chains.py::TestAutoImportsKeywordsAnnotationsAndResolve::test_completion_item_resolve_and_javadoc` `[COMP-18]`

#### GAP-026：链式类型图搜索与智能过滤仅支持赋值场景，不支持 `return` 上下文、`Optional`/`Map`/`Set` 转换及未完成链式 Lambda
- **ID 与严重级别**：`GAP-026` | `P1 核心缺失`
- **涉及 LSP 方法 / 特性**：`textDocument/completion`（Chained Type Graph 与语法恢复）
- **复现场景与黑盒实测对比**：
  - 场景：(1) `List<String> convertReturn(List<Integer> nums) { return nums.`；(2) `Optional<String> email = optUser.`；(3) `UserDto u = optUser.`；(4) `Set<String> keys = userMap.` 与 `List<String> names = userSet.`；(5) `userSet.stream().filter(u -> u.`。
  - 预期行为：(1) 过滤掉 `clear()`/`size()` 并提示 `stream().map(...).toList()`；(2)-(4) 分别推荐 `map(...)`、`orElse`/`orElseThrow`、`keySet()`、`stream()` 链；(5) 推断 `u` 为 `UserDto` 并补全 `getEmail`。
  - 实际输出：(1) 未过滤 `void clear()`；(2)-(5) 全部返回 **0 项 (`[]`)**。
- **在 `java17-spring-demo` 及日常开发中的影响**：尽管 README 将链式类型图作为核心亮点，但除基础 `List<A> -> List<B>` 赋值外，`UserServiceImpl.getUser` 中的 `Optional<User>` 链、`return` 语句及 Stream Lambda 中断输入均无法触发智能补全。
- **关联测试用例**：
  - `tests/blackbox/test_completion_and_chains.py::TestChainedTypeGraphAndRecovery::test_smart_filtering_in_return_context` `[COMP-21]`
  - `tests/blackbox/test_completion_and_chains.py::TestChainedTypeGraphAndRecovery::test_optional_to_optional_string_chain` `[COMP-22]`
  - `tests/blackbox/test_completion_and_chains.py::TestChainedTypeGraphAndRecovery::test_optional_unwrap_to_user` `[COMP-23]`
  - `tests/blackbox/test_completion_and_chains.py::TestChainedTypeGraphAndRecovery::test_map_and_set_transformations` `[COMP-24]`
  - `tests/blackbox/test_completion_and_chains.py::TestChainedTypeGraphAndRecovery::test_incomplete_syntax_mid_stream_lambda` `[COMP-30]`

---

### 3.4 悬停提示与签名帮助 (Hover & SignatureHelp)

#### GAP-027：`textDocument/hover` 不支持局部变量、类字段/常量与注解
- **ID 与严重级别**：`GAP-027` | `P1 核心缺失`
- **涉及 LSP 方法 / 特性**：`textDocument/hover`
- **复现场景与黑盒实测对比**：
  - 场景：在 `Showcase.java` 中分别悬停局部变量 `localCounter`、静态常量 `MAX_LIMIT`（带 Javadoc）、实例字段 `prefix` 与注解 `@Marker`。
  - 预期行为：返回变量/字段类型、修饰符与关联 Javadoc。
  - 实际输出：全部返回 `None`（空）。
- **在 `java17-spring-demo` 及日常开发中的影响**：日常查看变量类型或常量说明时完全无法通过 Hover 获取信息（模拟器 83 个探针中 `hover` 仅 25 处有效，且字段/变量命中率低至 1/4）。
- **关联测试用例**：
  - `tests/blackbox/test_hover_and_signature_help.py::TestSyntheticHoverAndSignatureHelp::test_hover_01_local_variable` `[HOVER-01]`
  - `tests/blackbox/test_hover_and_signature_help.py::TestSyntheticHoverAndSignatureHelp::test_hover_02_field_and_constant_with_javadoc` `[HOVER-02]`
  - `tests/blackbox/test_hover_and_signature_help.py::TestSyntheticHoverAndSignatureHelp::test_hover_09_annotation_hover` `[HOVER-09]`

#### GAP-028：`textDocument/hover` 将类悬停误解析为构造函数 Javadoc，且泛型方法返回值未做类型代换
- **ID 与严重级别**：`GAP-028` | `P1 核心缺失`
- **涉及 LSP 方法 / 特性**：`textDocument/hover`
- **复现场景与黑盒实测对比**：
  - 场景：(1) 在变量声明 `Person ada` 的类名 `Person` 上悬停；(2) 在 `List<Person> people; people.get(0)` 的 `.get` 上悬停。
  - 预期行为：(1) 展示 `class Person` 及其类级 Javadoc `"Represents a registered person."`；(2) 展示代换后的 `Person get(int index)`。
  - 实际输出：(1) 返回了构造函数签名与构造 Javadoc `'Person(String fullName, int age) / Creates a new Person.'`；(2) 返回原始未代换的 `'E get(int index)'`。
- **在 `java17-spring-demo` 及日常开发中的影响**：悬停查看类文档或集合元素方法返回类型时，无法看到真实类文档或实体类型（如 `List<User>.get(0)` 只会显示 `E`）。
- **关联测试用例**：
  - `tests/blackbox/test_hover_and_signature_help.py::TestSyntheticHoverAndSignatureHelp::test_hover_04_class_and_javadoc` `[HOVER-04]`
  - `tests/blackbox/test_hover_and_signature_help.py::TestSyntheticHoverAndSignatureHelp::test_hover_06_generics_instantiation_substitutes_type_parameter` `[HOVER-06]`

#### GAP-029：`textDocument/hover` 在现代 Java、Lombok 链、外部库失效，并在 `EsInitconstant` 中产生严重符号错配
- **ID 与严重级别**：`GAP-029` | `P1 核心缺失`
- **涉及 LSP 方法 / 特性**：`textDocument/hover`
- **复现场景与黑盒实测对比**：
  - 场景：(1) 悬停 `var` 推导变量、嵌套 Lambda 参数 `p`/`t`、`instanceof` 模式变量 `s`、Lombok `@Slf4j log`、`User.builder().name(...).build()` 的 `.name`/`.build`、JDK `RuntimeException`、AssertJ `assertThat`；(2) 在 `java17-spring-demo` 的 `EsInitconstant.java` 中悬停 `Function<TypeMapping.Builder, ObjectBuilder<TypeMapping>>` 的 Lambda 参数 `builder -> builder`。
  - 预期行为：(1) 返回对应的具体类型或方法/类签名；(2) `EsInitconstant` 的 `builder` 应解析为 Elasticsearch 的 `TypeMapping.Builder`。
  - 实际输出：(1) 全部返回 `None` / `""`；(2) `EsInitconstant` 的 `builder` 竟然返回了 **`UserBuilder builder()`**（跨文件同名方法 `User.builder()` 错误劫持了 Lambda 参数名 `builder`！）。
- **在 `java17-spring-demo` 及日常开发中的影响**：Lombok 日志对象 `log` 悬停无提示（与 README 声称的 `org.slf4j.Logger log` 不符），且同名方法全局盲目匹配会导致严重误导（如将 ES Lambda `builder` 识别成 `UserBuilder`）。
- **关联测试用例**：
  - `tests/blackbox/test_hover_and_signature_help.py::TestSyntheticHoverAndSignatureHelp::test_hover_07_var_local_type_inference` `[HOVER-07]`
  - `tests/blackbox/test_hover_and_signature_help.py::TestSyntheticHoverAndSignatureHelp::test_hover_08_nested_lambda_parameters` `[HOVER-08]`
  - `tests/blackbox/test_hover_and_signature_help.py::TestSpringDemoHoverAndSignatureHelp::test_hover_10_lombok_slf4j_log_hover` `[HOVER-10]`
  - `tests/blackbox/test_hover_and_signature_help.py::TestSpringDemoHoverAndSignatureHelp::test_hover_11_jdk_and_maven_dependency_types_and_methods` `[HOVER-11]`
  - `tests/blackbox/test_hover_and_signature_help.py::TestSpringDemoHoverAndSignatureHelp::test_hover_15_lombok_builder_chain_hover` `[HOVER-15]`
  - `tests/blackbox/test_spring_demo_e2e.py::TestSpringDemoE2E::test_e2e_02_slf4j_lombok_logger_workflow` `[E2E-02]`
  - `tests/blackbox/test_spring_demo_e2e.py::TestSpringDemoE2E::test_e2e_08_nested_generic_lambda_builder_es_initconstant` `[E2E-08]`
  - `tests/blackbox/test_lombok_and_frameworks.py::TestSpringDemoLombokSpringMyBatisAssertJ::test_lombok_slf4j_and_log4j2` `[FW-05]`
  - `tests/blackbox/test_modern_java_features.py::TestGenericsVarLambdas::test_var_from_method_call_and_hover` `[JAVA-08]`
  - `tests/blackbox/test_modern_java_features.py::TestGenericsVarLambdas::test_nested_lambda_hover_and_definition` `[JAVA-12]`
  - `tests/blackbox/test_modern_java_features.py::TestJava14To17AndConstructs::test_instanceof_pattern_matching` `[JAVA-19]`

#### GAP-030：`textDocument/signatureHelp` 对项目自研方法、构造方法与 Maven 依赖方法几乎全部返回空
- **ID 与严重级别**：`GAP-030` | `P1 核心缺失`
- **涉及 LSP 方法 / 特性**：`textDocument/signatureHelp`
- **复现场景与黑盒实测对比**：
  - 场景：分别在普通项目方法 `greet(...)`、变参 `joinAll(...)`、构造方法 `new Person(...)`、嵌套调用 `outer(pair(10, 20), ...)`、未闭合输入 `greet("Ada", `、`UserController` 中 `userService.getUser(` 与 `log.info("...", ` 触发参数提示。
  - 预期行为：返回对应方法的 `signatures` 列表与参数文档、高亮当前 `activeParameter`。
  - 实际输出：全部返回 `{'activeParameter': 0, 'activeSignature': 0, 'signatures': []}`（模拟器 24 个调用点中仅 `String.format` 2 个场景返回有效签名，其余 22 处全部为空）。
- **在 `java17-spring-demo` 及日常开发中的影响**：开发者在调用 Service 方法、Mapper 方法、构造函数或 SLF4J 日志占位符时完全没有参数提示。
- **关联测试用例**：
  - `tests/blackbox/test_hover_and_signature_help.py::TestSyntheticHoverAndSignatureHelp::test_sig_01_single_method_signature_help` `[SIG-01]`
  - `tests/blackbox/test_hover_and_signature_help.py::TestSyntheticHoverAndSignatureHelp::test_sig_03_varargs_active_parameter_clamped` `[SIG-03]`
  - `tests/blackbox/test_hover_and_signature_help.py::TestSyntheticHoverAndSignatureHelp::test_sig_04_constructor_signature_help` `[SIG-04]`
  - `tests/blackbox/test_hover_and_signature_help.py::TestSyntheticHoverAndSignatureHelp::test_sig_05_nested_calls_active_signature_and_param` `[SIG-05]`
  - `tests/blackbox/test_hover_and_signature_help.py::TestSpringDemoHoverAndSignatureHelp::test_sig_11_jdk_and_maven_signature_help` `[SIG-11]`
  - `tests/blackbox/test_hover_and_signature_help.py::TestSyntheticHoverAndSignatureHelp::test_sig_12_incomplete_mid_typing_call_signature_help` `[SIG-12]`
  - `tests/blackbox/test_spring_demo_e2e.py::TestSpringDemoE2E::test_e2e_09_live_signature_help_on_spring_demo_calls` `[E2E-09]`

#### GAP-031：`textDocument/signatureHelp` 发生同名方法错配且逗号/变参索引解析严重错误
- **ID 与严重级别**：`GAP-031` | `P1 核心缺失`
- **涉及 LSP 方法 / 特性**：`textDocument/signatureHelp`（词法作用域逗号统计、重载决议与 varargs 钳位）
- **复现场景与黑盒实测对比**：
  - 场景：(1) 在 `Showcase` 类中调用本类的 3 个 `format(...)` 重载（`[SIG-02]`、`[SIG-10]`）；(2) 在 `String.format("a, b", String.join(",", "x", "y"), 1)` 的字符串内部与嵌套调用后触发（`[SIG-13]`）；(3) 调用参数含字符串逗号、嵌套括号、Lambda 参数及泛型尖括号逗号的函数（`[SIG-06]`..`[SIG-09]`）。
  - 预期行为：(1) 返回 `Showcase` 的 3 个 `format` 重载；(2) 字符串内 `"a, |b"` 为 `activeParameter=0`，嵌套 `String.join(...)` 之后应忽略内部逗号并将变参钳位到 `activeParameter=1`；(3) 忽略非顶层实参逗号。
  - 实际输出：(1) `Showcase.format(...)` 被错误全局匹配为唯一签名 `java.lang.String.format(String format, Object... args)`；(2) `"a, |b"` 返回 **`activeParameter: 1`**（将字符串字面量内逗号计为参数分隔符），`String.join(",", "x", "y"), ` 后返回越界的 **`activeParameter: 6`**（仅 2 个参数却索引到 6，未忽略字符串/嵌套括号逗号且未对 varargs 钳位）；(3) 其它带嵌套逗号的工程方法返回空签名。
- **在 `java17-spring-demo` 及日常开发中的影响**：在任何带格式化字符串、Lambda 或嵌套调用的 `String.format` 中参数高亮完全错乱，同名方法如 `format()` 甚至直接显示错误类的签名。
- **关联测试用例**：
  - `tests/blackbox/test_hover_and_signature_help.py::TestSyntheticHoverAndSignatureHelp::test_sig_02_overloaded_methods_signature_help` `[SIG-02]`
  - `tests/blackbox/test_hover_and_signature_help.py::TestSyntheticHoverAndSignatureHelp::test_sig_06_commas_inside_string_literal_ignored` `[SIG-06]`
  - `tests/blackbox/test_hover_and_signature_help.py::TestSyntheticHoverAndSignatureHelp::test_sig_07_commas_inside_nested_parens_ignored` `[SIG-07]`
  - `tests/blackbox/test_hover_and_signature_help.py::TestSyntheticHoverAndSignatureHelp::test_sig_08_commas_inside_lambda_ignored` `[SIG-08]`
  - `tests/blackbox/test_hover_and_signature_help.py::TestSyntheticHoverAndSignatureHelp::test_sig_09_commas_inside_generic_angles_ignored` `[SIG-09]`
  - `tests/blackbox/test_hover_and_signature_help.py::TestSyntheticHoverAndSignatureHelp::test_sig_10_retrigger_after_comma` `[SIG-10]`
  - `tests/blackbox/test_hover_and_signature_help.py::TestSyntheticHoverAndSignatureHelp::test_sig_13_string_format_comma_and_varargs_bounds` `[SIG-13]`

---

### 3.5 诊断准确性（漏报与误报）(Diagnostics Accuracy)

#### GAP-032：基础语法错误（缺分号、未闭合大括号）完全漏报
- **ID 与严重级别**：`GAP-032` | `P1 核心缺失`
- **涉及 LSP 方法 / 特性**：`textDocument/publishDiagnostics`
- **复现场景与黑盒实测对比**：
  - 场景：分别打开缺分号 `int x = 1\n return x;`（`MissingSemicolon.java`）和缺闭合大括号 `}`（`UnclosedBrace.java`）。
  - 预期行为：立即发布 `severity=1` (Error) 的语法错误诊断。
  - 实际输出：均发布 `[]`（0 条诊断）。
- **在 `java17-spring-demo` 及日常开发中的影响**：用户在编码时最基础的拼写、缺分号、括号不匹配无法收到实时报错（模拟器错误注入 `syntax_error` 亦未检出）。
- **关联测试用例**：
  - `tests/blackbox/test_diagnostics_accuracy.py::TestInvalidJavaFalseNegatives::test_syntax_error_missing_semicolon` `[DIAG-13]`
  - `tests/blackbox/test_diagnostics_accuracy.py::TestInvalidJavaFalseNegatives::test_syntax_error_unclosed_brace` `[DIAG-14]`

#### GAP-033：未解析变量与方法调用校验（未知方法、参数个数/类型不匹配）全部漏报
- **ID 与严重级别**：`GAP-033` | `P1 核心缺失`
- **涉及 LSP 方法 / 特性**：`textDocument/publishDiagnostics`
- **复现场景与黑盒实测对比**：
  - 场景：虽然顶级未解析类型（`NoSuchType`、`BigDecimal`）能报错（`DIAG-16` 为 `PASS`），但测试未解析变量 `return unknownVar + 1;`、不存在方法 `text.nonExistentMethod()`、参数个数错误 `add(1)` 及参数类型错误 `doubleIt("notAnInt")`。
  - 预期行为：每类错误均应发布至少 1 条 Error 诊断。
  - 实际输出：以上 4 类语义错误全部返回 `[]`（0 条诊断）。
- **在 `java17-spring-demo` 及日常开发中的影响**：写错方法名、传错参数或拼错局部变量名在保存运行 `mvn compile` 前完全不会被服务端发现（模拟器注入对应错误亦为 0 检出）。
- **关联测试用例**：
  - `tests/blackbox/test_diagnostics_accuracy.py::TestInvalidJavaFalseNegatives::test_unresolved_variable` `[DIAG-15]`
  - `tests/blackbox/test_diagnostics_accuracy.py::TestInvalidJavaFalseNegatives::test_nonexistent_method_on_known_type` `[DIAG-17]`
  - `tests/blackbox/test_diagnostics_accuracy.py::TestInvalidJavaFalseNegatives::test_wrong_argument_count` `[DIAG-18]`
  - `tests/blackbox/test_diagnostics_accuracy.py::TestInvalidJavaFalseNegatives::test_wrong_argument_type` `[DIAG-19]`

#### GAP-034：赋值/返回值类型不兼容与非 `void` 方法缺少 `return` 漏报
- **ID 与严重级别**：`GAP-034` | `P1 核心缺失`
- **涉及 LSP 方法 / 特性**：`textDocument/publishDiagnostics`
- **复现场景与黑盒实测对比**：
  - 场景：测试 `int x = "hello";`（包括 `UserController.java` 中注入 `int __badType = "nope";`）、`int getNumber() { return "wrong"; }` 及 `if (flag) { return 1; }` 缺失默认分支 `return`。
  - 预期行为：报告类型不兼容与 missing return 语句错误。
  - 实际输出：均发布 `[]`（0 条类型/控制流诊断；在 `E2E-10` 中仅对 `BigDecimal` 报错而对 `int __badType = "nope";` 静默漏报）。
- **在 `java17-spring-demo` 及日常开发中的影响**：缺乏静态类型检查与控制流完整性验证，类型转换错误无法在编码期捕获。
- **关联测试用例**：
  - `tests/blackbox/test_diagnostics_accuracy.py::TestInvalidJavaFalseNegatives::test_incompatible_assignment` `[DIAG-20]`
  - `tests/blackbox/test_diagnostics_accuracy.py::TestInvalidJavaFalseNegatives::test_incompatible_return_type` `[DIAG-21]`
  - `tests/blackbox/test_diagnostics_accuracy.py::TestInvalidJavaFalseNegatives::test_missing_return_statement` `[DIAG-22]`
  - `tests/blackbox/test_spring_demo_e2e.py::TestSpringDemoE2E::test_e2e_10_live_error_diagnostics_and_code_action_quickfix` `[E2E-10]`

#### GAP-035：受检异常、未实现接口、重复声明、`final` 重赋值、跨类 `private` 访问及无效 `@Override` 漏报
- **ID 与严重级别**：`GAP-035` | `P1 核心缺失`
- **涉及 LSP 方法 / 特性**：`textDocument/publishDiagnostics`
- **复现场景与黑盒实测对比**：
  - 场景：依次测试未捕获受检 `throw new IOException("boom")`、未实现 `Runnable.run()`、重复签名 `dupMethod(int)` 与重复局部变量 `int x`、修改 `final` 变量/字段、跨类访问 `SecretHolder` 的 `private` 成员、以及无父类方法对应的 `@Override void bogus()`。
  - 预期行为：分别产出对应的编译级 Error 诊断。
  - 实际输出：6 类错误场景全部返回 `[]`。
- **在 `java17-spring-demo` 及日常开发中的影响**：接口重构新增方法后，未同步实现的类（如 `UserServiceImpl`）不会收到任何未实现错误提醒；受检异常与访问控制也缺少静默校验。
- **关联测试用例**：
  - `tests/blackbox/test_diagnostics_accuracy.py::TestInvalidJavaFalseNegatives::test_unhandled_checked_exception` `[DIAG-23]`
  - `tests/blackbox/test_diagnostics_accuracy.py::TestInvalidJavaFalseNegatives::test_unimplemented_interface_method` `[DIAG-24]`
  - `tests/blackbox/test_diagnostics_accuracy.py::TestInvalidJavaFalseNegatives::test_duplicate_local_variable_and_duplicate_method` `[DIAG-25]`
  - `tests/blackbox/test_diagnostics_accuracy.py::TestInvalidJavaFalseNegatives::test_reassign_final_variable_and_field` `[DIAG-26]`
  - `tests/blackbox/test_diagnostics_accuracy.py::TestInvalidJavaFalseNegatives::test_access_private_member_from_other_class` `[DIAG-27]`
  - `tests/blackbox/test_diagnostics_accuracy.py::TestInvalidJavaFalseNegatives::test_invalid_override_annotation` `[DIAG-28]`

#### GAP-036：未提供无用导入与未使用局部变量的静态提示（Warning/Hint）
- **ID 与严重级别**：`GAP-036` | `P2 体验缺陷`
- **涉及 LSP 方法 / 特性**：`textDocument/publishDiagnostics` (`DiagnosticTag.Unnecessary` / `severity` 2–4)
- **复现场景与黑盒实测对比**：
  - 场景：文件中存在未使用的 `import java.util.Date;` 或未使用局部变量 `int neverUsed = 99;`。
  - 预期行为：发布 Warning/Hint 级别或带 `tags: [1]` (Unnecessary) 的诊断提示。
  - 实际输出：均返回 `[]`。
- **在 `java17-spring-demo` 及日常开发中的影响**：编辑器无法置灰或警告冗余导入与死变量（这也直接导致后续 `Organize Imports` 无法识别并清除无用导入）。
- **关联测试用例**：
  - `tests/blackbox/test_diagnostics_accuracy.py::TestInvalidJavaFalseNegatives::test_unused_import_warning_or_hint` `[DIAG-29]`
  - `tests/blackbox/test_diagnostics_accuracy.py::TestInvalidJavaFalseNegatives::test_unused_local_variable_warning_or_hint` `[DIAG-30]`

#### GAP-037：对 `try (var ...)`、局部类、嵌套注解及多异常捕获合法 Java 代码产生误报
- **ID 与严重级别**：`GAP-037` | `P1 核心缺失`
- **涉及 LSP 方法 / 特性**：`textDocument/publishDiagnostics`（False Positives）
- **复现场景与黑盒实测对比**：
  - 场景：打开经 `javac --release 17` 验证 0 错误的合法代码：
    1. `VarValid.java` 中已 `import java.io.ByteArrayInputStream;` 并使用 `try (var stream = new ByteArrayInputStream(...))`；
    2. `NestedClassesValid.java` / `CoreConstructs.java` 中在方法体内定义局部类 `LocalCalc` / `LocalHelper`；
    3. `AnnotationsValid.java` 中在类内定义嵌套 `@interface MetaInfo`；
    4. `CoreConstructs.java` 中已 `import java.sql.SQLException;` 并使用多异常捕获 `catch (IOException | SQLException e)`。
  - 预期行为：均应返回 `0` Error 诊断。
  - 实际输出：分别误报 `ByteArrayInputStream cannot be resolved to a type`、`LocalCalc cannot be resolved to a type`、`LocalHelper cannot be resolved to a type`、`MetaInfo cannot be resolved to a type` 与 `SQLException cannot be resolved to a type`。
- **在 `java17-spring-demo` 及日常开发中的影响**：使用 `try (var ...)` 资源管理、方法内局部类、类内嵌套注解或 `catch (A | B e)` 时，编辑器会显示错误的“无法解析类型”红线干扰正常开发。
- **关联测试用例**：
  - `tests/blackbox/test_diagnostics_accuracy.py::TestValidJavaFalsePositives::test_var_type_inference_no_false_positive` `[DIAG-03]`
  - `tests/blackbox/test_diagnostics_accuracy.py::TestValidJavaFalsePositives::test_inner_nested_local_anonymous_classes_no_false_positive` `[DIAG-10]`
  - `tests/blackbox/test_diagnostics_accuracy.py::TestValidJavaFalsePositives::test_annotations_with_attributes_no_false_positive` `[DIAG-12]`
  - `tests/blackbox/test_modern_java_features.py::TestJava14To17AndConstructs::test_local_and_anonymous_classes` `[JAVA-22]`

#### GAP-038：实时 `didChange` 编辑及跨文件签名变更后未重新发布最新诊断
- **ID 与严重级别**：`GAP-038` | `P1 核心缺失`
- **涉及 LSP 方法 / 特性**：`textDocument/didChange`、`textDocument/publishDiagnostics`
- **复现场景与黑盒实测对比**：
  - 场景：(1) `LiveCalc.java` 打开无错后，通过 `didChange` 引入 `int x = missingSymbol;`；(2) `GreeterCaller.java` 调用 `svc.greet("world")` 打开无错后，将 `GreeterService.java` 方法签名通过 `didChange` 改为 `greet(int count)`。
  - 预期行为：8s 内自动为 `LiveCalc.java` 和 `GreeterCaller.java` 推送更新后的 Error 诊断，并在还原后清空。
  - 实际输出：两项测试均在 8.0s 等待 `publishDiagnostics` 通知时超时（`Latest stored diagnostics: []`）。
- **在 `java17-spring-demo` 及日常开发中的影响**：编辑某个服务接口参数后，所有已打开的调用方文件不会主动刷新报错，容易留下隐藏编译破坏。
- **关联测试用例**：
  - `tests/blackbox/test_diagnostics_accuracy.py::TestLiveDiagnosticsLifecycle::test_did_change_diagnostics_appear_and_clear` `[DIAG-31]`
  - `tests/blackbox/test_diagnostics_accuracy.py::TestLiveDiagnosticsLifecycle::test_cross_file_dependency_diagnostics_update` `[DIAG-32]`

---

### 3.6 现代 Java 语言特性 (Modern Java 8–17+)

> **领域现状**：`test_modern_java_features.py` 共 **28** 个测试用例全部为 `GAP_FAIL`（**0 PASS**）。

#### GAP-039：泛型通配符、菱形操作符、泛型方法返回推导与多级泛型链全面失效
- **ID 与严重级别**：`GAP-039` | `P1 核心缺失`
- **涉及 LSP 方法 / 特性**：`textDocument/completion`（泛型类型系统与替换）
- **复现场景与黑盒实测对比**：
  - 场景：分别测试上界通配符 `List<? extends User> upper.get(0).`、下界通配符 `List<? super Admin> lower.get(0).`、菱形操作符 `Map<String, List<User>> map = new HashMap<>(); map.get("a").get(0).`、泛型方法 `pickFirst(users).`、多界限类型参数 `<T extends Named & Comparable<T>> item.` 以及嵌套泛型 `Box<Pair<String, User>> box.get().getSecond().`。
  - 预期行为：准确推导元素或返回值类型为 `User`、`Object` 或多界限交集类型并补全对应成员。
  - 实际输出：6 个场景的补全全部返回 **0 项 (`[]`)**。
- **在 `java17-spring-demo` 及日常开发中的影响**：无法处理现代 Java 代码中普遍使用的嵌套集合、通用响应包装类（如 `ResponseEntity<Page<User>>`）及泛型 DAO/Service 返回值。
- **关联测试用例**：
  - `tests/blackbox/test_modern_java_features.py::TestGenericsVarLambdas::test_upper_bounded_wildcard` `[JAVA-01]`
  - `tests/blackbox/test_modern_java_features.py::TestGenericsVarLambdas::test_lower_bounded_wildcard` `[JAVA-02]`
  - `tests/blackbox/test_modern_java_features.py::TestGenericsVarLambdas::test_diamond_operator_type_resolution` `[JAVA-03]`
  - `tests/blackbox/test_modern_java_features.py::TestGenericsVarLambdas::test_generic_method_return_inference` `[JAVA-04]`
  - `tests/blackbox/test_modern_java_features.py::TestGenericsVarLambdas::test_bounded_type_parameter_completion` `[JAVA-05]`
  - `tests/blackbox/test_modern_java_features.py::TestGenericsVarLambdas::test_multi_level_generic_chaining` `[JAVA-06]`

#### GAP-040：Java 10+ `var` 局部变量与 `try-with-resources` 类型推导完全缺失
- **ID 与严重级别**：`GAP-040` | `P1 核心缺失`
- **涉及 LSP 方法 / 特性**：`textDocument/completion`、`textDocument/hover`、`textDocument/definition`
- **复现场景与黑盒实测对比**：
  - 场景：测试 `var list = new ArrayList<String>(); list.get(0).`、`var user = findUser(1L);` 的悬停与 `user.` 补全、`var names = users.stream().map(User::getName).toList(); names.get(0).`、增强型 `for (var u : users)` 以及 `try (var res = new Resource()) { res.`。
  - 预期行为：推导出 `var` 绑定变量的具体类型并支持补全、悬停与跳转。
  - 实际输出：所有 `var` 变量与成员的补全均返回 0 项，悬停返回 `None`/`""`，定义跳转返回 `[]`。
- **在 `java17-spring-demo` 及日常开发中的影响**：在 Java 17 项目中一旦使用 `var`（或 `try (var ...)`），所有 IDE 智能提示、悬停与跳转能力立即归零。
- **关联测试用例**：
  - `tests/blackbox/test_modern_java_features.py::TestGenericsVarLambdas::test_var_from_diamond_constructor` `[JAVA-07]`
  - `tests/blackbox/test_modern_java_features.py::TestGenericsVarLambdas::test_var_from_method_call_and_hover` `[JAVA-08]`
  - `tests/blackbox/test_modern_java_features.py::TestGenericsVarLambdas::test_var_from_stream_pipeline` `[JAVA-09]`
  - `tests/blackbox/test_modern_java_features.py::TestGenericsVarLambdas::test_var_in_enhanced_for_loop` `[JAVA-10]`
  - `tests/blackbox/test_modern_java_features.py::TestJava14To17AndConstructs::test_try_with_resources_and_multi_catch` `[JAVA-25]`

#### GAP-041：嵌套 Lambda 作用域推导与方法引用 (`::`) 在完整流式管道中失效
- **ID 与严重级别**：`GAP-041` | `P1 核心缺失`
- **涉及 LSP 方法 / 特性**：`textDocument/completion`、`textDocument/hover`、`textDocument/definition`
- **复现场景与黑盒实测对比**：
  - 场景：按 README 展示的嵌套 Lambda 模式构造 `users.stream().filter(x -> items.stream().anyMatch(y -> y.getId().equals(x.getId()))).map(it -> it.getEmail()).forEach(System.out::println)` 与三层 `flatMap/filter/map(z -> z.)`，以及方法引用 `User::getId`、`System.out::println`。
  - 预期行为：内层 `y.` 与被捕获外层 `x.`、三层 `z.`、尾随 `it.` 均补全对应元素方法；悬停/跳转到 `User.java` 与 `PrintStream.println`。
  - 实际输出：补全在 `y.`、`x.`、`z.`、`it.` 均返回 **0 项**；`hover(x)`、`hover(y)`、`hover(System.out::println)` 均为空；`definition(x/y/User::getId)` 均返回 `[]`（`simulator` 中 `lambda_param` 亦为 0/1）。
- **在 `java17-spring-demo` 及日常开发中的影响**：README 宣传的“深度嵌套 Lambda 词法作用域推导”在真实测试中未能复现；`EsInitconstant.java` 的 Lambda 参数与 `UserServiceSmokeTest.java` 的 `.extracting(User::getEmail)` 均因此无法准确工作。
- **关联测试用例**：
  - `tests/blackbox/test_modern_java_features.py::TestGenericsVarLambdas::test_nested_lambda_inner_and_outer_completion` `[JAVA-11]`
  - `tests/blackbox/test_modern_java_features.py::TestGenericsVarLambdas::test_nested_lambda_hover_and_definition` `[JAVA-12]`
  - `tests/blackbox/test_modern_java_features.py::TestGenericsVarLambdas::test_three_level_nested_lambda_and_trailing_map` `[JAVA-13]`
  - `tests/blackbox/test_modern_java_features.py::TestGenericsVarLambdas::test_method_references_navigation_and_diagnostics` `[JAVA-14]`

#### GAP-042：Java 14–17 现代语法（`record`、`sealed`、`switch`/`yield`、`instanceof` 模式匹配、文本块新 API）缺失
- **ID 与严重级别**：`GAP-042` | `P1 核心缺失`
- **涉及 LSP 方法 / 特性**：`textDocument/completion`、`textDocument/definition`、`textDocument/hover`、`textDocument/implementation`
- **复现场景与黑盒实测对比**：
  - 场景：测试 `record Point(int x, int y)`（`p.x()` 跳转与 `p.` 补全）、泛型记录 `Box<String>(T value)`（`box.value().`）、`sealed interface Shape permits Circle, Rect`（`implementation`）、`switch (day) { case TUE -> { String label = ...; yield label. } }`、`if (obj instanceof String s)`（`s` 悬停/跳转/补全）、以及文本块 `"""...""".formatted(...).stripIndent()`。
  - 预期行为：完整支持 Java 17 现代语法的组件访问、模式变量作用域、`sealed` 子类及 Java 11+ String API。
  - 实际输出：`Point.x()` 与 `p.` 未能关联组件、`Box<String>.value().` 返回 0 项、`sealed` `implementation` 返回 `[]`、`yield label.` 返回 0 项、`instanceof` 模式变量 `s` 悬停/跳转/补全全空、文本块 `json.` **仅返回 7 个硬编码 `String` 方法**（`format`, `isEmpty`, `length`, `toLowerCase`, `toUpperCase`, `trim`, `valueOf`），缺失 `stripIndent()` 等现代 `String` 方法。
- **在 `java17-spring-demo` 及日常开发中的影响**：使用 Java 17 项目中最典型的 DTO `record`、模式匹配与文本块时均缺失核心 IDE 支持。
- **关联测试用例**：
  - `tests/blackbox/test_modern_java_features.py::TestJava14To17AndConstructs::test_record_auto_accessors_and_definition` `[JAVA-15]`
  - `tests/blackbox/test_modern_java_features.py::TestJava14To17AndConstructs::test_generic_record_compact_constructor` `[JAVA-16]`
  - `tests/blackbox/test_modern_java_features.py::TestJava14To17AndConstructs::test_sealed_interface_and_permits` `[JAVA-17]`
  - `tests/blackbox/test_modern_java_features.py::TestJava14To17AndConstructs::test_switch_expression_arrow_and_yield` `[JAVA-18]`
  - `tests/blackbox/test_modern_java_features.py::TestJava14To17AndConstructs::test_instanceof_pattern_matching` `[JAVA-19]`
  - `tests/blackbox/test_modern_java_features.py::TestJava14To17AndConstructs::test_text_blocks_completion_and_diagnostics` `[JAVA-20]`

#### GAP-043：嵌套类、`Outer.this.`、局部类、富枚举、数组/变长参数及多异常捕获补全缺失
- **ID 与严重级别**：`GAP-043` | `P1 核心缺失`
- **涉及 LSP 方法 / 特性**：`textDocument/completion`、`textDocument/publishDiagnostics`
- **复现场景与黑盒实测对比**：
  - 场景：测试 `StaticNested sn.`、`CoreConstructs.this.`、方法内局部类 `lh.`、带字段与抽象方法的枚举 `Operation.PLUS.` / `Operation.`、变长参数 `String... args -> args.`、多异常 `catch (IOException | SQLException e) { e.` 以及数组 `arr.` 与 `arr[0].`。
  - 预期行为：补全嵌套类/外部类/局部类/枚举常量及方法、数组 `.length` 与元素 `String` 方法、`Throwable.getMessage`。
  - 实际输出：以上全部场景在补全时返回 **0 项 (`[]`)**（且 `LocalHelper` 与 `SQLException` 还会触发误报错误）。
- **在 `java17-spring-demo` 及日常开发中的影响**：`UserEnum`、ES Builder 等嵌套静态类以及数组/异常捕获变量均无法获得补全提示。
- **关联测试用例**：
  - `tests/blackbox/test_modern_java_features.py::TestJava14To17AndConstructs::test_inner_and_static_nested_classes` `[JAVA-21]`
  - `tests/blackbox/test_modern_java_features.py::TestJava14To17AndConstructs::test_local_and_anonymous_classes` `[JAVA-22]`
  - `tests/blackbox/test_modern_java_features.py::TestJava14To17AndConstructs::test_enum_with_fields_constructors_and_abstract_methods` `[JAVA-23]`
  - `tests/blackbox/test_modern_java_features.py::TestJava14To17AndConstructs::test_varargs_array_properties` `[JAVA-24]`
  - `tests/blackbox/test_modern_java_features.py::TestJava14To17AndConstructs::test_try_with_resources_and_multi_catch` `[JAVA-25]` `[JAVA-26]`
  - `tests/blackbox/test_modern_java_features.py::TestJava14To17AndConstructs::test_array_length_and_element_indexing` `[JAVA-27]`

---

### 3.7 Lombok、Spring Boot/OpenFeign、MyBatis 与测试框架 (Lombok & Frameworks)

#### GAP-044：Lombok `@Data` 定义跳转失效，且 `@Value` 与 `@Getter`/`@Setter(AccessLevel)` 违背语义生成非法或越权方法
- **ID 与严重级别**：`GAP-044` | `P1 核心缺失`
- **涉及 LSP 方法 / 特性**：`textDocument/definition`、`textDocument/completion`（Lombok 处理）
- **复现场景与黑盒实测对比**：
  - 场景：(1) 在 `UserController` 中对 `@Data` 生成的 `request.getEmail()` 请求 `definition`；(2) 对不可变 `@Value class ValuePoint` 变量 `vp.` 请求补全；(3) 对标注 `@Setter(AccessLevel.NONE)` 与 `@Getter(AccessLevel.PRIVATE)` 的 `AccessControlled ac.` 请求外部补全。
  - 预期行为：(1) 跳转至 `CreateUserRequest.java` 的 `email` 字段；(2) `@Value` 不可变类禁止生成任何 `set*` 方法；(3) 隐藏 `setReadOnlySecret` 与 `getInternalCode`。
  - 实际输出：(1) `request.getEmail()` 跳转返回 `[]`；(2) `@Value vp.` 仍错误生成了 `setLabel(${1:label})`；(3) `@Setter(AccessLevel.NONE)` 未生效，依然向外部暴露 `setReadOnlySecret(${1:readOnlySecret})`。
- **在 `java17-spring-demo` 及日常开发中的影响**：`@Data` getter/setter 可以补全和悬停，但无法 Go to Definition 回到实体字段；使用 `@Value` 或细粒度访问控制时会推荐出实际上编译报错的方法。
- **关联测试用例**：
  - `tests/blackbox/test_lombok_and_frameworks.py::TestSpringDemoLombokSpringMyBatisAssertJ::test_lombok_data_getters_setters_and_definition` `[FW-01]`
  - `tests/blackbox/test_lombok_and_frameworks.py::TestLombokComprehensiveAndMyBatisXml::test_lombok_value_immutable_getters_no_setters` `[FW-02]`
  - `tests/blackbox/test_lombok_and_frameworks.py::TestLombokComprehensiveAndMyBatisXml::test_lombok_getter_setter_access_levels` `[FW-03]`

#### GAP-045：Lombok `@Builder` 与 `toBuilder = true` 链式补全及链上悬停失效
- **ID 与严重级别**：`GAP-045` | `P1 核心缺失`
- **涉及 LSP 方法 / 特性**：`textDocument/completion`、`textDocument/hover`（Lombok `@Builder`）
- **复现场景与黑盒实测对比**：
  - 场景：在 `UserServiceImpl.java` 中测试 `User.builder().`（及紧接换行 `.`）、`tb.toBuilder().` 补全，以及对 `User.builder().name(...).build()` 链上 `.name` 和 `.build` 请求 `hover`。
  - 预期行为：`builder().` 和 `toBuilder().` 补全字段设置方法（`id`, `name`, `email` / `title`, `version`）和 `build()`；`.name()` 与 `.build()` 显示 `UserBuilder` 与 `User` 类型签名。
  - 实际输出：`User.builder().` 与 `tb.toBuilder().` 返回 **0 项**（或换行后 `User.builder()\n      .` 仅回退返回 46 个 Java 关键字）；`hover(User.builder())` 可见 `UserBuilder builder()`，但链上的 `.name()` 与 `.build()` 悬停均返回 `None`。
- **在 `java17-spring-demo` 及日常开发中的影响**：`UserServiceImpl.createUser` 使用了 `User.builder().name(...).email(...).build()` 构建实体，该核心写法目前完全无 Builder 方法补全。
- **关联测试用例**：
  - `tests/blackbox/test_lombok_and_frameworks.py::TestSpringDemoLombokSpringMyBatisAssertJ::test_lombok_builder_chain` `[FW-04]`
  - `tests/blackbox/test_lombok_and_frameworks.py::TestLombokComprehensiveAndMyBatisXml::test_lombok_to_builder` `[FW-23]`
  - `tests/blackbox/test_spring_demo_e2e.py::TestSpringDemoE2E::test_e2e_03_lombok_builder_and_data_accessors` `[E2E-03]`
  - `tests/blackbox/test_hover_and_signature_help.py::TestSpringDemoHoverAndSignatureHelp::test_hover_15_lombok_builder_chain_hover` `[HOVER-15]`

#### GAP-046：Lombok `@EqualsAndHashCode`、`@ToString`、`@With` 与 `@Delegate` 未生成成员
- **ID 与严重级别**：`GAP-046` | `P1 核心缺失`
- **涉及 LSP 方法 / 特性**：`textDocument/completion`、`textDocument/publishDiagnostics`
- **复现场景与黑盒实测对比**：
  - 场景：对标注 `@EqualsAndHashCode @ToString` 的 `CtorBean c1.`、标注 `@With` 的 `WithBean wb.` 以及标注 `@Delegate List<String> inner` 的 `ListWrapper lw.` 请求点补全。
  - 预期行为：分别生成 `equals`/`hashCode`/`toString`、`withName` 及委托的 `List` 方法（`size`, `add`, `get`, `clear`）。
  - 实际输出：`c1.` 仅返回 `['CtorBean', 'CtorBean', 'getCode', 'getCount', 'code', 'count']`；`wb.` 仅返回 `['WithBean', 'getName', 'name']`；`lw.` 仅返回 `['inner']`。
- **在 `java17-spring-demo` 及日常开发中的影响**：超出基础 `@Getter`/`@Setter` 之外的常用 Lombok 派生方法均缺失补全与符号识别。
- **关联测试用例**：
  - `tests/blackbox/test_lombok_and_frameworks.py::TestLombokComprehensiveAndMyBatisXml::test_lombok_constructors_and_equals_tostring_clean` `[FW-06]` `[FW-07]`
  - `tests/blackbox/test_lombok_and_frameworks.py::TestLombokComprehensiveAndMyBatisXml::test_lombok_with_fluent_chain` `[FW-08]`
  - `tests/blackbox/test_lombok_and_frameworks.py::TestLombokComprehensiveAndMyBatisXml::test_lombok_delegate` `[FW-10]`

#### GAP-047：Spring `@RequiredArgsConstructor` 注入调用跳转错误且 Spring Web 注解与跨模块 OpenFeign 断链
- **ID 与严重级别**：`GAP-047` | `P1 核心缺失`
- **涉及 LSP 方法 / 特性**：`textDocument/definition`（Spring DI、Spring Web 注解、跨模块 OpenFeign）
- **复现场景与黑盒实测对比**：
  - 场景：(1) `UserController` 中通过 `@RequiredArgsConstructor` 注入的 `userService.getUser(id)`；(2) `UserController` 上的 `@GetMapping`；(3) 在 `core` 模块中调用 `api` 模块接口 `UserFeignClient.getUserName(id)`。
  - 预期行为：(1) 跳至 `UserService.java`；(2) 跳至 Spring Web `GetMapping.java`；(3) 跨模块跳至 `api/src/main/java/com/example/demo/api/UserFeignClient.java`。
  - 实际输出：(1) 错跳回 `UserController.java` 第 36 行自身方法 `getUser`；(2) `@GetMapping` 返回 `[]`；(3) 跨模块 `UserFeignClient` / `getUserName` 返回 `[]`（即使在 `UserController` 或独立 `FrameworkProbe` / `FeignBridgeProbe` 中均无法跳转）。
- **在 `java17-spring-demo` 及日常开发中的影响**：Spring Boot 微服务开发中最核心的 Controller -> Service 跳转、API 模块 Feign 接口跳转及注解源码查阅全部受阻。
- **关联测试用例**：
  - `tests/blackbox/test_lombok_and_frameworks.py::TestSpringDemoLombokSpringMyBatisAssertJ::test_spring_constructor_di_and_implementation` `[FW-11]`
  - `tests/blackbox/test_lombok_and_frameworks.py::TestSpringDemoLombokSpringMyBatisAssertJ::test_spring_web_annotations_diagnostics_and_hover` `[FW-13]`
  - `tests/blackbox/test_lombok_and_frameworks.py::TestSpringDemoLombokSpringMyBatisAssertJ::test_cross_module_openfeign_navigation` `[FW-14]`
  - `tests/blackbox/test_navigation_and_symbols.py::TestSpringDemoNavigationAndDependencies::test_nav_12_cross_module_api_core_navigation` `[NAV-12]`
  - `tests/blackbox/test_spring_demo_e2e.py::TestSpringDemoE2E::test_e2e_05_cross_module_api_to_core_feign_navigation` `[E2E-05]`

#### GAP-048：MyBatis Mapper 方法泛型返回值推导及 AssertJ 流式断言 / 静态导入失效
- **ID 与严重级别**：`GAP-048` | `P1 核心缺失`
- **涉及 LSP 方法 / 特性**：`textDocument/completion`、`textDocument/definition`
- **复现场景与黑盒实测对比**：
  - 场景：(1) 在 `UserServiceImpl.java` 对 `userMapper.findById(id).`（返回 `Optional<User>`）触发补全；(2) 在 `UserServiceSmokeTest.java` 对静态导入的 `assertThat(users)` 请求 `definition` 并对 `assertThat(users).` 触发流式补全。
  - 预期行为：(1) 补全 `Optional` 方法 `orElseThrow`、`map`、`isPresent`、`orElse`；(2) `assertThat` 跳至 `org.assertj.core.api.Assertions`，且补全 `extracting`、`hasSize`、`isNotEmpty`。
  - 实际输出：`userMapper.findById(id).` 与 `assertThat(users).` 均返回 **0 项**；`definition(assertThat)` 返回 `[]`（模拟器亦复现 `static_import` 与 `chained_call` 0 命中）。
- **在 `java17-spring-demo` 及日常开发中的影响**：MyBatis Mapper 与 XML 跳转虽已打通（`E2E-06`、`FW-15`、`FW-16` 均为 `PASS`），但在业务层链式调用 Mapper 返回的 `Optional`/`List` 及编写 JUnit/AssertJ 单测时均无补全与跳转支持。
- **关联测试用例**：
  - `tests/blackbox/test_lombok_and_frameworks.py::TestSpringDemoLombokSpringMyBatisAssertJ::test_mybatis_return_type_completion` `[FW-18]`
  - `tests/blackbox/test_lombok_and_frameworks.py::TestSpringDemoLombokSpringMyBatisAssertJ::test_assertj_fluent_chain_completion` `[FW-20]`
  - `tests/blackbox/test_lombok_and_frameworks.py::TestSpringDemoLombokSpringMyBatisAssertJ::test_assertj_static_import_definition_and_completion` `[FW-21]`

---

### 3.8 重构、Code Action、格式化与 JDTLS 扩展 (Refactoring, Code Actions & Formatting)

#### GAP-049：`textDocument/codeAction` 完全缺乏诊断快速修复（QuickFix），且提取方法模板会破坏代码语法
- **ID 与严重级别**：`GAP-049` | `P1 核心缺失`
- **涉及 LSP 方法 / 特性**：`textDocument/codeAction` (`quickfix`)
- **复现场景与黑盒实测对比**：
  - 场景：对 8 类常见诊断问题请求 `codeAction`：缺失导入 `List`/`BigDecimal`、未定义局部变量 `missingLocal = 42`、未定义字段 `this.missingField = "abc"`、未定义方法 `computeSum(1, 2)`、未实现接口 `Runnable`、无用导入 `Date`、缺少/无效 `@Override`、未处理受检异常 `IOException`。
  - 预期行为：提供针对性的 `quickfix`（自动导入、创建局部/字段/方法桩、生成 `run()`、删除导入/注解、`try/catch` 或 `throws`）。
  - 实际输出：所有场景仅返回通用 `'Organize Imports'`（空 edits 或重插入无用 `Date`）和/或 `"Extract to method 'extractedMethod'"`；后者在 `return computeSum(1, 2);`、`this.missingField = "abc";`、`throw new IOException("boom");` 等处**强行将表达式片段包进 `private void extractedMethod()`**，在调用点留下 `return extractedMethod();(1, 2);` 或 `this. extractedMethod(); = "abc";` 等严重语法破坏代码。
- **在 `java17-spring-demo` 及日常开发中的影响**：用户遇到缺失 `BigDecimal` 导入或未实现接口时，按快捷键触发 Code Action 不仅无法修复问题，误选默认首选动作（`isPreferred: true`）还会直接损坏源码。
- **关联测试用例**：
  - `tests/blackbox/test_refactoring_and_code_actions.py::TestCodeActions::test_quickfix_auto_import_missing_class` `[ACT-01]`
  - `tests/blackbox/test_refactoring_and_code_actions.py::TestCodeActions::test_quickfix_create_missing_local_variable` `[ACT-02]`
  - `tests/blackbox/test_refactoring_and_code_actions.py::TestCodeActions::test_quickfix_create_missing_field` `[ACT-03]`
  - `tests/blackbox/test_refactoring_and_code_actions.py::TestCodeActions::test_quickfix_create_missing_method` `[ACT-04]`
  - `tests/blackbox/test_refactoring_and_code_actions.py::TestCodeActions::test_quickfix_implement_unimplemented_methods` `[ACT-05]`
  - `tests/blackbox/test_refactoring_and_code_actions.py::TestCodeActions::test_quickfix_remove_unused_import` `[ACT-06]`
  - `tests/blackbox/test_refactoring_and_code_actions.py::TestCodeActions::test_quickfix_override_annotation_fixes` `[ACT-07]`
  - `tests/blackbox/test_refactoring_and_code_actions.py::TestCodeActions::test_quickfix_unhandled_checked_exception` `[ACT-08]`
  - `tests/blackbox/test_spring_demo_e2e.py::TestSpringDemoE2E::test_e2e_10_live_error_diagnostics_and_code_action_quickfix` `[E2E-10]`

#### GAP-050：`source.organizeImports` 不清除无用导入，且缺少 Getter/Setter 生成、Extract Variable、Inline Variable 与 `codeAction/resolve`
- **ID 与严重级别**：`GAP-050` | `P2 体验缺陷`
- **涉及 LSP 方法 / 特性**：`textDocument/codeAction` (`source.*`, `refactor.*`)、`codeAction/resolve`
- **复现场景与黑盒实测对比**：
  - 场景：(1) 在含重复 `List`、未排序 `Map` 与无用 `Date` 的文件中执行 `source.organizeImports`；(2) 请求 `source` 生成 getter/setter/构造器；(3) 请求 `refactor.extract.variable` 与 `refactor.inline`；(4) 调用 `codeAction/resolve`。
  - 预期行为：排序并删除无用 `Date`；提供源码生成与变量提取/内联；`codeAction/resolve` 返回有效 `CodeAction` 对象。
  - 实际输出：(1) `Organize Imports` 完成去重与字典序排序，但仍保留无用的 `import java.util.Date;`；(2)-(3) 未提供任何对应重构项（仅 `Extract Method` 即 `ACT-12` 可用）；(4) `codeAction/resolve` 返回 `None`。
- **在 `java17-spring-demo` 及日常开发中的影响**：整理导入无法清理废弃依赖，常用日常重构工具链残缺。
- **关联测试用例**：
  - `tests/blackbox/test_refactoring_and_code_actions.py::TestCodeActions::test_source_organize_imports` `[ACT-09]`
  - `tests/blackbox/test_refactoring_and_code_actions.py::TestCodeActions::test_source_generate_getters_setters_constructors` `[ACT-10]`
  - `tests/blackbox/test_refactoring_and_code_actions.py::TestCodeActions::test_refactor_extract_variable` `[ACT-11]`
  - `tests/blackbox/test_refactoring_and_code_actions.py::TestCodeActions::test_refactor_inline_variable` `[ACT-13]`
  - `tests/blackbox/test_refactoring_and_code_actions.py::TestCodeActions::test_code_action_resolve` `[ACT-14]`

#### GAP-051：`textDocument/rename` 仅限单文件局部变量/参数，无法跨文件、跨实现或联动重命名类文件
- **ID 与严重级别**：`GAP-051` | `P1 核心缺失`
- **涉及 LSP 方法 / 特性**：`textDocument/rename` (`WorkspaceEdit` 跨文件编辑与 `RenameFile` resourceOperation)
- **复现场景与黑盒实测对比**：
  - 场景：跨文件重命名字段 `CounterBox.counter`、类方法 `FormatterSvc.formatTag`、接口方法 `Greeter.sayHello` 与 `UserService.listUsers`、类 `OldWidget` 与 `CreateUserRequest`。
  - 预期行为：生成包含所有调用点、`@Override` 实现类、跨文件 `import` 以及 `RenameFile`（`OldWidget.java -> NewWidget.java`）的 `WorkspaceEdit`。
  - 实际输出：局部变量与参数重命名通过（`REF-01`、`REF-02` 均为 `PASS`），但所有字段/方法/接口/类重命名**仅返回声明所在的 1 个文件的编辑**：`CounterUser`、`FormatterCaller`、`EnglishGreeter`、`UserServiceImpl`、`UserController`、`WidgetClient` 均未被更新，且未生成 `RenameFile` 操作。
- **在 `java17-spring-demo` 及日常开发中的影响**：在多文件项目中重命名任何服务方法（如 `UserService.listUsers`）或实体类（如 `CreateUserRequest`、`User`）会立即破坏所有调用方与实现类编译。
- **关联测试用例**：
  - `tests/blackbox/test_refactoring_and_code_actions.py::TestRenameOperations::test_rename_field_cross_file` `[REF-03]`
  - `tests/blackbox/test_refactoring_and_code_actions.py::TestRenameOperations::test_rename_method_single_class_and_caller` `[REF-04]`
  - `tests/blackbox/test_refactoring_and_code_actions.py::TestRenameOperations::test_rename_interface_method_updates_impl_and_callers` `[REF-05]`
  - `tests/blackbox/test_refactoring_and_code_actions.py::TestRenameOperations::test_rename_class_updates_usages_imports_and_file_op` `[REF-06]`
  - `tests/blackbox/test_spring_demo_e2e.py::TestSpringDemoE2E::test_e2e_11_cross_file_rename_service_method_and_model` `[E2E-11]`

#### GAP-052：`prepareRename` 放行字面量与外部 JDK 符号，且 `rename` 缺乏作用域冲突检测
- **ID 与严重级别**：`GAP-052` | `P2 体验缺陷`
- **涉及 LSP 方法 / 特性**：`textDocument/prepareRename`、`textDocument/rename`
- **复现场景与黑盒实测对比**：
  - 场景：(1) 在字符串字面量 `"hello"`、数字 `42`、JDK 类型 `String` 与 `println` 上请求 `prepareRename`；(2) 将局部变量 `first` 重命名为同作用域内已存在的变量名 `second`。
  - 预期行为：(1) 拒绝不可重命名目标（返回 `null` 或错误）；(2) 拒绝导致重复声明冲突的重命名请求。
  - 实际输出：(1) `prepareRename` 仅拒绝关键字 `public`/`void`，但对 `"hello"`、`42`、`String`、`println` 均返回 `{'placeholder': ..., 'range': ...}`（且模拟器中 83/83 探针全部返回 hit）；(2) `first -> second` 静默执行成功，产生重复声明 `int second = 1; int second = 2;`。
- **在 `java17-spring-demo` 及日常开发中的影响**：误触发重命名可能破坏字符串常量或外部调用，或静默制造局部命名冲突编译错误。
- **关联测试用例**：
  - `tests/blackbox/test_refactoring_and_code_actions.py::TestRenameOperations::test_prepare_rename_rejects_keywords_literals_and_external_symbols` `[REF-07]`
  - `tests/blackbox/test_refactoring_and_code_actions.py::TestRenameOperations::test_rename_conflict_detection` `[REF-08]`

#### GAP-053：`workspace/willRenameFiles` 与 `workspace/didRenameFiles` 未更新包声明与符号索引
- **ID 与严重级别**：`GAP-053` | `P2 体验缺陷`
- **涉及 LSP 方法 / 特性**：`workspace/willRenameFiles`、`workspace/didRenameFiles`
- **复现场景与黑盒实测对比**：
  - 场景：(1) 将 `Mover.java` 从 `pkga` 移动至 `pkgb`，先发 `willRenameFiles`；(2) 将 `AlphaType.java` 重命名为 `BetaType.java` 后发 `didRenameFiles`，随后在调用方查询 `BetaType` 定义。
  - 预期行为：(1) 返回更新 `package` 及 `MoverClient` `import` 的 `WorkspaceEdit`；(2) 刷新索引使 `definition(BetaType)` 跳至新 URI。
  - 实际输出：`willRenameFiles` 返回 `None`；`didRenameFiles` 后 `definition(BetaType)` 返回 `[]`。
- **在 `java17-spring-demo` 及日常开发中的影响**：在文件树中移动 Java 类包路径或重命名文件时无法自动修复 `package`/`import`，且原索引出现陈旧失效。
- **关联测试用例**：
  - `tests/blackbox/test_refactoring_and_code_actions.py::TestRenameOperations::test_workspace_will_rename_files` `[REF-09]`
  - `tests/blackbox/test_refactoring_and_code_actions.py::TestRenameOperations::test_workspace_did_rename_files_updates_index` `[REF-10]`

#### GAP-054：`textDocument/formatting` 返回越界 `TextEdit` 行号且格式化不生效，范围/键入格式化未实现
- **ID 与严重级别**：`GAP-054` | `P2 体验缺陷`
- **涉及 LSP 方法 / 特性**：`textDocument/formatting`、`textDocument/rangeFormatting`、`textDocument/onTypeFormatting`
- **复现场景与黑盒实测对比**：
  - 场景：(1) 对含 3 行内容（4 个行首）且包含多余空格与单行类的 `MessyFmt.java` 请求全量格式化（`tabSize=2`）；(2) 分别请求 `rangeFormatting` 与 `}` 触发的 `onTypeFormatting`。
  - 预期行为：返回合法范围内的规范 Java 缩进与换行编辑，并实现范围和键入格式化。
  - 实际输出：(1) `formatting` 返回越界范围 `range: {start: {line: 0, character: 0}, end: {line: 4, character: 0}}`（`Line 4 out of range (total lines=4)`），且新文本保留 `public class   MessyFmt{int x=1;`（未折叠空格、未换行，强行按 4 空格而非 `tabSize=2` 缩进）；(2) `rangeFormatting` 与 `onTypeFormatting` 未声明能力且均返回 `None`。
- **在 `java17-spring-demo` 及日常开发中的影响**：启用保存时自动格式化（Format on Save）在严格校验 LSP 范围的客户端（如 VS Code / Neovim）上会因行号越界被拒绝应用或破坏格式。
- **关联测试用例**：
  - `tests/blackbox/test_protocol_and_lifecycle.py::TestServerCapabilitiesAudit::test_proto_13_range_and_on_type_formatting_capabilities` `[PROTO-13]`
  - `tests/blackbox/test_refactoring_and_code_actions.py::TestFormattingAndJdtlsCommands::test_document_formatting` `[FMT-01]`
  - `tests/blackbox/test_refactoring_and_code_actions.py::TestFormattingAndJdtlsCommands::test_range_formatting` `[FMT-02]`
  - `tests/blackbox/test_refactoring_and_code_actions.py::TestFormattingAndJdtlsCommands::test_on_type_formatting` `[FMT-03]`

#### GAP-055：JDTLS 扩展 `java/overridableMethods` 与 `java/overrideMethods` 返回硬编码 Mock 数据
- **ID 与严重级别**：`GAP-055` | `P1 核心缺失`
- **涉及 LSP 方法 / 特性**：`java/overridableMethods`、`java/overrideMethods`
- **复现场景与黑盒实测对比**：
  - 场景：在合成测试文件 `SubWorker extends BaseWorker implements Runnable` 与 `OverrideTarget implements Runnable` 上调用 `java/overridableMethods`。
  - 预期行为：动态分析类继承链并返回 `run`、`execute`、`priority`。
  - 实际输出：服务端无视当前打开文件，硬编码返回 `[{'name': 'listUsers', 'parent': 'UserService', 'required': False, 'returnType': 'List<User>', 'signature': 'listUsers()', 'unimplemented': True}]`，导致 `java/overrideMethods` 也无法为真实类生成方法桩。
- **在 `java17-spring-demo` 及日常开发中的影响**：README 中列出的 JDTLS 扩展接口目前属于硬编码桩实现（仅针对 `UserService` 示例硬编码），在其它任何类或真实开发场景下完全不可用。
- **关联测试用例**：
  - `tests/blackbox/test_refactoring_and_code_actions.py::TestFormattingAndJdtlsCommands::test_java_overridable_methods` `[JDT-01]`
  - `tests/blackbox/test_refactoring_and_code_actions.py::TestFormattingAndJdtlsCommands::test_java_override_methods` `[JDT-02]`

---

### 3.9 项目布局、构建系统与外部依赖解析 (Project, Build & Dependencies)

> **注**：本节与 `java17-spring-demo` 跨模块/依赖问题（部分已在前文功能域体现）交叉汇总，覆盖项目布局、构建解析、源码提取与热更新问题。

#### GAP-056：多模块 Maven (`core` <-> `api`) 跨模块符号及 `src/test/java` 测试域导航断链
- **ID 与严重级别**：`GAP-056` | `P1 核心缺失`
- **涉及 LSP 方法 / 特性**：多模块 Maven 工程解析、`src/test/java` 源码根、`textDocument/definition`
- **复现场景与黑盒实测对比**：
  - 场景：(1) 在 `java17-spring-demo` 的 `core` 模块（`UserRemoteBridge`）中对 `api` 模块接口 `UserFeignClient` 请求 `definition`；(2) 在 `core/src/test/java/com/example/demo/service/UserServiceSmokeTest.java` 中对 `UserService`、`@SpringBootTest`、`@Test` 请求 `definition`。
  - 预期行为：跨模块跳至 `api/.../UserFeignClient.java`，测试类可跳至 `core/src/main/java/.../UserService.java` 及测试依赖源码。
  - 实际输出：虽然文件打开时未报红（0 错误），但上述 `definition` 全部返回 `[]`（模拟器探针亦显示跨模块 `definition` 命中 `0`）。
- **在 `java17-spring-demo` 及日常开发中的影响**：多模块微服务项目中无法从业务实现跳至公共 `api` 契约模块；从单元测试也无法跳转到被测生产代码。
- **关联测试用例**：
  - `tests/blackbox/test_project_and_dependencies.py::TestMultiModuleMavenSpringDemo::test_multimodule_cross_module_core_to_api` `[PROJ-01]`
  - `tests/blackbox/test_project_and_dependencies.py::TestMultiModuleMavenSpringDemo::test_spring_demo_test_scope_and_main_resolution` `[PROJ-05]`

#### GAP-057：缺少 `src/main/java` 与 `src/test/java` 及 Maven `<scope>test</scope>` 类路径隔离
- **ID 与严重级别**：`GAP-057` | `P1 核心缺失`
- **涉及 LSP 方法 / 特性**：Maven 作用域隔离、`textDocument/publishDiagnostics`
- **复现场景与黑盒实测对比**：
  - 场景：构建包含 `pom.xml`（`junit-jupiter-api` 为 `<scope>test</scope>`）、`src/test/java/com/demo/TestHelper.java` 与违规的 `src/main/java/com/demo/LeakyMain.java`（主源码中非法引用 `@Test` 和 `TestHelper.assist()`）。
  - 预期行为：`LeakyMain` 应至少产生 2 条 Error 诊断（禁止主源码依赖 test scope 及 test 目录类）。
  - 实际输出：`LeakyMain` 诊断为 `[]`（0 错误），未区分 `main` 与 `test` 编译类路径。
- **在 `java17-spring-demo` 及日常开发中的影响**：开发者若不小心在主业务代码中引入 `org.junit.jupiter.api.Test`、`AssertJ` 或测试辅助类，LSP 无法提示错误，直到 Maven `compile` 失败才发现。
- **关联测试用例**：
  - `tests/blackbox/test_project_and_dependencies.py::TestProjectLayoutsAndIsolation::test_main_vs_test_classpath_isolation` `[PROJ-06]`

#### GAP-058：单模块 Maven、Parent POM / `<dependencyManagement>` / `${property}` 与传递依赖跳转解析失效
- **ID 与严重级别**：`GAP-058` | `P1 核心缺失`
- **涉及 LSP 方法 / 特性**：Maven BOM / 属性插值 / 传递依赖、`textDocument/definition`
- **复现场景与黑盒实测对比**：
  - 场景：(1) 单模块 Maven 引入 `gson:2.10`，测试 `definition(Gson)`；(2) 父 POM 定义 `<properties><jackson.version>2.17.2</jackson.version></properties>` 和 `<dependencyManagement>`，子模块省略版本号引入 `jackson-databind`，测试 `definition` 到 `ObjectMapper` 及传递依赖 `JsonProcessingException`、`@JsonProperty`。
  - 预期行为：准确解析属性版本号与传递依赖并支持 `definition` 跳转。
  - 实际输出：单模块 `Gson` 与 BOM/传递依赖 `ObjectMapper`、`JsonProcessingException`、`JsonProperty` 的 `definition` 全部返回 `[]`。
- **在 `java17-spring-demo` 及日常开发中的影响**：Spring Boot 等广泛依赖 Parent POM + BOM 管理版本的项目无法跳转至大多数直接和传递三方依赖源码。
- **关联测试用例**：
  - `tests/blackbox/test_project_and_dependencies.py::TestProjectLayoutsAndIsolation::test_single_module_maven_with_dependency` `[PROJ-02]`
  - `tests/blackbox/test_project_and_dependencies.py::TestProjectLayoutsAndIsolation::test_maven_parent_bom_properties_and_transitive_deps` `[PROJ-07]`

#### GAP-059：Gradle (`build.gradle` / `build.gradle.kts`) 与独立 `.java` 文件源码/依赖跳转失效
- **ID 与严重级别**：`GAP-059` | `P1 核心缺失`
- **涉及 LSP 方法 / 特性**：Gradle 构建解析、无构建文件单文件模式
- **复现场景与黑盒实测对比**：
  - 场景：(1) 构造分别使用 `build.gradle` 和 `build.gradle.kts` 的 Gradle 项目，引用同工程 `GradHelper` 与依赖 `Gson`；(2) 打开无 `pom.xml`/`build.gradle` 的 `StandaloneFile.java`（使用 `java.util.List`）。
  - 预期行为：Gradle 项目解析本地文件与依赖；独立文件解析 JDK 类型并支持 `definition`。
  - 实际输出：Gradle Groovy 与 Kotlin DSL 两个变体的 `GradHelper` 与 `Gson` `definition` 均返回 `[]`；独立文件的 `List` `definition` 亦返回 `[]`。
- **在 `java17-spring-demo` 及日常开发中的影响**：Gradle 项目中不仅三方依赖无法跳转，甚至同工程跨文件 `GradHelper` 也无法通过 `definition` 跳转；临时打开单个 `.java` 脚本也缺乏 JDK 导航。
- **关联测试用例**：
  - `tests/blackbox/test_project_and_dependencies.py::TestProjectLayoutsAndIsolation::test_gradle_groovy_and_kotlin_dsl_projects` `[PROJ-03]`
  - `tests/blackbox/test_project_and_dependencies.py::TestProjectLayoutsAndIsolation::test_standalone_java_file_without_build_descriptor` `[PROJ-04]`

#### GAP-060：JDK `src.zip` 提取跳转与多数 Maven 依赖（含无 `-sources.jar` 的 CFR 字节码反编译）失效
- **ID 与严重级别**：`GAP-060` | `P1 核心缺失`
- **涉及 LSP 方法 / 特性**：JDK `src.zip` 解压导航、Maven 依赖源码与 `lib/cfr.jar` 字节码反编译降级
- **复现场景与黑盒实测对比**：
  - 场景：尽管带有本地 `*-sources.jar` 的 `slf4j-api:2.0.13` 和 `commons-io:2.22.0` 在合成单模块测试（`PROJ-09`，状态 `PASS`）中 `definition` 通过，但在以下两类关键场景失败：
    1. 点击 JDK 标准库符号 `List`、`ArrayList`、`RuntimeException`、`String.format`（`[PROJ-08]`, `[NAV-15]`, `[E2E-12]`）；
    2. 点击无 `-sources.jar` 的 `gson:2.10`（`[PROJ-10]`）或 `java17-spring-demo` 中的 `@RestController`、`@GetMapping`、`@Param`、`@Data`（`[NAV-16]`）。
  - 预期行为：JDK 类型自动从 JDK `src.zip` 提取 `.java` 返回 `file://` URI；无 `-sources.jar` 的依赖通过 `lib/cfr.jar` 反编译为 `.java` 后返回有效路径。
  - 实际输出：JDK 符号与 CFR 降级场景的 `definition` 全部返回 `[]`（模拟器 83 探针中 `definition` 对 JDK 与 Maven 依赖命中均为 `0`）。
- **在 `java17-spring-demo` 及日常开发中的影响**：无法查看 JDK 标准库源码实现，也无法反编译查阅无源码包的第三方 JAR（与 README 描述能力存在明显差距）。
- **关联测试用例**：
  - `tests/blackbox/test_project_and_dependencies.py::TestExternalLibraryInspection::test_jdk_src_zip_navigation` `[PROJ-08]`
  - `tests/blackbox/test_project_and_dependencies.py::TestExternalLibraryInspection::test_cfr_bytecode_decompilation_without_sources_jar` `[PROJ-10]`
  - `tests/blackbox/test_navigation_and_symbols.py::TestSpringDemoNavigationAndDependencies::test_nav_15_jdk_stdlib_source_navigation` `[NAV-15]`
  - `tests/blackbox/test_navigation_and_symbols.py::TestSpringDemoNavigationAndDependencies::test_nav_16_maven_dependency_source_or_decompiled_navigation` `[NAV-16]`
  - `tests/blackbox/test_spring_demo_e2e.py::TestSpringDemoE2E::test_e2e_12_jdk_and_maven_source_extraction_uris` `[E2E-12]`

#### GAP-061：动态新增 `.java` 文件与修改 `pom.xml` 依赖不会触发工作区重索引与类路径刷新
- **ID 与严重级别**：`GAP-061` | `P1 核心缺失`
- **涉及 LSP 方法 / 特性**：`workspace/didCreateFiles`、`workspace/didChangeWatchedFiles`、`textDocument/didSave`、`pom.xml` 重载
- **复现场景与黑盒实测对比**：
  - 场景：(1) `DynApp.java` 引用尚未创建的 `NewHelper` 触发报错后，在磁盘写入 `NewHelper.java` 并发送 `didCreateFiles` + `didChangeWatchedFiles(type=1)`；(2) `DynPomApp.java` 引用 `Gson` 但初始 `pom.xml` 无依赖，随后写入 `gson:2.10` 并发送 `didChangeWatchedFiles(type=2)` + `didSave(pom.xml)`。
  - 预期行为：(1) `DynApp` 错误自动消除且 `definition(NewHelper)` 跳至新文件；(2) 初始应报 `Gson` 未解析，更新 `pom.xml` 后类路径自动刷新、跳转生效且错误清除。
  - 实际输出：(1) `definition(NewHelper)` 仍返回 `[]`；(2) 初始未对缺失依赖的 `Gson` 报错（返回 `[]`），更新 `pom.xml` 后 `definition(Gson)` 也依然返回 `[]`。
- **在 `java17-spring-demo` 及日常开发中的影响**：新增依赖或新增文件后必须重启编辑器中的 LSP 会话才能使新类或新库生效。
- **关联测试用例**：
  - `tests/blackbox/test_project_and_dependencies.py::TestDynamicProjectUpdates::test_add_new_java_file_dynamically` `[PROJ-11]`
  - `tests/blackbox/test_project_and_dependencies.py::TestDynamicProjectUpdates::test_modify_pom_xml_dependencies_dynamically` `[PROJ-12]`

> 注：经对照 `/tmp/blackbox_results.json` 交叉核验，上述 **GAP-001 至 GAP-061**（为保持编号连续清晰，本节共整理 **61** 条去重缺陷条目，即 `GAP-001`..`GAP-061`）已 **100%** 覆盖全部 **204** 个 `GAP_FAIL` 测试用例与全部 **207** 个失败 `[TAG-XX]` 标签，无一遗漏。

---

## 4. 新版自动化浏览与模拟编程测试工具说明

### 4.1 旧工具 `src/tools/ptest.cpp` (`bin/ptest`) 的局限性

根据仓库内既有报告（[ptest_report_spring_demo.md](file:///home/fakename/workspace/java-lsp/ptest_report_spring_demo.md)、[ptest_report_test_java.md](file:///home/fakename/workspace/java-lsp/ptest_report_test_java.md)、[ptest_report_gson.md](file:///home/fakename/workspace/java-lsp/ptest_report_gson.md)）及对 `./bin/ptest` 的黑盒运行观察，旧版模拟测试工具存在以下结构性局限：

1. **非纯黑盒协议级通信**：`bin/ptest` 直接链接服务端内部模块并在同一进程内驱动调用，绕过了标准 JSON-RPC stdio（`Content-Length` 字节分帧、`initialize` 能力协商、异步诊断通知队列），因此完全无法发现负列号 `SIGABRT` 崩溃、UTF-8/UTF-16 偏移错位、增量 `didChange` 失效、`didClose` 破坏磁盘回退、`-32601`/`-32600` 协议错误码缺失等关键协议缺陷。
2. **检测维度极度单一**：旧工具在报告中仅统计两项指标：
   - **非常量 Token 补全率**：如 `java17-spring-demo` 报告 `189 / 260 (72.7%)`、`test-java` 报告 `130 / 240 (54.2%)`、`gson` 报告 `6043 / 9340 (64.7%)`，仅记录 Token 名字列表（Missed Tokens），既未区分前缀触发、`.` 触发、链式类型图、静态导入、自动导包 `additionalTextEdits`，也未检查 Top-5 排序与 Snippet 合法性。
   - **与 `javac` 的诊断数量差值（Diagnostic Discrepancies）**：仅对比既有静态文件的报错数量，完全没有测试“主动注入各类语法/语义错误后服务端能否及时检出并在还原后清除”。
3. **LSP 功能覆盖严重缺失**：完全没有覆盖 `hover`、`definition` / `declaration` / `typeDefinition` / `implementation` / `references`、`documentSymbol` / `workspace/symbol`、`signatureHelp`（含 `activeParameter`）、`codeAction`、`prepareRename` / `rename`、`formatting`、JDTLS 扩展、以及 Maven/Gradle/隔离类路径/热更新。
4. **污染与硬编码风险**：旧报告中记录了绝对开发路径（如 `/Users/suremoon/...`），且直接面向本地工程目录运行，缺乏独立的临时副本隔离保护与自动化断言分类机制。

### 4.2 新版工具体系：`project_simulator.py` 与 `tests/blackbox/` 套件

新版黑盒测试基础设施位于 `tests/blackbox/`，由纯 Python 标准库编写，完全通过 stdio JSON-RPC 与 `bin/java-lsp` 交互：

- **可复用黑盒客户端底座**：[lsp_client.py](file:///home/fakename/workspace/java-lsp/tests/blackbox/lsp_client.py)
  - 独立线程异步读取 `stdout` 响应/通知与持续排空 `stderr`（彻底规避 ~128KB 启动日志造成的管道死锁）。
  - 内置 `TempJavaProject` 临时工作区隔离器（自动跳过 `target/` 并清理 `.classpath_m2.txt`，确保原仓库 `java17-spring-demo/` 的 `git status` 始终洁净）。
  - 提供严格的 UTF-16 行列换算工具（`pos_of`、`after_of`、`range_of`、`utf16_len`）和诊断静默等待（`wait_for_diagnostics_quiescent`）。
  - 清晰区分 `LspResponseError`（JSON-RPC 错误响应）、`TimeoutError`（超时）与 `ServerCrashedError`（进程崩溃并附带 `returncode` 与末尾 `stderr`）。

- **自动化项目浏览与模拟编程器**：[project_simulator.py](file:///home/fakename/workspace/java-lsp/tests/blackbox/project_simulator.py)
  - 默认以 `isolated_copy=True` 运行于临时副本，且所有模拟编辑仅通过内存态 `textDocument/didChange` 执行，不污染磁盘源码。
  - **Mode 1 — Automated Project Browser（自动化项目浏览）**：
    - 自动发现项目内全部 `.java` 文件（忽略 `target/`、`build/`、`.git/` 等目录），逐个打开并检测合法编译代码是否存在误报诊断。
    - 全量探测文件级与工作区级结构端点：`documentSymbol`、`foldingRange`、`semanticTokens/full`、`inlayHint`、`codeLens` 与 `workspace/symbol`。
    - 自动提取跨所有文件与符号类别的代表性探针（`import`、`@Slf4j`、注解、跨文件/跨模块类型、方法调用、变量/字段、Lambda 参数、类型声明），系统性调用 `hover`、`definition`、`declaration`、`typeDefinition`、`implementation`、`references`、`documentHighlight`、`prepareRename`。
    - **退化结果与来源识别**：自动标记 **自回显**（self-echo：返回光标自身的零宽范围）和 **文件头退化**（`(0, 0)`），并精确分类跳转目标来源（`same_file`、`cross_file`、`cross_module`、`jdk`、`maven_dep`、`mybatis_xml`、`missing_file`）。
  - **Mode 2 — Simulated Human Coding（模拟人类编程）**：
    - **2.1 渐进输入与补全**：从真实语句中剥离成员名、前缀（前 2 字符）、构造函数、注解 `@`、静态导入，并额外注入 Lambda 参数、`BigDec` 自动导包及链式类型图场景，统计命中率、Top-5 命中率、Snippet 返回数与 `additionalTextEdits` 自动导包率。
    - **2.2 实时签名帮助**：在真实调用点模拟刚输入 `(` 与 `, ` 两种打字瞬间，核对返回的签名列表及 `activeParameter`。
    - **2.3 错误注入与实时诊断恢复**：在方法体中逐一注入 8 类典型开发失误（缺分号、未定义标识符、未知方法、参数个数/类型错误、不兼容赋值、缺导包、未实现接口方法），检验能否检出 `Error` 诊断、定位行是否准确，并在恢复代码后验证错误是否干净清除。
    - **2.4 Code Action 检验**：针对注入的缺导包与未实现接口错误触发 `textDocument/codeAction`，区分普通动作与真正的 `quickfix`。
    - **2.5 Rename 跨文件工作流**：挑选项目内高引用度的类、接口方法、字段与局部变量执行 `prepareRename` 和 `rename`，在内存中应用返回的所有 `TextEdit` 并统计实际受影响的文件数。

- **统一黑盒测试运行器**：[run_blackbox_tests.py](file:///home/fakename/workspace/java-lsp/tests/blackbox/run_blackbox_tests.py)
  - 自动发现并执行所有 `tests/blackbox/test_*.py` 套件。
  - 将每条用例精准分类为 `PASS`、`GAP_FAIL`（`AssertionError`，明确指向功能缺失）、`HARNESS_ERROR`（非断言异常，要求严格为 `0`）或 `SKIP`，并按模块和 `[TAG-XX]` 标签汇总表格与结构化 JSON 报告。

### 4.3 常见 CLI 使用示例

```bash
# 1. 对任意本地 Maven/Gradle/Java 项目运行全量双模式模拟并输出 Markdown + JSON 报告
python3 tests/blackbox/project_simulator.py /path/to/your-java-project \
  --md-out /tmp/sim_report.md \
  --json-out /tmp/sim_report.json \
  -v

# 2. 仅运行 Mode 1 自动化项目浏览（不执行模拟编辑），最多检查前 30 个文件
python3 tests/blackbox/project_simulator.py java17-spring-demo \
  --mode browse \
  --max-files 30 \
  --md-out /tmp/browse_only.md

# 3. 仅运行 Mode 2 模拟人类编程，并使用自定义 java-lsp 二进制和更长超时
python3 tests/blackbox/project_simulator.py java17-spring-demo \
  --mode simulate \
  --binary ./bin/java-lsp \
  --timeout 30 \
  --md-out /tmp/sim_only.md

# 4. 运行黑盒测试套件的特定领域（如仅诊断与重构、或按 Gap ID 过滤）
python3 tests/blackbox/run_blackbox_tests.py --filter "test_diagnostics_accuracy|ACT-01" -v

# 5. 生成全量 JSON 测试报告并在遇到首个底座或断言错误时立即停止（调试用）
python3 tests/blackbox/run_blackbox_tests.py --filter "PROTO-" -x -v
```
