#pragma once

#include "parser/java_ast.h"
#include <string>
#include <vector>
#include <unordered_map>
#include <memory>
#include <optional>

namespace javalsp {

enum class ScopeKind {
    FILE,
    CLASS,
    METHOD,
    BLOCK,
    LAMBDA
};

class Scope : public std::enable_shared_from_this<Scope> {
public:
    static std::shared_ptr<Scope> create(
        ScopeKind kind,
        std::shared_ptr<Scope> parent = nullptr,
        Range range = Range{{0, 0}, {999999, 999999}});

    Scope(ScopeKind kind, std::shared_ptr<Scope> parent = nullptr, Range range = Range{{0, 0}, {999999, 999999}});

    ScopeKind kind() const { return kind_; }
    std::shared_ptr<Scope> parent() const { return parent_; }
    Range range() const { return range_; }

    void addVariable(const JavaVariable& var);
    void addLambda(std::shared_ptr<JavaLambda> lambda);

    std::optional<JavaVariable> lookupVariable(const std::string& name) const;
    std::shared_ptr<Scope> findInnermostScope(const Position& pos);

    const std::vector<JavaVariable>& localVariables() const { return variables_; }
    const std::vector<std::shared_ptr<Scope>>& children() const { return children_; }

    void addChild(std::shared_ptr<Scope> child) {
        children_.push_back(child);
    }

private:
    ScopeKind kind_;
    std::shared_ptr<Scope> parent_;
    Range range_;
    std::vector<JavaVariable> variables_;
    std::vector<std::shared_ptr<JavaLambda>> lambdas_;
    std::vector<std::shared_ptr<Scope>> children_;
};

} // namespace javalsp
