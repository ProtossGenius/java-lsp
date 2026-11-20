# `java-lsp` 黑盒能力评估与源码级根因分析报告

> **评估基准**：`java-lsp 1.0.0`（Linux x86_64 ELF 构建版本），采用**「先纯黑盒实测、后白盒源码深度 Review」双视角方法论**。  
> **核心结论**：
> 1. **阶段一（纯黑盒实测）**：在不阅读任何内部实现源码的前提下，通过标准 LSP 3.17 JSON-RPC stdio 协议运行全量 **253** 个黑盒测试用例，通过 **49** 项（**19.4%**），暴露真实功能缺失或行为缺陷 **204** 项（`GAP_FAIL`，覆盖 **207** 个失败标签），测试底座异常 **0** 项（`HARNESS_ERROR == 0`），总耗时 `136.583s`。在真实多模块项目 `java17-spring-demo` 的模拟编程评估中，自动化浏览 83 个位置探针仅实现部分同文件/MyBatis 跳转，代码补全命中率 **58.3%**（28/48），`signatureHelp` 命中率 **8.3%**（2/24），错误注入实时诊断检出率 **12.5%**（1/8），且存在负列号引发 `SIGABRT` 崩溃、UTF-8 字节偏移混用、忽略增量编辑与跨文件重命名失效等关键缺陷。
> 2. **阶段二（白盒源码深度 Review）**：在黑盒结果全部固化后，对 `src/` 下全部核心模块（`main.cpp`、`lsp/`、`utils/`、`parser/`、`semantics/`、`completion/`、`project/`、`mybatis/` 及 `tools/ptest.cpp`）进行逐行 C++ 源码审计，将全部 **61** 条 `GAP-001`..`GAP-061` 精准归因到精确文件、符号与行号区间，提炼 **8** 项系统性架构根因（见 §5.1），并额外发现 **18** 项黑盒未覆盖的安全、稳定性与并发隐患（`CR-001`..`CR-018`，见 §5.2），据此给出 5 阶段修复演进路线图（见 §6）。

---

## 1. 评估方法、双视角分析框架与测试环境说明

