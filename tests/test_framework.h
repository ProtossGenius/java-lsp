#pragma once

#include <iostream>
#include <string>
#include <vector>
#include <functional>
#include <sstream>

namespace test {

struct TestCase {
    std::string suite;
    std::string name;
    std::function<void()> func;
};

inline std::vector<TestCase>& getRegistry() {
    static std::vector<TestCase> registry;
    return registry;
}

inline bool registerTest(const std::string& suite, const std::string& name, std::function<void()> func) {
    getRegistry().push_back({suite, name, func});
    return true;
}

#define TEST(suite, name) \
    void test_##suite##_##name(); \
    static bool reg_##suite##_##name = test::registerTest(#suite, #name, test_##suite##_##name); \
    void test_##suite##_##name()

#define ASSERT_TRUE(cond) do { \
    if (!(cond)) { \
        std::stringstream ss; \
        ss << "Assertion failed: " #cond " at " << __FILE__ << ":" << __LINE__; \
        throw std::runtime_error(ss.str()); \
    } \
} while (0)

#define ASSERT_FALSE(cond) ASSERT_TRUE(!(cond))

#define ASSERT_EQ(a, b) do { \
    if ((a) != (b)) { \
        std::stringstream ss; \
        ss << "Assertion failed: (" #a " == " #b ") [" << (a) << " != " << (b) << "] at " << __FILE__ << ":" << __LINE__; \
        throw std::runtime_error(ss.str()); \
    } \
} while (0)

#define ASSERT_NE(a, b) do { \
    if ((a) == (b)) { \
        std::stringstream ss; \
        ss << "Assertion failed: (" #a " != " #b ") [" << (a) << " == " << (b) << "] at " << __FILE__ << ":" << __LINE__; \
        throw std::runtime_error(ss.str()); \
    } \
} while (0)

} // namespace test
