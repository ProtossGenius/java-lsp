# ptest Simulation & Diagnostic Report

- **Target Project**: `/Users/suremoon/workspace/test-projects/gson`
- **Total Files Tested**: 20
- **Passed**: 15
- **Diagnostic Discrepancies**: 5
- **Non-constant Token Completion Rate**: 6043 / 9340 (64.7%)

## Test Results by File

| File | Tokens (Completed/Total) | Missed | LSP Errors | Javac Errors | Status |
| :--- | :---: | :---: | :---: | :---: | :---: |
| `BagOfPrimitivesDeserializationBenchmark.java` | 145/231 | 86 | 1 | 11 | **DIFF** |
| `SerializationBenchmark.java` | 42/57 | 15 | 1 | 7 | **DIFF** |
| `BagOfPrimitives.java` | 94/134 | 40 | 0 | 0 | **WARN** |
| `CollectionsDeserializationBenchmark.java` | 187/293 | 106 | 1 | 14 | **DIFF** |
| `NonUploadingCaliperRunner.java` | 22/47 | 25 | 0 | 0 | **WARN** |
| `ParseBenchmark.java` | 639/984 | 345 | 78 | 100 | **DIFF** |
| `GsonBuildConfig.java` | 6/7 | 1 | 0 | 0 | **WARN** |
| `VersionExclusionStrategyTest.java` | 172/251 | 79 | 0 | 0 | **WARN** |
| `JsonArrayAsListTest.java` | 564/909 | 345 | 0 | 0 | **WARN** |
| `PerformanceTest.java` | 536/762 | 226 | 0 | 0 | **WARN** |
| `JsonStreamParserTest.java` | 106/199 | 93 | 0 | 0 | **WARN** |
| `GsonTest.java` | 834/1282 | 448 | 0 | 0 | **WARN** |
| `LongSerializationPolicyTest.java` | 107/176 | 69 | 0 | 0 | **WARN** |
| `JsonPrimitiveTest.java` | 749/1061 | 312 | 0 | 0 | **WARN** |
| `JavaSerializationTest.java` | 94/153 | 59 | 0 | 0 | **WARN** |
| `OSGiManifestIT.java` | 182/325 | 143 | 0 | 0 | **WARN** |
| `JsonObjectTest.java` | 581/836 | 255 | 0 | 0 | **WARN** |
| `JsonObjectAsMapSuiteTest.java` | 106/157 | 51 | 0 | 0 | **WARN** |
| `JsonParserParameterizedTest.java` | 31/41 | 10 | 0 | 0 | **WARN** |
| `JsonReaderPathTest.java` | 846/1435 | 589 | 2 | 0 | **DIFF** |

## Discrepancy & Error Details

### /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/BagOfPrimitivesDeserializationBenchmark.java

- **LSP Error Count**: 1
  - `[LSP]` line 40: BeforeExperiment cannot be resolved to a type
- **Javac Error Count**: 11
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/BagOfPrimitivesDeserializationBenchmark.java:18: 错误: 程序包com.google.caliper不存在
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/BagOfPrimitivesDeserializationBenchmark.java:41: 错误: 找不到符号
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/BagOfPrimitivesDeserializationBenchmark.java:38: 错误: 找不到符号
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/BagOfPrimitivesDeserializationBenchmark.java:44: 错误: 找不到符号
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/BagOfPrimitivesDeserializationBenchmark.java:44: 错误: 找不到符号
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/BagOfPrimitivesDeserializationBenchmark.java:51: 错误: 找不到符号
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/BagOfPrimitivesDeserializationBenchmark.java:51: 错误: 找不到符号
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/BagOfPrimitivesDeserializationBenchmark.java:85: 错误: 找不到符号
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/BagOfPrimitivesDeserializationBenchmark.java:99: 错误: 找不到符号
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/BagOfPrimitivesDeserializationBenchmark.java:99: 错误: 找不到符号
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/BagOfPrimitivesDeserializationBenchmark.java:102: 错误: 找不到符号
- **Missed Tokens (86)**:
  - `String`
  - `String`
  - `args`
  - `class`
  - `BeforeExperiment`
  - `gson`
  - `json`
  - `gson`
  - `reps`
  - `i`
  - `gson`
  - `json`
  - `class`
  - `reps`
  - `i`
  - *(and 71 more)*