> **双视角评估说明**：本报告严格按「黑盒先行、白盒归因」两阶段完成——**阶段一**在零源码先验下以独立黑盒客户端驱动 `./bin/java-lsp`（§1.1–§1.3、§2、§3 现象与 §4.2），客观固化全部 253 个测试用例及 `java17-spring-demo` 83 探针的实测结论；**阶段二**随后对 `src/` 全部 C++ 实现逐行审计，补齐每条 `GAP` 的源码级根因与修复方案（§3）、剖析旧工具 `src/tools/ptest.cpp` 的代码级失效原因（§4.1），并系统性梳理架构级缺陷、隐藏风险与演进路线图（§5–§6）。

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
- **源码根因（Code Review）**：`parsePosition`（`src/lsp/protocol.cpp:167-176`）直接将 JSON 整数强转为 `int` 而未校验 `>= 0`；`Server::handleTextDocumentCompletion`（`src/lsp/server.cpp:272-276, 295`）将负 `character` 原样传入 `CompletionEngine::complete`；`CompletionEngine::extractTriggerAndPrefix`（`src/completion/completion_engine.cpp:41-61`）在 `src/completion/completion_engine.cpp:43` 仅钳制上界 `character > line.size()` 而未检查 `character < 0`，导致 `src/completion/completion_engine.cpp:47-48` 的 `wordStart = -5`，并在 `src/completion/completion_engine.cpp:58` 执行 `line.substr(wordStart, ...)` 时隐式转为 `size_t`（`SIZE_MAX - 4`）抛出未捕获的 `std::out_of_range`；由于 `Server::run`（`src/lsp/server.cpp:39-51`）与 `main`（`src/main.cpp:10-42`）均无 `try/catch`，进程直接 `std::terminate()` -> `SIGABRT`。
- **修复方案与涉及文件**：在 `parsePosition`（`src/lsp/protocol.cpp:167-176`）校验坐标范围并对非法参数返回 JSON-RPC `-32602`；在 `CompletionEngine::extractTriggerAndPrefix`（`src/completion/completion_engine.cpp:43`）补全 `if (character < 0) character = 0;`；在 `Server::run`（`src/lsp/server.cpp:41-49`）用 `try/catch` 兜底请求异常并返回 `-32603`。涉及文件：`src/lsp/protocol.cpp`、`src/lsp/server.cpp`、`src/completion/completion_engine.cpp`、`src/main.cpp`。

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
- **源码根因（Code Review）**：LSP 3.17 默认要求 `Position.character` 使用 UTF-16 code units，且 `Server::handleInitialize`（`src/lsp/server.cpp:156-188`）未协商 `positionEncoding`；但 `JavaParser::getNodeRange`（`src/parser/java_parser.cpp:38-46`）、`TypeChecker::getNodeRange`（`src/semantics/type_checker.cpp:15-23`）和 `NestedLambdaAnalyzer::getNodeRange`（`src/semantics/nested_lambda.cpp:14-22`）直接将 Tree-sitter `TSPoint::column`（UTF-8 字节偏移）写入 `Range`；同时 `Server::extractSymbolAtPosition`/`extractFullExpressionAtPosition`（`src/lsp/server.cpp:300-328`）、`handleTextDocumentDocumentSymbol`（`src/lsp/server.cpp:743-769`）、`references`/`prepareRename`/`rename`（`src/lsp/server.cpp:858, 895-905, 968, 995`）和 `CompletionEngine::extractTriggerAndPrefix`（`src/completion/completion_engine.cpp:41-61`）全部直接用 `std::string` 字节下标，遇到中文（3 UTF-8 字节 vs 1 UTF-16 unit）与 4 字节 Emoji（4 UTF-8 字节 vs 2 UTF-16 代理对）即产生向右偏移（如 +6）。
- **修复方案与涉及文件**：新增统一 UTF-8 字节偏移 <-> UTF-16 code unit 换算模块（`src/utils/utf_utils.{h,cpp}`），在 `Server` 入口将传入 LSP `Position` 转为字节偏移并在序列化响应/诊断前统一转为 UTF-16 `Range`（或配合 `capabilities.general.positionEncodings` 协商）。涉及文件：`src/utils/utf_utils.{h,cpp}`、`src/lsp/server.cpp`、`src/parser/java_parser.cpp`、`src/semantics/type_checker.cpp`、`src/semantics/nested_lambda.cpp`、`src/completion/completion_engine.cpp`。

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
- **源码根因（Code Review）**：`Server::handleInitialize`（`src/lsp/server.cpp:157`）写死 `{"textDocumentSync", 1}`（Full）；`Server::handleTextDocumentDidChange`（`src/lsp/server.cpp:207-221`）仅取 `(*cc)[0]` 的 `text` 字段直接调用 `pm_.updateFile(uri->str(), text->str())`，完全忽略 `change->get("range")` 与后续数组元素；当客户端发送增量片段 `"beta"` 时，整份文件被覆盖为 `"beta"`，且 `ProjectManager::updateFile`（`src/project/project_manager.cpp:148-153`）因新 AST `classes.empty()` 而把旧 `classes` 拼贴到已被破坏的 `sourceText` 上导致导航全失。
- **修复方案与涉及文件**：将 `textDocumentSync` 升级为 `2`（Incremental）；在 `Server::handleTextDocumentDidChange` 遍历 `contentChanges` 逐项读取 `range`（经 UTF-16 -> 字节偏移换算后对 `pm_.getFileContent(uri)` 执行 `replace`），全量应用后再调用 `pm_.updateFile` 与 `publishDiagnostics`。涉及文件：`src/lsp/server.cpp`、`src/project/project_manager.cpp`。

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
- **源码根因（Code Review）**：`Server::handleTextDocumentDidClose`（`src/lsp/server.cpp:223-231`）调用 `pm_.closeFile(uri->str())`，但 `ProjectManager::closeFile`（`src/project/project_manager.cpp:165-167`）是空实现（`// Keep cached CU for cross-file navigation`）。实测表象虽看似未读到磁盘，但白盒根因在于 `didClose` 既未清空脏缓冲也未从磁盘重载：此前 `didChange` 通过 `ProjectManager::updateFile`（`src/project/project_manager.cpp:132-163`）写入 `fileBuffers_`、`fileCUs_`、`fileScopes_` 与 `SymbolTable` 的内存版本（`memMethod()`）永久滞留并覆盖磁盘版本（`diskMethod()`），导致调用方 `hover(t.diskMethod())` 失败。
- **修复方案与涉及文件**：在 `ProjectManager::closeFile`（`src/project/project_manager.cpp:165-167`）加锁清理 `fileBuffers_`/`fileCUs_`/`fileScopes_`，调用 `SymbolTable::removeClassesByFile` 清理旧符号，若磁盘文件存在则调用 `indexJavaFile`（或重载 MyBatis XML）恢复磁盘态，并在 `Server::handleTextDocumentDidClose` 刷新/清理诊断。涉及文件：`src/project/project_manager.cpp`、`src/semantics/symbol_table.{h,cpp}`、`src/lsp/server.cpp`。

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
- **源码根因（Code Review）**：`Server::handleMessage`（`src/lsp/server.cpp:53-119`）对所有未识别带 `id` 请求落入 `src/lsp/server.cpp:115-118` 执行 `transport_.writeResponse(*idVal, nullptr)`（返回 `{"result": null}` 而非 JSON-RPC `-32601 Method not found`）；`Server`（`src/lsp/server.h:52`）无 `shutdownReceived_` 状态标志，`handleShutdown`（`src/lsp/server.cpp:190-192`）未记录关停态且 `handleMessage`（`src/lsp/server.cpp:53-119`）未拦截 `shutdown` 后的后续请求（应返回 `-32600`）；此外 `main`（`src/main.cpp:38-41`）无条件返回 `0`，未按 LSP 规范在未经 `shutdown` 直接 `exit` 时返回 `1`。
- **修复方案与涉及文件**：在 `Server`（`src/lsp/server.h`）增加 `initialized_` 与 `shutdownReceived_` 状态标志；`handleMessage` 在 `shutdownReceived_` 为真且非 `exit` 时返回 `-32600`，将未知请求兜底分支 `src/lsp/server.cpp:117` 改为 `transport_.writeError(*idVal, -32601, ...)`，并在 `main` 返回 `shutdownReceived_ ? 0 : 1`。涉及文件：`src/lsp/server.h`、`src/lsp/server.cpp`、`src/main.cpp`。

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
- **源码根因（Code Review）**：`Server::handleInitialize`（`src/lsp/server.cpp:156-177`）未声明 `capabilities.workspace`（`workspaceFolders` 与 `fileOperations`）；`Server::handleMessage`（`src/lsp/server.cpp:53-119`）仅分发了 `workspace/didRenameFiles`（`src/lsp/server.cpp:107-108`），静默丢弃 `workspace/didChangeWorkspaceFolders`、`workspace/didChangeWatchedFiles`、`workspace/didCreateFiles`、`workspace/didDeleteFiles` 与 `textDocument/didSave`；`ProjectManager`（`src/project/project_manager.h:36`、`src/project/project_manager.cpp:22-55`）仅维护单一 `rootPath_` 且仅在启动时一次性全量索引。
- **修复方案与涉及文件**：在 `Server::handleInitialize` 声明 `workspace.workspaceFolders` 与 `workspace.fileOperations`；在 `ProjectManager` 支持多工作区根目录 `workspaceRoots_` 与增量增删/重载文件接口；在 `Server::handleMessage` 补齐上述工作区与文件监听通知处理并触发跨文件诊断刷新。涉及文件：`src/lsp/server.cpp`、`src/project/project_manager.{h,cpp}`、`src/semantics/symbol_table.{h,cpp}`。

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
- **源码根因（Code Review）**：`Server::publishDiagnostics`（`src/lsp/server.cpp:246`）传入空 `TSNode{}` 调用 `TypeChecker::check`；`TypeChecker::collectDiagnostics`（`src/semantics/type_checker.cpp:249-354`）仅检查 `annotation`/`marker_annotation`（`src/semantics/type_checker.cpp:262`）、`scoped_type_identifier`（`src/semantics/type_checker.cpp:290`）与 `type_identifier`（`src/semantics/type_checker.cpp:319`），完全不检查 `ts_node_is_error(node)`、`type == "ERROR"` 或 `ts_node_is_missing(node)`；同时 `JavaParser::parseNode`（`src/parser/java_parser.cpp:151-154`）只向下递归 `ERROR` 节点而不记录任何语法诊断（`JavaCompilationUnit` 无语法错误字段），因此破损文件产生 `[]`。
- **修复方案与涉及文件**：在 `JavaParser::parse` 或 `TypeChecker::collectDiagnostics` 扫描 `ERROR` 与 `ts_node_is_missing(node)` 节点并产出 `severity=1` 语法错误诊断（同时屏蔽 `ERROR` 子树内的次生误报类型错误），并在 `JavaCompilationUnit` 缓存 `TSTree*` 以免重复解析。涉及文件：`src/semantics/type_checker.cpp`、`src/parser/java_parser.cpp`、`src/parser/java_ast.h`、`src/lsp/server.cpp`。

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
- **源码根因（Code Review）**：`Server::handleInitialize`（`src/lsp/server.cpp:156-177`）未声明 `diagnosticProvider` 与 `linkedEditingRangeProvider`；`Server::handleMessage`（`src/lsp/server.cpp:53-119`）也无 `textDocument/diagnostic`、`workspace/diagnostic` 和 `textDocument/linkedEditingRange` 分发逻辑，请求直接落入 `src/lsp/server.cpp:117` 返回 `null`。
- **修复方案与涉及文件**：在 `Server::handleInitialize` 声明 `diagnosticProvider` 与 `linkedEditingRangeProvider`，在 `Server::handleMessage` 新增 `handleTextDocumentDiagnostic`（包装 `TypeChecker::check` 输出 `DocumentDiagnosticReport`）与 `handleTextDocumentLinkedEditingRange`（基于作用域 AST 收集同标识编辑区间）。涉及文件：`src/lsp/server.{h,cpp}`、`src/lsp/protocol.{h,cpp}`。

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
- **源码根因（Code Review）**：`Server::handleTextDocumentDefinition`（`src/lsp/server.cpp:330-447`）先在 `src/lsp/server.cpp:361` 用 `extractSymbolAtPosition`（`src/lsp/server.cpp:300-313`）仅截取光标下单个词元（丢弃 `userService.` 或 `this.` 前缀）；随后 Step 1（`src/lsp/server.cpp:367-381`）调用 `inner->lookupVariable`（`src/semantics/scope.cpp:31-39`）无条件优先命中构造参数 `title`（导致 `this.title` 跳至形参而非字段）；Step 2（`src/lsp/server.cpp:383-403`）在未判断接收者的情况下遍历当前文件 `cu->classes` 成员，使 `UserController` 中 `userService.listUsers()`/`getUser()` 按同名立即命中 Controller 自身方法；最关键的是 Step 3（`src/lsp/server.cpp:405-444`）只调用 `DecompilationService::resolveSourceForFqn`（仅查归档），完全不检查工作区 `cls->filePath`。
- **修复方案与涉及文件**：基于光标处 Tree-sitter 节点（`field_access` / `method_invocation` / `identifier`）区分 `this.member`（直接查当前类字段/方法及父类，跳过局部变量）、`recv.member`（用 `TypeChecker::resolveExpressionType` 解析接收者类并定位成员声明）与无限定符符号；对工作区类（`!targetCls->filePath.empty()`）直接返回 `Location{fs::pathToUri(targetCls->filePath), targetRange}`，仅外部归档类才走 `DecompilationService`。涉及文件：`src/lsp/server.cpp`、`src/semantics/type_checker.cpp`、`src/semantics/scope.cpp`。

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
- **源码根因（Code Review）**：对于 `new Widget(...)`，`Server::handleTextDocumentDefinition`（`src/lsp/server.cpp:383-444`）既未识别 `object_creation_expression`，又只将 `Widget` 送入 `DecompilationService`（工作区类 `sourceFilePath` 为空直接返回 `nullopt`），更未在 `cls->methods` 中筛选 `m.isConstructor`（`src/parser/java_ast.h:80`、`src/parser/java_parser.cpp:320`）；对于方法重载（`w1.compute(10)` vs `"ten"` vs `(1, 2)`），`handleTextDocumentDefinition` 从未解析实参列表，`TypeChecker::resolveExpressionType`（`src/semantics/type_checker.cpp:40-47, 65-80`）剥离括号后只判断 `m.name == member` 并取首条命中。
- **修复方案与涉及文件**：在 Tree-sitter AST 上提取 `object_creation_expression` 与 `method_invocation` 的 `argument_list`，结合字面量与表达式类型推导各实参 `JavaTypeRef`，实现按参数个数（含 `spread_parameter` 变参）与可赋值兼容度打分的重载/构造器决议函数，并跳转到工作区 `cls->filePath` 中的 `matchedMethod.nameRange`。涉及文件：`src/lsp/server.cpp`、`src/semantics/type_checker.cpp`、`src/parser/java_parser.cpp`、`src/parser/java_ast.h`。

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
- **源码根因（Code Review）**：在 `Widget.describe()` 中调用 `super.describe()` 时，`Server::handleTextDocumentDefinition`（`src/lsp/server.cpp:361, 394-402`）提取裸名 `describe` 并直接命中 `cu->classes` 中的子类方法自身；在内部类 `CoreConstructs.Child.describe()` 中，`Child` 位于 `CoreConstructs.innerClasses` 而非顶级 `cu->classes`（`src/lsp/server.cpp:384`），故命中为空后落入 `DecompilationService` 返回 `[]`；同时 `Scope`（`src/semantics/scope.h:12-53`）未绑定 `this`/`super`，`TypeChecker::resolveExpressionType`（`src/semantics/type_checker.cpp:25-122`）从不处理 `super` 接收者或 `extendsType`（且 `JavaParser::parseClass` `src/parser/java_parser.cpp:222` 遗漏带包前缀的 `scoped_type_identifier` 父类）。
- **修复方案与涉及文件**：递归定位包含光标的最内层 `JavaClass`（含 `innerClasses`）；识别 `super.member` 或 `super(...)` 接收者，沿 `enclosingCls->extendsType` 在 `SymbolTable` 中向上解析父类（支持 `scoped_type_identifier`）并返回目标成员 `Location`。涉及文件：`src/lsp/server.cpp`、`src/semantics/type_checker.cpp`、`src/parser/java_parser.cpp`。

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
- **源码根因（Code Review）**：(1) 链式调用：`Server::extractFullExpressionAtPosition`（`src/lsp/server.cpp:315-328`）在 `(` / `)` 处截断，把 `w2.withCount(5).asBase().describe()` 砍成片段，且 `handleTextDocumentDefinition`（`src/lsp/server.cpp:405-444`）仍不跳转工作区 `cls->filePath`；(2) 静态导入：`JavaParser::parseImport`（`src/parser/java_parser.cpp:169-187`）丢弃 `static` 修饰符，`SymbolTable::resolveClass`（`src/semantics/symbol_table.cpp:273-305`）和 `Server` 不解析静态导入成员；(3) 枚举：`JavaParser::parseClass`（`src/parser/java_parser.cpp:255-269`）忽略 `enum_constant` 与包裹枚举字段/方法的 `enum_body_declarations`；(4) 嵌套/内部/匿名类：`JavaCompilationUnit::findClass`/`findClassAt`（`src/parser/java_ast.h:147-159`）和 `Server`（`src/lsp/server.cpp:384`）忽略 `innerClasses`，`collectLocalVariables`（`src/parser/java_parser.cpp:387-465`）忽略匿名类 `class_body` 与局部类。
- **修复方案与涉及文件**：用 Tree-sitter `method_invocation`/`field_access` 链逐跳求值并跳转工作区 `cls->filePath`；在导入结构中记录 `isStatic`/`isWildcard`；在 `JavaParser::parseClass` 解析 `enum_constant` 与 `enum_body_declarations`，并为内部类/局部类/匿名类构建嵌套 `JavaClass` 与 `CLASS` 作用域。涉及文件：`src/lsp/server.cpp`、`src/parser/java_ast.h`、`src/parser/java_parser.cpp`、`src/semantics/type_checker.cpp`、`src/semantics/symbol_table.cpp`。

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
- **源码根因（Code Review）**：`Server::handleTextDocumentTypeDefinition`（`src/lsp/server.cpp:625-663`）在 `src/lsp/server.cpp:643` 用会在括号处截断的 `extractFullExpressionAtPosition` 截取表达式后，在 `src/lsp/server.cpp:649-660` 获取到 `JavaTypeRef t` 并调用 `symTab_.resolveClass(t.name, *cu)`，却**完全不检查工作区 `cls->filePath`**，直接把 `fqn` 送入 `DecompilationService::resolveSourceForFqn(fqn)`（`src/project/decompilation.cpp:129-235`）：工作区类 `sourceFilePath` 为空故返回 `[]`；JDK 类因 `locateJdkSrcZip`（`src/project/decompilation.cpp:31-48`）只查 macOS `/opt/homebrew/...` 与 `$JAVA_HOME/lib/src.zip`（Linux 上 `src.zip` 缺失或未设 `JAVA_HOME` 时为空）同样返回 `[]`；`var` 变量因未推导类型导致 `t.name` 为空。
- **修复方案与涉及文件**：改用 AST 求值表达式类型（含链式调用与 `var`）；若目标 `JavaClass` 拥有 `!cls->filePath.empty()` 则直接返回 `{fs::pathToUri(cls->filePath), cls->nameRange}`；外部类型传入 `&symTab_` 调用 `DecompilationService::resolveSourceForFqn` 并修复 Linux JDK `src.zip`/`.jmod` 路径发现（`locateJdkSrcZip`）与声明位置定位。涉及文件：`src/lsp/server.cpp`、`src/project/decompilation.cpp`、`src/semantics/type_checker.cpp`。

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
- **源码根因（Code Review）**：`Server::handleTextDocumentImplementation`（`src/lsp/server.cpp:523-623`）完全硬编码路径规则：(1) `src/lsp/server.cpp:594-618` 对接口仅拼接 `<dir>/impl/<cls.name>Impl.java` 并按 `m.name == symbol` 匹配方法，当光标位于接口名 `UserService` 时无方法匹配，导致 `targetRange` 保留默认 `{{0,0},{0,0}}`；(2) `sealed` 接口与非 `/impl/*Impl.java` 实现类全部漏掉（`JavaParser::parseClass` `src/parser/java_parser.cpp:189-246` 也未解析 `permits` 子句）；(3) `@Slf4j log` 在 `src/lsp/server.cpp:548-557` 依赖 `DecompilationService`（`src/project/decompilation.cpp:223-232`）硬编码的 `logback-classic-1.5.6-sources.jar`，失败后在 `Impl.java` 中落入 `src/lsp/server.cpp:560-574` 自回显当前位置，在其它文件落入 `src/lsp/server.cpp:622` 返回 `[]`。
- **修复方案与涉及文件**：在 `JavaClass`（`src/parser/java_ast.h`）增加 `permitsTypes` 并在 `JavaParser::parseClass` 解析 `permits`；在 `SymbolTable` 建立基于 `extendsType`/`implementsTypes`/`permitsTypes` 的反向子类型索引；重写 `handleTextDocumentImplementation` 遍历直接与传递实现类/方法，移除 `/impl/*Impl.java` 硬编码与 `Impl.java` 自回显分支，并修复外部 JAR/CFR 反编译兜底。涉及文件：`src/lsp/server.cpp`、`src/parser/java_ast.h`、`src/parser/java_parser.cpp`、`src/semantics/symbol_table.{h,cpp}`、`src/project/decompilation.cpp`。

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
- **源码根因（Code Review）**：`Server::handleTextDocumentReferences`（`src/lsp/server.cpp:822-869`）存在三处根因：(1) `src/lsp/server.cpp:823-831` 从未读取 `params["context"]["includeDeclaration"]`；(2) `src/lsp/server.cpp:843-866` 遍历磁盘上所有 `.java` 文件（忽略 `pm_` 未保存的内存修改）逐行 `fline.find(symbol, c)` 做纯子串匹配，且 `src/lsp/server.cpp:853-854` 未校验 `'_'`、未跳过注释/字符串；(3) 完全不做作用域和重载/接收者类型核对，导致局部变量 `shadow` 跨方法/跨文件泄漏，同名重载方法全部混杂。
- **修复方案与涉及文件**：解析 `context.includeDeclaration` 并按需剔除声明位置；先语义定位光标符号归属（局部变量/参数限定在声明 `Scope` 内经 AST token 精确匹配且避开遮蔽作用域与 `other.name`；成员/类型跨内存缓冲与工作区 AST 验证接收者类型及重载签名匹配）。涉及文件：`src/lsp/server.cpp`、`src/project/project_manager.cpp`、`src/semantics/type_checker.cpp`。

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
- **源码根因（Code Review）**：`Server::handleTextDocumentDocumentSymbol`（`src/lsp/server.cpp:731-772`）在 `src/lsp/server.cpp:743-769` 只遍历顶层 `cu->classes`，**完全遗漏 `cls.innerClasses`**，且未将 `ClassKind::RECORD` 映射到独立类型与组件子节点（`src/lsp/server.cpp:746`）；同时 `JavaParser::parseClass`（`src/parser/java_parser.cpp:255-269`）未处理 `enum_constant`、`enum_body_declarations`（tree-sitter-java 将枚举字段/构造/方法包裹于此节点下）和 `record_declaration` 的 `formal_parameters`（record 组件）；此外 `ProjectManager::registerClassAndInners`（`src/project/project_manager.cpp:59`）直接调用 `LombokHandler::processClass` 原地注入 `range={{0,0},{0,0}}` 合成方法导致大纲包含退化坐标。
- **修复方案与涉及文件**：抽出递归 `buildClassSymbol` 遍历 `cls.innerClasses`，将枚举常量标为 `EnumMember(22)`、构造器标为 `Constructor(9)` 并过滤无源码范围的 Lombok 合成成员；扩展 `JavaParser::parseClass` 解析 `enum_constant`、`enum_body_declarations` 与 record 组件（`formal_parameters`）。涉及文件：`src/lsp/server.cpp`、`src/parser/java_parser.cpp`、`src/parser/java_ast.h`。

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
- **源码根因（Code Review）**：`Server::handleInitialize`（`src/lsp/server.cpp:156-177`）未声明 `workspaceSymbolProvider`，`Server::handleMessage`（`src/lsp/server.cpp:53-119`）也无 `"workspace/symbol"` 分支，请求直落 `src/lsp/server.cpp:117` 返回 `null`。
- **修复方案与涉及文件**：在 `handleInitialize` 声明 `workspaceSymbolProvider: true`，新增 `Server::handleWorkspaceSymbol` 从 `symTab_.getAllClasses()` 检索工作区类（含 `innerClasses`）、方法与字段，按子串/CamelCase 匹配并返回 UTF-16 `SymbolInformation[]`。涉及文件：`src/lsp/server.{h,cpp}`。

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
- **源码根因（Code Review）**：`Server::handleInitialize`（`src/lsp/server.cpp:156-177`）缺少 `documentHighlightProvider`、`foldingRangeProvider`、`selectionRangeProvider`；`Server::handleMessage`（`src/lsp/server.cpp:53-119`）无对应分发分支，均在 `src/lsp/server.cpp:117` 返回 `null`。
- **修复方案与涉及文件**：在 `handleInitialize` 声明三项能力并在 `Server` 实现基于 Tree-sitter CST 的 `handleTextDocumentDocumentHighlight`（同作用域/同符号读写高亮）、`handleTextDocumentFoldingRange`（类体/方法体/连续导入/块注释）与 `handleTextDocumentSelectionRange`（沿 `ts_node_parent` 向上构造选区链）。涉及文件：`src/lsp/server.{h,cpp}`、`src/lsp/protocol.{h,cpp}`。

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
- **源码根因（Code Review）**：`Server::handleInitialize`（`src/lsp/server.cpp:156-177`）与 `Server::handleMessage`（`src/lsp/server.cpp:53-119`）均未实现 `textDocument/semanticTokens/full`、`textDocument/inlayHint`、`textDocument/prepareCallHierarchy` (+ `incomingCalls`/`outgoingCalls`)、`textDocument/prepareTypeHierarchy` (+ `supertypes`/`subtypes`)、`textDocument/codeLens` 与 `textDocument/documentLink`，全部落入 `src/lsp/server.cpp:117` 返回 `null`。
- **修复方案与涉及文件**：在 `handleInitialize` 声明完整能力（含 `semanticTokensProvider.legend`）并在 `Server` 依托 Tree-sitter CST 与 `SymbolTable` 反向索引实现语义 Token 5 元组编码、形参/推导类型 InlayHint、调用与继承层级图及 CodeLens/DocumentLink 处理器。涉及文件：`src/lsp/server.{h,cpp}`、`src/lsp/protocol.{h,cpp}`、`src/semantics/symbol_table.{h,cpp}`。

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
- **源码根因（Code Review）**：`CompletionEngine::addClassMembers`（`src/completion/completion_engine.cpp:287-381`）存在三处缺陷：(1) 仅遍历当前类 `cls.methods`/`cls.fields`（`src/completion/completion_engine.cpp:294, 371`），从不向上遍历 `cls.extendsType`、`cls.implementsTypes` 与 `java.lang.Object`；(2) 从未跳过 `if (m.isConstructor) continue;`（`src/completion/completion_engine.cpp:294`），把写入 `cls.methods` 的构造函数（`src/parser/java_parser.cpp:320`）当作实例方法以 `kind=2`、`sortText="020_..."` 输出，导致 `020_UserAccount` 排在 `020_getUser` 之前；(3) `JavaParser::parseClass`（`src/parser/java_parser.cpp:218-242`）不解析接口 `extends_interfaces` 与 `scoped_type_identifier`，`SymbolTable::registerJdkTypes`（`src/semantics/symbol_table.cpp:449-468`）中的 `Object` 缺失 `getClass`/`notify`/`wait` 等方法，且 `JarIndexer::indexArchive`（`src/project/jar_indexer.cpp:84-170`）仅生成无方法/无父类的空类桩。
- **修复方案与涉及文件**：在 `CompletionEngine::addClassMembers` 过滤 `m.isConstructor`，实现带 visited 集合的 BFS/DFS 沿 `extendsType -> implementsTypes -> Object` 继承链聚合签名去重后的成员并按继承深度分层 `sortText`（`000_` > `010_` > `020_` > `040_`）；在 `JavaParser::parseClass` 补齐 `extends_interfaces`/`scoped_type_identifier`；完善 `Object` 基类方法并在 `JarIndexer` 解析 `.class` 字节码成员与父类元数据。涉及文件：`src/completion/completion_engine.cpp`、`src/parser/java_parser.cpp`、`src/semantics/symbol_table.cpp`、`src/project/jar_indexer.cpp`。

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
- **源码根因（Code Review）**：(1) 静态上下文被 `TypeChecker::resolveExpressionType` 破坏：`CompletionEngine::complete`（`src/completion/completion_engine.cpp:174-192`）仅在 `receiverType.name.empty()` 时才进入 `src/completion/completion_engine.cpp:179-186` 设置 `isStatic = true`，但 `TypeChecker::resolveExpressionType("UserAccount", ...)`（`src/semantics/type_checker.cpp:116-119`）查到类名直接返回非空 `JavaTypeRef{"UserAccount", ...}`，致使 `src/completion/completion_engine.cpp:179` 为假、走入 `else` 分支 `isStatic = false`，将实例方法混入静态类名补全；(2) `addClassMembers`（`src/completion/completion_engine.cpp:370-380`）用 `if (!isStaticOnly)` 整块隐藏字段（连 `static` 常量一并屏蔽）；(3) `CompletionEngine::addClassMembers`（`src/completion/completion_engine.h:28-33`）未传入调用方包名与当前类信息，也从不检查 `private`/`protected`/package-private 修饰符。
- **修复方案与涉及文件**：在表达式解析结果中显式区分「类型引用（静态上下文）」与「实例表达式」；在 `addClassMembers` 改为按 `f.isStatic()` 过滤字段而非整块跳过，并传入调用方 `cu.packageName` 与外围类链按 JLS §6.6 执行可见性过滤。涉及文件：`src/completion/completion_engine.{h,cpp}`、`src/semantics/type_checker.cpp`、`src/parser/java_ast.h`。

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
- **源码根因（Code Review）**：(1) 自动导包：`CompletionEngine::complete`（`src/completion/completion_engine.cpp:250-257`）生成的类补全项从未填充 `CompletionItem::additionalTextEdits`（`src/lsp/protocol.h:67`、`src/lsp/protocol.cpp:63-69`），且 `SymbolTable::findClassesMatchingPrefix`（`src/semantics/symbol_table.cpp:331-342`）在无序 `unordered_map` 中满 50 条即 `break`（`src/semantics/symbol_table.cpp:338`），会漏掉大量候选；(2) `import` 语句补全：`extractTriggerAndPrefix`（`src/completion/completion_engine.cpp:97`）在空格处停止，对 `import java.util.` 返回 `receiver="java.util"` 后 `resolveClass` 失败，`CompletionEngine` 从未调用 `SymbolTable::getClassesInPackage`（`src/semantics/symbol_table.cpp:323-329`）且 `SymbolTable` 无子包前缀索引；(3) 静态导入：`JavaParser::parseImport`（`src/parser/java_parser.cpp:169-187`）丢失 `static` 关键字，`CompletionEngine` 不检索静态导入成员，且 `JarIndexer`（`src/project/jar_indexer.cpp:84-170`）未索引外部 JAR（`Collections`、`Assertions`）的方法。
- **修复方案与涉及文件**：升级 `JavaCompilationUnit::imports`（`src/parser/java_ast.h:142`）为含 `isStatic`/`isWildcard` 的结构体；在 `CompletionEngine` 识别 `import [static] ...` 补全包与类成员、在无接收者补全中解析静态导入符号，移除 50 条无序截断并为未导入类附带插入 `import ...;` 的 `additionalTextEdits`；配合 `JarIndexer` 字节码方法索引。涉及文件：`src/completion/completion_engine.cpp`、`src/parser/java_ast.h`、`src/parser/java_parser.cpp`、`src/semantics/symbol_table.{h,cpp}`、`src/project/jar_indexer.cpp`。

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
- **源码根因（Code Review）**：`CompletionEngine::complete`（`src/completion/completion_engine.cpp:216-258`）在无 `.` 的标识符补全中从未检测光标前是否存在 `new ` 关键字（或赋值 LHS 泛型目标类型），`src/completion/completion_engine.cpp:250-257` 仅从 `findClassesMatchingPrefix` 为每个类发出一条 `kind = 7`（Class）条目，完全忽略 `cls.methods` 中已经解析好的全部 `m.isConstructor == true` 重载构造器（`src/parser/java_parser.cpp:320, 382-384`）。
- **修复方案与涉及文件**：在无接收者上下文中检测 `new\s+<prefix>` 前缀，遍历匹配具体类的可见构造器（无显式构造器时合成无参默认构造器），为每个重载生成 `kind = 4`（Constructor）的带参 Snippet 补全项（支持泛型菱形 `<>()` 及未导入类的 `additionalTextEdits`）。涉及文件：`src/completion/completion_engine.cpp`、`src/parser/java_parser.cpp`。

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
- **源码根因（Code Review）**：(1) `@interface` 丢失：`JavaParser::parseNode`（`src/parser/java_parser.cpp:148-150`）与 `JavaParser::parseClass`（`src/parser/java_parser.cpp:264-266`）遗漏 `annotation_type_declaration`、`annotation_type_body` 与 `annotation_type_element_declaration`，致使自定义注解 `@interface MyTag` 根本不进入 `SymbolTable`；(2) `@` 上下文未过滤：`extractTriggerAndPrefix`（`src/completion/completion_engine.cpp:59-61`）剥离 `'@'` 后未向 `complete` 传递注解触发标记，导致 `@Dat` 混入普通类并被 `findClassesMatchingPrefix`（`src/semantics/symbol_table.cpp:331-342`）的 50 条截断淘汰；(3) `@MyTag(` 在 `'('` 处被截断为 `{"", ""}` 且无注解属性解析；(4) 类体 `@Override`：`ProjectManager::registerClassAndInners`（`src/project/project_manager.cpp:62`）将类体标记为 `ScopeKind::CLASS`，`CompletionEngine::complete` 从未在 `CLASS` 作用域内检索父类/接口可重写方法。
- **修复方案与涉及文件**：在 `ClassKind` 增加 `ANNOTATION`（`src/parser/java_ast.h:105-110`）并在 `JavaParser` 解析 `@interface` 及其属性元素；在 `CompletionEngine` 区分 `@` 注解触发、`@Annot(` 属性名列表及 `ScopeKind::CLASS` 类体内可重写方法 `@Override` 桩生成。涉及文件：`src/parser/java_ast.h`、`src/parser/java_parser.cpp`、`src/completion/completion_engine.{h,cpp}`、`src/semantics/symbol_table.cpp`。

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
- **源码根因（Code Review）**：`Server::handleInitialize`（`src/lsp/server.cpp:160`）明确写死 `"resolveProvider", false`；`Server::handleMessage`（`src/lsp/server.cpp:67-119`）未处理 `"completionItem/resolve"`（直落 `src/lsp/server.cpp:117` 返回 `null`）；`CompletionItem`（`src/lsp/protocol.h:57-68`）与 `protocol.cpp`（`src/lsp/protocol.cpp:46-71`）均缺失 `data` 负载字段与反序列化 helper `parseCompletionItem`。
- **修复方案与涉及文件**：在 `CompletionItem` 增加 `std::optional<llvm::json::Value> data` 与序列化/反序列化支持；补全阶段写入 `{fqn, member, signature, callerUri}` 到 `data`；设置 `resolveProvider: true` 并实现 `Server::handleCompletionItemResolve` 按需填充完整 Javadoc、`detail` 与延迟 `additionalTextEdits`。涉及文件：`src/lsp/protocol.{h,cpp}`、`src/lsp/server.{h,cpp}`、`src/completion/completion_engine.cpp`。

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
- **源码根因（Code Review）**：(1) `return` 上下文失效：`CompletionEngine::extractExpectedType`（`src/completion/completion_engine.cpp:113-145`）仅搜当前行 `'='`（`src/completion/completion_engine.cpp:119`），`return nums.` 导致 `expectedType` 为空、跳过 `graphSearch_.searchPaths`（`src/completion/completion_engine.cpp:199`）与返回类型过滤（`src/completion/completion_engine.cpp:312`）；(2) `Optional`/`Map`/`Set` 零方法：`SymbolTable::registerJdkTypes`（`src/semantics/symbol_table.cpp:376-385`）通过 `registerSimpleClass` 注册空方法壳，仅 `Object`/`String`/`List`/`Stream` 有方法；(3) `TypeGraphSearch`（`src/completion/graph_search.cpp:12-145`）无 `Map` 边，在 `src/completion/graph_search.cpp:120-129` 仅针对 `toTypePattern == "Stream"` 更新 `currentArg`（导致 `Optional<A> -> Optional<B>` 保留旧 `UserDto` 无法匹配 `String`，`Optional<A> -> Any` 保留字面 `"Any"` 无法匹配 `UserDto`），且 `src/completion/graph_search.cpp:20, 37` 使用非法 Snippet 语法 `"map($1-it->$2)"`；(4) 流式 Lambda 失效：`ProjectManager`（`src/project/project_manager.cpp:6`）引入了 `nested_lambda.h` 却从未调用 `NestedLambdaAnalyzer`，且 `inferLambdaParamType`（`src/semantics/nested_lambda.cpp:44-48`）在首个 `.` 处截断接收者链。
- **修复方案与涉及文件**：扩展 `extractExpectedType` 从 enclosing `JavaMethod::returnType` 与方法实参提取预期类型；重构 `TypeGraphSearch` 支持模式变量绑定（`A`/`B`/`K`/`V`）、补齐 `Map`/`Optional`/`Set` 转换边与合法 `${1:it}` Snippet；为 `Optional`/`Map`/`Set` 补充成员并启用 `NestedLambdaAnalyzer` + 完整链路类型推导。涉及文件：`src/completion/completion_engine.cpp`、`src/completion/graph_search.cpp`、`src/semantics/symbol_table.cpp`、`src/semantics/nested_lambda.cpp`、`src/project/project_manager.cpp`。

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
- **源码根因（Code Review）**：`Server::handleTextDocumentHover`（`src/lsp/server.cpp:665-729`）仅定义 `const JavaMethod* matchedMethod = nullptr`（`src/lsp/server.cpp:686`），**完全没有渲染 `JavaVariable`（局部变量）、`JavaField`（字段/常量）或 `JavaClass`（类/注解）的代码分支**；成员分支（`src/lsp/server.cpp:690-703`）仅搜 `rcls->methods`，无限定符分支（`src/lsp/server.cpp:705-716`）跳过 `inner->lookupVariable`、`cu->classes` 字段与 `symTab_.resolveClass`；同时 `JavaParser::parseField`（`src/parser/java_parser.cpp:278-314`）与 `parseClass`（`src/parser/java_parser.cpp:189-276`）也未提取前驱 `block_comment` Javadoc（只有 `parseMethod` `src/parser/java_parser.cpp:323-326` 提取了）。
- **修复方案与涉及文件**：重构 `handleTextDocumentHover` 按 AST 节点与作用域层级依次渲染：注解（`@interface` + `cls->docComment`）、接收者成员（方法与字段兼查，含父类）、局部变量/形参（`Scope::lookupVariable`）、本类字段与类/枚举/记录定义；在 `JavaParser::parseField` 和 `parseClass` 补齐 `block_comment` Javadoc 提取。涉及文件：`src/lsp/server.cpp`、`src/parser/java_parser.cpp`。

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
- **源码根因（Code Review）**：(1) 类悬停被构造器劫持：`JavaParser::parseMethod`（`src/parser/java_parser.cpp:320, 348`）将构造函数以 `m.name = cls.name` 存入 `cls.methods` 并提取其 Javadoc，而 `parseClass`（`src/parser/java_parser.cpp:189-276`）不提取类 Javadoc；在 `Person ada` 上悬停 `Person` 时，`Server::handleTextDocumentHover`（`src/lsp/server.cpp:705-716`）遍历全表方法匹配 `m.name == "Person"` 而不校验 AST 节点是否为 `type_identifier`，误显示构造器签名与构造器 Javadoc；(2) 泛型未代换：`Server::handleTextDocumentHover`（`src/lsp/server.cpp:693-702, 719`）虽拿到 `rt = List<Person>`，却直接打印原始 `matchedMethod->returnType.toString()`（`"E"`），从未按 `rt.typeArguments` 做泛型实参替换。
- **修复方案与涉及文件**：根据光标 AST 上下文区分 `type_identifier` 类引用与 `object_creation_expression` 构造调用，对类引用格式化类声明与类 `docComment`；在 `JavaClass`/`JavaMethod` 记录 `typeParameters` 并实现通用 `substituteTypeArgs` 替换返回值与参数类型后再渲染 Hover Markdown。涉及文件：`src/lsp/server.cpp`、`src/parser/java_parser.cpp`、`src/parser/java_ast.h`、`src/semantics/type_checker.cpp`。

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
- **源码根因（Code Review）**：(1) `EsInitconstant` 符号劫持：`ProjectManager::registerClassAndInners`（`src/project/project_manager.cpp:71-89`）从未构建 `ScopeKind::LAMBDA` 子作用域（`NestedLambdaAnalyzer` 为死代码且对 `x -> ...` 单参数失效），`Server::handleTextDocumentHover`（`src/lsp/server.cpp:705-716`）在局部匹配失败后跌入**全工作区无作用域 `symTab_.getAllClasses()` 裸方法名循环**，从而用 `User` 类上由 `LombokHandler::applyBuilder`（`src/semantics/lombok.cpp:230-236`）生成的 `User.builder()` 劫持了 Elasticsearch Lambda 参数 `builder`；(2) Lombok `@Slf4j log` 挂在 `cls.fields`（`src/semantics/lombok.cpp:108-118`）而被 `hover` 忽略，`@Builder` 未生成 `UserBuilder` 类且 `extractFullExpressionAtPosition`（`src/lsp/server.cpp:315-328`）在 `(` 处截断链式调用；(3) `var`、Lambda、`instanceof` 模式变量未被 `JavaParser::collectLocalVariables`（`src/parser/java_parser.cpp:387-465`）记录，外部类 `RuntimeException`（`src/semantics/symbol_table.cpp:358`）与静态导入 `assertThat` 因无 `JavaClass`/静态导入 Hover 分支而返回空。
- **修复方案与涉及文件**：立即删除 `Server::handleTextDocumentHover`（`src/lsp/server.cpp:705-716`）中的全局 `getAllClasses()` 方法扫描；启用修复后的 `NestedLambdaAnalyzer`（含类字段初始化器如 `MEtA_OBJECT_MAPPING`），用 Tree-sitter AST 求值链式调用、`var`/模式匹配/Lambda 参数、`log` 字段、Lombok `Builder` 合成类、类定义与静态导入成员。涉及文件：`src/lsp/server.cpp`、`src/semantics/nested_lambda.cpp`、`src/semantics/lombok.cpp`、`src/parser/java_parser.cpp`、`src/project/project_manager.cpp`。

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
- **源码根因（Code Review）**：`Server::handleTextDocumentSignatureHelp`（`src/lsp/server.cpp:774-820`）完全未访问 `cu->classes`、`Scope`、`TypeChecker` 或 `SymbolTable`：`src/lsp/server.cpp:787-791` 仅读单行，`src/lsp/server.cpp:795` 找最左侧 `'('`，而 `src/lsp/server.cpp:801-816` 整个查询逻辑只有一个硬编码分支 `if (methodName == "format" || beforeParen.find("String.format") != std::string::npos)`，导致所有工程方法、构造函数、`log.info`、AssertJ 与 Maven 依赖调用全部返回空签名。
- **修复方案与涉及文件**：从光标跨行向后词法/AST 扫描匹配最近未闭合 `'('`，提取调用目标表达式（区分 `new Type(`、`recv.method(`、同文件/静态导入 `method(`），通过 `TypeChecker` 与 `SymbolTable`（结合字节码索引后的 JDK/JAR 方法签名）收集全部候选重载构造器/方法生成 `SignatureInformation[]`。涉及文件：`src/lsp/server.cpp`、`src/semantics/type_checker.cpp`、`src/project/jar_indexer.cpp`。

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
- **源码根因（Code Review）**：`Server::handleTextDocumentSignatureHelp`（`src/lsp/server.cpp:794-817`）存在三处严重实现缺陷：(1) 同名劫持：`src/lsp/server.cpp:801` 只要 `methodName == "format"` 即无条件返回 `String.format` 硬编码签名，劫持了 `Showcase.format(...)` 重载；(2) 最左括号错位：`src/lsp/server.cpp:795` 使用 `line.find('(')` 从行首向右找最外层括号而非从光标向后找最内层未闭合 `'('`，导致嵌套调用 `outer(pair(10, 20), ...)` 锁错外层调用；(3) 裸逗号计数：`src/lsp/server.cpp:811-815` 逐字符统计 `if (line[i] == ',') commas++;`，将字符串字面量（`"a, b"`）、嵌套括号（`String.join(",", "x", "y")`）、Lambda 与泛型 `<>` 内逗号全部计入实参索引，且从未针对变参（`isArray`）把 `activeParameter` 钳位到 `params.size() - 1`（输出越界 `6`）。
- **修复方案与涉及文件**：改用逆向括号平衡扫描定位最内层 `'('` 并基于作用域解析真实被调方法重载集合；重写前向逗号计数器跳过字符串/字符字面量、注释以及嵌套 `()`/`[]`/`{}`/`<>`；当匹配签名尾参为变参（`isArray`）时钳位 `activeParameter = std::min(commas, static_cast<int>(params.size()) - 1)`。涉及文件：`src/lsp/server.cpp`。

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
- **源码根因（Code Review）**：对比 `lib/tree-sitter-java.so` 实测：`int x = 1\n return x;` 与未闭合 `{` 均会置位 `ts_node_has_error(root)` 并生成 `ERROR` 或零宽 `ts_node_is_missing(node)`（`; [MISSING]` / `} [MISSING]`）节点；但 `TypeChecker::collectDiagnostics`（`src/semantics/type_checker.cpp:249-354`）仅处理 `annotation`/`marker_annotation`、`scoped_type_identifier` 与 `type_identifier` 三类节点，**从未检测 `ERROR` 或 `ts_node_is_missing()`**；且 `JavaParser::parseNode`（`src/parser/java_parser.cpp:151-154`）进入 `ERROR` 仅寻找内嵌声明而不向 `JavaCompilationUnit`（`src/parser/java_ast.h:138-160`）收集任何语法错误。
- **修复方案与涉及文件**：在 `JavaParser` 或 `TypeChecker::collectDiagnostics` 遍历中捕获叶级 `ERROR` 与 `ts_node_is_missing(node)` 节点并产出 `severity=1` 语法错误诊断（同时停止在 `ERROR` 子树内部运行类型解析，防止错误恢复词元诱发二次误报）。涉及文件：`src/semantics/type_checker.cpp`、`src/parser/java_parser.cpp`、`src/parser/java_ast.h`。

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
- **源码根因（Code Review）**：`TypeChecker::collectDiagnostics`（`src/semantics/type_checker.cpp:249-354`）完全不遍历表达式中的 `identifier`、`method_invocation`、`field_access` 或 `object_creation_expression`；`TypeChecker::check`（`src/semantics/type_checker.cpp:131`）创建的 `FILE` 级 `rootScope` 在 `src/semantics/type_checker.cpp:350-353` 原封不动向下透传，从未构建或切换 `CLASS`/`METHOD`/`BLOCK` 作用域；同时 `TypeChecker::resolveExpressionType`（`src/semantics/type_checker.cpp:25-122`）只看 `m.name == member`（忽略实参个数/类型与无接收者方法调用），`JarIndexer::indexArchive`（`src/project/jar_indexer.cpp:84-170`）生成的外部类方法列表全空且 `SymbolTable::registerJdkTypes`（`src/semantics/symbol_table.cpp:470-504`）仅硬编码少量方法，致使类型检查器无法校验未知变量/方法与实参匹配。
- **修复方案与涉及文件**：在 `TypeChecker` 构建带声明位置约束的嵌套 CST 作用域检查器：解析表达式 `identifier`（报未定义变量）、`method_invocation`（结合接收者与静态导入查候选重载并校验参数个数与类型兼容性）；并完善 `JarIndexer` 的 `.class` 方法签名读取。涉及文件：`src/semantics/type_checker.{h,cpp}`、`src/semantics/scope.{h,cpp}`、`src/project/jar_indexer.cpp`。

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
- **源码根因（Code Review）**：`TypeChecker::collectDiagnostics`（`src/semantics/type_checker.cpp:249-354`）从未检查 `variable_declarator` 初始化器、`assignment_expression`、`return_statement` 与非 `void` 方法控制流可达性；`TypeChecker::resolveExpressionType`（`src/semantics/type_checker.cpp:25-122`）缺失字面量/运算符类型推导与 `isAssignable(expected, actual)` 规则（基础类型拓宽、装箱拆箱与子类型传递）；且缺少 `allPathsReturn` 控制流分析。
- **修复方案与涉及文件**：在 `TypeChecker` 实现 CST 表达式求值 `inferNodeType` 与 `isAssignable` 赋值兼容性判定，校验变量初始化/赋值/返回值类型，并对非 `void` 非 `abstract` 方法实现基于 `block`/`if`/`switch`/`try`/`throw`/`return` 的必达返回检查 `definitelyReturnsOrThrows`。涉及文件：`src/semantics/type_checker.{h,cpp}`、`src/parser/java_ast.h`。

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
- **源码根因（Code Review）**：6 类错误对应 6 处源码缺口：(1) 受检异常：`JavaMethod`（`src/parser/java_ast.h:69-92`）无 `thrownTypes` 字段，`JavaParser::parseMethod`（`src/parser/java_parser.cpp:328-380`）忽略 `throws`，`TypeChecker` 不检查 `throw`/调用异常与 `try/catch` 覆盖；(2) 未实现接口：`TypeChecker` 不校验非抽象类是否实现父接口/抽象父类全部抽象方法，且 `SymbolTable::registerJdkTypes`（`src/semantics/symbol_table.cpp:361`）把 `Runnable` 注册为空方法 `ClassKind::CLASS`；(3) 重复声明：`JavaMethod::signature`（`src/parser/java_ast.h:83-91`）带参数名导致同参不同名无法判重，而 `Scope::addVariable`（`src/semantics/scope.cpp:17-25`）直接覆盖同名变量，且 `ProjectManager::registerClassAndInners`（`src/project/project_manager.cpp:71-89`）将所有块内变量拍平进同一 `METHOD` 作用域；(4) `final` 重赋值：`JavaParser::collectLocalVariables`（`src/parser/java_parser.cpp:390-460`）从未设置 `jv.isFinal`（`src/parser/java_ast.h:66`），`TypeChecker` 也未拦截对 `final` 变量的赋值或 `++`/`--`；(5) `private` 跨类访问：`JavaField`/`JavaMethod` 缺可见性 helper，`TypeChecker::resolveExpressionType`（`src/semantics/type_checker.cpp:100-113`）盲扫全文件/同包所有字段而不判权限；(6) 无效 `@Override`：`TypeChecker`（`src/semantics/type_checker.cpp:249-354`）从未核对带 `@Override` 的方法是否存在可覆写父签名。
- **修复方案与涉及文件**：为 `JavaMethod`/`JavaField`/`JavaClass` 补充 `thrownTypes`、纯类型 `erasedSignature()` 与可见性/抽象标志；在 `JavaParser` 解析 `throws` 与 `isFinal`；修复 `Scope` 块级作用域与重声明检测（拒绝静默覆盖）；在 `TypeChecker` 实现上述 6 项语义规则校验，并完善 `Runnable` 等核心接口方法或改为字节码索引。涉及文件：`src/parser/java_ast.h`、`src/parser/java_parser.cpp`、`src/semantics/scope.{h,cpp}`、`src/semantics/symbol_table.cpp`、`src/semantics/type_checker.cpp`、`src/project/project_manager.cpp`。

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
- **源码根因（Code Review）**：`Diagnostic`（`src/lsp/protocol.h:44-50`）缺失 LSP `tags` 字段（`DiagnosticTag::Unnecessary = 1`）；`TypeChecker::isTypeDeclarationOrImport`（`src/semantics/type_checker.cpp:155-157`）直接跳过 `import_declaration`，且 `TypeChecker::collectDiagnostics`（`src/semantics/type_checker.cpp:249-354`）在遍历中既不记录哪些显式导入被实际引用，也不统计局部 `JavaVariable` 是否被读取。
- **修复方案与涉及文件**：在 `Diagnostic`（`src/lsp/protocol.{h,cpp}`）增加 `std::vector<int> tags` 并序列化；在 `TypeChecker::check` 结束阶段对照已使用导入集合与局部变量读取标记，对冗余显式导入及未使用局部变量输出 `severity = 4`（或 `2`）且带 `tags = {1}` 的诊断。涉及文件：`src/lsp/protocol.{h,cpp}`、`src/semantics/type_checker.cpp`。

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
- **源码根因（Code Review）**：4 类误报各对应明确缺陷：(1) `try (var stream = new ByteArrayInputStream(...))`：`SymbolTable::registerJdkTypes`（`src/semantics/symbol_table.cpp:388-390`）只硬编码 8 个 `java.io` 类而漏掉 `ByteArrayInputStream`；同时本机 `/usr/lib/jvm/default-java/lib/src.zip` 不可用使 `DependencyResolver::resolveJdk`（`src/project/dependency_resolver.cpp:137-154`）走 `.jmod`，但 **`JarIndexer::readZipEntries`（`src/project/jar_indexer.cpp:54-67`）未处理 `.jmod` 文件头 4 字节魔数 `JM\x01\x00` 偏移**，直接 `seekg(eocd.offsetCentralDir)` 导致读不到 `PK\x01\x02` 而立即 `break`，令所有 `.jmod` 索引出 **0 个类**；(2) 局部类 `LocalCalc`/`LocalHelper`：`JavaParser::collectLocalVariables`（`src/parser/java_parser.cpp:387-465`）忽略方法体内的 `class_declaration`，`TypeChecker::isTypeDeclarationOrImport`（`src/semantics/type_checker.cpp:164-170`）只豁免了声明名本身而在后续 `LocalCalc lc = new LocalCalc();` 处报错；(3) 嵌套 `@interface MetaInfo`：`JavaParser::parseNode`（`src/parser/java_parser.cpp:148-150`）与 `parseClass`（`src/parser/java_parser.cpp:264-267`）均遗漏 `annotation_type_declaration`，导致 `@MetaInfo(...)` 在 `TypeChecker::collectDiagnostics`（`src/semantics/type_checker.cpp:262-286`）报错；(4) `catch (IOException | SQLException e)`：`SQLException` 既不在 `registerJdkTypes` 硬编码列表中，又因 `java.sql.jmod` 受上述 4 字节 `.jmod` 偏移 Bug 影响返回 0 个类。
- **修复方案与涉及文件**：修复 `JarIndexer::readZipEntries`（`src/project/jar_indexer.cpp:56`）识别 `.jmod` 魔数/利用 `eocdFileOffset - eocd.sizeCentralDir` 定位中央目录并补齐 JDK 类型；在 `JavaParser` 支持 `annotation_type_declaration`（`ClassKind::ANNOTATION`）与块内局部类/枚举/记录收集；在 `TypeChecker` 查找当前词法作用域可见的局部类型。涉及文件：`src/project/jar_indexer.cpp`、`src/parser/java_ast.h`、`src/parser/java_parser.cpp`、`src/semantics/type_checker.cpp`、`src/semantics/symbol_table.cpp`。

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
- **源码根因（Code Review）**：(1) 同文件 `didChange` 漏报：`Server::handleTextDocumentDidChange`（`src/lsp/server.cpp:219-220`）虽调用了 `pm_.updateFile` 和 `publishDiagnostics(uri)`，但 `TypeChecker::collectDiagnostics`（`src/semantics/type_checker.cpp:249-354`）不检验表达式未解析符号（`missingSymbol`），输出仍是 `[]`；(2) 跨文件签名联动失效：`handleTextDocumentDidChange`（`src/lsp/server.cpp:220`）仅重算并推送**当前编辑 URI**，而 `Server` 未维护已打开文件集合及引用依赖图，故 `GreeterCaller.java` 永不被重检；同时 `SymbolTable::addClass`（`src/semantics/symbol_table.cpp:57-59`）在 `!existing->methods.empty() && cls.methods.empty()` 时拒绝覆盖，且无 `removeClassesByFile` 清理删改类。
- **修复方案与涉及文件**：在 `SymbolTable` 增加按文件原子替换/移除类 API（移除源码更新时 `src/semantics/symbol_table.cpp:57-59` 的空方法保护）；在 `Server` 维护 `openDocuments_` 与反向依赖集合，当工作区文件公共签名变更时向相关已打开文档重新触发 `publishDiagnostics`；配合 `TypeChecker` 的方法调用/符号校验。涉及文件：`src/lsp/server.{h,cpp}`、`src/semantics/symbol_table.{h,cpp}`、`src/project/project_manager.cpp`、`src/semantics/type_checker.cpp`。

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
- **源码根因（Code Review）**：(1) 通配符：`JavaTypeRef`（`src/parser/java_ast.h:11-40`）无通配符及界限模型，`JavaParser::parseType`（`src/parser/java_parser.cpp:74-86`）把 `? extends User`/`? super Admin` 当普通字符拼接为 `ref.name`，后续 `symTab.resolveClass("? extends User")` 必然失败；(2) 菱形操作符与 `Map`：`java.util.Map`（`src/semantics/symbol_table.cpp:378`）为 0 方法空壳，`JavaClass` 无 `typeParameters`（`<K, V>`），`TypeChecker::resolveExpressionType`（`src/semantics/type_checker.cpp:69-78`）仅硬编码用 `typeArguments[0]` 替换 `"E"`/`"T"` 而不支持多类型参数映射或嵌套泛型替换，且 `parseType` 对 `new HashMap<>()` 不回推 LHS 目标泛型参数；(3) 泛型方法 `<T> T pickFirst(List<T>)` 与有界类型参数 `<T extends Named & Comparable<T>>`：`JavaMethod`/`JavaClass` 无 `typeParameters` 及交集界限，`parseMethod` 忽略 `type_parameters`，`resolveExpressionType`（`src/semantics/type_checker.cpp:90-121`）既不支持无点号方法调用也不做实参泛型统一；(4) 多级泛型链 `Box<Pair<String, User>>`：`CompletionEngine::addClassMembers`（`src/completion/completion_engine.cpp:299-306`）同样仅替换 `"E"`/`"T"` 而丢失 `<A, B>` 中的第二个实参 `User`。
- **修复方案与涉及文件**：扩展 `JavaTypeRef` 支持通配符（`UNBOUNDED`/`EXTENDS`/`SUPER`）与多界限，在 `JavaClass`/`JavaMethod` 记录 `typeParameters` 并在 `JavaParser` 解析；实现递归泛型实参替换环境 `TypeEnv`（含通配符捕获、菱形推断、无接收者方法调用与参数类型合一推导），并补齐 `Map`/`Set`/`Optional` 等标准容器方法签名。涉及文件：`src/parser/java_ast.h`、`src/parser/java_parser.cpp`、`src/semantics/type_checker.cpp`、`src/completion/completion_engine.cpp`、`src/semantics/symbol_table.cpp`。

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
- **源码根因（Code Review）**：`JavaParser::collectLocalVariables`（`src/parser/java_parser.cpp:387-465`）在遇到 `local_variable_declaration`（`src/parser/java_parser.cpp:390-413`）和 `enhanced_for_statement`（`src/parser/java_parser.cpp:414-437`）时，仅把 `var` 当作 `type_identifier` 存下 `jv.type.name = "var"`，既不记录初始化表达式（`variable_declarator` 的 `value` 子节点）也不记录可迭代容器表达式；而 `try_with_resources_statement -> resource_specification -> resource` 完全未被 `collectLocalVariables` 遍历，导致 `try (var res = ...)` 中的 `res` 在 `Scope` 中根本不存在；`TypeChecker::resolveExpressionType`（`src/semantics/type_checker.cpp:25-122`）也不支持 `new ArrayList<String>()`、无接收者调用 `findUser(1L)` 与流式方法引用管道求值。
- **修复方案与涉及文件**：在 `JavaVariable`（`src/parser/java_ast.h:61-67`）保存初始化表达式或直接在作用域构建期推导 `var` 类型（支持 `new Type<...>()` 对象创建、方法调用与 `for (var u : iterable)` 元素类型抽取）；在 `JavaParser::collectLocalVariables` 补齐 `try-with-resources` `resource` 变量采集。涉及文件：`src/parser/java_ast.h`、`src/parser/java_parser.cpp`、`src/semantics/type_checker.cpp`、`src/project/project_manager.cpp`。

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
- **源码根因（Code Review）**：(1) **死代码**：`ProjectManager` 仅在 `src/project/project_manager.cpp:6` `#include "semantics/nested_lambda.h"`，`registerClassAndInners`（`src/project/project_manager.cpp:57-93`）**从未在生产路径调用过 `NestedLambdaAnalyzer::analyzeMethodLambdas`**，致使所有 `ScopeKind::LAMBDA` 子作用域在运行期均为零；(2) **单参数 Lambda 解析 Bug**：针对 `lib/tree-sitter-java.so` 实测，`x -> ...` 的 `ts_node_child_by_field_name(node, "parameters", 10)` 返回非空的 `identifier` 节点，而 `NestedLambdaAnalyzer::walkNodeForLambdas`（`src/semantics/nested_lambda.cpp:148-205`）以 `if (ts_node_is_null(paramsNode))` 作为单参数分支条件（`src/semantics/nested_lambda.cpp:149`），导致 `x -> ...` 全部落入 `else` 分支并因 `identifier` 无子节点而产生 0 个参数；(3) **流式链路截断与依赖缺口**：`NestedLambdaAnalyzer`（`src/semantics/nested_lambda.h:12-16`）未接收 `cu` 与 `symTab`，`inferLambdaParamType`（`src/semantics/nested_lambda.cpp:44-57`）在首个 `.` 处截断接收者链，`Stream` 在 `SymbolTable::registerJdkTypes`（`src/semantics/symbol_table.cpp:566-601`）缺失 `flatMap`/`forEach`，且 `Scope::lambdas_`（`src/semantics/scope.h:34, 51`）只写无读；(4) **方法引用（`::`）缺失**：解析器与求值器均未处理 `method_reference` 节点。
- **修复方案与涉及文件**：修复 `walkNodeForLambdas` 区分 `identifier`、`inferred_parameters` 与 `formal_parameters`；将 `NestedLambdaAnalyzer`（传入 `cu`、`symTab` 并支持类字段初始化器如 `EsInitconstant`）接入 `ProjectManager::registerClassAndInners`；改用 `TypeChecker::resolveExpressionType` 求值完整流式接收者链与 SAM 函数式接口参数类型，并补充 `method_reference` 绑定与 `Stream` 完整方法。涉及文件：`src/project/project_manager.cpp`、`src/semantics/nested_lambda.{h,cpp}`、`src/semantics/scope.h`、`src/semantics/type_checker.cpp`、`src/semantics/symbol_table.cpp`。

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
- **源码根因（Code Review）**：5 项 Java 14–17 语法各自对应源码缺口：(1) `record`：`JavaParser::parseClass`（`src/parser/java_parser.cpp:201-269`）忽略 `record_declaration` 下的 `formal_parameters` 组件与 `compact_constructor_declaration`，导致无组件字段、无规范构造器与无访问器方法 `x()`/`value()`；(2) `sealed`/`permits`：`JavaClass`（`src/parser/java_ast.h:112-136`）无 `permitsTypes`，`parseClass` 不解析 `permits` `type_list`，且 `handleTextDocumentImplementation`（`src/lsp/server.cpp:594-618`）硬编码 `/impl/*Impl.java`；(3) `switch`/`yield`：Tree-sitter 对不完整 `yield label.` 会将整座 `ModernSyntax` 类降级为 `ERROR` 节点而仅剩内部类 `Circle`/`Rect`，`ProjectManager::updateFile`（`src/project/project_manager.cpp:148-153`）因 `cu->classes` 非空而覆盖丢弃原本的 `switchExpr` 方法作用域使 `label` 消失；(4) `instanceof` 模式匹配：`JavaParser::collectLocalVariables`（`src/parser/java_parser.cpp:387-465`）不收集 `instanceof_expression` 模式变量 `s`；(5) 文本块 String API：`SymbolTable::registerJdkTypes`（`src/semantics/symbol_table.cpp:470-504`）仅硬编码 7 个 `String` 方法且 `.jmod` 索引为 0 类，缺失 `stripIndent`/`formatted`。
- **修复方案与涉及文件**：扩展 `JavaParser::parseClass` 生成 record 组件字段、访问器、紧凑构造器及 `permitsTypes`；在 `collectLocalVariables` 收集 `instanceof_expression` 模式变量并增加 `ERROR` 块内局部声明容错恢复（保留未受损外围作用域）；修复 `.jmod` 索引与字节码解析以提供完整 Java 17 `String` API。涉及文件：`src/parser/java_ast.h`、`src/parser/java_parser.cpp`、`src/project/project_manager.cpp`、`src/semantics/symbol_table.cpp`、`src/project/jar_indexer.cpp`、`src/lsp/server.cpp`。

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
- **源码根因（Code Review）**：7 个子项根因如下：(1) `Outer.this.`：`TypeChecker::resolveExpressionType`（`src/semantics/type_checker.cpp:34-88`）将 `CoreConstructs.this` 拆为查找字段 `"this"` 而失败；(2) 局部类/匿名类：`JavaParser::collectLocalVariables`（`src/parser/java_parser.cpp:387-465`）忽略方法内 `class_declaration`（`LocalHelper`）及匿名类 `class_body`；(3) 富枚举：`JavaParser::parseClass`（`src/parser/java_parser.cpp:255-269`）只扫 `enum_body` 直属子节点的字段/方法，忽略 `enum_constant` 与包裹枚举字段/构造/方法的 `enum_body_declarations`；(4) 变参 `String... args`：`tree-sitter-java` 将变量名放在 `spread_parameter -> variable_declarator -> identifier`，而 `JavaParser::parseMethod`（`src/parser/java_parser.cpp:353-373`）只匹配直接 `identifier` 子节点且未置 `ptype.isArray = true`，导致 `args` 被整条丢弃；(5) `try (var res = ...)`：`collectLocalVariables` 忽略 `resource` 节点；(6) 多异常 `catch (IOException | SQLException e)`：`collectLocalVariables`（`src/parser/java_parser.cpp:445-446`）将整个 `catch_type` 解析成不存在的类名 `"IOException | SQLException"`；(7) 数组 `arr.`/`arr[0].`：`TypeChecker::resolveExpressionType` 不处理 `isArray` 的 `.length` 属性与 `[...]` 下标解包。
- **修复方案与涉及文件**：在 `JavaParser` 解析 `enum_constant`、`enum_body_declarations`（并合成 `values()`/`valueOf()`）、`spread_parameter -> variable_declarator`（置 `isArray=true`）、局部/匿名类与多异常公共上界（回退 `Exception`/`Throwable`）；在 `TypeChecker` 支持 `Outer.this`、数组 `.length` 与 `expr[i]` 元素解包。涉及文件：`src/parser/java_parser.cpp`、`src/semantics/type_checker.cpp`、`src/semantics/symbol_table.cpp`、`src/completion/completion_engine.cpp`。

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
- **源码根因（Code Review）**：(1) `@Data` 跳转字段失效：`LombokHandler::applyGetters`（`src/semantics/lombok.cpp:159-164`）与 `applySetters`（`src/semantics/lombok.cpp:183-189`）生成的合成方法均未把 `m.nameRange`/`m.range` 设为对应字段 `f.nameRange`/`f.range`（保持 `{0,0}`），且 `Server::handleTextDocumentDefinition`（`src/lsp/server.cpp:383-446`）也未跳转工作区类成员；(2) `@Value` 非法生成 setter：`LombokHandler::processClass`（`src/semantics/lombok.cpp:85-87`）写死 `if (hasData || hasValue) applyData(cls);`，而 `applyData`（`src/semantics/lombok.cpp:120-122`）无条件调用 `applySetters(cls)`（仅跳过显式 `f.isFinal()` 字段），`src/semantics/lombok.cpp:94-96` 还按显式 `final` 误生成 0 参构造器；(3) `AccessLevel` 与字段注解失效：`JavaParser::parseModifiersAndAnnotations`（`src/parser/java_parser.cpp:102-107`）将 `@Setter(AccessLevel.NONE)` 存为裸文本，而 `JavaClass::hasAnnotation`（`src/parser/java_ast.h:130-135`）做全等匹配无法识别带参或带包注解，`applyGetters`/`applySetters` 也从未检查字段级 `f.annotations` 而一律硬编码 `public`（`src/semantics/lombok.cpp:162, 187`），且 `CompletionEngine::addClassMembers`（`src/completion/completion_engine.cpp:294-380`）不校验访问修饰符。
- **修复方案与涉及文件**：为 Lombok 合成 getter/setter 赋 `m.nameRange = f.nameRange`；拆出独立的 `applyValue(cls)`（视非静态字段为隐式 `final`、禁调 `applySetters` 并生成全参构造器）；实现带参数/包名解析的结构化注解匹配以处理字段级 `@Getter`/`@Setter(AccessLevel.*)`，并配合 `CompletionEngine` 可见性过滤与跨文件 `definition` 成员跳转。涉及文件：`src/semantics/lombok.cpp`、`src/parser/java_ast.h`、`src/parser/java_parser.cpp`、`src/completion/completion_engine.cpp`、`src/lsp/server.cpp`。

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
- **源码根因（Code Review）**：`LombokHandler::applyBuilder`（`src/semantics/lombok.cpp:230-236`）**仅**向宿主类添加 `public static <ClassName>Builder builder()` 方法，从不在 `cls.innerClasses` 或 `SymbolTable` 中**合成 `<ClassName>Builder` 类及其流式字段方法和 `build()`**（`SymbolTable::addClass` 在 `src/semantics/symbol_table.cpp:124-126` 原本会自动递归注册 `innerClasses`，但因为没有生成该内部类导致 `resolveClass("UserBuilder")` 为 `nullptr`）；同时 `@Builder(toBuilder = true)` 在 `JavaClass::hasAnnotation("Builder")`（`src/parser/java_ast.h:130-135`）因精确相等比较返回 **`false`**，使 `builder()` 与 `toBuilder()` 均未生成。
- **修复方案与涉及文件**：规范化注解名与参数提取以识别 `@Builder(...)` 与 `toBuilder = true`；在 `applyBuilder` 中构建 `<ClassName>Builder` 内部 `JavaClass` 挂入 `cls.innerClasses`（为每个非静态字段生成流式 setter 且 `nameRange = f.nameRange`，外加 `build()`），当 `toBuilder = true` 时同时生成 `toBuilder()` 实例方法。涉及文件：`src/semantics/lombok.cpp`、`src/parser/java_ast.h`、`src/project/project_manager.cpp`。

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
- **源码根因（Code Review）**：`LombokHandler::processClass`（`src/semantics/lombok.cpp:71-106`）将 `equals`/`hashCode`/`toString` 绑死在 `applyData`（`src/semantics/lombok.cpp:124-142`）内部而从未独立响应 `@EqualsAndHashCode`/`@ToString`；完全未实现 `@With`、`@Delegate`、`@UtilityClass` 与 `@Log4j2`（`@UtilityClass` 仅因 `GAP-021` 的静态误判偶然看似部分通过），且 `SymbolTable::registerJdkTypes`（`src/semantics/symbol_table.cpp:435-443`）缺少 `Value`/`RequiredArgsConstructor`/`AccessLevel`/`With`/`Delegate`/`UtilityClass`/`Log4j2`；此外 `ProjectManager::indexWorkspace`（`src/project/project_manager.cpp:36, 54, 59`）先对 `.java` 调用 `LombokHandler::processClass` 再调用 `DependencyResolver::resolveAll`，导致 `@Delegate` 处理时 `java.util.List` 等外部依赖类型尚未完全索引。
- **修复方案与涉及文件**：在 `LombokHandler` 实现独立的 `applyToString`、`applyEqualsAndHashCode`、`applyWith`、`applyUtilityClass`、`applyLog4j2` 及可重复运行的后置传递 `applyDelegate`（在 `DependencyResolver::resolveAll` 完成后展开被委托字段类型方法并做泛型替换与签名去重），并补全 `SymbolTable` 中的 Lombok 注解类。涉及文件：`src/semantics/lombok.{h,cpp}`、`src/project/project_manager.cpp`、`src/semantics/symbol_table.cpp`。

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
- **源码根因（Code Review）**：(1) Spring DI 误跳：`Server::handleTextDocumentDefinition`（`src/lsp/server.cpp:361-403`）仅提取裸名 `getUser` 且优先遍历 `cu->classes`（`UserController`）成员，导致 `userService.getUser(id)` 立即错跳回 Controller 自身方法；(2) 跨模块 OpenFeign 断链：`ProjectManager::indexWorkspace`（`src/project/project_manager.cpp:32-37, 58`）已将 `api` 与 `core` 模块的 `UserFeignClient`（带 `cls.filePath`）写入 `SymbolTable`，但 `handleTextDocumentDefinition` Step 3（`src/lsp/server.cpp:406-444`）完全忽略 `cls->filePath` 只调 `DecompilationService`，对 `feignClient.getUserName(id)` 甚至解析出返回类型 `String` 去反编译 `java.lang.String`；同时 `DependencyResolver::resolveMaven`（`src/project/dependency_resolver.cpp:213`）在子模块单独跑 `mvn dependency:build-classpath`，若未 `mvn install` 则解析 sibling 失败；(3) Spring Web `@GetMapping`：`spring-web-6.1.11.jar` 无 `-sources.jar`，走 CFR 反编译时 `DecompilationService::locateCfrJar`（`src/project/decompilation.cpp:50-68`）仅查 macOS 绝对路径和 `cwd` 相对路径 `"lib/cfr.jar"`，当工作目录在待测项目下时定位失败。
- **修复方案与涉及文件**：重构 `handleTextDocumentDefinition` 先依据表达式/AST 解析接收者类型并跳转工作区 `targetCls->filePath` 对应类/成员；基于二进制可执行路径（`/proc/self/exe`）定位 `lib/cfr.jar`；在 `DependencyResolver::resolveMaven` 建模 Maven Reactor `<modules>` 映射直接关联兄弟模块源码根。涉及文件：`src/lsp/server.cpp`、`src/project/decompilation.cpp`、`src/project/dependency_resolver.cpp`、`src/project/project_manager.cpp`。

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
- **源码根因（Code Review）**：(1) `Optional<User>`：`SymbolTable::registerJdkTypes`（`src/semantics/symbol_table.cpp:345-353, 378`）把 `Optional` 注册为空方法占位符，且 `JarIndexer::indexArchive`（`src/project/jar_indexer.cpp:84-170`）不解析 `.class` 方法，`CompletionEngine::addClassMembers`（`src/completion/completion_engine.cpp:301`）仅替换 `"E"`/`"T"`（不含 `"U"`/`"R"`）；(2) AssertJ 静态导入：`JavaParser::parseImport`（`src/parser/java_parser.cpp:169-187`）丢弃 `static` 标志，`Server::handleTextDocumentDefinition`（`src/lsp/server.cpp:406`）与 `CompletionEngine::complete`（`src/completion/completion_engine.cpp:216-258`）均不支持静态导入方法检索，且 `Assertions` 无方法字节码索引；(3) AssertJ 流式链：`TypeChecker::resolveExpressionType("assertThat(users)", ...)`（`src/semantics/type_checker.cpp:34-92`）因无 `.` 号跳过方法求值而将其作为变量名查空，同时 `CompletionEngine::complete`（`src/completion/completion_engine.cpp:157-166`）只截取单行文本导致换行后的 `.` 链路丢失前缀。
- **修复方案与涉及文件**：在 `JarIndexer` 解析 `.class` 字节码方法与泛型 `Signature`（并补齐 `Optional` 等核心类方法）；支持 `isStatic` 导入并在 `TypeChecker`/`Server`/`CompletionEngine` 解析静态方法调用与泛型返回值实例化（如 `ListAssert<User>`）；支持跨换行表达式/AST 链路提取。涉及文件：`src/project/jar_indexer.cpp`、`src/semantics/symbol_table.cpp`、`src/parser/java_parser.cpp`、`src/semantics/type_checker.cpp`、`src/completion/completion_engine.cpp`、`src/lsp/server.cpp`。

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
- **源码根因（Code Review）**：`Server::handleTextDocumentCodeAction`（`src/lsp/server.cpp:1092-1317`）与重复实现的 `handleJavaGetRefactorEdit`（`src/lsp/server.cpp:1337-1388`）存在两类根因：(1) QuickFix 极度匮乏（`src/lsp/server.cpp:1123-1221`）：仅处理 `" cannot be resolved to a type"` 与同文件编辑距离 `<= 2` 的方法拼写错误；导入修复调用 `symTab_.findClassesMatchingPrefix(typeName)`（`src/semantics/symbol_table.cpp:331-342`）在 `src/semantics/symbol_table.cpp:338` 触发 50 条无序截断（如 `List*` 丢失 `java.util.List`），且未实现创建局部变量/字段/方法、实现未实现接口方法、删除无用导入、`@Override` 修复与受检异常 `try/catch`/`throws` 等 QuickFix；(2) 提取方法破坏语法（`src/lsp/server.cpp:1262-1314, 1337-1388`）：`src/lsp/server.cpp:1279-1287` 无视字符列范围收集整行文本，而 `src/lsp/server.cpp:1290-1292` 仅替换子行 `reqRange`，对 `return computeSum(1, 2);` 或 `this.missingField = "abc";` 留下残缺语法（`return     extractedMethod();\n(1, 2);`），且固定把方法插在全文件最后一个 `}` 前并忽略 `context.only`。
- **修复方案与涉及文件**：新增无上限精确类名查询 `findClassesByExactName` 并钳位导包行号；基于标准化诊断码实现全部 8 类 QuickFix；合并 `Extract Method` 与 `handleJavaGetRefactorEdit`，基于 Tree-sitter 校验选区为完整语句或表达式并按精确字符区间替换、推导出入参/返回值后插在所属类的闭括号前，同时尊重 `context.only`。涉及文件：`src/lsp/server.cpp`、`src/semantics/symbol_table.{h,cpp}`、`src/semantics/type_checker.cpp`。

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
- **源码根因（Code Review）**：`Server::handleTextDocumentCodeAction` 中的 `Organize Imports`（`src/lsp/server.cpp:1223-1260`）仅对 `cu->imports` 执行 `std::sort` 与 `std::unique`，从不比对 AST 中实际使用的类型/静态符号，故保留所有无用导入；因 `JavaParser::parseImport`（`src/parser/java_parser.cpp:169-187`）丢失 `static`，还会把 `import static` 误改写为普通 `import`（且 `src/lsp/server.cpp:1249` 的 `lastImpLine + 1` 在末尾无换行时越界）；此外 `Server` 未实现 `source.generate.*`、`refactor.extract.variable`、`refactor.inline`，`handleInitialize`（`src/lsp/server.cpp:176`）也未声明 `codeActionProvider.resolveProvider` 且 `handleMessage`（`src/lsp/server.cpp:53-119`）未分发 `codeAction/resolve`。
- **修复方案与涉及文件**：通过 Tree-sitter CST 收集正文中被引用的类型、静态导入成员及 Javadoc `{@link}`，裁剪未引用导入并分组排序普通/静态导入；补充生成 Getter/Setter/构造器、提取变量与内联变量重构；声明并实现 `codeAction/resolve`。涉及文件：`src/lsp/server.{h,cpp}`、`src/parser/java_parser.cpp`、`src/lsp/protocol.{h,cpp}`。

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
- **源码根因（Code Review）**：`Server::handleTextDocumentRename`（`src/lsp/server.cpp:911-1026`）对非局部符号（`src/lsp/server.cpp:977-1005`）仅逐行扫描当前文件 `cu->sourceText` 并写入 `we.changes[uri]`，完全不查询工作区其余调用方、`@Override` 实现类或 `import` 引用；同时 `WorkspaceEdit`（`src/lsp/protocol.h:106-108`、`src/lsp/protocol.cpp:142-154`）不支持序列化 `documentChanges` 与 `RenameFile` 资源操作，`src/lsp/server.cpp:1020-1023` 直接在服务端调用 `pm_.renameFile`（`src/lsp/server.cpp:1022`）修改磁盘文件而不是在 `WorkspaceEdit` 返回 `RenameFile` 操作。
- **修复方案与涉及文件**：扩展 `WorkspaceEdit` 支持 `documentChanges`（`TextDocumentEdit` + `RenameFile` `{kind: "rename", oldUri, newUri}`）；基于跨文件语义引用解析（含父类/接口/实现类方法继承链与 `import`）收集全工作区编辑，移除 `src/lsp/server.cpp:1022` 磁盘直接改名副作用，改为当重命名顶层类且与文件名一致时输出 `RenameFile`。涉及文件：`src/lsp/protocol.{h,cpp}`、`src/lsp/server.cpp`、`src/project/project_manager.cpp`。

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
- **源码根因（Code Review）**：(1) `Server::handleTextDocumentPrepareRename`（`src/lsp/server.cpp:871-909`）在 `src/lsp/server.cpp:889-893` 仅校验 `symbol.empty() || isPrimitiveOrKeyword(symbol)`，而 `extractSymbolAtPosition`（`src/lsp/server.cpp:300-313`）对字符串字面量、数字、注释内容或 JDK 外部符号均能切出合法词元并放行；(2) `Server::handleTextDocumentRename`（`src/lsp/server.cpp:911-1026`）从未校验 `newName` 合法性，也未校验 `innerScope`、子作用域或同类成员是否已有同名符号冲突（且 `src/lsp/server.cpp:949` 在作用域结束行为 0 时把字节长度 `cu->sourceText.size()` 当行号使用）。
- **修复方案与涉及文件**：在 `prepareRename` 检查光标处 Tree-sitter 节点必须为可解析的标识符/类型标识（排除字面量、注释、关键字），且目标声明属于可写工作区文件（`!cls->filePath.empty()`）；在 `rename` 校验 `newName` 符合 Java 标识符规范并在所属作用域链/类成员中查重，冲突时返回 JSON-RPC `-32602`。涉及文件：`src/lsp/server.cpp`、`src/semantics/scope.cpp`。

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
- **源码根因（Code Review）**：`Server::handleInitialize`（`src/lsp/server.cpp:156-177`）未声明 `workspace.fileOperations`，`Server::handleMessage`（`src/lsp/server.cpp:53-119`）未处理 `workspace/willRenameFiles`（落入 `src/lsp/server.cpp:117` 返回 `null`）；同时收到 `workspace/didRenameFiles`（`src/lsp/server.cpp:1319-1335`）时，`ProjectManager::renameFile`（`src/project/project_manager.cpp:176-200`）优先读取旧路径残余内存 `fileBuffers_[oldUri]`（`src/project/project_manager.cpp:180-182`）覆盖新文件磁盘内容，`SymbolTable`（`src/semantics/symbol_table.h:12-33`）缺少按文件移除旧类 `AlphaType` 的接口，且 `handleTextDocumentDefinition`（`src/lsp/server.cpp:405-444`）也未跳转工作区 `cls->filePath`。
- **修复方案与涉及文件**：声明 `willRename`/`didRename` 并实现 `Server::handleWorkspaceWillRenameFiles`（推算新包名生成更新 `package` 与全工作区 `import` 的 `WorkspaceEdit`）；为 `SymbolTable` 增加 `removeClassesByFile`，在 `ProjectManager::renameFile` 清除旧文件符号并在目标新路径已存在且未脏编辑时从磁盘读取新文件内容。涉及文件：`src/lsp/server.{h,cpp}`、`src/project/project_manager.cpp`、`src/semantics/symbol_table.{h,cpp}`。

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
- **源码根因（Code Review）**：`Server::handleTextDocumentFormatting`（`src/lsp/server.cpp:1028-1075`）在 `src/lsp/server.cpp:1067-1069` 以 `'\n'` 计数 `totalLines` 后写死 `edit.range = Range{{0, 0}, {totalLines + 1, 0}}` 必然越界 1 行；`src/lsp/server.cpp:1046-1064` 仅去行首空白并按 `indentLevel * 4` 强行缩进，完全忽略 `params["options"]["tabSize"]` 与 `insertSpaces`、不折叠多余空格、不在 `{`/`;`/`}` 断行且受字符串内 `{` 与 CRLF `'\r'` 干扰；同时 `handleInitialize`/`handleMessage` 未实现 `rangeFormatting` 与 `onTypeFormatting`。
- **修复方案与涉及文件**：将全量格式化 `TextEdit::range` 精确计算为文档末行末列（UTF-16）；读取并遵守 `tabSize`/`insertSpaces`；基于 Tree-sitter CST 或词法 Token 流实现保留字符串/注释原貌的 Java 断行与缩进格式化器，并补齐 `textDocument/rangeFormatting` 与 `textDocument/onTypeFormatting`。涉及文件：`src/lsp/server.{h,cpp}`。

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
- **源码根因（Code Review）**：`Server::handleJavaOverridableMethods`（`src/lsp/server.cpp:1390-1403`）完全不读取 `params`，直接返回硬编码常量条目 `name="listUsers", parent="UserService"`；`Server::handleJavaOverrideMethods`（`src/lsp/server.cpp:1405-1408`）则直接返回空 `WorkspaceEdit`。
- **修复方案与涉及文件**：在 `handleJavaOverridableMethods` 解析 `params.textDocument.uri`（及可选光标位置）对应 `JavaClass`，沿 `extendsType`/`implementsTypes`/`Object` 收集非 `private`/`static`/`final` 可覆写方法并标注当前类尚未实现的抽象方法；在 `handleJavaOverrideMethods` 生成 `@Override` 方法桩插入目标类体末尾。涉及文件：`src/lsp/server.cpp`、`src/semantics/symbol_table.cpp`。

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
- **源码根因（Code Review）**：(1) 跨模块工作区类跳转缺失：`ProjectManager::indexWorkspace`（`src/project/project_manager.cpp:32-37, 58`）已将 `api`、`core` 与 `src/test/java` 索引入 `SymbolTable`（含 `cls.filePath`），但 `Server::handleTextDocumentDefinition`（`src/lsp/server.cpp:405-446`）仅走只查外部归档的 `DecompilationService::resolveSourceForFqn`（`src/lsp/server.cpp:408`），从未返回工作区 `cls->filePath`；(2) Maven 多模块 Reactor 隔离：`DependencyResolver::resolveMaven`（`src/project/dependency_resolver.cpp:177-245`）在每个子目录单独执行 `mvn dependency:build-classpath`（`src/project/dependency_resolver.cpp:213`），未预先 `mvn install` 时子模块依赖兄弟模块失败；(3) 测试依赖跳转：`assertThat` 因 `JavaParser::parseImport`（`src/parser/java_parser.cpp:169-187`）丢失 `static` 标志导致无法解析，`@SpringBootTest` 缺源码包时 `locateCfrJar`（`src/project/decompilation.cpp:50-68`）因 `cwd` 相对路径失败，且 `p2/osgi/bundle/junit-jupiter-api` 使用 OSGi `.source` 命名而非 `*-sources.jar`（`src/project/decompilation.cpp:173`）。
- **修复方案与涉及文件**：在 `handleTextDocumentDefinition` 优先解析工作区 `cls->filePath` 及其成员范围与静态导入；在 `DependencyResolver::resolveMaven` 解析父 POM `<modules>` 建立兄弟模块源码根映射；修复 `locateCfrJar` 路径定位并移除 `p2/osgi/bundle` 干扰。涉及文件：`src/lsp/server.cpp`、`src/project/dependency_resolver.cpp`、`src/project/decompilation.cpp`、`src/parser/java_parser.cpp`。

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
- **源码根因（Code Review）**：4 处源码缺失共同打破类路径隔离：(1) `ProjectManager::indexWorkspace`（`src/project/project_manager.cpp:29-37, 57-93`）将工作区全部 `.java`（含 `src/main/java`、`src/test/java` 与 `target/`）混入单一扁平 `SymbolTable`（`src/semantics/symbol_table.h:12-33`），未记录 `SourceScope`（`MAIN` vs `TEST`）；(2) `DependencyResolver::resolveMaven`（`src/project/dependency_resolver.cpp:213`）未区分 `-DincludeScope=compile` 与 `-DincludeScope=test`；(3) `DependencyResolver::resolveM2Repository`（`src/project/dependency_resolver.cpp:85-102`）无条件把 `~/.m2/repository/p2/osgi/bundle`（含 JUnit 5）与 JUnit 4 注入所有工程；(4) `TypeChecker::collectDiagnostics`（`src/semantics/type_checker.cpp:249-354`）既不按调用源根过滤可见域，也不校验 `TestHelper.assist()` 等表达式接收者。
- **修复方案与涉及文件**：为每个模块区分 `MAIN` 与 `TEST` 类路径索引源（Maven 分离 `compile` 与 `test` scope，移除无条件 `p2/osgi/bundle` 与 JUnit 4 扫描）；在 `SymbolTable` 为类/依赖打上 `(moduleId, scope)` 标签并在 `TypeChecker` 与 `CompletionEngine` 按调用方所属源码根实施可见性隔离。涉及文件：`src/project/project_manager.cpp`、`src/project/dependency_resolver.cpp`、`src/semantics/symbol_table.{h,cpp}`、`src/semantics/type_checker.cpp`。

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
- **源码根因（Code Review）**：(1) 无 `-sources.jar` 时的 CFR 失败：`gson-2.10`、`jackson-databind-2.17.2` 无源码包时 `DecompilationService::resolveSourceForFqn`（`src/project/decompilation.cpp:159-206`）回退 CFR，但 `locateCfrJar`（`src/project/decompilation.cpp:50-68`）仅查 macOS 路径和 `cwd` 相对路径 `"lib/cfr.jar"`，在测试临时目录下失效；(2) `resolveM2Repository` 版本污染：`DependencyResolver::resolveAll`（`src/project/dependency_resolver.cpp:69-73`）在 Maven 之前无条件索引 `p2/osgi/bundle` 中的 `com.google.gson-2.14.0.jar` 污染版本与缺失判断；(3) POM 启发式子串脆弱：`resolveMaven`（`src/project/dependency_resolver.cpp:193-204`）用 `pomContent.find("<dependencies>") == npos` 跳过继承父 POM 依赖的子工程却误判含 `<dependencyManagement>` 的父工程，缺乏原生 `<parent>`/`<properties>` `${...}`/BOM/传递依赖 XML 解析（且无 `-o` 离线回退与退出码检查）；(4) 内部类源码提取：`src/project/decompilation.cpp:167-180` 对 `Outer$Inner` 拼接 `Outer$Inner.java` 而未剥离 `$Inner` 提取 `Outer.java`。
- **修复方案与涉及文件**：基于可执行文件路径定位 `cfr.jar`，提取 `-sources.jar`/`src.zip` 时剥离 `$Inner`；移除无条件 `p2/osgi/bundle` 扫描；用真正的 XML 解析器替换 POM 字符串判断，支持 Reactor/Parent/BOM/`${property}` 原生解析与 `mvn -o` 优先级。涉及文件：`src/project/dependency_resolver.cpp`、`src/project/decompilation.cpp`。

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
- **源码根因（Code Review）**：(1) `DependencyResolver::resolveGradle`（`src/project/dependency_resolver.cpp:247-265`）从未执行 `gradle`/`./gradlew`、未解析 `build.gradle(.kts)` 依赖声明也未检索 `~/.gradle/caches/modules-2/files-2.1`，仅检查已编译输出目录 `build/classes/java/main`，且 `src/project/dependency_resolver.cpp:248-251` 在存在 `build.gradle` 时跳过 `build.gradle.kts`；(2) Gradle 同工程文件 `GradHelper` 虽已入库，但 `Server::handleTextDocumentDefinition`（`src/lsp/server.cpp:405-446`）因忽略 `cls->filePath` 无法跳转；(3) 独立 `.java` 文件中 `List` 跳转受 `GAP-060` 的 JDK `src.zip`/`.jmod` 缺陷阻断。
- **修复方案与涉及文件**：在 `resolveGradle` 同时扫描 `build.gradle` 与 `build.gradle.kts`（排除 `build/`），通过临时 init-script 调用 `./gradlew`/`gradle` 导出类路径并配合静态坐标解析 `~/.gradle/caches` 与 `~/.m2`；协同修复 `handleTextDocumentDefinition` 与 JDK 源码提取。涉及文件：`src/project/dependency_resolver.cpp`、`src/lsp/server.cpp`、`src/project/decompilation.cpp`。

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
- **源码根因（Code Review）**：5 重缺陷串联导致 JDK 与无源码 Maven 依赖跳转全灭：(1) `DecompilationService::locateJdkSrcZip`（`src/project/decompilation.cpp:31-48`）只查 macOS `/opt/homebrew/...` 与 `$JAVA_HOME/lib/src.zip`，Linux 下未设 `JAVA_HOME` 时不复用 `DependencyResolver::findJavaHome`（`src/project/dependency_resolver.cpp:35-61`）；(2) 当主机无 `src.zip` 转而索引 `/usr/lib/jvm/.../jmods/*.jmod` 时，`SymbolTable::addClass`（`src/semantics/symbol_table.cpp:57-59`）在保留预注册类方法时丢弃 `.jmod` 的 `sourceFilePath`，`resolveSourceForFqn`（`src/project/decompilation.cpp:184-194`）提取 `.jmod` 忽略 `"classes/"` 前缀，`getJdkSource`（`src/project/decompilation.cpp:87-94`）仅试 `"java.base/"` 与裸路径（且 `src/project/decompilation.cpp:71` 有 `&&` 逻辑错误），`resolveJdk`（`src/project/dependency_resolver.cpp:139-147`）仅硬编码 7 个 `.jmod`；(3) `locateCfrJar`（`src/project/decompilation.cpp:50-68`）依赖 `cwd` 相对路径在项目目录外失效；(4) `fs::extractZipEntry`（`src/utils/fs_utils.cpp:149, 163-169`）将 `2>&1` 错误输出写入缓存文件返回 `true` 污染缓存，且双引号包裹 `"$entryName"` 令 `/bin/sh` 把 `$Inner` 展开为空；(5) `decompileClass`（`src/project/decompilation.cpp:116-127`）返回目录而非反编译后的 `.java` 文件。
- **修复方案与涉及文件**：以进程内 `zlib` 解压与 `fork`/`execvp` 替换 `popen("unzip ... 2>&1")`；用 `/proc/self/exe` 定位 `lib/cfr.jar` 并复用 `findJavaHome()` 扫描全量模块与 `*.jmod`；支持 `.jmod` 的 `classes/` 字节码提取进 CFR、`src.zip` 全模块扫描及 `SymbolTable::addClass` 合并 `sourceFilePath`。涉及文件：`src/project/decompilation.cpp`、`src/project/dependency_resolver.cpp`、`src/utils/fs_utils.cpp`、`src/semantics/symbol_table.cpp`。

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
- **源码根因（Code Review）**：(1) `Server::handleInitialize`（`src/lsp/server.cpp:156-177`）与 `handleMessage`（`src/lsp/server.cpp:67-119`）未支持 `workspace/didCreateFiles`、`workspace/didDeleteFiles`、`workspace/didChangeWatchedFiles` 与 `textDocument/didSave`；(2) `ProjectManager::removeFile`/`updateFile`（`src/project/project_manager.cpp:132-174`）不能从 `SymbolTable`（`src/semantics/symbol_table.h:12-33`）剔除旧类，`Server` 无已打开文档列表供跨文件重新 `publishDiagnostics`；(3) `ProjectManager::updateFile`（`src/project/project_manager.cpp:137-141`）把所有 `.xml` 导向 `MyBatisXmlService` 而忽略 `pom.xml`/`build.gradle`，且 `DependencyResolver::resolveMaven`（`src/project/dependency_resolver.cpp:206-218`）只要见到 `.classpath_m2.txt` 就无条件复用，不比较 `pom.xml` mtime/哈希；(4) 初始未报 `Gson` 错系 `DependencyResolver::resolveM2Repository`（`src/project/dependency_resolver.cpp:85-93`）提前无条件加载 `p2/osgi/bundle/com.google.gson-2.14.0.jar` 所致。
- **修复方案与涉及文件**：实现动态文件监听/创建/删除/保存通知处理；在 `SymbolTable` 增加按文件与按 JAR 源剔除/增量更新 API 并对全部 `openDocuments_` 重推诊断；将 `.classpath_m2.txt` 移入 `-storage` 并基于 `pom.xml` 内容校验失效，在 `pom.xml`/`build.gradle*` 变更时刷新依赖并移除 `p2/osgi/bundle` 无条件扫描。涉及文件：`src/lsp/server.cpp`、`src/project/project_manager.cpp`、`src/project/dependency_resolver.cpp`、`src/semantics/symbol_table.{h,cpp}`。

