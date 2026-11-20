"""Black-box tests for java-lsp diagnostics accuracy (false positives, false negatives, live lifecycle)."""

from __future__ import annotations

import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any

from tests.blackbox.lsp_client import (
    LspClient,
    LspClientError,
    REPO_ROOT,
    TempJavaProject,
    path_to_uri,
)

SPRING_DEMO_DIR = REPO_ROOT / "java17-spring-demo"
M2_REPO = Path.home() / ".m2" / "repository"
LOMBOK_JAR = M2_REPO / "org" / "projectlombok" / "lombok" / "1.18.38" / "lombok-1.18.38.jar"

SIMPLE_MAVEN_POM = """\
<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0"
         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0 https://maven.apache.org/xsd/maven-4.0.0.xsd">
  <modelVersion>4.0.0</modelVersion>
  <groupId>com.example</groupId>
  <artifactId>diag-test-proj</artifactId>
  <version>1.0.0</version>
  <properties>
    <maven.compiler.source>17</maven.compiler.source>
    <maven.compiler.target>17</maven.compiler.target>
    <project.build.sourceEncoding>UTF-8</project.build.sourceEncoding>
  </properties>
  <dependencies>
    <dependency>
      <groupId>org.projectlombok</groupId>
      <artifactId>lombok</artifactId>
      <version>1.18.38</version>
      <scope>provided</scope>
    </dependency>
    <dependency>
      <groupId>org.slf4j</groupId>
      <artifactId>slf4j-api</artifactId>
      <version>2.0.13</version>
    </dependency>
  </dependencies>
  <build>
    <pluginManagement>
      <plugins>
        <plugin>
          <groupId>org.apache.maven.plugins</groupId>
          <artifactId>maven-dependency-plugin</artifactId>
          <version>3.6.1</version>
        </plugin>
      </plugins>
    </pluginManagement>
  </build>
</project>
"""


def _javac_compile(files: dict[str, str], extra_cp: list[Path] | None = None) -> tuple[int, str]:
    """Compile Java sources with real javac (--release 17) to verify ground-truth validity."""
    with tempfile.TemporaryDirectory(prefix="javac_verify_") as tmp:
        root = Path(tmp)
        src_paths: list[str] = []
        for rel, content in files.items():
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            src_paths.append(str(p))
        out_dir = root / "out"
        out_dir.mkdir(exist_ok=True)
        cmd = ["javac", "--release", "17", "-d", str(out_dir)]
        if extra_cp:
            cp_str = os.pathsep.join(str(p) for p in extra_cp if p.exists())
            if cp_str:
                cmd.extend(["-cp", cp_str])
        cmd.extend(src_paths)
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        combined = (proc.stdout + "\n" + proc.stderr).strip()
        return proc.returncode, combined


def _is_error_diag(diag: dict[str, Any]) -> bool:
    """Return True if LSP Diagnostic is Error severity (1) or unspecified."""
    sev = diag.get("severity")
    return sev is None or sev == 1


def _format_diags(diags: list[dict[str, Any]]) -> str:
    if not diags:
        return "[]"
    parts = []
    for d in diags:
        rng = d.get("range", {})
        st = rng.get("start", {})
        parts.append(
            f"(line={st.get('line')}, col={st.get('character')}, "
            f"sev={d.get('severity')}, code={d.get('code')!r}, "
            f"tags={d.get('tags')}, msg={d.get('message')!r})"
        )
    return "[" + "; ".join(parts) + "]"


def _collect_spring_java_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for sub in ("api/src", "core/src"):
        base = root / sub
        if base.exists():
            files.extend(sorted(base.rglob("*.java")))
    return files


class _BaseDiagTestCase(unittest.TestCase):
    """Shared safe LSP helpers ensuring zero HARNESS_ERROR outcomes."""

    client: LspClient
    project: TempJavaProject

    def _safe_open_and_await_diags(
        self,
        uri: str,
        text: str,
        tag: str,
        timeout: float = 5.0,
        quiet_period: float = 0.25,
        require_notification: bool = False,
    ) -> list[dict[str, Any]]:
        try:
            seq = self.client.did_open(uri, text)
            return self.client.wait_for_diagnostics_quiescent(
                uri, timeout=timeout, quiet_period=quiet_period, after_seq=seq
            )
        except TimeoutError as exc:
            if require_notification:
                self.fail(
                    f"[{tag}] Timed out after {timeout}s waiting for publishDiagnostics on {uri}. "
                    f"Latest stored diagnostics: {_format_diags(self.client.get_diagnostics(uri))}; "
                    f"error={exc}"
                )
            return self.client.get_diagnostics(uri)
        except LspClientError as exc:
            self.fail(f"[{tag}] LSP client error while waiting for diagnostics on {uri}: {exc}")

    def _safe_change_and_await_diags(
        self,
        uri: str,
        text: str,
        tag: str,
        predicate: Any = None,
        timeout: float = 8.0,
    ) -> list[dict[str, Any]]:
        try:
            seq = self.client.did_change(uri, text=text)
            return self.client.wait_for_diagnostics(
                uri, timeout=timeout, after_seq=seq, predicate=predicate, settle_time=0.25
            )
        except TimeoutError as exc:
            self.fail(
                f"[{tag}] Timed out after {timeout}s waiting for updated publishDiagnostics after didChange on {uri}. "
                f"Latest stored diagnostics: {_format_diags(self.client.get_diagnostics(uri))}; "
                f"error={exc}"
            )
        except LspClientError as exc:
            self.fail(f"[{tag}] LSP client error after didChange on {uri}: {exc}")