### /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/SerializationBenchmark.java

- **LSP Error Count**: 1
  - `[LSP]` line 39: BeforeExperiment cannot be resolved to a type
- **Javac Error Count**: 7
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/SerializationBenchmark.java:18: 错误: 程序包com.google.caliper不存在
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/SerializationBenchmark.java:19: 错误: 程序包com.google.caliper不存在
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/SerializationBenchmark.java:33: 错误: 找不到符号
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/SerializationBenchmark.java:34: 错误: 找不到符号
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/SerializationBenchmark.java:40: 错误: 找不到符号
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/SerializationBenchmark.java:37: 错误: 找不到符号
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/SerializationBenchmark.java:43: 错误: 找不到符号
- **Missed Tokens (15)**:
  - `Param`
  - `String`
  - `args`
  - `class`
  - `BeforeExperiment`
  - `gson`
  - `pretty`
  - `setPrettyPrinting`
  - `create`
  - `bag`
  - `reps`
  - `i`
  - `String`
  - `gson`
  - `bag`

### /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/BagOfPrimitives.java

- **Missed Tokens (40)**:
  - `String`
  - `DEFAULT_VALUE`
  - `String`
  - `longValue`
  - `intValue`
  - `booleanValue`
  - `stringValue`
  - `intValue`
  - `String`
  - `longValue`
  - `intValue`
  - `booleanValue`
  - `stringValue`
  - `result`
  - `booleanValue`
  - *(and 25 more)*

### /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/CollectionsDeserializationBenchmark.java

- **LSP Error Count**: 1
  - `[LSP]` line 45: BeforeExperiment cannot be resolved to a type
- **Javac Error Count**: 14
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/CollectionsDeserializationBenchmark.java:18: 错误: 程序包com.google.caliper不存在
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/CollectionsDeserializationBenchmark.java:36: 错误: 找不到符号
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/CollectionsDeserializationBenchmark.java:46: 错误: 找不到符号
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/CollectionsDeserializationBenchmark.java:37: 错误: 找不到符号
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/CollectionsDeserializationBenchmark.java:43: 错误: 找不到符号
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/CollectionsDeserializationBenchmark.java:49: 错误: 找不到符号
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/CollectionsDeserializationBenchmark.java:51: 错误: 找不到符号
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/CollectionsDeserializationBenchmark.java:59: 错误: 找不到符号
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/CollectionsDeserializationBenchmark.java:70: 错误: 找不到符号
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/CollectionsDeserializationBenchmark.java:97: 错误: 找不到符号
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/CollectionsDeserializationBenchmark.java:114: 错误: 找不到符号
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/CollectionsDeserializationBenchmark.java:117: 错误: 找不到符号
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/CollectionsDeserializationBenchmark.java:117: 错误: 找不到符号
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/CollectionsDeserializationBenchmark.java:120: 错误: 找不到符号
- **Missed Tokens (106)**:
  - `String`
  - `String`
  - `args`
  - `class`
  - `BeforeExperiment`
  - `gson`
  - `i`
  - `bags`
  - `json`
  - `gson`
  - `LIST_TYPE`
  - `reps`
  - `i`
  - `reps`
  - `gson`
  - *(and 91 more)*

### /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/NonUploadingCaliperRunner.java

- **Missed Tokens (25)**:
  - `String`
  - `String`
  - `first`
  - `String`
  - `others`
  - `others`
  - `length`
  - `String`
  - `String`
  - `result`
  - `String`
  - `others`
  - `length`
  - `result`
  - `arraycopy`
  - *(and 10 more)*

### /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/ParseBenchmark.java

