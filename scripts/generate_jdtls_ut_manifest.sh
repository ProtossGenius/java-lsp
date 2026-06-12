#!/usr/bin/env bash

set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
submodule_root="$repo_root/third_party/eclipse.jdt.ls"
manifest_dir="$repo_root/testdata/manifests"
manifest_file="$manifest_dir/jdtls-ut-files.txt"
summary_file="$manifest_dir/jdtls-ut-summary.txt"

if [[ ! -d "$submodule_root/.git" && ! -f "$submodule_root/.git" ]]; then
  echo "missing JDTLS checkout at $submodule_root" >&2
  exit 1
fi

mkdir -p "$manifest_dir"

cd "$submodule_root"

find org.eclipse.jdt.ls.tests org.eclipse.jdt.ls.tests.syntaxserver \
  -type f \( -name '*Test.java' -o -name '*Tests.java' \) \
  | sort > "$manifest_file"

test_count="$(wc -l < "$manifest_file" | tr -d ' ')"
test_commit="$(git rev-parse HEAD)"

cat > "$summary_file" <<EOF
upstream_repo: eclipse-jdtls/eclipse.jdt.ls
upstream_commit: $test_commit
unit_test_file_count: $test_count
manifest: testdata/manifests/jdtls-ut-files.txt
EOF

