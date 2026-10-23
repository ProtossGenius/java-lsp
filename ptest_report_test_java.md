# ptest Simulation & Diagnostic Report

- **Target Project**: `/Users/suremoon/workspace/test-java`
- **Total Files Tested**: 9
- **Passed**: 9
- **Diagnostic Discrepancies**: 0
- **Non-constant Token Completion Rate**: 130 / 240 (54.2%)

## Test Results by File

| File | Tokens (Completed/Total) | Missed | LSP Errors | Javac Errors | Status |
| :--- | :---: | :---: | :---: | :---: | :---: |
| `UserController.java` | 0/0 | 0 | 0 | 0 | **PASS** |
| `DemoApplicationTests.java` | 4/4 | 0 | 0 | 0 | **PASS** |
| `LoginControllerTests.java` | 32/88 | 56 | 0 | 0 | **WARN** |
| `NvimRenameSpec1780020060.java` | 3/4 | 1 | 0 | 0 | **WARN** |
| `NvimRenameSpec1780020307.java` | 3/4 | 1 | 0 | 0 | **WARN** |
| `UserDTO.java` | 0/0 | 0 | 0 | 0 | **PASS** |
| `LoginController.java` | 78/125 | 47 | 0 | 0 | **WARN** |
| `NvimRenameSpec1780020454.java` | 3/4 | 1 | 0 | 0 | **WARN** |
| `DemoApplication.java` | 7/11 | 4 | 0 | 0 | **WARN** |

## Discrepancy & Error Details

### /Users/suremoon/workspace/test-java/src/test/java/com/example/demo/LoginControllerTests.java

- **Missed Tokens (56)**:
  - `Exception`
  - `mockMvc`
  - `perform`
  - `get`
  - `andExpect`
  - `status`
  - `isOk`
  - `andExpect`
  - `content`
  - `string`
  - `containsString`
  - `Exception`
  - `mockMvc`
  - `perform`
  - `post`
  - *(and 41 more)*

### /Users/suremoon/workspace/test-java/src/main/java/com/example/demo/NvimRenameSpec1780020060.java

- **Missed Tokens (1)**:
  - `String`

### /Users/suremoon/workspace/test-java/src/main/java/com/example/demo/NvimRenameSpec1780020307.java

- **Missed Tokens (1)**:
  - `String`

### /Users/suremoon/workspace/test-java/src/main/java/com/example/demo/LoginController.java

- **Missed Tokens (47)**:
  - `String`
  - `String`
  - `String`
  - `String`
  - `String`
  - `configuredUsername`
  - `configuredPassword`
  - `required`
  - `String`
  - `error`
  - `session`
  - `model`
  - `isLoggedIn`
  - `model`
  - `addAttribute`
  - *(and 32 more)*

### /Users/suremoon/workspace/test-java/src/main/java/com/example/demo/NvimRenameSpec1780020454.java

- **Missed Tokens (1)**:
  - `String`

### /Users/suremoon/workspace/test-java/src/main/java/com/example/demo/DemoApplication.java

- **Missed Tokens (4)**:
  - `String`
  - `args`
  - `run`
  - `class`