- **LSP Error Count**: 78
  - `[LSP]` line 123: BeforeExperiment cannot be resolved to a type
  - `[LSP]` line 228: JsonFactory cannot be resolved to a type
  - `[LSP]` line 229: JsonFactoryBuilder cannot be resolved to a type
  - `[LSP]` line 232: com.fasterxml.jackson.core.JsonParser cannot be resolved to a type
  - `[LSP]` line 289: ObjectMapper cannot be resolved to a type
  - `[LSP]` line 308: JsonProperty cannot be resolved to a type
  - `[LSP]` line 309: JsonProperty cannot be resolved to a type
  - `[LSP]` line 310: JsonProperty cannot be resolved to a type
  - `[LSP]` line 311: JsonProperty cannot be resolved to a type
  - `[LSP]` line 312: JsonProperty cannot be resolved to a type
  - `[LSP]` line 313: JsonProperty cannot be resolved to a type
  - `[LSP]` line 314: JsonProperty cannot be resolved to a type
  - `[LSP]` line 315: JsonProperty cannot be resolved to a type
  - `[LSP]` line 316: JsonProperty cannot be resolved to a type
  - `[LSP]` line 317: JsonProperty cannot be resolved to a type
  - `[LSP]` line 318: JsonProperty cannot be resolved to a type
  - `[LSP]` line 319: JsonProperty cannot be resolved to a type
  - `[LSP]` line 320: JsonProperty cannot be resolved to a type
  - `[LSP]` line 321: JsonProperty cannot be resolved to a type
  - `[LSP]` line 322: JsonProperty cannot be resolved to a type
  - `[LSP]` line 323: JsonProperty cannot be resolved to a type
  - `[LSP]` line 324: JsonProperty cannot be resolved to a type
  - `[LSP]` line 325: JsonProperty cannot be resolved to a type
  - `[LSP]` line 326: JsonProperty cannot be resolved to a type
  - `[LSP]` line 327: JsonProperty cannot be resolved to a type
  - `[LSP]` line 332: JsonProperty cannot be resolved to a type
  - `[LSP]` line 333: JsonProperty cannot be resolved to a type
  - `[LSP]` line 334: JsonProperty cannot be resolved to a type
  - `[LSP]` line 335: JsonProperty cannot be resolved to a type
  - `[LSP]` line 336: JsonProperty cannot be resolved to a type
  - `[LSP]` line 337: JsonProperty cannot be resolved to a type
  - `[LSP]` line 338: JsonProperty cannot be resolved to a type
  - `[LSP]` line 339: JsonProperty cannot be resolved to a type
  - `[LSP]` line 340: JsonProperty cannot be resolved to a type
  - `[LSP]` line 341: JsonProperty cannot be resolved to a type
  - `[LSP]` line 342: JsonProperty cannot be resolved to a type
  - `[LSP]` line 343: JsonProperty cannot be resolved to a type
  - `[LSP]` line 344: JsonProperty cannot be resolved to a type
  - `[LSP]` line 345: JsonProperty cannot be resolved to a type
  - `[LSP]` line 346: JsonProperty cannot be resolved to a type
  - `[LSP]` line 347: JsonProperty cannot be resolved to a type
  - `[LSP]` line 348: JsonProperty cannot be resolved to a type
  - `[LSP]` line 349: JsonProperty cannot be resolved to a type
  - `[LSP]` line 350: JsonProperty cannot be resolved to a type
  - `[LSP]` line 351: JsonProperty cannot be resolved to a type
  - `[LSP]` line 353: JsonProperty cannot be resolved to a type
  - `[LSP]` line 357: JsonProperty cannot be resolved to a type
  - `[LSP]` line 358: JsonProperty cannot be resolved to a type
  - `[LSP]` line 359: JsonProperty cannot be resolved to a type
  - `[LSP]` line 360: JsonProperty cannot be resolved to a type
  - `[LSP]` line 361: JsonProperty cannot be resolved to a type
  - `[LSP]` line 362: JsonProperty cannot be resolved to a type
  - `[LSP]` line 363: JsonProperty cannot be resolved to a type
  - `[LSP]` line 364: JsonProperty cannot be resolved to a type
  - `[LSP]` line 365: JsonProperty cannot be resolved to a type
  - `[LSP]` line 366: JsonProperty cannot be resolved to a type
  - `[LSP]` line 367: JsonProperty cannot be resolved to a type
  - `[LSP]` line 368: JsonProperty cannot be resolved to a type
  - `[LSP]` line 369: JsonProperty cannot be resolved to a type
  - `[LSP]` line 370: JsonProperty cannot be resolved to a type
  - `[LSP]` line 371: JsonProperty cannot be resolved to a type
  - `[LSP]` line 375: JsonProperty cannot be resolved to a type
  - `[LSP]` line 376: JsonProperty cannot be resolved to a type
  - `[LSP]` line 377: JsonProperty cannot be resolved to a type
  - `[LSP]` line 379: JsonProperty cannot be resolved to a type
  - `[LSP]` line 383: JsonProperty cannot be resolved to a type
  - `[LSP]` line 384: JsonProperty cannot be resolved to a type
  - `[LSP]` line 408: JsonProperty cannot be resolved to a type
  - `[LSP]` line 417: JsonProperty cannot be resolved to a type
  - `[LSP]` line 418: JsonProperty cannot be resolved to a type
  - `[LSP]` line 419: JsonProperty cannot be resolved to a type
  - `[LSP]` line 420: JsonProperty cannot be resolved to a type
  - `[LSP]` line 422: JsonProperty cannot be resolved to a type
  - `[LSP]` line 426: JsonProperty cannot be resolved to a type
  - `[LSP]` line 427: JsonProperty cannot be resolved to a type
  - `[LSP]` line 428: JsonProperty cannot be resolved to a type
  - `[LSP]` line 451: JsonProperty cannot be resolved to a type
  - `[LSP]` line 460: JsonProperty cannot be resolved to a type