class TestSpringDemoDiagnostics(_BaseDiagTestCase):
    """False-positive checks across all valid files of java17-spring-demo."""

    _init_error: str | None = None

    @classmethod
    def setUpClass(cls) -> None:
        cls.project = TempJavaProject(prefix="diag_spring_")
        cls.client = LspClient(root_path=cls.project.root, default_timeout=20.0)
        try:
            cls.project.copy_from(SPRING_DEMO_DIR)
            cls.client.start()
            cls.client.initialize_and_ready(root_path=cls.project.root, timeout=60.0)
        except Exception as exc:
            cls._init_error = f"setUpClass initialize failed: {exc}"

    @classmethod
    def tearDownClass(cls) -> None:
        try:
            cls.client.close()
        finally:
            cls.project.close()

    def setUp(self) -> None:
        if self._init_error is not None:
            self.fail(f"[DIAG-01] {self._init_error}")

    def test_spring_demo_all_files_no_false_positive_errors(self) -> None:
        """[DIAG-01] Valid compiling files in java17-spring-demo must have 0 Error diagnostics."""
        java_files = _collect_spring_java_files(self.project.root)
        self.assertGreaterEqual(
            len(java_files),
            11,
            f"[DIAG-01] Expected >=11 Java files in copied java17-spring-demo, found {len(java_files)}",
        )

        # Open all files and capture diagnostics for each.
        seq_map: dict[Path, int] = {}
        for jf in java_files:
            text = jf.read_text(encoding="utf-8")
            uri = path_to_uri(jf)
            try:
                seq_map[jf] = self.client.did_open(uri, text)
            except LspClientError as exc:
                self.fail(f"[DIAG-01] Failed to didOpen {jf}: {exc}")

        # Give server time to publish diagnostics across opened files.
        deadline = time.monotonic() + 8.0
        unexpected_errors: dict[str, str] = {}
        for idx, jf in enumerate(java_files):
            uri = path_to_uri(jf)
            rem = max(0.4, deadline - time.monotonic())
            wait_t = min(3.0, rem) if idx == 0 else min(1.0, rem)
            try:
                diags = self.client.wait_for_diagnostics_quiescent(
                    uri, timeout=wait_t, quiet_period=0.2, after_seq=seq_map[jf]
                )
            except TimeoutError:
                diags = self.client.get_diagnostics(uri)
            except LspClientError as exc:
                self.fail(f"[DIAG-01] Server error while reading diagnostics for {jf}: {exc}")

            errors = [d for d in diags if _is_error_diag(d)]
            if errors:
                rel = str(jf.relative_to(self.project.root))
                unexpected_errors[rel] = _format_diags(errors)

        for jf in java_files:
            try:
                self.client.did_close(path_to_uri(jf))
            except LspClientError:
                pass

        self.assertEqual(
            {},
            unexpected_errors,
            "[DIAG-01] Expected 0 Error diagnostics across all valid java17-spring-demo files, "
            f"but got false positives: {unexpected_errors}",
        )