> 注：经对照 `/tmp/blackbox_results.json` 交叉核验，上述 **GAP-001 至 GAP-061**（为保持编号连续清晰，本节共整理 **61** 条去重缺陷条目，即 `GAP-001`..`GAP-061`）已 **100%** 覆盖全部 **204** 个 `GAP_FAIL` 测试用例与全部 **207** 个失败 `[TAG-XX]` 标签，无一遗漏。

---

## 4. 新版自动化浏览与模拟编程测试工具说明

### 4.1 旧工具 `src/tools/ptest.cpp` (`bin/ptest`) 的局限性

结合既有报告（[ptest_report_spring_demo.md](file:///home/fakename/workspace/java-lsp/ptest_report_spring_demo.md)、[ptest_report_test_java.md](file:///home/fakename/workspace/java-lsp/ptest_report_test_java.md)、[ptest_report_gson.md](file:///home/fakename/workspace/java-lsp/ptest_report_gson.md)）及对 `src/tools/ptest.cpp`（共 573 行，在 `CMakeLists.txt` 直接静态链接核心库）的逐行源码审计，旧版模拟测试工具存在以下 6 项结构性缺陷，导致其既漏掉了几乎全部关键协议/语义缺陷，又产出严重失真的指标（例如在 `java17-spring-demo` 报告 `72.7%` 补全率与 `0` 诊断差异）：

1. **进程内白盒直连绕过 LSP 传输与 `Server` 协议层（`src/tools/ptest.cpp:1-9, 314-320, 360, 440-445`）**：直接实例化 `ProjectManager::instance()`、`CompletionEngine` 与 `TypeChecker::check` 调用，完全绕过 `Transport`（`Content-Length` 帧解析）与 `Server` 分发循环；也未调用 `DecompilationService::setCacheDir`（`src/main.cpp:30`），默认写污染 `~/.cache/java-lsp/sources`；`src/tools/ptest.cpp:360` 传入空 `TSNode{}` 触发二次全量重解析；因此彻底无法测出负列号 `SIGABRT`（`GAP-001`）、`didChange` 增量编辑失效（`GAP-003`）、`didClose` 脏缓存滞留（`GAP-004`）、`-32601`/`-32600` 缺失（`GAP-005`）及 >64KB `stderr` 管道死锁。
2. **沿用 UTF-8 字节列偏移，掩盖 UTF-16 错位（`src/tools/ptest.cpp:216-227`）**：`getPositionFromOffset` 逐字节递增 `pos.character++`，与待测 `CompletionEngine` 使用完全相同的 UTF-8 字节偏移错误模型，无法暴露中文与代理对 Emoji 的 UTF-16 偏移漂移（`GAP-002`），也从不测试越界或负数坐标（`GAP-001`）。
3. **词法切分器与补全目标选取严重失真（`src/tools/ptest.cpp:37-134, 137-213, 299-307`）**：
   - `tokenizeSource`（`src/tools/ptest.cpp:60-134`）仅把 `std::isalnum(c) || c == '_'` 归为标识符（`src/tools/ptest.cpp:121-125`），`src/tools/ptest.cpp:129` 把 `@` 切为独立单字符 Token，而 `isConstantOrDelimiter`（`src/tools/ptest.cpp:44`）又豁免了 `'@'`，导致 `@Data` 被拆成无意义的 `"@"`（`src/tools/ptest.cpp:322` 剥离 `@` 后变为 `target == ""`）与 `"Data"`；同时 `src/tools/ptest.cpp:95-104` 无法处理 Java 15+ 文本块 `"""..."""`，`src/tools/ptest.cpp:70, 121` 将有符号 `char` 传入 `<cctype>` 在非 ASCII 字节上构成 UB。
   - `partitionJavaFile`（`src/tools/ptest.cpp:137-213`）仅切割到首个顶级类 `{`（`src/tools/ptest.cpp:201`），把枚举常量定义、方法声明名（`listUsers`/`getUser`）、形参名（`args`/`id`）、局部变量声明名（`i`）与关键字（`class`）全留在 `bodyTokens` 中作为「待补全目标」，造成大量伪漏报（而体空文件 `UserMapperImpl.java` 因 `0/0` 被判 `PASS`）。
4. **渐进输入循环破坏文件后续上下文且验收过宽（`src/tools/ptest.cpp:299-351`）**：
   - `src/tools/ptest.cpp:312` 用 `partialDoc = currentText + typed + footer` 截断光标之后的全部字段、方法与嵌套类；`src/tools/ptest.cpp:314, 349` 在每个字符敲击时原地修改全局单例 `ProjectManager` 索引；
   - `src/tools/ptest.cpp:320-338` 遍历 `c = 1..token.size()` 只要 `list.items` 任意位置出现前缀匹配即 `break` 判胜，不检验 Top-5 排序、`kind`（构造器混入方法，`GAP-020`）、`private` 可见性泄露（`GAP-021`）、静态过滤、自动导包 `additionalTextEdits`（`GAP-022`）或非法候选过滤（`GAP-026`）。
5. **诊断基线比对逻辑存在重大漏检漏洞（`src/tools/ptest.cpp:230-265, 357-406`）**：
   - **`lspErrors == 0` 时直接跳过 `javac`（`src/tools/ptest.cpp:371-402`）**：在无 root `pom.xml` 或测试代码未被 `mvn compile` 覆盖时，只要 `TypeChecker` 漏报返回 `0`，`src/tools/ptest.cpp:401` 直接置 `res.javacErrors = 0` 并判 `diagnosticsMatch = true`！
   - **仅比对错误总数（`src/tools/ptest.cpp:404-406`）**：`res.javacErrors != res.lspErrors` 既不核对行号也不核对消息，1 条误报 + 1 条漏报也会 `1 == 1` 判为一致；完全无主动错误注入测试；`runProjectCompiler`（`src/tools/ptest.cpp:230-265`）仅支持 root `pom.xml`（用脆弱下标 `src/tools/ptest.cpp:257` `line.substr(start, (colon + 5) - start)` 截取路径）且回退 `javac -proc:none`（`src/tools/ptest.cpp:380`）禁用 Lombok 处理导致正常文件误报。
6. **LSP 能力覆盖面极窄且直接污染原工程目录（`src/tools/ptest.cpp:239, 380-386, 411-573`）**：零调用 `hover`、`definition`/`declaration`/`typeDefinition`/`implementation`/`references`、`documentSymbol`/`workspace/symbol`、`signatureHelp`、`codeAction`、`prepareRename`/`rename`、`formatting`、JDTLS 扩展及动态文件/POM 刷新；直接面向 `projectRoot` 运行（`src/tools/ptest.cpp:441`）写入 `.classpath_m2.txt` 污染仓库，通过 `popen`（`src/tools/ptest.cpp:239, 386`）拼接未转义路径，并在 `--max-files` 非数字时因未捕获 `std::stoi`（`src/tools/ptest.cpp:428`）崩溃。

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

---

## 5. 源码架构级根因与额外发现的隐藏缺陷

### 5.1 架构级系统性根因（Systemic Root Causes）

逐行审计全量 C++ 实现后可见，上述 **61** 条 `GAP` 并非孤立 Bug，而是由以下 **8** 项贯穿多子系统的架构级反模式集中驱动：

| 架构级根因 | 核心代码位置与缺陷机制 | 关联 `GAP-XXX` |
| :--- | :--- | :--- |
| **1. 文本行扫描代替 Tree-sitter AST/CST 语义求值** | `Server`（`src/lsp/server.cpp:300-328, 356-361, 424-438, 643, 678-716, 787-816, 835-865, 884-903, 926-1002, 1046-1064, 1235-1314`）、`TypeChecker::resolveExpressionType`（`src/semantics/type_checker.cpp:25-88`）与 `CompletionEngine`（`src/completion/completion_engine.cpp:41-166`）抛弃已生成的 Tree-sitter AST，每请求重新按 `\n` 切片并在 `( )` / `.` 处做启发式字符串截断与全工作区裸文本扫描，丢失接收者链、实参、作用域与字面量边界。 | `GAP-009`..`GAP-015`, `GAP-027`..`GAP-031`, `GAP-048`, `GAP-049`..`GAP-054` |
| **2. 扁平全局 `SymbolTable`（无文件归属、作用域隔离、字节码方法与反向继承图）** | `SymbolTable`（`src/semantics/symbol_table.h:12-33`, `src/semantics/symbol_table.cpp:47-93, 204-317, 331-605`）缺少按文件移除与 `(module, MAIN/TEST)` 隔离，隐式兜底 `java.util.*` 与全局同名首项（`findClassByName`），50 条截断搜索；`JarIndexer::indexArchive`（`src/project/jar_indexer.cpp:84-200`）只建空方法/空父类桩；`JavaClass`/`JavaMethod` 缺泛型形参与规范擦除签名。 | `GAP-010`, `GAP-014`, `GAP-020`..`GAP-024`, `GAP-026`, `GAP-028`, `GAP-033`..`GAP-035`, `GAP-037`..`GAP-039`, `GAP-046`, `GAP-048`, `GAP-055`, `GAP-057` |
| **3. `.jmod` 4 字节魔数偏移错误与 JDK/CFR 定位/解压机制失效** | `JarIndexer::readZipEntries`（`src/project/jar_indexer.cpp:54-67`）未跳过 `.jmod` 文件头 `JM\x01\x00` 4 字节偏移致使所有 `.jmod` 索引出 0 类；`DecompilationService`（`src/project/decompilation.cpp:31-97, 116-235`）硬编码 macOS 路径与 `cwd` 相对 `lib/cfr.jar`、忽略 `.jmod` `classes/` 前缀且 `decompileClass` 返回目录；`fs::extractZipEntry`（`src/utils/fs_utils.cpp:144-169`）用 `2>&1` 污染缓存且 `/bin/sh` 展开 `$Inner`。 | `GAP-013`, `GAP-014`, `GAP-037`, `GAP-042`, `GAP-043`, `GAP-047`, `GAP-056`, `GAP-058`..`GAP-060` |
| **4. 3 节点浅层 `TypeChecker` 与断链的 `Scope` / `NestedLambdaAnalyzer`** | `TypeChecker::collectDiagnostics`（`src/semantics/type_checker.cpp:124-149, 249-354`）仅核对 `annotation`/`scoped_type_identifier`/`type_identifier` 三节点且透传死参数 `FILE` 作用域；`Scope`（`src/semantics/scope.h:12-55`, `src/semantics/scope.cpp:17-39`）无块级隔离且覆盖同名变量；`NestedLambdaAnalyzer`（`src/semantics/nested_lambda.cpp:24-225`）在 `ProjectManager`（`src/project/project_manager.cpp:6, 57-93`）中从未被调用且错判单参 Lambda `parameters` 节点。 | `GAP-007`, `GAP-026`, `GAP-029`, `GAP-032`..`GAP-043` |
| **5. 缺失增量同步、文档生命周期清理与跨文件依赖/资源编辑模型** | `Server::handleTextDocumentDidChange`（`src/lsp/server.cpp:207-221`）忽略 `range`；`ProjectManager::closeFile`（`src/project/project_manager.cpp:165-167`）为空桩；`Server::handleTextDocumentDefinition` 忽略工作区 `cls->filePath`（`src/lsp/server.cpp:405-446`）；`WorkspaceEdit`（`src/lsp/protocol.h:106-108`）不支持 `documentChanges`/`RenameFile`；`Server` 未维护 `openDocuments_` 依赖刷新与多工作区根目录。 | `GAP-003`..`GAP-006`, `GAP-009`, `GAP-015`, `GAP-038`, `GAP-047`, `GAP-051`, `GAP-053`, `GAP-056`, `GAP-058`, `GAP-059`, `GAP-061` |
| **6. UTF-8 字节偏移混充 UTF-16 Code Units 与协议边界防御缺失** | `JavaParser::getNodeRange`（`src/parser/java_parser.cpp:38-46`）、`TypeChecker::getNodeRange`（`src/semantics/type_checker.cpp:15-23`）、`NestedLambdaAnalyzer::getNodeRange`（`src/semantics/nested_lambda.cpp:14-22`）及 `Server` 文本扫描均输出原生 UTF-8 字节列；`parsePosition`（`src/lsp/protocol.cpp:167-176`）放行负值；`Server::handleMessage`（`src/lsp/server.cpp:53-119, 156-192`）缺错误码 `-32601`/`-32600`、越界格式行号（`src/lsp/server.cpp:1069`）与大量 LSP 3.17 能力。 | `GAP-001`, `GAP-002`, `GAP-005`, `GAP-008`, `GAP-016`..`GAP-019`, `GAP-025`, `GAP-050`, `GAP-054` |
| **7. Java 17 AST 结构性遗漏与 Lombok 语义简化** | `JavaParser`（`src/parser/java_parser.cpp:48-465`）与 `JavaAST`（`src/parser/java_ast.h:11-160`）缺通配符/界限、`@interface`、枚举常量与 `enum_body_declarations`、`record` 组件/紧凑构造器、`permits`、`spread_parameter` `variable_declarator`、`try` `resource`、`instanceof` 模式变量、局部/匿名类与 `var`/`static` 导入；`LombokHandler`（`src/semantics/lombok.cpp:71-236`）混淆 `@Value`/`@Data`、忽略 `AccessLevel` 与 `Builder` 类生成及 `@With`/`@Delegate`。 | `GAP-012`, `GAP-014`, `GAP-016`, `GAP-022`, `GAP-024`, `GAP-035`, `GAP-037`, `GAP-039`..`GAP-046` |
| **8. 脆弱正则/子串构建解析、OSGi 污染与硬编码桩实现** | `DependencyResolver`（`src/project/dependency_resolver.cpp:78-103, 177-265`）全扫 `p2/osgi/bundle`、用子串查 POM、无 Gradle 依赖解析且 `.classpath_m2.txt` 无过期校验；`MyBatisXmlService`（`src/mybatis/mybatis_xml.cpp:34-132, 216-512`）逐行正则解析 XML；`Server`（`src/lsp/server.cpp:523-623, 774-820, 1390-1408`）存在 `/impl/*Impl.java`、`String.format` 与 `UserService.listUsers` 硬编码桩。 | `GAP-014`, `GAP-030`, `GAP-031`, `GAP-047`, `GAP-055`..`GAP-059`, `GAP-061` |

---

### 5.2 黑盒测试未覆盖的额外代码缺陷与安全/稳定性风险（Hidden Defects Found in Code Review）

以下 **18** 项缺陷（`CR-001`..`CR-018`）由白盒源码 Review 独立检出，当前黑盒测试套件尚未触发或仅间接涉及，但对生产稳定性、安全性、跨平台可用性与内存安全构成实质风险：

| ID | 严重级别 | 源码精确位置 | 缺陷描述与触发风险 | 修复建议 |
| :--- | :---: | :--- | :--- | :--- |
| **`CR-001`** | **P0** (安全) | `src/utils/fs_utils.cpp:144-169`<br>`src/project/dependency_resolver.cpp:15-28, 213-214`<br>`src/project/decompilation.cpp:122-123, 198-199`<br>`src/mybatis/mysql_service.cpp:13-22, 98-107, 126-145`<br>`src/tools/ptest.cpp:239-242, 379-386` | **多处 `popen()` / `/bin/sh -c` OS 命令注入**：将未经转义的工作区路径、JAR 路径、ZIP entry 名、MySQL 参数及 Java 注解文本直接拼接进双引号 Shell 命令并交由 `popen()` 执行。若路径或注解含 `"`、`$()`、`` ` `` 或 `;`，将触发任意命令执行。 | 全面移除 `popen()`：外部进程（`mvn`、`gradle`、`java -jar cfr.jar`、`javac`）改用 `fork()`+`execvp()`/`posix_spawn()` 显式传 `argv[]`；ZIP 提取改用进程内 `zlib`；MySQL 查询改用 C 客户端库。 |
| **`CR-002`** | **P0** (崩溃/DoS) | `src/lsp/transport.cpp:11-48`<br>`src/lsp/server.cpp:53-66` | **`Transport` `std::stoull` 崩溃、Header 大小写敏感、无上限内存分配与 JSON-RPC 校验缺失**：`src/lsp/transport.cpp:24` 对非法 `Content-Length: abc` 调 `std::stoull` 抛出未捕获异常（`SIGABRT`）；`src/lsp/transport.cpp:22-23` 严格匹配大小写 `"Content-Length: "`；`src/lsp/transport.cpp:33` 对超大长度无上限 `resize` 导致 OOM；`src/lsp/transport.cpp:41-45` JSON 解析失败直接返回 `std::nullopt` 致使 `Server::run` 退出循环（未回 `-32700`）；`src/lsp/server.cpp:53-66` 遇到带 `id` 但无合法字符串 `method` 的请求静默丢弃（未回 `-32600`）。 | 按 RFC 大小写无关解析 Header 并修剪空白；改用 `std::from_chars` 并限制单帧上限（如 64MB）；区分流 EOF 与 JSON 解析错误（返回 `-32700`），规范校验 `"jsonrpc":"2.0"` 与 `id`/`method`（返回 `-32600`）。 |
| **`CR-003`** | **P0** (内存泄漏) | `src/semantics/scope.h:48, 52`<br>`src/semantics/scope.cpp:5-15`<br>`src/project/project_manager.cpp:158-162` | **`Scope::parent_` `std::shared_ptr` 循环引用永久泄漏**：`Scope::parent_`（`src/semantics/scope.h:48`）与 `Scope::children_`（`src/semantics/scope.h:52`）互持强引用 `std::shared_ptr<Scope>`，每次 `Scope::create`（`src/semantics/scope.cpp:5-15`）双向绑定形成闭环。每次 `didChange` 重建 `fileScopes_[uri]`（`src/project/project_manager.cpp:158-162`）时整棵旧 `Scope` 树与 `JavaLambda` 永远无法析构。 | 将 `Scope::parent_` 改为 `std::weak_ptr<Scope>`，在 `parent()` 与 `lookupVariable()` 中调用 `parent_.lock()` 访问父作用域。 |
| **`CR-004`** | **P0** (安全/完整性) | `src/mybatis/mysql_service.h:17-23`<br>`src/mybatis/mysql_service.cpp:40-96, 98-166`<br>`src/mybatis/mybatis_xml.cpp:398-401` | **MySQL SQL 注入、`/proc/cmdline` 密码泄露、硬编码回退与失败缓存**：`getColumns`（`src/mybatis/mysql_service.cpp:122-137`）将 `config_.database` 与来自 `@TableName` 注解的 `tableName` 直接拼接进 SQL（SQLi）；`src/mybatis/mysql_service.cpp:102, 129, 141` 在命令行传入 `-p<password>` 可被本机 `ps`/`/proc/<pid>/cmdline` 读取；默认连 `root@localhost:3306/demo`（`src/mybatis/mysql_service.h:17-23`），`src/mybatis/mysql_service.cpp:136-137` 跨全部 Schema 兜底查询，`src/mybatis/mybatis_xml.cpp:399-401` 兜底查 `"user"` 表；离线时 `src/mybatis/mysql_service.cpp:163` 将空列列表永久缓存；`detectConfig`（`src/mybatis/mysql_service.cpp:76-78`）发现任意 `application.yml`（含 `target/` 下）即跳过 `application.properties` 且忽略 YAML 中账号密码。 | 改用 `libmysqlclient` 参数化/转义查询并移除 CLI 密码参数；删除 `"demo"`/`"user"` 及跨库兜底；不缓存离线/异常查询结果；`detectConfig` 排除构建输出目录并解析完整数据源配置。 |
| **`CR-005`** | **P1** (数据损坏) | `src/utils/fs_utils.cpp:149, 163-169`<br>`src/project/decompilation.cpp:87-94, 122, 184-200` | **`2>&1` 污染缓存与 `$Inner` Shell 变量展开损坏内部类**：`fs::runCommand`（`src/utils/fs_utils.cpp:149`）拼接 `2>&1` 且忽略 `pclose()` 退出码；当 `unzip -p` 失败（如 `DecompilationService::getJdkSource` `src/project/decompilation.cpp:87-90` 优先试探 `java.base/`）输出 `"caution: filename not matched..."` 到 stderr 时，`fs::extractZipEntry`（`src/utils/fs_utils.cpp:163-169`）因 `content` 非空把报错文本当作 `.java`/`.class` 写入磁盘缓存并返回 `true`；同时双引号包裹 `"$entryName"` / `"$classOut"` 使 `/bin/sh` 把 `Outer$Inner.class` 中的 `$Inner` 展开为空字符串。 | 使用进程内 `zlib` 提取 ZIP 条目并先检查状态码；对外部 `cfr.jar` 使用 `execvp` 传参（不经过 Shell）且仅在退出码为 `0` 且产物合法时写入缓存。 |
| **`CR-006`** | **P1** (崩溃/性能) | `src/utils/fs_utils.cpp:112-142`<br>`src/project/project_manager.cpp:32, 40`<br>`src/lsp/server.cpp:844` | **符号链接循环无限递归（`SIGSEGV`）与盲目扫描构建/VCS 目录**：`fs::listFilesRecursively`（`src/utils/fs_utils.cpp:125-131`）跟随目录软链接（`DT_LNK`）却不记录已访问 `(st_dev, st_ino)`，遇到循环软链接直接栈溢出崩溃；且不跳过 `.git/`、`target/`、`build/`、`node_modules/`，造成重复索引与启动耗时飙升。 | 记录已访问 `(st_dev, st_ino)` 集合（或拒绝跟随循环符号链接），并在目录遍历层跳过 `.git`、`target`、`build`、`out`、`.idea`、`node_modules` 等目录。 |
| **`CR-007`** | **P1** (死锁/并发) | `src/utils/logger.h:18-38`<br>`src/utils/logger.cpp:7-55`<br>`src/main.cpp:27` | **`stderr` 同步洪泛管道死锁与 `Logger` 线程安全漏洞**：`Logger::log`（`src/utils/logger.cpp:54`）无条件向 `std::cerr` 打印全量 `INFO` 日志（启动期输出约 709 行 / 128.8KB，超过 Linux 64KB 管道上限，若客户端未并发排空 `stderr` 即在 `initialize` 死锁）；`src/utils/logger.cpp:37` 调用非线程安全 `std::localtime`；`src/utils/logger.cpp:21, 29` 对 `minLevel_` 存在锁外读/锁内写数据竞争；`src/utils/logger.cpp:23` 重复 `init()` 未关闭旧句柄；全局静态析构阶段写日志有静态析构顺序隐患。 | 将 `minLevel_` 改为 `std::atomic<LogLevel>`，改用 `localtime_r`；`init()` 重新打开前先关闭旧 `logFile_`；默认仅向 `stderr` 输出 `WARN`/`ERROR`（`INFO` 仅落盘，或由 `--verbose` 开关控制）。 |
| **`CR-008`** | **P1** (崩溃/移植) | `src/parser/tree_sitter_loader.cpp:17-22, 24-73`<br>`src/parser/java_parser.h:34`<br>`src/project/project_manager.cpp:12-20`<br>`src/main.cpp:32-34` | **静态析构顺序 `dlclose` 悬垂指针、初始化数据竞争与失败静默降级**：`ProjectManager::instance()`（`src/project/project_manager.cpp:12-20`）构造 `JavaParser` 早于 `JavaParser::init()` 首次触发 `TreeSitterLoader::instance()`，导致进程退出时 `~TreeSitterLoader()`（`src/parser/tree_sitter_loader.cpp:17-22`）先 `dlclose` 动态库、`~JavaParser()` 再调用已卸载的 `ts_parser_delete`；`initialize()` 无 `std::call_once` 保护且硬编码 `/Users/suremoon/...` 路径（`src/parser/tree_sitter_loader.cpp:35`）；`newParser()`（`src/parser/tree_sitter_loader.cpp:68-73`）未检验 `ts_parser_set_language` 返回值与 ABI 版本；`src/main.cpp:32-34` 在初始化失败时仅打印警告并继续空转启动（所有解析返回空）。 | 使用基于二进制所在目录的路径探测 `.so`；`initialize()` 改用 `std::call_once` 并校验 `ts_parser_set_language` 与 `ts_language_version`；进程期保留 `libHandle_`（或使 `JavaParser` 持有共享加载器生命周期）；加载失败在 `main.cpp` 直接非零退出。 |
| **`CR-009`** | **P1** (崩溃/UB) | `src/mybatis/mybatis_xml.cpp:205, 208, 244-248, 283-287, 390-391`<br>`src/tools/ptest.cpp:70, 121`<br>`src/completion/completion_engine.cpp:41-58` | **未钳位字符串边界访问与 `<cctype>` 有符号 `char` 未定义行为**：`MyBatisXmlService::findDefinitions`（`src/mybatis/mybatis_xml.cpp:244-248, 283-287`）与 `complete`（`src/mybatis/mybatis_xml.cpp:390-391`）未校验 `pos.character < 0` 或 `> line.size()`（直接索引 `line[left - 1]` / `substr` 越界崩溃）；同时 `src/mybatis/mybatis_xml.cpp:205, 208, 245, 284` 与 `src/tools/ptest.cpp:70, 121` 将有符号 `char` 直接传入 `std::isupper`/`std::tolower`/`std::isspace`/`std::isalnum`，遇 `>= 0x80` UTF-8 字节触发 C++ 未定义行为。 | 所有行列下标统一经非负且 `<= line.size()` 边界钳位；所有 `<cctype>` 调用统一强制转换 `static_cast<unsigned char>(c)`。 |
| **`CR-010`** | **P1** (UB/内存) | `src/project/jar_indexer.cpp:25-82` | **`JarIndexer::readZipEntries` 未对齐指针强转 UB、缺读边界校验与 ZIP64 缺失**：`src/project/jar_indexer.cpp:69-71` 对变长字节缓冲区 `cdBuffer[p + 28]` 直接执行 `*reinterpret_cast<const uint16_t*>`（违反严格别名与内存对齐规则）；`src/project/jar_indexer.cpp:54-58` 未校验 `offsetCentralDir + sizeCentralDir <= fileSize`（损坏或恶意 JAR 可触发巨额内存分配），且不检查 `file.read()` 是否成功及 ZIP64 EOCD。 | 改用 `std::memcpy`/小端字节移位 helper 读取字段；校验 `offsetCentralDir + sizeCentralDir <= fileSize`、`file.good()` 与 `p + 46 + fnameLen + extraLen + commentLen <= cdBuffer.size()`；补充 ZIP64 支持。 |
| **`CR-011`** | **P1** (正确性) | `src/semantics/scope.cpp:17-39`<br>`src/project/project_manager.cpp:71-89` | **`Scope::addVariable` 静默覆盖遮蔽变量与扁平块级作用域**：`Scope::addVariable`（`src/semantics/scope.cpp:17-25`）遇同名变量直接原地覆盖（丢失前一变量类型/位置），同时 `ProjectManager::registerClassAndInners`（`src/project/project_manager.cpp:71-89`）将方法内不同 `if`/`for`/`try` 子块的变量全部平铺入 `METHOD` 作用域且 `lookupVariable`（`src/semantics/scope.cpp:31-39`）不检查使用点是否在声明点之后，导致兄弟块互串及前向误解析。 | 为语句块/`for`/`try`/`catch`/`switch_rule` 构建独立 `ScopeKind::BLOCK` 子作用域；`addVariable` 保留全部声明（冲突时记录供诊断）；`lookupVariable(name, pos)` 仅命中 `var.range.start <= pos` 的声明。 |
| **`CR-012`** | **P1** (正确性) | `src/semantics/symbol_table.h:16-33`<br>`src/semantics/symbol_table.cpp:47-93`<br>`src/project/project_manager.cpp:132-200` | **`SymbolTable` 不可淘汰过期类、`addClass` 空方法拒更新及双键重复指针**：`SymbolTable` 仅有全局 `clear()` 无按文件剔除接口，重命名/删除类后幽灵类常驻；`addClass`（`src/semantics/symbol_table.cpp:57-59`）拒绝用空方法类覆盖已有类（删光类内方法时旧方法残留）；`src/semantics/symbol_table.cpp:60-65` 在 `existing->fqn != fqn` 时同时写入两个 Key 并向 `classesByPackage_`（`src/semantics/symbol_table.cpp:67-79`）追加重复指针。 | 维护 `classesByFile_[filePath]`，提供 `replaceFileClasses(filePath, ...)` 原子清理 `classesByFqn_`、`fqnsByName_`、`classesByPackage_` 与 `normalizedToActualFqn_`；`classesByPackage_` 改存 FQN 字符串而非裸指针以防重散列悬空或重复。 |
| **`CR-013`** | **P1** (性能/准确) | `src/project/dependency_resolver.cpp:63-103, 206-218` | **无条件全量索引 `p2/osgi/bundle` & JUnit4 与 `.classpath_m2.txt` 过期不失效**：`resolveM2Repository`（`src/project/dependency_resolver.cpp:84-102`）不管工程类型一律索引 `~/.m2/repository/p2/osgi/bundle` 全部 OSGi JAR（本机 ~353 个包）与 JUnit 4，造成 ~13.8s 冷启动阻塞、~128KB 日志洪泛与跨工程版本/依赖污染；`resolveMaven`（`src/project/dependency_resolver.cpp:206-218`）只要见到 `.classpath_m2.txt` 即复用而不核对 `pom.xml` 修改时间或内容哈希，且写脏源码目录。 | 仅在检测到 Eclipse Tycho/PDE 配置时启用 `p2/osgi/bundle` 索引，移除硬编码 JUnit 4 兜底；将 `.classpath_m2.txt` 缓存迁入 `-storage` 并绑定 `(pomPath, mtime, sha256)` 校验失效。 |
| **`CR-014`** | **P1** (并发/协议) | `src/lsp/server.cpp:138-140`<br>`src/project/project_manager.cpp:22-55`<br>`src/project/dependency_resolver.cpp:105-118`<br>`src/semantics/symbol_table.h:12-33` | **`handleInitialize` 同步阻塞（~13.8s）与单线程/无锁单例隐患**：`Server::handleInitialize`（`src/lsp/server.cpp:138-140`）同步等待全工作区及数百个 JAR 索引完毕才回响应，极易触发客户端初始化超时；而 `SymbolTable`、`DependencyResolver` 与 `JavaParser::parser_` 本身均无并发读写锁，若改为后台异步索引将立即发生数据竞争。 | 将全量索引延后至 `initialized` 后在后台工作线程执行并通过 `$/progress` 上报进度；同时为 `SymbolTable`（`std::shared_mutex`）、`DependencyResolver` 与 `JavaParser` 补齐线程安全同步。 |
| **`CR-015`** | **P2** (健壮性) | `src/utils/fs_utils.cpp:16-46, 71-110`<br>`src/main.cpp:11-28` | **URI 百分比编码缺失、`%00` 空字节走私、`readFile` 空文件歧义与 CLI 尾参忽略**：`pathToUri`（`src/utils/fs_utils.cpp:106-110`）未对空格/`#`/`?`/`%`/非 ASCII 字符编码导致与客户端 URI 不匹配；`urlDecode`（`src/utils/fs_utils.cpp:16-38`）允许 `%00` 注入路径且 `uriToPath` 不兼容 `file://localhost/`；`readFile`（`src/utils/fs_utils.cpp:40-46`）无法区分 0 字节空文件与读取失败；`src/main.cpp:15-16, 26` 若把 `-storage` 放在无参尾端会被静默忽略且 `ensureDir` 失败未校验。 | 为 `pathToUri` 增加 RFC 百分比编码，`uriToPath` 拒绝 `%00` 并支持 `file://localhost/`；`readFile` 返回 `std::optional<std::string>`；`main.cpp` 校验 `-storage` 缺参错误与 `ensureDir` 返回值。 |
| **`CR-016`** | **P2** (正确性) | `src/parser/java_parser.cpp:94-118, 206, 287, 299-313, 333`<br>`src/parser/java_ast.h:32-39`<br>`src/project/decompilation.cpp:16-24, 71, 116-127, 193-204` | **多字段声明覆盖、修饰符死递归与反编译缓存缺陷**：`JavaParser::parseField`（`src/parser/java_parser.cpp:299-313`）遇到 `int a, b;` 时用后者覆盖 `field.name`（丢失 `a`）；`parseModifiersAndAnnotations`（`src/parser/java_parser.cpp:94-118`）在 `src/parser/java_parser.cpp:109` 丢弃了递归返回的修饰符，导致 3 个调用处（`src/parser/java_parser.cpp:206, 287, 333`）重复手写循环；`JavaTypeRef::equals`（`src/parser/java_ast.h:32-39`）未比较 `fqn` 与 `isPrimitive`；`DecompilationService` 构造器（`src/project/decompilation.cpp:16-24`）在 `src/main.cpp:30` 调用 `setCacheDir` 前即在 `~/.cache/java-lsp/sources` 建目录，`getJdkSource` `src/project/decompilation.cpp:71` `&&` 判空永假，`decompileClass`（`src/project/decompilation.cpp:126`）返回目录而非文件，且 `src/project/decompilation.cpp:193-204` 遗留 `.class` 临时文件永不清理。 | 修复 `parseField` 为每个 `variable_declarator` 生成独立 `JavaField`；合并修饰符递归逻辑；修复 `JavaTypeRef::equals`；惰性初始化反编译缓存目录、`src/project/decompilation.cpp:71` 改为 `||`、返回精确 `.java` 路径并在 CFR 后清理临时 `.class`。 |
| **`CR-017`** | **P2** (性能/准确) | `src/lsp/server.cpp:246, 356-360, 1046-1064`<br>`src/semantics/type_checker.cpp:133-143, 151-247, 323-324`<br>`src/semantics/symbol_table.cpp:476-486` | **重复双解析、`O(N)` 行扫、CRLF `'\r'` 残余与 2 字母大写类型盲区**：`publishDiagnostics`（`src/lsp/server.cpp:246`）传空 `TSNode{}` 迫使 `TypeChecker::check`（`src/semantics/type_checker.cpp:133-143`）每次重新创建与销毁 `TSParser*`/`TSTree*`；`Server` 所有接口均 `O(file_size)` 逐行 `std::getline` 扫描（且在 CRLF 文件留下尾随 `'\r'` 破坏长度与格式化混行）；`TypeChecker`（`src/semantics/type_checker.cpp:324`）对所有 `<= 2` 字母大写开头类型（如 `Ab`）直接豁免检查掩盖错误；`SymbolTable::registerJdkTypes`（`src/semantics/symbol_table.cpp:479, 483`）把 `String.length`/`isEmpty` 返回值误标为 `"java.lang.int"` / `"java.lang.boolean"`（`isPrimitive=false`）。 | 在 `JavaCompilationUnit` 缓存行偏移索引与 `TSTree*` 并统一剥离 `'\r'`；以自顶向下作用域栈替代 `src/semantics/type_checker.cpp:324` 启发式与重复祖先回溯；修复 `int`/`boolean` 的 `isPrimitive=true` 属性。 |
| **`CR-018`** | **P2** (正确性) | `src/mybatis/mybatis_xml.cpp:34-132, 227-233, 251, 397-496`<br>`src/project/dependency_resolver.cpp:159, 178, 193-204, 248`<br>`src/completion/completion_engine.cpp:218-282`<br>`src/lsp/server.cpp:15-26, 424-438, 1266, 1337-1388` | **XML/POM 逐行正则脆弱、`uriByNamespace_` 幽灵映射与补全排序倒挂**：`indexMapperXml`（`src/mybatis/mybatis_xml.cpp:45-126`）无法解析跨行 XML 标签、调序属性或注释/CDATA，重复索引时从不清理旧 `uriByNamespace_`；`findDefinitions`（`src/mybatis/mybatis_xml.cpp:229-232, 251`）无匹配时盲取首个 Mapper 且将任意含 `"Mapper"` 的词跳往 XML；`complete`（`src/mybatis/mybatis_xml.cpp:397, 413, 486`）受同行已闭合属性及小写 SQL 限制；`fs::listFilesRecursively` 用后缀匹配使 `"pom.xml"` 误命中 `"foo-pom.xml"`；`CompletionEngine::complete`（`src/completion/completion_engine.cpp:219, 267`）未沿 `scope->parent()` 收集外层局部变量且空 `sortText` 使 `"0_abstract"` 关键字排在 `"0_user"` 变量之前；`src/lsp/server.cpp:15-26` 将 `"val"` 当 Java 关键字而漏 `sealed`/`permits`/`yield`，`src/lsp/server.cpp:1266` 在单行方法上失效，`src/lsp/server.cpp:1337-1388` 重复拷贝残缺 Extract Method。 | 为 Mapper XML 与 `pom.xml` 引入真正 XML 解析器（如 `pugixml`），精确匹配文件名并清理旧映射；补全时遍历 `scope->parent()` 且令局部变量优先于关键字排序；基于 `JavaParser` 定位反编译符号宣言行并更新关键字表、单行方法范围及公共 Extract Method 助手。 |

---

## 6. 分阶段修复与架构演进路线图

基于各缺陷间的数据流依赖关系（传输安全与坐标规范 -> 基础 AST/符号索引/工程模型 -> 类型与诊断 -> 导航/悬停/补全 -> 框架/重构与 LSP 3.17 扩展），建议按以下 **5** 个阶段依次推进修复，并在每阶段结束后执行对应的黑盒回归验收命令：

| 阶段 | 核心目标与重构边界 | 重点改造 `src/` 模块 | 解决的 `GAP-XXX` 与 `CR-XXX` |
| :--- | :--- | :--- | :--- |
| **Phase 0**<br>崩溃、安全、传输与协议底线 | 消除全部 `SIGABRT`/OS 命令注入/SQL 注入/内存泄漏/管道死锁；引入 UTF-8 <-> UTF-16 统一坐标转换、增量 `didChange` 与 `didClose` 磁盘回退、完善 `-32601`/`-32600`/`-32700` 状态机。 | `src/main.cpp`<br>`src/lsp/transport.cpp`<br>`src/lsp/protocol.{h,cpp}`<br>`src/lsp/server.{h,cpp}`<br>`src/utils/fs_utils.cpp`<br>`src/utils/logger.{h,cpp}`<br>`src/utils/utf_utils.{h,cpp}`（新增）<br>`src/semantics/scope.{h,cpp}`<br>`src/parser/tree_sitter_loader.cpp`<br>`src/completion/completion_engine.cpp`<br>`src/mybatis/mysql_service.cpp`<br>`src/mybatis/mybatis_xml.cpp` | **GAP**: `GAP-001`, `GAP-002`, `GAP-003`, `GAP-004`, `GAP-005`<br>**CR**: `CR-001`..`CR-009`, `CR-015` |
| **Phase 1**<br>AST、符号表、`.jmod`/字节码索引与多模块工程基础 | 修复 `.jmod` 4 字节偏移与 `JarIndexer` 字节码成员/泛型解析；在 `JavaAST`/`JavaParser` 补全 `@interface`、枚举、`record`、`sealed`/`permits`、局部/匿名类与块级作用域；重构 `SymbolTable` 按文件增量替换、`MAIN/TEST` 隔离与反向继承图；实现多模块 Maven Reactor/BOM、Gradle 依赖解析、可执行路径定位 `cfr.jar`、动态文件/POM 热更新与后台异步索引。 | `src/parser/java_ast.h`<br>`src/parser/java_parser.cpp`<br>`src/semantics/scope.{h,cpp}`<br>`src/semantics/symbol_table.{h,cpp}`<br>`src/project/jar_indexer.cpp`<br>`src/project/dependency_resolver.cpp`<br>`src/project/decompilation.cpp`<br>`src/project/project_manager.{h,cpp}`<br>`src/lsp/server.cpp` | **GAP**: `GAP-006`, `GAP-016`, `GAP-037`, `GAP-038`, `GAP-042`, `GAP-043`, `GAP-056`, `GAP-057`, `GAP-058`, `GAP-059`, `GAP-060`, `GAP-061`<br>**CR**: `CR-010`, `CR-011`, `CR-012`, `CR-013`, `CR-014`, `CR-016` |
| **Phase 2**<br>类型系统、现代 Java 推导、Lambda 与完整诊断引擎 | 激活并修复 `NestedLambdaAnalyzer`（单参 `x -> ...`、全链 SAM 推导与 `::` 方法引用）；升级 `TypeChecker` 支持通配符/菱形/泛型方法合一、`var` 与 `try-with-resources` 推导、语法 `ERROR`/`MISSING` 诊断、表达式/重载/赋值/返回/控制流/受检异常/接口未实现/`@Override`/`final`/访问控制与无用导入/变量 `Unnecessary` 提示。 | `src/semantics/type_checker.{h,cpp}`<br>`src/semantics/nested_lambda.{h,cpp}`<br>`src/parser/java_parser.cpp`<br>`src/lsp/protocol.{h,cpp}`<br>`src/project/project_manager.cpp` | **GAP**: `GAP-007`, `GAP-032`, `GAP-033`, `GAP-034`, `GAP-035`, `GAP-036`, `GAP-039`, `GAP-040`, `GAP-041`<br>**CR**: `CR-017` |
| **Phase 3**<br>导航、悬停、签名帮助与链式补全 | 全面废除 `Server` 字符串行扫描与全局裸名匹配，改用 AST + 语义求值驱动 `definition`/`typeDefinition`/`implementation`/`references`（支持工作区跳转、重载/构造器/`this.`/`super.`/链式/静态导入与 `includeDeclaration`）；完善 `hover`（局部变量/字段/类/泛型实参替换）；重写 `signatureHelp` 跨行平衡括号定位与真实重载解析；完善 `CompletionEngine` 继承/静态/可见性/`additionalTextEdits` 自动导包/`import`/`new `/注解/重写/惰性 `resolve` 与 `TypeGraphSearch` 模式变量绑定。 | `src/lsp/server.cpp`<br>`src/completion/completion_engine.{h,cpp}`<br>`src/completion/graph_search.{h,cpp}`<br>`src/semantics/type_checker.cpp`<br>`src/mybatis/mybatis_xml.cpp` | **GAP**: `GAP-009`, `GAP-010`, `GAP-011`, `GAP-012`, `GAP-013`, `GAP-014`, `GAP-015`, `GAP-020`, `GAP-021`, `GAP-022`, `GAP-023`, `GAP-024`, `GAP-025`, `GAP-026`, `GAP-027`, `GAP-028`, `GAP-029`, `GAP-030`, `GAP-031`<br>**CR**: `CR-018` |
| **Phase 4**<br>Lombok/框架、重构/格式化与 LSP 3.17 扩展 | 完善 Lombok 全家桶（`@Value` 不可变、`AccessLevel`、字段 `nameRange` 绑定、`Builder` 合成类与 `toBuilder`、`@With`/`@Delegate`/`@EqualsAndHashCode`/`@ToString`/`@Log4j2`）；基于诊断码实现 8 类 QuickFix、清理无用导入、修复 AST 级 `Extract Method`、跨文件 `Rename`（含 `documentChanges`/`RenameFile`、`prepareRename` 防护与冲突检测）、`willRenameFiles` 包联动、合规 Java 格式化与 JDTLS `java/overridableMethods`/`java/overrideMethods` 真实实现；补全 `workspace/symbol`、`semanticTokens`、`inlayHint`、`callHierarchy`/`typeHierarchy`、`diagnosticProvider` 等 LSP 3.17 能力。 | `src/semantics/lombok.{h,cpp}`<br>`src/lsp/server.{h,cpp}`<br>`src/lsp/protocol.{h,cpp}`<br>`src/project/project_manager.cpp` | **GAP**: `GAP-008`, `GAP-017`, `GAP-018`, `GAP-019`, `GAP-044`, `GAP-045`, `GAP-046`, `GAP-047`, `GAP-048`, `GAP-049`, `GAP-050`, `GAP-051`, `GAP-052`, `GAP-053`, `GAP-054`, `GAP-055` |

### 6.1 分阶段与全量黑盒验收命令

```bash
# Phase 0：崩溃、协议底线与编码增量同步验收
python3 tests/blackbox/run_blackbox_tests.py --filter "PROTO-02|PROTO-17|PROTO-19|PROTO-21|PROTO-22|PROTO-26|PROTO-29" -v

# Phase 1：AST/JMOD/多模块工程/类路径隔离与热更新验收
python3 tests/blackbox/run_blackbox_tests.py --filter "test_project_and_dependencies|PROTO-15|PROTO-23|PROTO-24|PROTO-25|SYM-01|SYM-02|DIAG-03|DIAG-10|DIAG-12|DIAG-31|DIAG-32" -v

# Phase 2：类型系统、现代 Java 特性与诊断准确性验收
python3 tests/blackbox/run_blackbox_tests.py --filter "test_diagnostics_accuracy|test_modern_java_features|PROTO-28" -v

# Phase 3：导航、悬停、签名帮助与链式补全验收
python3 tests/blackbox/run_blackbox_tests.py --filter "test_navigation_and_symbols|test_hover_and_signature_help|test_completion_and_chains" -v

# Phase 4：Lombok/框架、重构/CodeAction/格式化与 LSP 3.17 扩展验收
python3 tests/blackbox/run_blackbox_tests.py --filter "test_lombok_and_frameworks|test_refactoring_and_code_actions|test_spring_demo_e2e|PROTO-05|PROTO-06|PROTO-07|PROTO-08|PROTO-09|PROTO-10|PROTO-11|PROTO-12|PROTO-13|PROTO-14" -v

# 全量收口回归验收（预期 253/253 PASS，GAP_FAIL == 0，HARNESS_ERROR == 0）
python3 tests/blackbox/run_blackbox_tests.py --strict --json-out /tmp/blackbox_results.json
python3 tests/blackbox/project_simulator.py java17-spring-demo \
  --md-out docs/simulator_report_spring_demo.md \
  --json-out /tmp/simulator_report_spring_demo.json
```
