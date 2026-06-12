#!/usr/bin/env bash

set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source_root="$repo_root/third_party/spring-petclinic"

if [[ ! -f "$source_root/pom.xml" || ! -f "$source_root/build.gradle" ]]; then
  echo "missing spring-petclinic checkout at $source_root" >&2
  exit 1
fi

workdir="$(mktemp -d "${TMPDIR:-/tmp}/petclinic-refactor-XXXXXX")"
trap 'rm -rf "$workdir"' EXIT

gradle_java_home="${JAVA17_HOME:-}"
if [[ -z "$gradle_java_home" ]] && command -v /usr/libexec/java_home >/dev/null 2>&1; then
  gradle_java_home="$(/usr/libexec/java_home -v 17 2>/dev/null || true)"
fi
if [[ -n "$gradle_java_home" ]] && ! "$gradle_java_home/bin/java" -version 2>&1 | grep -q 'version "17\.'; then
  gradle_java_home=""
fi

rsync -a \
  --exclude '.git' \
  --exclude '.gradle' \
  --exclude 'build' \
  --exclude 'target' \
  "$source_root/" "$workdir/"

find "$workdir" -type f -name '*.java' -print0 | xargs -0 perl -0pi -e 's/\bPetTypeFormatter\b/PetKindFormatter/g'
mv \
  "$workdir/src/main/java/org/springframework/samples/petclinic/owner/PetTypeFormatter.java" \
  "$workdir/src/main/java/org/springframework/samples/petclinic/owner/PetKindFormatter.java"
mv \
  "$workdir/src/test/java/org/springframework/samples/petclinic/owner/PetTypeFormatterTests.java" \
  "$workdir/src/test/java/org/springframework/samples/petclinic/owner/PetKindFormatterTests.java"

owner_controller="$workdir/src/main/java/org/springframework/samples/petclinic/owner/OwnerController.java"
perl -0pi -e '
  s/private final OwnerRepository owners;/private final OwnerRepository ownerRepository;/g;
  s/public OwnerController\(OwnerRepository owners\)/public OwnerController(OwnerRepository ownerRepository)/g;
  s/this\.owners = owners;/this.ownerRepository = ownerRepository;/g;
  s/this\.owners\.findById/this.ownerRepository.findById/g;
  s/this\.owners\.save/this.ownerRepository.save/g;
  s/return owners\.findByLastNameStartingWith\(lastname, pageable\);/return ownerRepository.findByLastNameStartingWith(lastname, pageable);/g;
  s/findPaginatedForOwnersLastName/findOwnerPageByLastName/g;
' "$owner_controller"

chmod +x "$workdir/mvnw" "$workdir/gradlew"

(
  cd "$workdir"
  ./mvnw -q -DskipTests test-compile
  if [[ -n "$gradle_java_home" ]]; then
    JAVA_HOME="$gradle_java_home" ./gradlew --no-daemon -q testClasses
  else
    echo "skipping Gradle testClasses: no local JDK 17 installation found"
  fi
)

echo "refactor compile verification succeeded"
