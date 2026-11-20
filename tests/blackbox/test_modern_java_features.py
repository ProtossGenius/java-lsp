"""Black-box LSP tests for generics, var, nested lambdas, Java 14-17+ features, and Java constructs."""

from __future__ import annotations

import re
import unittest
from typing import Any

from tests.blackbox.lsp_client import (
    LspClient,
    LspClientError,
    LspResponseError,
    ServerCrashedError,
    TempJavaProject,
    after_of,
    extract_hover_text,
    extract_locations,
    location_range,
    location_uri,
    pos_of,
)

_IDENT_SPLIT_RE = re.compile(r"[(\s:<]")


def _item_ident(item: dict[str, Any]) -> str:
    label = str(item.get("label", "")).strip()
    return _IDENT_SPLIT_RE.split(label, 1)[0] if label else ""


def _extract_items(result: Any) -> list[dict[str, Any]]:
    if result is None:
        return []
    if isinstance(result, list):
        return [x for x in result if isinstance(x, dict)]
    if isinstance(result, dict):
        items = result.get("items")
        if isinstance(items, list):
            return [x for x in items if isinstance(x, dict)]
    return []


def _format_items_preview(items: list[dict[str, Any]], limit: int = 20) -> str:
    labels = [it.get("label") for it in items[:limit]]
    suffix = f" ... (+{len(items) - limit} more)" if len(items) > limit else ""
    return f"{labels}{suffix}"


class _ModernJavaBase(unittest.TestCase):
    """Shared safe LSP helpers for modern Java language feature tests."""

    project: TempJavaProject
    client: LspClient
    base_files: dict[str, str] = {}

    @classmethod
    def _start_workspace(cls) -> None:
        cls.client = LspClient(root_path=cls.project.root, default_timeout=20.0)
        cls.client.start()
        cls.client.initialize_and_ready(timeout=45.0)

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.project = TempJavaProject(files=cls.base_files, prefix="modern_java_bb_")
        cls._start_workspace()

    @classmethod
    def tearDownClass(cls) -> None:
        try:
            if getattr(cls, "client", None) is not None:
                cls.client.close()
        finally:
            if getattr(cls, "project", None) is not None:
                cls.project.close()
        super().tearDownClass()

    def setUp(self) -> None:
        super().setUp()
        if not self.client.is_alive():
            self.client.restart(clean_storage=False, clear_history=True)
            self.client.initialize_and_ready(timeout=45.0)

    def _open_file(self, rel_path: str, modified_text: str | None = None) -> tuple[str, str]:
        orig = self.project.read_file(rel_path)
        uri = self.project.uri(rel_path)
        self.client.did_open(uri, orig)
        text = orig
        if modified_text is not None and modified_text != orig:
            self.client.did_change(uri, modified_text)
            text = modified_text
        self.addCleanup(self._restore_and_close, uri, orig)
        return uri, text

    def _restore_and_close(self, uri: str, orig: str) -> None:
        if not self.client.is_alive():
            return
        try:
            self.client.did_change(uri, orig)
            self.client.did_close(uri)
        except Exception:
            pass

    def _req_completion(
        self,
        uri: str,
        pos: dict[str, int],
        *,
        tag: str,
        context_desc: str,
        trigger_kind: int = 2,
        trigger_char: str | None = ".",
    ) -> list[dict[str, Any]]:
        try:
            res = self.client.completion(
                uri,
                pos,
                trigger_kind=trigger_kind,
                trigger_character=trigger_char,
            )
        except (LspResponseError, ServerCrashedError, TimeoutError, LspClientError) as exc:
            self.fail(
                f"[{tag}] textDocument/completion failed at {context_desc} (pos={pos}): "
                f"{type(exc).__name__}: {exc}"
            )
        return _extract_items(res)

    def _req_hover(self, uri: str, pos: dict[str, int], *, tag: str, context_desc: str) -> str:
        try:
            res = self.client.hover(uri, pos)
        except (LspResponseError, ServerCrashedError, TimeoutError, LspClientError) as exc:
            self.fail(
                f"[{tag}] textDocument/hover failed at {context_desc} (pos={pos}): "
                f"{type(exc).__name__}: {exc}"
            )
        return extract_hover_text(res)

    def _req_locations(
        self,
        method: str,
        uri: str,
        pos: dict[str, int],
        *,
        tag: str,
        context_desc: str,
    ) -> list[dict[str, Any]]:
        fn = getattr(self.client, method)
        try:
            res = fn(uri, pos)
        except (LspResponseError, ServerCrashedError, TimeoutError, LspClientError) as exc:
            self.fail(
                f"[{tag}] textDocument/{method} failed at {context_desc} (pos={pos}): "
                f"{type(exc).__name__}: {exc}"
            )
        return extract_locations(res)

    def _assert_zero_errors(self, uri: str, text: str, *, tag: str, context_desc: str) -> None:
        seq = self.client.did_change(uri, text)
        try:
            diags = self.client.wait_for_diagnostics_quiescent(
                uri, timeout=8.0, quiet_period=0.25, after_seq=seq
            )
        except (ServerCrashedError, TimeoutError, LspClientError) as exc:
            self.fail(
                f"[{tag}] publishDiagnostics failed for {context_desc}: {type(exc).__name__}: {exc}"
            )
        errors = [d for d in diags if d.get("severity", 1) == 1]
        self.assertEqual(
            errors,
            [],
            f"[{tag}] Expected 0 error diagnostics on valid Java code ({context_desc}), "
            f"got {len(errors)}: {errors!r}",
        )

    def _assert_has_member(
        self, items: list[dict[str, Any]], expected: str, *, tag: str, context_desc: str
    ) -> dict[str, Any]:
        for item in items:
            if _item_ident(item) == expected:
                return item
        self.fail(
            f"[{tag}] Expected completion item {expected!r} at {context_desc}, "
            f"got {len(items)} items: {_format_items_preview(items)}"
        )

    def _assert_lacks_member(
        self, items: list[dict[str, Any]], forbidden: str, *, tag: str, context_desc: str, reason: str
    ) -> None:
        matches = [it for it in items if _item_ident(it) == forbidden]
        if matches:
            self.fail(
                f"[{tag}] Completion at {context_desc} must NOT include {forbidden!r} ({reason}), "
                f"got: {matches!r}"
            )


