#include "semantics/scope.h"

namespace javalsp {

std::shared_ptr<Scope> Scope::create(ScopeKind kind, std::shared_ptr<Scope> parent, Range range) {
    auto s = std::make_shared<Scope>(kind, parent, range);
    if (parent) {
        parent->addChild(s);
    }
    return s;
}

Scope::Scope(ScopeKind kind, std::shared_ptr<Scope> parent, Range range)
    : kind_(kind), parent_(parent), range_(range) {
}

void Scope::addVariable(const JavaVariable& var) {
    for (auto& v : variables_) {
        if (v.name == var.name) {
            v = var; // Shadowing or updating
            return;
        }
    }
    variables_.push_back(var);
}

void Scope::addLambda(std::shared_ptr<JavaLambda> lambda) {
    lambdas_.push_back(lambda);
}

std::optional<JavaVariable> Scope::lookupVariable(const std::string& name) const {
    for (const auto& v : variables_) {
        if (v.name == name) return v;
    }
    if (parent_) {
        return parent_->lookupVariable(name);
    }
    return std::nullopt;
}

std::shared_ptr<Scope> Scope::findInnermostScope(const Position& pos) {
    if (!range_.contains(pos)) return nullptr;

    for (auto it = children_.rbegin(); it != children_.rend(); ++it) {
        if ((*it)->range().contains(pos)) {
            auto inner = (*it)->findInnermostScope(pos);
            if (inner) return inner;
        }
    }
    return shared_from_this();
}

} // namespace javalsp