class TestValidJavaFalsePositives(_BaseDiagTestCase):
    """False-positive checks on modern/valid Java constructs compiled against JDK 17 (+ Lombok)."""

    _emits_clean_diags: bool | None = None
    _init_error: str | None = None

    @classmethod
    def setUpClass(cls) -> None:
        cls.project = TempJavaProject(
            files={"pom.xml": SIMPLE_MAVEN_POM},
            prefix="diag_valid_",
        )
        cls.client = LspClient(root_path=cls.project.root, default_timeout=20.0)
        try:
            cls.client.start()
            cls.client.initialize_and_ready(root_path=cls.project.root, timeout=60.0)
        except Exception as exc:
            cls._init_error = f"setUpClass initialize failed: {exc}"

    @classmethod
    def tearDownClass(cls) -> None:
        try:
            cls.client.close()
        finally:
            cls.project.close()

    def setUp(self) -> None:
        if self._init_error is not None:
            self.fail(f"[DIAG-VALID] {self._init_error}")
        if not self.client.is_alive():
            try:
                self.client.restart(clean_storage=False)
                self.client.initialize_and_ready(root_path=self.project.root, timeout=60.0)
            except Exception as exc:
                self.fail(f"[DIAG-VALID] Server restart failed: {exc}")

    def _verify_valid_java(
        self,
        tag: str,
        files: dict[str, str],
        target_rel: str,
        check_javac: bool = True,
        extra_cp: list[Path] | None = None,
    ) -> None:
        if check_javac:
            rc, out = _javac_compile(files, extra_cp=extra_cp)
            self.assertEqual(
                0,
                rc,
                f"[{tag}] Test fixture bug: javac failed to compile valid fixture {target_rel}:\n{out}",
            )

        written_uris: list[str] = []
        for rel, content in files.items():
            self.project.write_file(rel, content)
            written_uris.append(self.project.uri(rel))

        def _cleanup() -> None:
            for u in written_uris:
                try:
                    self.client.did_close(u)
                except LspClientError:
                    pass
            for rel in files:
                self.project.remove_file(rel)

        self.addCleanup(_cleanup)

        # Open non-target helper files first, then target file.
        for rel, content in files.items():
            if rel != target_rel:
                try:
                    self.client.did_open(self.project.uri(rel), content)
                except LspClientError as exc:
                    self.fail(f"[{tag}] Server error opening helper {rel}: {exc}")

        target_uri = self.project.uri(target_rel)
        target_text = files[target_rel]
        wait_t = 3.0 if self.__class__._emits_clean_diags is not False else 0.75
        try:
            seq = self.client.did_open(target_uri, target_text)
            diags = self.client.wait_for_diagnostics_quiescent(
                target_uri, timeout=wait_t, quiet_period=0.25, after_seq=seq
            )
            self.__class__._emits_clean_diags = True
        except TimeoutError:
            self.__class__._emits_clean_diags = False
            diags = self.client.get_diagnostics(target_uri)
        except LspClientError as exc:
            self.fail(f"[{tag}] Server error on didOpen {target_rel}: {exc}")

        errors = [d for d in diags if _is_error_diag(d)]
        self.assertEqual(
            [],
            errors,
            f"[{tag}] False-positive Error diagnostics reported on valid compiling code ({target_rel}).\n"
            f"Actual diagnostics: {_format_diags(diags)}\nExpected: 0 error diagnostics.",
        )

    def test_generics_and_wildcards_no_false_positive(self) -> None:
        """[DIAG-02] Generic methods, bounded type params, wildcards, and diamond must produce 0 errors."""
        code = """\
package com.example.valid;

import java.util.ArrayList;
import java.util.List;

public class GenericsValid {
  static class Box<T> {
    private final T value;
    Box(T value) { this.value = value; }
    T get() { return value; }
  }

  public static <T extends Comparable<T>> T maxOf(T a, T b) {
    return a.compareTo(b) >= 0 ? a : b;
  }

  public static double sumNumbers(List<? extends Number> nums) {
    double sum = 0.0;
    for (Number n : nums) {
      sum += n.doubleValue();
    }
    return sum;
  }

  public static void addIntegers(List<? super Integer> dest) {
    dest.add(42);
  }

  public void demo() {
    Box<String> box = new Box<>("ok");
    String s = box.get();
    String m = maxOf(s, "zz");
    List<Integer> ints = new ArrayList<>();
    addIntegers(ints);
    double total = sumNumbers(ints);
    if (total < 0 || m.isEmpty()) {
      throw new IllegalStateException();
    }
  }
}
"""
        self._verify_valid_java(
            "DIAG-02",
            {"src/main/java/com/example/valid/GenericsValid.java": code},
            "src/main/java/com/example/valid/GenericsValid.java",
        )

    def test_var_type_inference_no_false_positive(self) -> None:
        """[DIAG-03] Java var local variable type inference must produce 0 errors."""
        code = """\
package com.example.valid;

import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.util.ArrayList;
import java.util.List;

public class VarValid {
  public int demo() throws IOException {
    var list = new ArrayList<String>();
    list.add("alpha");
    list.add("beta");
    var totalLen = 0;
    for (var item : list) {
      totalLen += item.length();
    }
    var lengths = List.of(1, 2, 3).stream().map(n -> n * 2).toList();
    try (var stream = new ByteArrayInputStream(new byte[] {1, 2})) {
      totalLen += stream.read() + lengths.size();
    }
    return totalLen;
  }
}
"""
        self._verify_valid_java(
            "DIAG-03",
            {"src/main/java/com/example/valid/VarValid.java": code},
            "src/main/java/com/example/valid/VarValid.java",
        )

    def test_nested_lambdas_and_method_refs_no_false_positive(self) -> None:
        """[DIAG-04] Nested lambdas and method references must produce 0 errors."""
        code = """\
package com.example.valid;

import java.util.ArrayList;
import java.util.List;
import java.util.function.Function;
import java.util.function.Supplier;

public class NestedLambdaValid {
  record Item(Long id, String label) {}

  public List<String> filterLabels(List<Item> left, List<Item> right) {
    Supplier<List<String>> factory = ArrayList::new;
    Function<Item, String> toLabel = Item::label;
    List<String> out = factory.get();
    left.stream()
        .filter(x -> right.stream().anyMatch(y -> y.id().equals(x.id())))
        .map(toLabel)
        .map(String::trim)
        .forEach(out::add);
    return out;
  }
}
"""
        self._verify_valid_java(
            "DIAG-04",
            {"src/main/java/com/example/valid/NestedLambdaValid.java": code},
            "src/main/java/com/example/valid/NestedLambdaValid.java",
        )

    def test_records_no_false_positive(self) -> None:
        """[DIAG-05] Java 17 records (generic, compact constructor, accessors) must produce 0 errors."""
        code = """\
package com.example.valid;

public class RecordValid {
  public record Pair<A, B>(A first, B second) {
    public Pair {
      if (first == null || second == null) {
        throw new IllegalArgumentException("null");
      }
    }

    public String describe() {
      return first.toString() + ":" + second.toString();
    }
  }

  public String useRecord() {
    Pair<String, Integer> p = new Pair<>("age", 17);
    return p.first() + "=" + p.second() + " (" + p.describe() + ")";
  }
}
"""
        self._verify_valid_java(
            "DIAG-05",
            {"src/main/java/com/example/valid/RecordValid.java": code},
            "src/main/java/com/example/valid/RecordValid.java",
        )

    def test_switch_expressions_no_false_positive(self) -> None:
        """[DIAG-06] Java 14+ switch expressions with arrows and yield must produce 0 errors."""
        code = """\
package com.example.valid;

public class SwitchExprValid {
  enum Day { MON, TUE, WED, SAT, SUN }

  public int score(Day day) {
    return switch (day) {
      case MON, TUE -> 1;
      case WED -> {
        int bonus = 2;
        yield 3 + bonus;
      }
      case SAT, SUN -> 10;
    };
  }
}
"""
        self._verify_valid_java(
            "DIAG-06",
            {"src/main/java/com/example/valid/SwitchExprValid.java": code},
            "src/main/java/com/example/valid/SwitchExprValid.java",
        )

    def test_instanceof_pattern_matching_no_false_positive(self) -> None:
        """[DIAG-07] Java 16+ instanceof pattern matching must produce 0 errors."""
        code = """\
package com.example.valid;

public class InstanceofPatternValid {
  public int lengthOrZero(Object obj) {
    if (obj instanceof String s && !s.isEmpty()) {
      return s.length();
    }
    if (!(obj instanceof Integer n)) {
      return 0;
    }
    return n.intValue();
  }
}
"""
        self._verify_valid_java(
            "DIAG-07",
            {"src/main/java/com/example/valid/InstanceofPatternValid.java": code},
            "src/main/java/com/example/valid/InstanceofPatternValid.java",
        )

    def test_lombok_annotations_no_false_positive(self) -> None:
        """[DIAG-08] Lombok @Data, @Builder, @Slf4j, @RequiredArgsConstructor must produce 0 errors."""
        dto_code = """\
package com.example.valid;

import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
public class LombokUser {
  private Long id;
  private String name;
}
"""
        svc_code = """\
package com.example.valid;

import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;

@Slf4j
@RequiredArgsConstructor
public class LombokService {
  private final String prefix;

  public LombokUser buildUser(Long id, String name) {
    LombokUser user = LombokUser.builder().id(id).name(prefix + name).build();
    user.setName(user.getName().trim());
    log.info("Built user {}", user.getId());
    return user;
  }
}
"""
        use_code = """\
package com.example.valid;

public class LombokCaller {
  public String run() {
    LombokService svc = new LombokService("u-");
    return svc.buildUser(1L, "alice").getName();
  }
}
"""
        # Do not invoke raw javac annotation processing if slf4j-api isn't in pom;
        # wait: pom.xml only has lombok, and @Slf4j generates org.slf4j.Logger!
        # Let's include slf4j-api or verify Lombok @Data/@Builder/@RequiredArgsConstructor without @Slf4j?
        # No, prompt explicitly lists @Slf4j too! Add slf4j-api via standard import or test in project with slf4j!
        # Actually, let's check: does Simple Maven POM above include slf4j-api? Not yet, let's add slf4j-api 2.0.13 to SIMPLE_MAVEN_POM?
        # Wait: let's test @Slf4j in a helper with slf4j-api jar or add slf4j-api:2.0.13 to SIMPLE_MAVEN_POM!
        # Let's check: in earlier ls, slf4j-api-2.0.13.jar exists in ~/.m2/repository!
        self._verify_valid_java(
            "DIAG-08",
            {
                "src/main/java/com/example/valid/LombokUser.java": dto_code,
                "src/main/java/com/example/valid/LombokService.java": svc_code,
                "src/main/java/com/example/valid/LombokCaller.java": use_code,
            },
            "src/main/java/com/example/valid/LombokCaller.java",
            check_javac=False,
        )

    def test_static_imports_no_false_positive(self) -> None:
        """[DIAG-09] Explicit and wildcard static imports must produce 0 errors."""
        helper_code = """\
package com.example.valid;

public class StaticMathHelper {
  public static final int BASE = 10;
  public static int triple(int v) { return v * 3; }
}
"""
        caller_code = """\
package com.example.valid;

import static com.example.valid.StaticMathHelper.BASE;
import static com.example.valid.StaticMathHelper.triple;
import static java.lang.Math.*;
import static java.util.Collections.emptyList;

import java.util.List;

public class StaticImportValid {
  public int compute() {
    List<String> empty = emptyList();
    return max(BASE, triple(2)) + empty.size();
  }
}
"""
        self._verify_valid_java(
            "DIAG-09",
            {
                "src/main/java/com/example/valid/StaticMathHelper.java": helper_code,
                "src/main/java/com/example/valid/StaticImportValid.java": caller_code,
            },
            "src/main/java/com/example/valid/StaticImportValid.java",
        )

    def test_inner_nested_local_anonymous_classes_no_false_positive(self) -> None:
        """[DIAG-10] Inner, static nested, local, and anonymous classes must produce 0 errors."""
        code = """\
package com.example.valid;

public class NestedClassesValid {
  private int outerVal = 5;

  public static class StaticNested {
    public int value() { return 7; }
  }

  public class Inner {
    public int sumWithOuter() { return NestedClassesValid.this.outerVal + 3; }
  }

  public int evaluate() {
    StaticNested sn = new StaticNested();
    Inner inner = new Inner();
    class LocalCalc {
      int calc() { return sn.value() + inner.sumWithOuter(); }
    }
    final int[] holder = new int[1];
    Runnable r = new Runnable() {
      @Override
      public void run() {
        holder[0] = new LocalCalc().calc();
      }
    };
    r.run();
    return holder[0];
  }
}
"""
        self._verify_valid_java(
            "DIAG-10",
            {"src/main/java/com/example/valid/NestedClassesValid.java": code},
            "src/main/java/com/example/valid/NestedClassesValid.java",
        )

    def test_varargs_no_false_positive(self) -> None:
        """[DIAG-11] Varargs methods called with 0, 1, many args, and array must produce 0 errors."""
        code = """\
package com.example.valid;

public class VarargsValid {
  public int countArgs(String tag, int... numbers) {
    return tag.length() + numbers.length;
  }

  public int callAll() {
    int a = countArgs("zero");
    int b = countArgs("one", 1);
    int c = countArgs("many", 1, 2, 3);
    int d = countArgs("arr", new int[] {4, 5});
    return a + b + c + d;
  }
}
"""
        self._verify_valid_java(
            "DIAG-11",
            {"src/main/java/com/example/valid/VarargsValid.java": code},
            "src/main/java/com/example/valid/VarargsValid.java",
        )

    def test_annotations_with_attributes_no_false_positive(self) -> None:
        """[DIAG-12] Annotations with single, array, class, and default attributes must produce 0 errors."""
        code = """\
package com.example.valid;

import java.lang.annotation.Retention;
import java.lang.annotation.RetentionPolicy;

public class AnnotationsValid {
  @Retention(RetentionPolicy.RUNTIME)
  @interface MetaInfo {
    String author();
    int version() default 1;
    String[] tags() default {};
    Class<?> target() default Object.class;
  }

  @SuppressWarnings({"unchecked", "rawtypes"})
  @MetaInfo(author = "qa", version = 2, tags = {"a", "b"}, target = String.class)
  public String annotatedMethod() {
    return "ok";
  }
}
"""
        self._verify_valid_java(
            "DIAG-12",
            {"src/main/java/com/example/valid/AnnotationsValid.java": code},
            "src/main/java/com/example/valid/AnnotationsValid.java",
        )