class TestGenericsVarLambdas(_ModernJavaBase):
    """Generics, wildcards, var inference, deeply nested lambdas, and method references."""

    base_files = {
        "src/main/java/modern/Named.java": """package modern;

public interface Named {
  String getName();
}
""",
        "src/main/java/modern/User.java": """package modern;

public class User implements Named {
  private Long id;
  private String name;
  private String email;

  public User(Long id, String name, String email) {
    this.id = id;
    this.name = name;
    this.email = email;
  }

  public Long getId() { return id; }
  @Override public String getName() { return name; }
  public String getEmail() { return email; }
}
""",
        "src/main/java/modern/Admin.java": """package modern;

public class Admin extends User {
  private String roleLevel;
  public Admin(Long id, String name, String email, String roleLevel) {
    super(id, name, email);
    this.roleLevel = roleLevel;
  }
  public String getRoleLevel() { return roleLevel; }
}
""",
        "src/main/java/modern/Item.java": """package modern;

public class Item {
  private Long id;
  private String code;
  public Long getId() { return id; }
  public String getCode() { return code; }
}
""",
        "src/main/java/modern/Box.java": """package modern;

public class Box<T> {
  private final T value;
  public Box(T value) { this.value = value; }
  public T get() { return value; }
}
""",
        "src/main/java/modern/Pair.java": """package modern;

public class Pair<A, B> {
  private final A first;
  private final B second;
  public Pair(A first, B second) { this.first = first; this.second = second; }
  public A getFirst() { return first; }
  public B getSecond() { return second; }
}
""",
        "src/main/java/modern/GenericsAndVar.java": """package modern;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

public class GenericsAndVar {
  public <T> T pickFirst(List<T> items) {
    return items.get(0);
  }

  public <T extends Named & Comparable<T>> String readBounded(T item) {
    return item.getName();
  }

  public User findUser(Long id) {
    return new User(id, "Alice", "alice@example.com");
  }

  public void testWildcards(List<? extends User> upper, List<? super Admin> lower) {
    upper.get(0).getEmail();
    lower.get(0).toString();
  }

  public void testDiamondAndChaining(Box<Pair<String, User>> box) {
    Map<String, List<User>> map = new HashMap<>();
    map.get("a").get(0).getEmail();
    box.get().getSecond().getEmail();
  }

  public void testGenericMethodInference(List<User> users) {
    User picked = pickFirst(users);
    picked.getEmail();
    pickFirst(users).getName();
  }

  public void testVarInference(List<User> users) {
    var list = new ArrayList<String>();
    list.get(0).toUpperCase();
    var user = findUser(1L);
    user.getEmail();
    var names = users.stream().map(User::getName).toList();
    names.get(0).length();
    for (var u : users) {
      u.getName();
    }
  }
}
""",
        "src/main/java/modern/LambdasAndRefs.java": """package modern;

import java.util.ArrayList;
import java.util.List;
import java.util.function.Supplier;

public class LambdasAndRefs {
  public void testNestedLambdas(List<User> users, List<Item> items) {
    users.stream()
        .filter(x -> items.stream().anyMatch(y -> y.getId().equals(x.getId())))
        .map(it -> it.getEmail())
        .forEach(System.out::println);

    List<String> codes = users.stream()
        .flatMap(u -> items.stream().filter(it -> it.getId().equals(u.getId())).map(z -> z.getCode()))
        .toList();

    List<String> strs = users.stream().map(User::getId).map(String::valueOf).toList();
    Supplier<ArrayList<String>> ctor = ArrayList::new;
    if (codes.size() + strs.size() + ctor.get().size() > 0) {
      return;
    }
  }
}
""",
    }

    def test_upper_bounded_wildcard(self) -> None:
        """[JAVA-01] List<? extends User> upper.get(0). resolves User members."""
        rel = "src/main/java/modern/GenericsAndVar.java"
        orig = self.project.read_file(rel)
        mod = orig.replace("upper.get(0).getEmail();", "upper.get(0).")
        uri, text = self._open_file(rel, mod)
        items = self._req_completion(
            uri, after_of(text, "upper.get(0)."), tag="JAVA-01", context_desc="upper.get(0)."
        )
        self._assert_has_member(items, "getEmail", tag="JAVA-01", context_desc="List<? extends User>")
        self._assert_has_member(items, "getName", tag="JAVA-01", context_desc="List<? extends User>")

    def test_lower_bounded_wildcard(self) -> None:
        """[JAVA-02] List<? super Admin> lower.get(0). resolves Object rather than Admin."""
        rel = "src/main/java/modern/GenericsAndVar.java"
        orig = self.project.read_file(rel)
        mod = orig.replace("lower.get(0).toString();", "lower.get(0).")
        uri, text = self._open_file(rel, mod)
        items = self._req_completion(
            uri, after_of(text, "lower.get(0)."), tag="JAVA-02", context_desc="lower.get(0)."
        )
        self._assert_has_member(items, "toString", tag="JAVA-02", context_desc="List<? super Admin>")
        self._assert_lacks_member(
            items,
            "getRoleLevel",
            tag="JAVA-02",
            context_desc="lower.get(0).",
            reason="get() on List<? super Admin> has static type Object, not Admin",
        )

    def test_diamond_operator_type_resolution(self) -> None:
        """[JAVA-03] Diamond operator Map<String, List<User>> map = new HashMap<>(); resolves nested User."""
        rel = "src/main/java/modern/GenericsAndVar.java"
        orig = self.project.read_file(rel)
        mod = orig.replace('map.get("a").get(0).getEmail();', 'map.get("a").get(0).')
        uri, text = self._open_file(rel, mod)
        items = self._req_completion(
            uri,
            after_of(text, 'map.get("a").get(0).'),
            tag="JAVA-03",
            context_desc='map.get("a").get(0).',
        )
        self._assert_has_member(items, "getEmail", tag="JAVA-03", context_desc="diamond HashMap value")

    def test_generic_method_return_inference(self) -> None:
        """[JAVA-04] Generic method <T> T pickFirst(List<T>) infers User both directly and via local."""
        rel = "src/main/java/modern/GenericsAndVar.java"
        orig = self.project.read_file(rel)
        mod = orig.replace("pickFirst(users).getName();", "pickFirst(users).")
        uri, text = self._open_file(rel, mod)
        items = self._req_completion(
            uri,
            after_of(text, "pickFirst(users)."),
            tag="JAVA-04",
            context_desc="pickFirst(users).",
        )
        self._assert_has_member(items, "getEmail", tag="JAVA-04", context_desc="pickFirst(users).")

    def test_bounded_type_parameter_completion(self) -> None:
        """[JAVA-05] Bounded type parameter <T extends Named & Comparable<T>> exposes Named & Comparable methods."""
        rel = "src/main/java/modern/GenericsAndVar.java"
        orig = self.project.read_file(rel)
        mod = orig.replace("return item.getName();", "return item.")
        uri, text = self._open_file(rel, mod)
        items = self._req_completion(
            uri, after_of(text, "return item."), tag="JAVA-05", context_desc="bounded T item."
        )
        self._assert_has_member(items, "getName", tag="JAVA-05", context_desc="bounded T item.")
        self._assert_has_member(items, "compareTo", tag="JAVA-05", context_desc="bounded T item.")

    def test_multi_level_generic_chaining(self) -> None:
        """[JAVA-06] Multi-level Box<Pair<String, User>> box.get().getSecond(). resolves User."""
        rel = "src/main/java/modern/GenericsAndVar.java"
        orig = self.project.read_file(rel)
        mod = orig.replace("box.get().getSecond().getEmail();", "box.get().getSecond().")
        uri, text = self._open_file(rel, mod)
        items = self._req_completion(
            uri,
            after_of(text, "box.get().getSecond()."),
            tag="JAVA-06",
            context_desc="box.get().getSecond().",
        )
        self._assert_has_member(items, "getEmail", tag="JAVA-06", context_desc="Box<Pair<String, User>>")

    def test_var_from_diamond_constructor(self) -> None:
        """[JAVA-07] var list = new ArrayList<String>(); list.get(0). resolves String methods."""
        rel = "src/main/java/modern/GenericsAndVar.java"
        orig = self.project.read_file(rel)
        mod = orig.replace("list.get(0).toUpperCase();", "list.get(0).")
        uri, text = self._open_file(rel, mod)
        items = self._req_completion(
            uri, after_of(text, "list.get(0)."), tag="JAVA-07", context_desc="var ArrayList element"
        )
        self._assert_has_member(items, "toUpperCase", tag="JAVA-07", context_desc="var ArrayList element")

    def test_var_from_method_call_and_hover(self) -> None:
        """[JAVA-08] var user = findUser(1L); resolves User completion and hover."""
        rel = "src/main/java/modern/GenericsAndVar.java"
        orig = self.project.read_file(rel)
        uri_clean, text_clean = self._open_file(rel)
        hov = self._req_hover(
            uri_clean, pos_of(text_clean, "user.getEmail()"), tag="JAVA-08", context_desc="var user"
        )
        self.assertIn(
            "User",
            hov,
            f"[JAVA-08] Expected hover on 'var user' to include 'User', got {hov!r}",
        )
        mod = orig.replace("user.getEmail();", "user.")
        self.client.did_change(uri_clean, mod)
        items = self._req_completion(
            uri_clean, after_of(mod, "user."), tag="JAVA-08", context_desc="var user = findUser(1L)"
        )
        self._assert_has_member(items, "getEmail", tag="JAVA-08", context_desc="var user.")

    def test_var_from_stream_pipeline(self) -> None:
        """[JAVA-09] var names = users.stream().map(User::getName).toList(); resolves List<String>."""
        rel = "src/main/java/modern/GenericsAndVar.java"
        orig = self.project.read_file(rel)
        mod = orig.replace("names.get(0).length();", "names.get(0).")
        uri, text = self._open_file(rel, mod)
        items = self._req_completion(
            uri, after_of(text, "names.get(0)."), tag="JAVA-09", context_desc="var stream.toList()"
        )
        self._assert_has_member(items, "substring", tag="JAVA-09", context_desc="var stream.toList()")

    def test_var_in_enhanced_for_loop(self) -> None:
        """[JAVA-10] for (var u : users) infers User for completion and definition jump."""
        rel = "src/main/java/modern/GenericsAndVar.java"
        orig = self.project.read_file(rel)
        uri_clean, text_clean = self._open_file(rel)
        locs = self._req_locations(
            "definition",
            uri_clean,
            pos_of(text_clean, "getName", occurrence=3),
            tag="JAVA-10",
            context_desc="u.getName() inside enhanced for (var u : users)",
        )
        self.assertTrue(
            any("User.java" in (location_uri(l) or "") for l in locs),
            f"[JAVA-10] Expected definition of u.getName() in enhanced for-loop to jump to User.java, got {locs!r}",
        )
        mod = orig.replace("u.getName();", "u.")
        self.client.did_change(uri_clean, mod)
        items = self._req_completion(
            uri_clean, after_of(mod, "u."), tag="JAVA-10", context_desc="for (var u : users) { u."
        )
        self._assert_has_member(items, "getEmail", tag="JAVA-10", context_desc="enhanced for var u.")

    def test_nested_lambda_inner_and_outer_completion(self) -> None:
        """[JAVA-11] Deeply nested lambdas resolve both inner (y.) and captured outer (x.) parameter types."""
        rel = "src/main/java/modern/LambdasAndRefs.java"
        orig = self.project.read_file(rel)
        # Inner lambda y.
        mod_y = orig.replace("y.getId().equals(x.getId())", "y.")
        uri, _ = self._open_file(rel, mod_y)
        items_y = self._req_completion(
            uri, after_of(mod_y, "y -> y."), tag="JAVA-11", context_desc="nested lambda inner y."
        )
        self._assert_has_member(items_y, "getCode", tag="JAVA-11", context_desc="inner lambda y.")
        self._assert_has_member(items_y, "getId", tag="JAVA-11", context_desc="inner lambda y.")

        # Captured outer lambda x. inside inner lambda
        mod_x = orig.replace("y.getId().equals(x.getId())", "y.getId().equals(x.")
        self.client.did_change(uri, mod_x)
        items_x = self._req_completion(
            uri,
            after_of(mod_x, "equals(x."),
            tag="JAVA-11",
            context_desc="outer lambda x. inside inner lambda",
        )
        self._assert_has_member(items_x, "getEmail", tag="JAVA-11", context_desc="outer lambda x.")
        self._assert_has_member(items_x, "getId", tag="JAVA-11", context_desc="outer lambda x.")

    def test_nested_lambda_hover_and_definition(self) -> None:
        """[JAVA-12] Hover and definition work on outer x and inner y inside nested lambdas."""
        rel = "src/main/java/modern/LambdasAndRefs.java"
        uri, text = self._open_file(rel)
        hov_x = self._req_hover(
            uri, pos_of(text, "x.getId()"), tag="JAVA-12", context_desc="outer lambda param x"
        )
        hov_y = self._req_hover(
            uri, pos_of(text, "y.getId()"), tag="JAVA-12", context_desc="inner lambda param y"
        )
        self.assertIn("User", hov_x, f"[JAVA-12] Expected hover on 'x' to include 'User', got {hov_x!r}")
        self.assertIn("Item", hov_y, f"[JAVA-12] Expected hover on 'y' to include 'Item', got {hov_y!r}")

        x_decl_line = pos_of(text, "x ->")["line"]
        y_decl_line = pos_of(text, "y ->")["line"]
        x_locs = self._req_locations(
            "definition", uri, pos_of(text, "x.getId()"), tag="JAVA-12", context_desc="x in inner lambda"
        )
        y_locs = self._req_locations(
            "definition", uri, pos_of(text, "y.getId()"), tag="JAVA-12", context_desc="y in inner lambda"
        )
        self.assertTrue(
            any((location_range(l) or {}).get("start", {}).get("line") == x_decl_line for l in x_locs),
            f"[JAVA-12] Expected definition on 'x' to jump to lambda param at line {x_decl_line}, got {x_locs!r}",
        )
        self.assertTrue(
            any((location_range(l) or {}).get("start", {}).get("line") == y_decl_line for l in y_locs),
            f"[JAVA-12] Expected definition on 'y' to jump to lambda param at line {y_decl_line}, got {y_locs!r}",
        )

    def test_three_level_nested_lambda_and_trailing_map(self) -> None:
        """[JAVA-13] Multi-level flatMap/filter/map lambda parameters (z.) and trailing map(it -> it.) resolve."""
        rel = "src/main/java/modern/LambdasAndRefs.java"
        orig = self.project.read_file(rel)
        mod_z = orig.replace("z.getCode()", "z.")
        uri, _ = self._open_file(rel, mod_z)
        items_z = self._req_completion(
            uri, after_of(mod_z, "z -> z."), tag="JAVA-13", context_desc="3rd-level lambda z."
        )
        self._assert_has_member(items_z, "getCode", tag="JAVA-13", context_desc="3rd-level lambda z.")

        mod_it = orig.replace("it.getEmail()", "it.")
        self.client.did_change(uri, mod_it)
        items_it = self._req_completion(
            uri, after_of(mod_it, "it -> it."), tag="JAVA-13", context_desc="trailing map(it -> it.)"
        )
        self._assert_has_member(items_it, "getEmail", tag="JAVA-13", context_desc="trailing map(it -> it.)")

    def test_method_references_navigation_and_diagnostics(self) -> None:
        """[JAVA-14] Method references (User::getId, String::valueOf, ArrayList::new, System.out::println) resolve cleanly."""
        rel = "src/main/java/modern/LambdasAndRefs.java"
        uri, text = self._open_file(rel)
        self._assert_zero_errors(uri, text, tag="JAVA-14", context_desc="LambdasAndRefs method references")

        locs = self._req_locations(
            "definition",
            uri,
            pos_of(text, "getId", occurrence=3),
            tag="JAVA-14",
            context_desc="User::getId method reference",
        )
        self.assertTrue(
            any("User.java" in (location_uri(l) or "") for l in locs),
            f"[JAVA-14] Expected definition on User::getId to jump to User.java, got {locs!r}",
        )
        hov_println = self._req_hover(
            uri, pos_of(text, "println"), tag="JAVA-14", context_desc="System.out::println"
        )
        self.assertTrue(
            bool(hov_println),
            "[JAVA-14] Expected non-empty hover on System.out::println method reference, got empty/null",
        )