- **Javac Error Count**: 100
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/ParseBenchmark.java:19: 错误: 程序包com.fasterxml.jackson.annotation不存在
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/ParseBenchmark.java:20: 错误: 程序包com.fasterxml.jackson.core不存在
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/ParseBenchmark.java:21: 错误: 程序包com.fasterxml.jackson.core不存在
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/ParseBenchmark.java:22: 错误: 程序包com.fasterxml.jackson.core不存在
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/ParseBenchmark.java:23: 错误: 程序包com.fasterxml.jackson.core.type不存在
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/ParseBenchmark.java:24: 错误: 程序包com.fasterxml.jackson.databind不存在
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/ParseBenchmark.java:25: 错误: 程序包com.fasterxml.jackson.databind不存在
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/ParseBenchmark.java:26: 错误: 程序包com.fasterxml.jackson.databind不存在
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/ParseBenchmark.java:27: 错误: 程序包com.fasterxml.jackson.databind.json不存在
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/ParseBenchmark.java:28: 错误: 程序包com.google.caliper不存在
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/ParseBenchmark.java:29: 错误: 程序包com.google.caliper不存在
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/ParseBenchmark.java:72: 错误: 找不到符号
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/ParseBenchmark.java:74: 错误: 找不到符号
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/ParseBenchmark.java:290: 错误: 找不到符号
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/ParseBenchmark.java:60: 错误: 找不到符号
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/ParseBenchmark.java:61: 错误: 找不到符号
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/ParseBenchmark.java:124: 错误: 找不到符号
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/ParseBenchmark.java:309: 错误: 找不到符号
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/ParseBenchmark.java:310: 错误: 找不到符号
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/ParseBenchmark.java:311: 错误: 找不到符号
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/ParseBenchmark.java:312: 错误: 找不到符号
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/ParseBenchmark.java:313: 错误: 找不到符号
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/ParseBenchmark.java:314: 错误: 找不到符号
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/ParseBenchmark.java:315: 错误: 找不到符号
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/ParseBenchmark.java:316: 错误: 找不到符号
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/ParseBenchmark.java:317: 错误: 找不到符号
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/ParseBenchmark.java:318: 错误: 找不到符号
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/ParseBenchmark.java:319: 错误: 找不到符号
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/ParseBenchmark.java:320: 错误: 找不到符号
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/ParseBenchmark.java:321: 错误: 找不到符号
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/ParseBenchmark.java:322: 错误: 找不到符号
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/ParseBenchmark.java:323: 错误: 找不到符号
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/ParseBenchmark.java:324: 错误: 找不到符号
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/ParseBenchmark.java:325: 错误: 找不到符号
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/ParseBenchmark.java:326: 错误: 找不到符号
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/ParseBenchmark.java:327: 错误: 找不到符号
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/ParseBenchmark.java:328: 错误: 找不到符号
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/ParseBenchmark.java:333: 错误: 找不到符号
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/ParseBenchmark.java:334: 错误: 找不到符号
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/ParseBenchmark.java:335: 错误: 找不到符号
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/ParseBenchmark.java:336: 错误: 找不到符号
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/ParseBenchmark.java:337: 错误: 找不到符号
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/ParseBenchmark.java:338: 错误: 找不到符号
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/ParseBenchmark.java:339: 错误: 找不到符号
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/ParseBenchmark.java:340: 错误: 找不到符号
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/ParseBenchmark.java:341: 错误: 找不到符号
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/ParseBenchmark.java:342: 错误: 找不到符号
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/ParseBenchmark.java:343: 错误: 找不到符号
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/ParseBenchmark.java:344: 错误: 找不到符号
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/ParseBenchmark.java:345: 错误: 找不到符号
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/ParseBenchmark.java:346: 错误: 找不到符号
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/ParseBenchmark.java:347: 错误: 找不到符号
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/ParseBenchmark.java:348: 错误: 找不到符号
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/ParseBenchmark.java:349: 错误: 找不到符号
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/ParseBenchmark.java:350: 错误: 找不到符号
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/ParseBenchmark.java:351: 错误: 找不到符号
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/ParseBenchmark.java:352: 错误: 找不到符号
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/ParseBenchmark.java:354: 错误: 找不到符号
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/ParseBenchmark.java:358: 错误: 找不到符号
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/ParseBenchmark.java:359: 错误: 找不到符号
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/ParseBenchmark.java:360: 错误: 找不到符号
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/ParseBenchmark.java:361: 错误: 找不到符号
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/ParseBenchmark.java:362: 错误: 找不到符号
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/ParseBenchmark.java:363: 错误: 找不到符号
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/ParseBenchmark.java:364: 错误: 找不到符号
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/ParseBenchmark.java:365: 错误: 找不到符号
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/ParseBenchmark.java:366: 错误: 找不到符号
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/ParseBenchmark.java:367: 错误: 找不到符号
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/ParseBenchmark.java:368: 错误: 找不到符号
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/ParseBenchmark.java:369: 错误: 找不到符号
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/ParseBenchmark.java:370: 错误: 找不到符号
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/ParseBenchmark.java:371: 错误: 找不到符号
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/ParseBenchmark.java:372: 错误: 找不到符号
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/ParseBenchmark.java:376: 错误: 找不到符号
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/ParseBenchmark.java:377: 错误: 找不到符号
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/ParseBenchmark.java:378: 错误: 找不到符号
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/ParseBenchmark.java:380: 错误: 找不到符号
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/ParseBenchmark.java:384: 错误: 找不到符号
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/ParseBenchmark.java:385: 错误: 找不到符号
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/ParseBenchmark.java:418: 错误: 找不到符号
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/ParseBenchmark.java:419: 错误: 找不到符号
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/ParseBenchmark.java:420: 错误: 找不到符号
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/ParseBenchmark.java:421: 错误: 找不到符号
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/ParseBenchmark.java:423: 错误: 找不到符号
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/ParseBenchmark.java:427: 错误: 找不到符号
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/ParseBenchmark.java:428: 错误: 找不到符号
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/ParseBenchmark.java:429: 错误: 找不到符号
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/ParseBenchmark.java:461: 错误: 找不到符号
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/ParseBenchmark.java:452: 错误: 找不到符号
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/ParseBenchmark.java:409: 错误: 找不到符号
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/ParseBenchmark.java:64: 错误: 找不到符号
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/ParseBenchmark.java:65: 错误: 找不到符号
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/ParseBenchmark.java:66: 错误: 找不到符号
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/ParseBenchmark.java:165: 错误: 找不到符号
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/ParseBenchmark.java:229: 错误: 找不到符号
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/ParseBenchmark.java:231: 错误: 程序包JsonFactory不存在
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/ParseBenchmark.java:230: 错误: 找不到符号
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/ParseBenchmark.java:233: 错误: 程序包com.fasterxml.jackson.core不存在
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/ParseBenchmark.java:237: 错误: 找不到符号
  - `[JAVAC]` /Users/suremoon/workspace/test-projects/gson/metrics/src/main/java/com/google/gson/metrics/ParseBenchmark.java:296: 错误: 找不到符号
