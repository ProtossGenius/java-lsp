#!/usr/bin/env bash

set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cd "$repo_root"

git submodule update --init --recursive third_party/eclipse.jdt.ls third_party/spring-petclinic

"$repo_root/scripts/generate_jdtls_ut_manifest.sh"