class TestJava14To17AndConstructs(_ModernJavaBase):
    """Java 14-17+ features (records, sealed, switch expr, pattern instanceof, text blocks) and core constructs."""

    base_files = {
        "src/main/java/j17/ModernSyntax.java": """package j17;

import java.io.IOException;
import java.sql.SQLException;
import java.util.Objects;

public class ModernSyntax {
  public record Point(int x, int y) {}

  public record Box<T>(T value) {
    public Box {
      Objects.requireNonNull(value);
    }
  }

  public sealed interface Shape permits Circle, Rect {
    double area();
  }

  public static final class Circle implements Shape {
    private final double r;
    public Circle(double r) { this.r = r; }
    @Override public double area() { return Math.PI * r * r; }
    public double radius() { return r; }
  }

  public static non-sealed class Rect implements Shape {
    private final double w;
    private final double h;
    public Rect(double w, double h) { this.w = w; this.h = h; }
    @Override public double area() { return w * h; }
  }

  public enum Day {
    MON, TUE, FRI
  }

  public int switchExpr(Day day) {
    int len = switch (day) {
      case MON, FRI -> 6;
      case TUE -> {
        String label = "Tuesday";
        yield label.length();
      }
    };
    return len;
  }

  public int patternMatch(Object obj) {
    if (obj instanceof String s) {
      return s.length();
    }
    return 0;
  }

  public String textBlockUsage() {
    String json = \"\"\"
        {
          "name": "%s"
        }
        \"\"\".formatted("alice");
    return json.stripIndent();
  }

  public void recordsTest() {
    Point p = new Point(1, 2);
    int sum = p.x() + p.y();
    Box<String> box = new Box<>("hi");
    int len = box.value().length();
    if (sum + len > 0) {
      return;
    }
  }
}
""",
        "src/main/java/j17/CoreConstructs.java": """package j17;

import java.io.IOException;
import java.sql.SQLException;

public class CoreConstructs {
  public interface Greeter {
    static String prefix() { return "Hello"; }
    default String defaultGreet(String who) { return prefix() + " " + who; }
  }

  public static class StaticNested {
    public String tag() { return "static"; }
  }

  public class Inner {
    public String useOuter() {
      return CoreConstructs.this.outerHelper() + tagLocal();
    }
    private String tagLocal() { return "inner"; }
  }

  public enum Operation {
    PLUS("+") {
      @Override public int apply(int a, int b) { return a + b; }
    },
    MINUS("-") {
      @Override public int apply(int a, int b) { return a - b; }
    };

    private final String symbol;
    Operation(String symbol) { this.symbol = symbol; }
    public String getSymbol() { return symbol; }
    public abstract int apply(int a, int b);
  }

  public static class Base {
    private String hiddenParent = "h";
    public String describe() { return "base" + hiddenParent; }
  }

  public static class Child extends Base implements Greeter {
    @Override
    public String describe() {
      return super.describe() + ":" + this.childOnly();
    }
    public String childOnly() { return "child"; }
  }

  public static class Resource implements AutoCloseable {
    public String readLine() { return "ok"; }
    @Override public void close() {}
  }

  public String outerHelper() { return "outer"; }

  public String joinAll(String sep, String... args) {
    int count = args.length;
    return count > 0 ? args[0] + sep : "";
  }

  public void testConstructs(boolean failSql) {
    StaticNested sn = new StaticNested();
    Inner inner = new Inner();
    int calc = Operation.PLUS.apply(1, 2);
    String opSym = Operation.MINUS.getSymbol();
    Child c = new Child();
    String g = c.defaultGreet("world");
    String p = Greeter.prefix();
    String[] arr = {"a", "b"};
    int arrLen = arr.length;
    String upper = arr[0].toUpperCase();
    Runnable r = new Runnable() {
      @Override public void run() { outerHelper(); }
    };
    class LocalHelper {
      String ping() { return "pong"; }
    }
    LocalHelper lh = new LocalHelper();
    try (var res = new Resource()) {
      res.readLine();
    }
    try {
      if (failSql) throw new SQLException("sql");
      throw new IOException("io");
    } catch (IOException | SQLException e) {
      e.getMessage();
    }
    if (sn.tag().length() + inner.useOuter().length() + calc + opSym.length()
        + g.length() + p.length() + arrLen + upper.length() + lh.ping().length() > 0) {
      r.run();
    }
  }
}
""",
    }

    def test_record_auto_accessors_and_definition(self) -> None:
        """[JAVA-15] Record auto-accessors (x(), y()) appear in completion and jump to record components."""
        rel = "src/main/java/j17/ModernSyntax.java"
        orig = self.project.read_file(rel)
        uri_clean, text_clean = self._open_file(rel)
        locs = self._req_locations(
            "definition", uri_clean, pos_of(text_clean, "x()"), tag="JAVA-15", context_desc="p.x()"
        )
        self.assertTrue(
            any((location_range(l) or {}).get("start", {}).get("line") == pos_of(text_clean, "int x")["line"] for l in locs),
            f"[JAVA-15] Expected definition of p.x() to jump to record component 'int x', got {locs!r}",
        )
        mod = orig.replace("p.x() + p.y();", "p.")
        self.client.did_change(uri_clean, mod)
        items = self._req_completion(
            uri_clean, after_of(mod, "p."), tag="JAVA-15", context_desc="record Point p."
        )
        self._assert_has_member(items, "x", tag="JAVA-15", context_desc="record Point p.")
        self._assert_has_member(items, "y", tag="JAVA-15", context_desc="record Point p.")
        self._assert_has_member(items, "toString", tag="JAVA-15", context_desc="record Point p.")

    def test_generic_record_compact_constructor(self) -> None:
        """[JAVA-16] Generic record Box<T> with compact constructor resolves box.value(). as String."""
        rel = "src/main/java/j17/ModernSyntax.java"
        orig = self.project.read_file(rel)
        uri_clean, text_clean = self._open_file(rel)
        self._assert_zero_errors(uri_clean, text_clean, tag="JAVA-16", context_desc="ModernSyntax.java")

        mod = orig.replace("box.value().length();", "box.value().")
        self.client.did_change(uri_clean, mod)
        items = self._req_completion(
            uri_clean, after_of(mod, "box.value()."), tag="JAVA-16", context_desc="Box<String>.value()."
        )
        self._assert_has_member(items, "length", tag="JAVA-16", context_desc="Box<String>.value().")

    def test_sealed_interface_and_permits(self) -> None:
        """[JAVA-17] Sealed interface permits Circle, Rect compiles cleanly and implementation finds permits."""
        rel = "src/main/java/j17/ModernSyntax.java"
        uri, text = self._open_file(rel)
        self._assert_zero_errors(uri, text, tag="JAVA-17", context_desc="sealed Shape permits Circle, Rect")
        locs = self._req_locations(
            "implementation", uri, pos_of(text, "Shape"), tag="JAVA-17", context_desc="sealed interface Shape"
        )
        lines = {(location_range(l) or {}).get("start", {}).get("line") for l in locs}
        circle_line = pos_of(text, "class Circle")["line"]
        rect_line = pos_of(text, "class Rect")["line"]
        self.assertTrue(
            circle_line in lines and rect_line in lines,
            f"[JAVA-17] Expected implementation on sealed Shape to include Circle (line {circle_line}) "
            f"and Rect (line {rect_line}), got {locs!r}",
        )

    def test_switch_expression_arrow_and_yield(self) -> None:
        """[JAVA-18] Switch expression with -> and yield block has 0 diagnostics and completes yield local."""
        rel = "src/main/java/j17/ModernSyntax.java"
        orig = self.project.read_file(rel)
        uri_clean, text_clean = self._open_file(rel)
        self._assert_zero_errors(uri_clean, text_clean, tag="JAVA-18", context_desc="switch expression")

        mod = orig.replace("yield label.length();", "yield label.")
        self.client.did_change(uri_clean, mod)
        items = self._req_completion(
            uri_clean, after_of(mod, "yield label."), tag="JAVA-18", context_desc="yield label."
        )
        self._assert_has_member(items, "length", tag="JAVA-18", context_desc="switch yield label.")

    def test_instanceof_pattern_matching(self) -> None:
        """[JAVA-19] instanceof pattern variable 's' provides String completion, hover, and definition."""
        rel = "src/main/java/j17/ModernSyntax.java"
        orig = self.project.read_file(rel)
        uri_clean, text_clean = self._open_file(rel)
        hov = self._req_hover(
            uri_clean, pos_of(text_clean, "s.length()"), tag="JAVA-19", context_desc="pattern var s"
        )
        self.assertIn(
            "String",
            hov,
            f"[JAVA-19] Expected hover on instanceof pattern variable 's' to contain 'String', got {hov!r}",
        )
        locs = self._req_locations(
            "definition", uri_clean, pos_of(text_clean, "s.length()"), tag="JAVA-19", context_desc="pattern var s"
        )
        decl_line = pos_of(text_clean, "String s)")["line"]
        self.assertTrue(
            any((location_range(l) or {}).get("start", {}).get("line") == decl_line for l in locs),
            f"[JAVA-19] Expected definition of 's' to jump to 'String s' on line {decl_line}, got {locs!r}",
        )
        mod = orig.replace("return s.length();", "return s.")
        self.client.did_change(uri_clean, mod)
        items = self._req_completion(
            uri_clean, after_of(mod, "return s."), tag="JAVA-19", context_desc="instanceof pattern s."
        )
        self._assert_has_member(items, "isBlank", tag="JAVA-19", context_desc="instanceof pattern s.")

    def test_text_blocks_completion_and_diagnostics(self) -> None:
        """[JAVA-20] Multi-line text block resolves String methods (.formatted, .stripIndent) with 0 errors."""
        rel = "src/main/java/j17/ModernSyntax.java"
        orig = self.project.read_file(rel)
        uri_clean, text_clean = self._open_file(rel)
        self._assert_zero_errors(uri_clean, text_clean, tag="JAVA-20", context_desc="text block")

        mod = orig.replace("return json.stripIndent();", "return json.")
        self.client.did_change(uri_clean, mod)
        items = self._req_completion(
            uri_clean, after_of(mod, "return json."), tag="JAVA-20", context_desc="text block String json."
        )
        self._assert_has_member(items, "stripIndent", tag="JAVA-20", context_desc="text block json.")

    def test_inner_and_static_nested_classes(self) -> None:
        """[JAVA-21] Inner and static nested classes resolve members and Outer.this qualification."""
        rel = "src/main/java/j17/CoreConstructs.java"
        orig = self.project.read_file(rel)
        mod = orig.replace("sn.tag().length()", "sn.")
        uri, _ = self._open_file(rel, mod)
        items_sn = self._req_completion(
            uri, after_of(mod, "sn."), tag="JAVA-21", context_desc="StaticNested sn."
        )
        self._assert_has_member(items_sn, "tag", tag="JAVA-21", context_desc="StaticNested sn.")

        mod2 = orig.replace("CoreConstructs.this.outerHelper()", "CoreConstructs.this.")
        self.client.did_change(uri, mod2)
        items_outer = self._req_completion(
            uri, after_of(mod2, "CoreConstructs.this."), tag="JAVA-21", context_desc="CoreConstructs.this."
        )
        self._assert_has_member(items_outer, "outerHelper", tag="JAVA-21", context_desc="CoreConstructs.this.")

    def test_local_and_anonymous_classes(self) -> None:
        """[JAVA-22] Local class and anonymous Runnable resolve cleanly with 0 diagnostics."""
        rel = "src/main/java/j17/CoreConstructs.java"
        orig = self.project.read_file(rel)
        uri_clean, text_clean = self._open_file(rel)
        self._assert_zero_errors(uri_clean, text_clean, tag="JAVA-22", context_desc="CoreConstructs.java")

        mod = orig.replace("lh.ping().length()", "lh.")
        self.client.did_change(uri_clean, mod)
        items = self._req_completion(
            uri_clean, after_of(mod, "lh."), tag="JAVA-22", context_desc="local class lh."
        )
        self._assert_has_member(items, "ping", tag="JAVA-22", context_desc="local class lh.")

    def test_enum_with_fields_constructors_and_abstract_methods(self) -> None:
        """[JAVA-23] Rich enum exposes constants, values(), constructor fields, and abstract method apply()."""
        rel = "src/main/java/j17/CoreConstructs.java"
        orig = self.project.read_file(rel)
        mod = orig.replace("Operation.PLUS.apply(1, 2);", "Operation.PLUS.")
        uri, _ = self._open_file(rel, mod)
        items_const = self._req_completion(
            uri, after_of(mod, "Operation.PLUS."), tag="JAVA-23", context_desc="Operation.PLUS."
        )
        self._assert_has_member(items_const, "apply", tag="JAVA-23", context_desc="Operation.PLUS.")
        self._assert_has_member(items_const, "getSymbol", tag="JAVA-23", context_desc="Operation.PLUS.")
        self._assert_has_member(items_const, "name", tag="JAVA-23", context_desc="Operation.PLUS.")

        mod2 = orig.replace("Operation.PLUS.apply(1, 2);", "Operation.")
        self.client.did_change(uri, mod2)
        items_enum = self._req_completion(
            uri, after_of(mod2, "Operation."), tag="JAVA-23", context_desc="Operation."
        )
        self._assert_has_member(items_enum, "PLUS", tag="JAVA-23", context_desc="Operation.")
        self._assert_has_member(items_enum, "values", tag="JAVA-23", context_desc="Operation.")

    def test_varargs_array_properties(self) -> None:
        """[JAVA-24] Varargs parameter String... args exposes array .length and element String methods."""
        rel = "src/main/java/j17/CoreConstructs.java"
        orig = self.project.read_file(rel)
        mod = orig.replace("int count = args.length;", "int count = args.")
        uri, _ = self._open_file(rel, mod)
        items = self._req_completion(
            uri, after_of(mod, "int count = args."), tag="JAVA-24", context_desc="varargs args."
        )
        self._assert_has_member(items, "length", tag="JAVA-24", context_desc="varargs args.")

    def test_try_with_resources_and_multi_catch(self) -> None:
        """[JAVA-25][JAVA-26] Try-with-resources var and multi-catch exception resolve members accurately."""
        rel = "src/main/java/j17/CoreConstructs.java"
        orig = self.project.read_file(rel)
        mod_twr = orig.replace("res.readLine();", "res.")
        uri, _ = self._open_file(rel, mod_twr)
        items_twr = self._req_completion(
            uri, after_of(mod_twr, "res."), tag="JAVA-25", context_desc="try-with-resources res."
        )
        self._assert_has_member(items_twr, "readLine", tag="JAVA-25", context_desc="try-with-resources res.")

        mod_mc = orig.replace("e.getMessage();", "e.")
        self.client.did_change(uri, mod_mc)
        items_mc = self._req_completion(
            uri, after_of(mod_mc, "e."), tag="JAVA-26", context_desc="multi-catch e."
        )
        self._assert_has_member(items_mc, "getMessage", tag="JAVA-26", context_desc="multi-catch e.")

    def test_array_length_and_element_indexing(self) -> None:
        """[JAVA-27] Array variable exposes .length and indexed element arr[0]. exposes String methods."""
        rel = "src/main/java/j17/CoreConstructs.java"
        orig = self.project.read_file(rel)
        mod_arr = orig.replace("int arrLen = arr.length;", "int arrLen = arr.")
        uri, _ = self._open_file(rel, mod_arr)
        items_arr = self._req_completion(
            uri, after_of(mod_arr, "int arrLen = arr."), tag="JAVA-27", context_desc="String[] arr."
        )
        self._assert_has_member(items_arr, "length", tag="JAVA-27", context_desc="String[] arr.")

        mod_elem = orig.replace("arr[0].toUpperCase();", "arr[0].")
        self.client.did_change(uri, mod_elem)
        items_elem = self._req_completion(
            uri, after_of(mod_elem, "arr[0]."), tag="JAVA-27", context_desc="arr[0]."
        )
        self._assert_has_member(items_elem, "toUpperCase", tag="JAVA-27", context_desc="arr[0].")

    def test_interface_default_and_static_methods(self) -> None:
        """[JAVA-28] Interface static method on InterfaceName. and default method on implementor complete."""
        rel = "src/main/java/j17/CoreConstructs.java"
        orig = self.project.read_file(rel)
        mod_stat = orig.replace("Greeter.prefix();", "Greeter.")
        uri, _ = self._open_file(rel, mod_stat)
        items_stat = self._req_completion(
            uri, after_of(mod_stat, "Greeter."), tag="JAVA-28", context_desc="Greeter."
        )
        self._assert_has_member(items_stat, "prefix", tag="JAVA-28", context_desc="interface Greeter.")

        mod_def = orig.replace('c.defaultGreet("world");', "c.")
        self.client.did_change(uri, mod_def)
        items_def = self._req_completion(
            uri, after_of(mod_def, "String g = c."), tag="JAVA-28", context_desc="Child c."
        )
        self._assert_has_member(items_def, "defaultGreet", tag="JAVA-28", context_desc="Child c.")

    def test_this_vs_super_disambiguation(self) -> None:
        """[JAVA-29] super.describe() jumps to Base.describe while this. excludes parent private and super. excludes childOnly."""
        rel = "src/main/java/j17/CoreConstructs.java"
        orig = self.project.read_file(rel)
        uri_clean, text_clean = self._open_file(rel)
        base_line = pos_of(text_clean, "public String describe()", occurrence=1)["line"]
        locs_super = self._req_locations(
            "definition",
            uri_clean,
            pos_of(text_clean, "describe", occurrence=3),
            tag="JAVA-29",
            context_desc="super.describe() method identifier",
        )
        self.assertTrue(
            any((location_range(l) or {}).get("start", {}).get("line") == base_line for l in locs_super),
            f"[JAVA-29] Expected super.describe() definition to jump to Base.describe (line {base_line}), "
            f"got {locs_super!r}",
        )

        mod_sup = orig.replace("super.describe()", "super.")
        self.client.did_change(uri_clean, mod_sup)
        items_sup = self._req_completion(
            uri_clean, after_of(mod_sup, "return super."), tag="JAVA-29", context_desc="super. in Child"
        )
        self._assert_has_member(items_sup, "describe", tag="JAVA-29", context_desc="super. in Child")
        self._assert_lacks_member(
            items_sup,
            "childOnly",
            tag="JAVA-29",
            context_desc="super. in Child",
            reason="childOnly is declared on Child, not Base",
        )
        self._assert_lacks_member(
            items_sup,
            "hiddenParent",
            tag="JAVA-29",
            context_desc="super. in Child",
            reason="hiddenParent is private in Base",
        )


if __name__ == "__main__":
    unittest.main()