- **Missed Tokens (345)**:
  - `Param`
  - `Param`
  - `TWEETS`
  - `READER_SHORT`
  - `READER_LONG`
  - `gsonType`
  - `jacksonType`
  - `typeToken`
  - `typeReference`
  - `gsonType`
  - `typeToken`
  - `jacksonType`
  - `typeReference`
  - `JACKSON_STREAM`
  - `newParser`
  - *(and 330 more)*

### /Users/suremoon/workspace/test-projects/gson/gson/target/generated-sources/java-templates/com/google/gson/internal/GsonBuildConfig.java

- **Missed Tokens (1)**:
  - `String`

### /Users/suremoon/workspace/test-projects/gson/gson/src/test/java/com/google/gson/VersionExclusionStrategyTest.java

- **Missed Tokens (79)**:
  - `excluder`
  - `c`
  - `assertThat`
  - `isFalse`
  - `assertThat`
  - `isFalse`
  - `excluder`
  - `c`
  - `assertThat`
  - `isTrue`
  - `assertThat`
  - `isTrue`
  - `excluder`
  - `f`
  - `assertThat`
  - *(and 64 more)*

### /Users/suremoon/workspace/test-projects/gson/gson/src/test/java/com/google/gson/JsonArrayAsListTest.java

- **Missed Tokens (345)**:
  - `Test`
  - `a`
  - `asList`
  - `assertThat`
  - `isEqualTo`
  - `assertThrows`
  - `class`
  - `assertThrows`
  - `class`
  - `a`
  - `assertThat`
  - `isEqualTo`
  - `Test`
  - `a`
  - `asList`
  - *(and 330 more)*

