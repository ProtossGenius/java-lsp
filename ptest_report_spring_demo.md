# ptest Simulation & Diagnostic Report

- **Target Project**: `/Users/suremoon/.config/nvim/test-projects/java17-spring-demo`
- **Total Files Tested**: 12
- **Passed**: 12
- **Diagnostic Discrepancies**: 0
- **Non-constant Token Completion Rate**: 189 / 260 (72.7%)

## Test Results by File

| File | Tokens (Completed/Total) | Missed | LSP Errors | Javac Errors | Status |
| :--- | :---: | :---: | :---: | :---: | :---: |
| `UserServiceSmokeTest.java` | 15/20 | 5 | 0 | 0 | **WARN** |
| `UserMapperImpl.java` | 0/0 | 0 | 0 | 0 | **PASS** |
| `UserMapper.java` | 18/28 | 10 | 0 | 0 | **WARN** |
| `UserController.java` | 63/79 | 16 | 0 | 0 | **WARN** |
| `Table.java` | 1/3 | 2 | 0 | 0 | **WARN** |
| `CreateUserRequest.java` | 10/12 | 2 | 0 | 0 | **WARN** |
| `User.java` | 12/15 | 3 | 0 | 0 | **WARN** |
| `UserEnum.java` | 2/7 | 5 | 0 | 0 | **WARN** |
| `UserServiceImpl.java` | 48/62 | 14 | 0 | 0 | **WARN** |
| `UserService.java` | 6/11 | 5 | 0 | 0 | **WARN** |
| `DemoApplication.java` | 9/15 | 6 | 0 | 0 | **WARN** |
| `UserFeignClient.java` | 5/8 | 3 | 0 | 0 | **WARN** |

## Discrepancy & Error Details

### /Users/suremoon/.config/nvim/test-projects/java17-spring-demo/core/src/test/java/com/example/demo/service/UserServiceSmokeTest.java

- **Missed Tokens (5)**:
  - `userService`
  - `assertThat`
  - `extracting`
  - `getEmail`
  - `contains`

### /Users/suremoon/.config/nvim/test-projects/java17-spring-demo/core/src/main/java/com/example/demo/mapper/UserMapper.java

- **Missed Tokens (10)**:
  - `findAll`
  - `findById`
  - `Long`
  - `insert`
  - `user`
  - `updateStatus`
  - `Integer`
  - `eu`
  - `chaosFindAll`
  - `findAll`

### /Users/suremoon/.config/nvim/test-projects/java17-spring-demo/core/src/main/java/com/example/demo/controller/UserController.java

- **Missed Tokens (16)**:
  - `log`
  - `userService`
  - `Long`
  - `log`
  - `log`
  - `log`
  - `log`
  - `log`
  - `i`
  - `log`
  - `String`
  - `userService`
  - `CREATED`
  - `request`
  - `log`
  - *(and 1 more)*

### /Users/suremoon/.config/nvim/test-projects/java17-spring-demo/core/src/main/java/com/example/demo/model/Table.java

- **Missed Tokens (2)**:
  - `String`
  - `value`

### /Users/suremoon/.config/nvim/test-projects/java17-spring-demo/core/src/main/java/com/example/demo/model/CreateUserRequest.java

- **Missed Tokens (2)**:
  - `String`
  - `String`

### /Users/suremoon/.config/nvim/test-projects/java17-spring-demo/core/src/main/java/com/example/demo/model/User.java

- **Missed Tokens (3)**:
  - `Long`
  - `String`
  - `String`

### /Users/suremoon/.config/nvim/test-projects/java17-spring-demo/core/src/main/java/com/example/demo/model/UserEnum.java

- **Missed Tokens (5)**:
  - `AVAILABLE`
  - `DELETED`
  - `test`
  - `Map`
  - `of`

### /Users/suremoon/.config/nvim/test-projects/java17-spring-demo/core/src/main/java/com/example/demo/service/impl/UserServiceImpl.java

- **Missed Tokens (14)**:
  - `log`
  - `userMapper`
  - `Long`
  - `log`
  - `userMapper`
  - `orElseThrow`
  - `request`
  - `name`
  - `getName`
  - `email`
  - `getEmail`
  - `build`
  - `log`
  - `userMapper`

### /Users/suremoon/.config/nvim/test-projects/java17-spring-demo/core/src/main/java/com/example/demo/service/UserService.java

- **Missed Tokens (5)**:
  - `listUsers`
  - `getUser`
  - `Long`
  - `createUser`
  - `request`

### /Users/suremoon/.config/nvim/test-projects/java17-spring-demo/core/src/main/java/com/example/demo/DemoApplication.java

- **Missed Tokens (6)**:
  - `String`
  - `args`
  - `log`
  - `getProperty`
  - `run`
  - `class`

### /Users/suremoon/.config/nvim/test-projects/java17-spring-demo/api/src/main/java/com/example/demo/api/UserFeignClient.java

- **Missed Tokens (3)**:
  - `String`
  - `getUserName`
  - `Long`

