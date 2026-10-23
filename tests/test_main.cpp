#include "test_framework.h"
#include <iostream>

int main() {
    auto& tests = test::getRegistry();
    std::cout << "Running " << tests.size() << " unit tests...\n";
    int passed = 0;
    int failed = 0;

    for (const auto& t : tests) {
        std::cout << "  RUN  [" << t.suite << "." << t.name << "]\n";
        try {
            t.func();
            std::cout << "  PASS [" << t.suite << "." << t.name << "]\n";
            passed++;
        } catch (const std::exception& e) {
            std::cout << "  FAIL [" << t.suite << "." << t.name << "]: " << e.what() << "\n";
            failed++;
        }
    }

    std::cout << "\n=== Tests summary: " << passed << " passed, " << failed << " failed ===\n";
    return failed == 0 ? 0 : 1;
}