### /Users/suremoon/workspace/test-projects/gson/gson/src/test/java/com/google/gson/metrics/PerformanceTest.java

- **Missed Tokens (226)**:
  - `gson`
  - `Test`
  - `Test`
  - `sb`
  - `append`
  - `String`
  - `stackTrace`
  - `sb`
  - `toString`
  - `sb`
  - `append`
  - `stackTrace`
  - `String`
  - `json`
  - `stackTrace`
  - *(and 211 more)*

### /Users/suremoon/workspace/test-projects/gson/gson/src/test/java/com/google/gson/JsonStreamParserTest.java

- **Missed Tokens (93)**:
  - `parser`
  - `Test`
  - `String`
  - `parser`
  - `assertThat`
  - `isEqualTo`
  - `String`
  - `parser`
  - `assertThat`
  - `isEqualTo`
  - `Test`
  - `assertThat`
  - `parser`
  - `isTrue`
  - `assertThat`
  - *(and 78 more)*

### /Users/suremoon/workspace/test-projects/gson/gson/src/test/java/com/google/gson/GsonTest.java

- **Missed Tokens (448)**:
  - `f`
  - `DOUBLE`
  - `LAZILY_PARSED_NUMBER`
  - `gsonBuilder`
  - `CUSTOM_EXCLUDER`
  - `gsonBuilder`
  - `CUSTOM_FIELD_NAMING_STRATEGY`
  - `gsonBuilder`
  - `gsonBuilder`
  - `gsonBuilder`
  - `CUSTOM_OBJECT_TO_NUMBER_STRATEGY`
  - `gsonBuilder`
  - `CUSTOM_NUMBER_TO_NUMBER_STRATEGY`
  - `Test`
  - `assertThat`
  - *(and 433 more)*