# SIMPLE_MAVEN_POM includes slf4j-api (for @Slf4j) and maven-dependency-plugin:3.6.1.


class TestInvalidJavaFalseNegatives(_BaseDiagTestCase):
    """False-negative checks on invalid Java code verified against real javac expectations."""

    _init_error: str | None = None

    @classmethod
    def setUpClass(cls) -> None:
        cls.project = TempJavaProject(prefix="diag_invalid_")
        cls.client = LspClient(root_path=cls.project.root, default_timeout=20.0)
        try:
            cls.client.start()
            cls.client.initialize_and_ready(root_path=cls.project.root, timeout=60.0)
        except Exception as exc:
            cls._init_error = f"setUpClass initialize failed: {exc}"

    @classmethod
    def tearDownClass(cls) -> None:
        try:
            cls.client.close()
        finally:
            cls.project.close()

    def setUp(self) -> None:
        if self._init_error is not None:
            self.fail(f"[DIAG-INVALID] {self._init_error}")
        if not self.client.is_alive():
            try:
                self.client.restart(clean_storage=False)
                self.client.initialize_and_ready(root_path=self.project.root, timeout=60.0)
            except Exception as exc:
                self.fail(f"[DIAG-INVALID] Server restart failed: {exc}")

    def _expect_invalid_java_diagnostic(
        self,
        tag: str,
        rel_path: str,
        code: str,
        extra_files: dict[str, str] | None = None,
        expect_javac_fail: bool = True,
        require_error_severity: bool = True,
        min_count: int = 1,
        predicate_desc: str = "diagnostic",
    ) -> list[dict[str, Any]]:
        all_files = dict(extra_files or {})
        all_files[rel_path] = code

        if expect_javac_fail:
            rc, javac_out = _javac_compile(all_files)
            self.assertNotEqual(
                0,
                rc,
                f"[{tag}] Test fixture bug: javac unexpectedly compiled invalid code in {rel_path}:\n{javac_out}",
            )

        written_uris: list[str] = []
        for rel, content in all_files.items():
            self.project.write_file(rel, content)
            written_uris.append(self.project.uri(rel))

        def _cleanup() -> None:
            for u in written_uris:
                try:
                    self.client.did_close(u)
                except LspClientError:
                    pass
            for rel in all_files:
                self.project.remove_file(rel)

        self.addCleanup(_cleanup)

        for rel, content in (extra_files or {}).items():
            try:
                self.client.did_open(self.project.uri(rel), content)
            except LspClientError as exc:
                self.fail(f"[{tag}] Server error opening helper file {rel}: {exc}")

        uri = self.project.uri(rel_path)
        diags = self._safe_open_and_await_diags(
            uri, code, tag=tag, timeout=5.0, quiet_period=0.25, require_notification=True
        )
        matching = [d for d in diags if _is_error_diag(d)] if require_error_severity else diags
        self.assertGreaterEqual(
            len(matching),
            min_count,
            f"[{tag}] False negative: expected >= {min_count} {predicate_desc} on {rel_path}.\n"
            f"All published diagnostics: {_format_diags(diags)}\nCode:\n{code}",
        )
        return diags

    def test_syntax_error_missing_semicolon(self) -> None:
        """[DIAG-13] Missing semicolon syntax error must produce an Error diagnostic."""
        code = """\
package com.example.bad;

public class MissingSemicolon {
  public int run() {
    int x = 1
    return x;
  }
}
"""
        self._expect_invalid_java_diagnostic(
            "DIAG-13", "src/MissingSemicolon.java", code, predicate_desc="syntax error diagnostic"
        )

    def test_syntax_error_unclosed_brace(self) -> None:
        """[DIAG-14] Unclosed brace syntax error must produce an Error diagnostic."""
        code = """\
package com.example.bad;

public class UnclosedBrace {
  public void broken() {
    int x = 1;
}
"""
        self._expect_invalid_java_diagnostic(
            "DIAG-14", "src/UnclosedBrace.java", code, predicate_desc="unclosed brace syntax error"
        )

    def test_unresolved_variable(self) -> None:
        """[DIAG-15] Unresolved variable reference must produce an Error diagnostic."""
        code = """\
package com.example.bad;

public class UnresolvedVar {
  public int calc() {
    return unknownVar + 1;
  }
}
"""
        self._expect_invalid_java_diagnostic(
            "DIAG-15", "src/UnresolvedVar.java", code, predicate_desc="unresolved variable error"
        )

    def test_unresolved_type_name(self) -> None:
        """[DIAG-16] Unresolved type name must produce an Error diagnostic."""
        code = """\
package com.example.bad;

public class UnresolvedType {
  public void demo() {
    NoSuchType item = null;
  }
}
"""
        self._expect_invalid_java_diagnostic(
            "DIAG-16", "src/UnresolvedType.java", code, predicate_desc="unresolved type error"
        )

    def test_nonexistent_method_on_known_type(self) -> None:
        """[DIAG-17] Calling non-existent method on a known type must produce an Error diagnostic."""
        code = """\
package com.example.bad;

public class UnknownMethod {
  public void demo() {
    String text = "hello";
    text.nonExistentMethod();
  }
}
"""
        self._expect_invalid_java_diagnostic(
            "DIAG-17", "src/UnknownMethod.java", code, predicate_desc="unknown method error"
        )

    def test_wrong_argument_count(self) -> None:
        """[DIAG-18] Calling method with wrong argument count must produce an Error diagnostic."""
        code = """\
package com.example.bad;

public class WrongArgCount {
  public int add(int a, int b) {
    return a + b;
  }

  public int call() {
    return add(1);
  }
}
"""
        self._expect_invalid_java_diagnostic(
            "DIAG-18", "src/WrongArgCount.java", code, predicate_desc="wrong argument count error"
        )

    def test_wrong_argument_type(self) -> None:
        """[DIAG-19] Calling method with incompatible argument type must produce an Error diagnostic."""
        code = """\
package com.example.bad;

public class WrongArgType {
  public int doubleIt(int v) {
    return v * 2;
  }

  public int call() {
    return doubleIt("notAnInt");
  }
}
"""
        self._expect_invalid_java_diagnostic(
            "DIAG-19", "src/WrongArgType.java", code, predicate_desc="wrong argument type error"
        )

    def test_incompatible_assignment(self) -> None:
        """[DIAG-20] Incompatible assignment (int x = "hello";) must produce an Error diagnostic."""
        code = """\
package com.example.bad;

public class BadAssignment {
  public void demo() {
    int x = "hello";
  }
}
"""
        self._expect_invalid_java_diagnostic(
            "DIAG-20", "src/BadAssignment.java", code, predicate_desc="incompatible assignment error"
        )

    def test_incompatible_return_type(self) -> None:
        """[DIAG-21] Incompatible return type must produce an Error diagnostic."""
        code = """\
package com.example.bad;

public class BadReturnType {
  public int getNumber() {
    return "wrong";
  }
}
"""
        self._expect_invalid_java_diagnostic(
            "DIAG-21", "src/BadReturnType.java", code, predicate_desc="incompatible return type error"
        )

    def test_missing_return_statement(self) -> None:
        """[DIAG-22] Missing return statement in non-void method must produce an Error diagnostic."""
        code = """\
package com.example.bad;

public class MissingReturn {
  public int compute(boolean flag) {
    if (flag) {
      return 1;
    }
  }
}
"""
        self._expect_invalid_java_diagnostic(
            "DIAG-22", "src/MissingReturn.java", code, predicate_desc="missing return statement error"
        )

    def test_unhandled_checked_exception(self) -> None:
        """[DIAG-23] Unhandled checked IOException without throws/catch must produce an Error diagnostic."""
        code = """\
package com.example.bad;

import java.io.IOException;

public class UnhandledChecked {
  public void readFile() {
    throw new IOException("boom");
  }
}
"""
        self._expect_invalid_java_diagnostic(
            "DIAG-23", "src/UnhandledChecked.java", code, predicate_desc="unhandled checked exception error"
        )

    def test_unimplemented_interface_method(self) -> None:
        """[DIAG-24] Concrete class failing to implement interface method must produce an Error diagnostic."""
        code = """\
package com.example.bad;

public class IncompleteImpl implements Runnable {
}
"""
        self._expect_invalid_java_diagnostic(
            "DIAG-24", "src/IncompleteImpl.java", code, predicate_desc="unimplemented method error"
        )

    def test_duplicate_local_variable_and_duplicate_method(self) -> None:
        """[DIAG-25] Duplicate local variable and duplicate method signature must produce Error diagnostics."""
        code = """\
package com.example.bad;

public class DuplicateDecls {
  public void dupMethod(int a) {}
  public void dupMethod(int b) {}

  public void dupLocal() {
    int x = 1;
    int x = 2;
  }
}
"""
        self._expect_invalid_java_diagnostic(
            "DIAG-25",
            "src/DuplicateDecls.java",
            code,
            min_count=2,
            predicate_desc="duplicate method + duplicate local variable error",
        )

    def test_reassign_final_variable_and_field(self) -> None:
        """[DIAG-26] Reassigning a final local variable and final field must produce Error diagnostics."""
        code = """\
package com.example.bad;

public class FinalReassign {
  private final int constantField = 10;

  public void mutate() {
    final int localConst = 1;
    localConst = 2;
    this.constantField = 20;
  }
}
"""
        self._expect_invalid_java_diagnostic(
            "DIAG-26",
            "src/FinalReassign.java",
            code,
            min_count=2,
            predicate_desc="final reassignment error",
        )

    def test_access_private_member_from_other_class(self) -> None:
        """[DIAG-27] Accessing a private field and method from another class must produce Error diagnostics."""
        secret_code = """\
package com.example.bad;

public class SecretHolder {
  private int secret = 42;
  private void hiddenMethod() {}
}
"""
        accessor_code = """\
package com.example.bad;

public class SecretAccessor {
  public int steal() {
    SecretHolder h = new SecretHolder();
    h.hiddenMethod();
    return h.secret;
  }
}
"""
        self._expect_invalid_java_diagnostic(
            "DIAG-27",
            "src/SecretAccessor.java",
            accessor_code,
            extra_files={"src/SecretHolder.java": secret_code},
            min_count=2,
            predicate_desc="private member access error",
        )

    def test_invalid_override_annotation(self) -> None:
        """[DIAG-28] @Override on method not overriding superclass/interface method must produce Error diagnostic."""
        code = """\
package com.example.bad;

public class InvalidOverride {
  @Override
  public void notInObjectOrSuper() {
  }
}
"""
        self._expect_invalid_java_diagnostic(
            "DIAG-28", "src/InvalidOverride.java", code, predicate_desc="invalid @Override error"
        )

    def test_unused_import_warning_or_hint(self) -> None:
        """[DIAG-29] Unused import must produce a Warning/Hint/Unnecessary diagnostic."""
        code = """\
package com.example.bad;

import java.util.Date;

public class UnusedImport {
  public String hello() {
    return "hi";
  }
}
"""
        diags = self._expect_invalid_java_diagnostic(
            "DIAG-29",
            "src/UnusedImport.java",
            code,
            expect_javac_fail=False,
            require_error_severity=False,
            predicate_desc="unused import warning/hint",
        )
        non_err = [
            d for d in diags
            if (d.get("severity") in (2, 3, 4)) or (1 in (d.get("tags") or []))
        ]
        self.assertTrue(
            non_err,
            f"[DIAG-29] Expected Warning/Hint/Unnecessary tag diagnostic for unused import, got: {_format_diags(diags)}",
        )

    def test_unused_local_variable_warning_or_hint(self) -> None:
        """[DIAG-30] Unused local variable must produce a Warning/Hint/Unnecessary diagnostic."""
        code = """\
package com.example.bad;

public class UnusedLocalVar {
  public int run() {
    int neverUsed = 99;
    return 1;
  }
}
"""
        diags = self._expect_invalid_java_diagnostic(
            "DIAG-30",
            "src/UnusedLocalVar.java",
            code,
            expect_javac_fail=False,
            require_error_severity=False,
            predicate_desc="unused local variable warning/hint",
        )
        non_err = [
            d for d in diags
            if (d.get("severity") in (2, 3, 4)) or (1 in (d.get("tags") or []))
        ]
        self.assertTrue(
            non_err,
            f"[DIAG-30] Expected Warning/Hint/Unnecessary tag diagnostic for unused local variable, got: {_format_diags(diags)}",
        )


