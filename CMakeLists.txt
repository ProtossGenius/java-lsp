cmake_minimum_required(VERSION 3.20)
project(java_lsp C CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

# LLVM configuration
set(CMAKE_PREFIX_PATH "/opt/homebrew/opt/llvm" ${CMAKE_PREFIX_PATH})
find_package(LLVM REQUIRED CONFIG)
message(STATUS "Found LLVM ${LLVM_PACKAGE_VERSION}")

include_directories(
    ${LLVM_INCLUDE_DIRS}
    ${CMAKE_CURRENT_SOURCE_DIR}/include
    ${CMAKE_CURRENT_SOURCE_DIR}/src
    /opt/homebrew/opt/tree-sitter/include
)

link_directories(
    /opt/homebrew/opt/tree-sitter/lib
)

add_definitions(${LLVM_DEFINITIONS})

llvm_map_components_to_libnames(LLVM_LIBS Support Core)

set(CORE_SOURCES
    src/utils/logger.cpp
    src/utils/fs_utils.cpp
    src/lsp/protocol.cpp
    src/lsp/transport.cpp
    src/lsp/server.cpp
    src/parser/tree_sitter_loader.cpp
    src/parser/java_parser.cpp
    src/semantics/lombok.cpp
    src/semantics/scope.cpp
    src/semantics/nested_lambda.cpp
    src/semantics/symbol_table.cpp
    src/semantics/type_checker.cpp
    src/completion/graph_search.cpp
    src/completion/completion_engine.cpp
    src/project/jar_indexer.cpp
    src/project/dependency_resolver.cpp
    src/mybatis/mysql_service.cpp
    src/mybatis/mybatis_xml.cpp
    src/project/decompilation.cpp
    src/project/project_manager.cpp
)

add_library(java_lsp_core STATIC ${CORE_SOURCES})
target_link_libraries(java_lsp_core ${LLVM_LIBS} tree-sitter dl)

# Executable java-lsp
add_executable(java-lsp src/main.cpp)
target_link_libraries(java-lsp java_lsp_core ${LLVM_LIBS} tree-sitter dl)

# Post-build: copy executable to bin/
add_custom_command(TARGET java-lsp POST_BUILD
    COMMAND ${CMAKE_COMMAND} -E make_directory ${CMAKE_CURRENT_SOURCE_DIR}/bin
    COMMAND ${CMAKE_COMMAND} -E copy $<TARGET_FILE:java-lsp> ${CMAKE_CURRENT_SOURCE_DIR}/bin/java-lsp
)

# Executable ptest
add_executable(ptest src/tools/ptest.cpp)
target_link_libraries(ptest java_lsp_core ${LLVM_LIBS} tree-sitter dl)
add_custom_command(TARGET ptest POST_BUILD
    COMMAND ${CMAKE_COMMAND} -E make_directory ${CMAKE_CURRENT_SOURCE_DIR}/bin
    COMMAND ${CMAKE_COMMAND} -E copy $<TARGET_FILE:ptest> ${CMAKE_CURRENT_SOURCE_DIR}/bin/ptest
)

# Test executable
enable_testing()
add_executable(java-lsp-tests
    tests/test_main.cpp
    tests/test_parser.cpp
    tests/test_lombok.cpp
    tests/test_nested_lambda.cpp
    tests/test_chained_completion.cpp
    tests/jdtls_translated_tests.cpp
)
target_link_libraries(java-lsp-tests java_lsp_core ${LLVM_LIBS} tree-sitter dl)

add_test(NAME AllTests COMMAND java-lsp-tests)