### /Users/suremoon/workspace/test-projects/gson/gson/src/test/java/com/google/gson/LongSerializationPolicyTest.java

- **Missed Tokens (69)**:
  - `Test`
  - `DEFAULT`
  - `serialize`
  - `assertThat`
  - `isTrue`
  - `assertThat`
  - `isFalse`
  - `assertThat`
  - `isTrue`
  - `assertThat`
  - `isEqualTo`
  - `Test`
  - `setLongSerializationPolicy`
  - `DEFAULT`
  - `create`
  - *(and 54 more)*

### /Users/suremoon/workspace/test-projects/gson/gson/src/test/java/com/google/gson/JsonPrimitiveTest.java

- **Missed Tokens (312)**:
  - `Test`
  - `assertThrows`
  - `class`
  - `Boolean`
  - `assertThrows`
  - `class`
  - `Number`
  - `assertThrows`
  - `class`
  - `String`
  - `assertThrows`
  - `class`
  - `Test`
  - `assertThat`
  - `isTrue`
  - *(and 297 more)*

### /Users/suremoon/workspace/test-projects/gson/gson/src/test/java/com/google/gson/JavaSerializationTest.java

- **Missed Tokens (59)**:
  - `Test`
  - `Map`
  - `String`
  - `Integer`
  - `getType`
  - `Map`
  - `String`
  - `Integer`
  - `gson`
  - `type`
  - `Map`
  - `String`
  - `Integer`
  - `serializedCopy`
  - `assertThat`
  - *(and 44 more)*

### /Users/suremoon/workspace/test-projects/gson/gson/src/test/java/com/google/gson/integration/OSGiManifestIT.java

- **Missed Tokens (143)**:
  - `url`
  - `manifest`
  - `String`
  - `findManifest`
  - `assertWithMessage`
  - `that`
  - `url`
  - `toString`
  - `endsWith`
  - `manifestAttributes`
  - `getMainAttributes`
  - `String`
  - `String`
  - `name`
  - `manifestAttributes`
  - *(and 128 more)*

### /Users/suremoon/workspace/test-projects/gson/gson/src/test/java/com/google/gson/JsonObjectTest.java

- **Missed Tokens (255)**:
  - `Test`
  - `String`
  - `assertThat`
  - `isFalse`
  - `assertThat`
  - `isNull`
  - `jsonObj`
  - `assertThat`
  - `isEqualTo`
  - `assertThat`
  - `isEqualTo`
  - `assertThat`
  - `isFalse`
  - `assertThat`
  - `isNull`
  - *(and 240 more)*

### /Users/suremoon/workspace/test-projects/gson/gson/src/test/java/com/google/gson/JsonObjectAsMapSuiteTest.java

- **Missed Tokens (51)**:
  - `String`
  - `String`
  - `Map`
  - `entry`
  - `Map`
  - `entry`
  - `Map`
  - `entry`
  - `Map`
  - `entry`
  - `Map`
  - `entry`
  - `Map`
  - `String`
  - `Object`
  - *(and 36 more)*

### /Users/suremoon/workspace/test-projects/gson/gson/src/test/java/com/google/gson/JsonParserParameterizedTest.java

- **Missed Tokens (10)**:
  - `String`
  - `asList`
  - `String`
  - `Test`
  - `json`
  - `String`
  - `adapter`
  - `assertThat`
  - `isEqualTo`
  - `json`

### /Users/suremoon/workspace/test-projects/gson/gson/src/test/java/com/google/gson/stream/JsonReaderPathTest.java

- **LSP Error Count**: 2
  - `[LSP]` line 37: Parameterized.Parameters cannot be resolved to a type
  - `[LSP]` line 43: Parameterized.Parameter cannot be resolved to a type
- **Javac Error Count**: 0
- **Missed Tokens (589)**:
  - `Parameters`
  - `name`
  - `Object`
  - `asList`
  - `Object`
  - `STRING_READER`
  - `Object`
  - `OBJECT_READER`
  - `Parameter`
  - `Test`
  - `factory`
  - `create`
  - `assertThat`
  - `isEqualTo`
  - `assertThat`
  - *(and 574 more)*