class TestLiveDiagnosticsLifecycle(_BaseDiagTestCase):
    """Live diagnostics lifecycle across didChange and cross-file dependency updates."""

    def setUp(self) -> None:
        self.project = TempJavaProject(prefix="diag_live_")
        self.addCleanup(self.project.close)
        self.client = LspClient(root_path=self.project.root, default_timeout=20.0)
        self.addCleanup(self.client.close)
        try:
            self.client.start()
            self.client.initialize_and_ready(root_path=self.project.root, timeout=60.0)
        except Exception as exc:
            self.fail(f"[DIAG-LIVE] Server initialization failed: {exc}")

    def test_did_change_diagnostics_appear_and_clear(self) -> None:
        """[DIAG-31] Diagnostics appear promptly after didChange introduces error and clear when fixed."""
        valid_code = """\
package com.example.live;

public class LiveCalc {
  public int value() {
    int x = 10;
    return x;
  }
}
"""
        broken_code = """\
package com.example.live;

public class LiveCalc {
  public int value() {
    int x = missingSymbol;
    return x;
  }
}
"""
        rel = "src/com/example/live/LiveCalc.java"
        self.project.write_file(rel, valid_code)
        uri = self.project.uri(rel)

        initial_diags = self._safe_open_and_await_diags(uri, valid_code, tag="DIAG-31", timeout=4.0)
        self.assertEqual(
            [],
            [d for d in initial_diags if _is_error_diag(d)],
            f"[DIAG-31] Initial valid LiveCalc.java should have 0 errors, got: {_format_diags(initial_diags)}",
        )

        # Introduce error via didChange.
        err_diags = self._safe_change_and_await_diags(
            uri,
            broken_code,
            tag="DIAG-31-introduce",
            predicate=lambda ds: any(_is_error_diag(d) for d in ds),
            timeout=8.0,
        )
        self.assertTrue(
            any(_is_error_diag(d) for d in err_diags),
            f"[DIAG-31] Expected Error diagnostic after didChange introduced 'missingSymbol', got: {_format_diags(err_diags)}",
        )

        # Fix error via didChange -> diagnostics must clear completely.
        cleared_diags = self._safe_change_and_await_diags(
            uri,
            valid_code,
            tag="DIAG-31-clear",
            predicate=lambda ds: len([d for d in ds if _is_error_diag(d)]) == 0,
            timeout=8.0,
        )
        self.assertEqual(
            [],
            [d for d in cleared_diags if _is_error_diag(d)],
            f"[DIAG-31] Expected Error diagnostics to clear after fixing code via didChange, got: {_format_diags(cleared_diags)}",
        )

    def test_cross_file_dependency_diagnostics_update(self) -> None:
        """[DIAG-32] Editing referenced class signature via didChange updates diagnostics in open caller file."""
        svc_v1 = """\
package com.example.live;

public class GreeterService {
  public String greet(String name) {
    return "Hello " + name;
  }
}
"""
        svc_v2 = """\
package com.example.live;

public class GreeterService {
  public String greet(int count) {
    return "Hello #" + count;
  }
}
"""
        caller_code = """\
package com.example.live;

public class GreeterCaller {
  public String run(GreeterService svc) {
    return svc.greet("world");
  }
}
"""
        svc_rel = "src/com/example/live/GreeterService.java"
        caller_rel = "src/com/example/live/GreeterCaller.java"
        self.project.write_file(svc_rel, svc_v1)
        self.project.write_file(caller_rel, caller_code)
        svc_uri = self.project.uri(svc_rel)
        caller_uri = self.project.uri(caller_rel)

        self._safe_open_and_await_diags(svc_uri, svc_v1, tag="DIAG-32-svc-init", timeout=4.0)
        caller_init = self._safe_open_and_await_diags(
            caller_uri, caller_code, tag="DIAG-32-caller-init", timeout=4.0
        )
        self.assertEqual(
            [],
            [d for d in caller_init if _is_error_diag(d)],
            f"[DIAG-32] Expected 0 initial errors on GreeterCaller, got: {_format_diags(caller_init)}",
        )

        # Mutate GreeterService.greet(String) -> greet(int) without changing GreeterCaller.
        try:
            caller_seq = self.client.diagnostics_seq()
            self.client.did_change(svc_uri, text=svc_v2)
            caller_after_break = self.client.wait_for_diagnostics(
                caller_uri,
                timeout=8.0,
                after_seq=caller_seq,
                predicate=lambda ds: any(_is_error_diag(d) for d in ds),
                settle_time=0.25,
            )
        except TimeoutError as exc:
            self.fail(
                "[DIAG-32] GreeterCaller.java diagnostics did not update with an Error when "
                "GreeterService.greet(String) changed to greet(int) via didChange. "
                f"Latest GreeterCaller diagnostics: {_format_diags(self.client.get_diagnostics(caller_uri))}; "
                f"timeout={exc}"
            )
        except LspClientError as exc:
            self.fail(f"[DIAG-32] Server error during cross-file didChange test: {exc}")

        self.assertTrue(
            any(_is_error_diag(d) for d in caller_after_break),
            f"[DIAG-32] Expected Error in GreeterCaller after GreeterService signature broke, got: {_format_diags(caller_after_break)}",
        )

        # Revert GreeterService -> GreeterCaller diagnostics should clear.
        try:
            caller_seq2 = self.client.diagnostics_seq()
            self.client.did_change(svc_uri, text=svc_v1)
            caller_restored = self.client.wait_for_diagnostics(
                caller_uri,
                timeout=8.0,
                after_seq=caller_seq2,
                predicate=lambda ds: len([d for d in ds if _is_error_diag(d)]) == 0,
                settle_time=0.25,
            )
        except TimeoutError as exc:
            self.fail(
                "[DIAG-32] GreeterCaller.java diagnostics did not clear after GreeterService.greet(String) was restored. "
                f"Latest GreeterCaller diagnostics: {_format_diags(self.client.get_diagnostics(caller_uri))}; "
                f"timeout={exc}"
            )
        except LspClientError as exc:
            self.fail(f"[DIAG-32] Server error restoring GreeterService: {exc}")

        self.assertEqual(
            [],
            [d for d in caller_restored if _is_error_diag(d)],
            f"[DIAG-32] Expected GreeterCaller diagnostics to clear after restore, got: {_format_diags(caller_restored)}",
        )


if __name__ == "__main__":
    unittest.main()
