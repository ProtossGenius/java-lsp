"""Black-box LSP 3.17 navigation (definition/declaration/typeDef/impl/refs) and symbol/hierarchy tests."""

from __future__ import annotations

import shutil
import unittest
from pathlib import Path
from typing import Any

from tests.blackbox.lsp_client import (
    REPO_ROOT,
    LspClient,
    LspClientError,
    LspResponseError,
    ServerCrashedError,
    TempJavaProject,
    extract_locations,
    flatten_document_symbols,
    location_range,
    location_uri,
    pos_of,
    range_of,
    uri_exists_on_disk,
    uri_to_path,
)

SPRING_DEMO_DIR = REPO_ROOT / "java17-spring-demo"


def _minimal_pom(artifact: str = "nav-demo") -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<project xmlns="http://maven.apache.org/POM/4.0.0"\n'
        '         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"\n'
        '         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0 '
        'https://maven.apache.org/xsd/maven-4.0.0.xsd">\n'
        "  <modelVersion>4.0.0</modelVersion>\n"
        "  <groupId>com.acme</groupId>\n"
        f"  <artifactId>{artifact}</artifactId>\n"
        "  <version>1.0.0</version>\n"
        "  <properties>\n"
        "    <maven.compiler.source>17</maven.compiler.source>\n"
        "    <maven.compiler.target>17</maven.compiler.target>\n"
        "  </properties>\n"
        "</project>\n"
    )


def _safe_req(
    case: unittest.TestCase,
    client: LspClient,
    method: str,
    params: Any = None,
    timeout: float = 20.0,
) -> Any:
    try:
        return client.request(method, params, timeout=timeout)
    except LspResponseError as exc:
        case.fail(
            f"LSP request {method!r} params={params!r} returned JSON-RPC error "
            f"code={exc.code} message={exc.message!r}; expected valid response"
        )
    except (ServerCrashedError, TimeoutError, LspClientError) as exc:
        case.fail(f"LSP request {method!r} params={params!r} failed: {exc}")


def _assert_loc_matches(
    case: unittest.TestCase,
    label: str,
    raw_result: Any,
    expected_uri: str,
    expected_line: int | None = None,
    expected_range: dict[str, Any] | None = None,
) -> dict[str, Any]:
    locs = extract_locations(raw_result)
    case.assertTrue(
        locs,
        f"{label}: expected location in {expected_uri} (line={expected_line}), got {raw_result!r}",
    )
    matched = None
    for loc in locs:
        u = location_uri(loc)
        r = location_range(loc)
        if u == expected_uri:
            if expected_line is None or (r and r["start"]["line"] == expected_line):
                matched = loc
                break
    if matched is None:
        case.fail(
            f"{label}: expected uri={expected_uri!r}"
            + (f" at line={expected_line}" if expected_line is not None else "")
            + f", got locations={locs!r}"
        )
    if expected_range is not None:
        actual_rng = location_range(matched)
        case.assertEqual(
            actual_rng,
            expected_range,
            f"{label}: expected exact range {expected_range!r}, got {actual_rng!r}",
        )
    return matched


SYNTH_FILES: dict[str, str] = {
    "pom.xml": _minimal_pom("nav-synth"),
    "src/main/java/com/acme/nav/Greeter.java": (
        "package com.acme.nav;\n\n"
        "public interface Greeter {\n"
        "  String greet(String target);\n\n"
        "  default String greetLoud(String target) {\n"
        "    return greet(target).toUpperCase();\n"
        "  }\n"
        "}\n"
    ),
    "src/main/java/com/acme/nav/BaseComponent.java": (
        "package com.acme.nav;\n\n"
        "public class BaseComponent {\n"
        "  protected String baseName;\n\n"
        "  public BaseComponent(String baseName) {\n"
        "    this.baseName = baseName;\n"
        "  }\n\n"
        "  public String describe() {\n"
        "    return baseName;\n"
        "  }\n"
        "}\n"
    ),
    "src/main/java/com/acme/nav/Color.java": (
        "package com.acme.nav;\n\n"
        "public enum Color {\n"
        "  RED(1),\n"
        "  GREEN(2),\n"
        "  CRIMSON(3);\n\n"
        "  private final int code;\n\n"
        "  Color(int code) {\n"
        "    this.code = code;\n"
        "  }\n\n"
        "  public int getCode() {\n"
        "    return code;\n"
        "  }\n"
        "}\n"
    ),
    "src/main/java/com/acme/nav/MathHelper.java": (
        "package com.acme.nav;\n\n"
        "public final class MathHelper {\n"
        "  public static final int MAGIC_FACTOR = 42;\n\n"
        "  public static int computeMagic(int x) {\n"
        "    return x * MAGIC_FACTOR;\n"
        "  }\n"
        "}\n"
    ),
    "src/main/java/com/acme/nav/RecordSample.java": (
        "package com.acme.nav;\n\n"
        "public record Point(int x, int y) {\n"
        "  public int sum() {\n"
        "    return x + y;\n"
        "  }\n"
        "}\n"
    ),
    "src/main/java/com/acme/nav/Widget.java": (
        "package com.acme.nav;\n\n"
        "public class Widget extends BaseComponent implements Greeter {\n"
        "  private final String title;\n"
        "  private int count;\n\n"
        "  public Widget(String title) {\n"
        "    this(title, 0);\n"
        "  }\n\n"
        "  public Widget(String title, int count) {\n"
        "    super(title);\n"
        "    this.title = title;\n"
        "    this.count = count;\n"
        "  }\n\n"
        "  @Override\n"
        "  public String describe() {\n"
        "    return super.describe() + \":\" + this.title;\n"
        "  }\n\n"
        "  public String describeSelf() {\n"
        "    return this.describe();\n"
        "  }\n\n"
        "  @Override\n"
        "  public String greet(String target) {\n"
        "    return title + \"->\" + target;\n"
        "  }\n\n"
        "  public String compute(int n) {\n"
        "    return \"int:\" + n;\n"
        "  }\n\n"
        "  public String compute(String s) {\n"
        "    return \"str:\" + s;\n"
        "  }\n\n"
        "  public String compute(int a, int b) {\n"
        "    return \"pair:\" + (a + b);\n"
        "  }\n\n"
        "  public Widget withCount(int count) {\n"
        "    this.count = count;\n"
        "    return this;\n"
        "  }\n\n"
        "  public BaseComponent asBase() {\n"
        "    return this;\n"
        "  }\n\n"
        "  public String getTitle() {\n"
        "    return title;\n"
        "  }\n\n"
        "  public static class StaticNested {\n"
        "    public String tag() {\n"
        "      return \"static-nested\";\n"
        "    }\n"
        "  }\n\n"
        "  public class InnerHelper {\n"
        "    public String readTitle() {\n"
        "      return title;\n"
        "    }\n"
        "  }\n"
        "}\n"
    ),
    "src/main/java/com/acme/nav/NavRunner.java": (
        "package com.acme.nav;\n\n"
        "import static com.acme.nav.Color.CRIMSON;\n"
        "import static com.acme.nav.MathHelper.MAGIC_FACTOR;\n"
        "import static com.acme.nav.MathHelper.computeMagic;\n\n"
        "import java.util.List;\n\n"
        "public class NavRunner {\n"
        "  private final String prefix = \"hi:\";\n\n"
        "  public String runAll(String inputName) {\n"
        "    int localVal = 7;\n"
        "    int doubled = computeMagic(localVal) + MAGIC_FACTOR;\n"
        "    Widget w1 = new Widget(inputName);\n"
        "    Widget w2 = new Widget(inputName, doubled);\n"
        "    String o1 = w1.compute(10);\n"
        "    String o2 = w1.compute(\"ten\");\n"
        "    String o3 = w1.compute(1, 2);\n"
        "    String chained = w2.withCount(5).asBase().describe();\n"
        "    int enumCode = CRIMSON.getCode() + Color.RED.getCode();\n"
        "    String nested = new Widget.StaticNested().tag();\n"
        "    String inner = w1.new InnerHelper().readTitle();\n"
        "    Greeter anon = new Greeter() {\n"
        "      @Override\n"
        "      public String greet(String target) {\n"
        "        return prefix + target;\n"
        "      }\n"
        "    };\n"
        "    Point pt = new Point(3, 4);\n"
        "    List<String> items = List.of(\"a\", \"b\");\n"
        "    List<String> upper = items.stream().map(it -> it.toUpperCase()).toList();\n"
        "    return o1 + o2 + o3 + chained + enumCode + nested + inner + anon.greet(inputName) + pt.sum() + upper.size();\n"
        "  }\n\n"
        "  public int methodA() {\n"
        "    int shadow = 1;\n"
        "    return shadow + 10;\n"
        "  }\n\n"
        "  public int methodB() {\n"
        "    int shadow = 2;\n"
        "    return shadow + 20;\n"
        "  }\n"
        "}\n"
    ),
}


class TestSyntheticNavigationAndSymbols(unittest.TestCase):
    """[NAV/SYM] Navigation, references, documentSymbol, and extended LSP 3.17 features on synthetic fixtures."""

    project: TempJavaProject
    client: LspClient

    @classmethod
    def setUpClass(cls) -> None:
        cls.project = TempJavaProject(files=SYNTH_FILES, create_root_marker=True)
        cls.client = LspClient(root_path=cls.project.root, default_timeout=20.0)
        try:
            cls.client.initialize_and_ready(timeout=60.0)
            for rel in SYNTH_FILES:
                if rel.endswith(".java"):
                    cls.client.did_open(cls.project.uri(rel), SYNTH_FILES[rel])
        except Exception:
            cls.client.close()
            cls.project.close()
            raise

    @classmethod
    def tearDownClass(cls) -> None:
        try:
            cls.client.close()
        finally:
            cls.project.close()

    def setUp(self) -> None:
        if not self.client.is_alive():
            self.client.restart(clean_storage=False, clear_history=True)
            self.client.initialize_and_ready(timeout=60.0)
            for rel in SYNTH_FILES:
                if rel.endswith(".java"):
                    self.client.did_open(self.project.uri(rel), SYNTH_FILES[rel])

    def _uri(self, name: str) -> str:
        return self.project.uri(f"src/main/java/com/acme/nav/{name}")

    def _txt(self, name: str) -> str:
        return SYNTH_FILES[f"src/main/java/com/acme/nav/{name}"]

    def test_nav_01_local_variable_definition(self) -> None:
        """[NAV-01] definition on local variable usage jumps to its declaration in same method."""
        nav = self._txt("NavRunner.java")
        nav_uri = self._uri("NavRunner.java")
        decl_rng = range_of(nav, "localVal", occurrence=1)
        use_pos = pos_of(nav, "localVal", occurrence=2)
        res = _safe_req(
            self,
            self.client,
            "textDocument/definition",
            {"textDocument": {"uri": nav_uri}, "position": use_pos},
        )
        _assert_loc_matches(
            self,
            "definition(localVal)",
            res,
            nav_uri,
            expected_line=decl_rng["start"]["line"],
        )

    def test_nav_02_parameter_definition(self) -> None:
        """[NAV-02] definition on method parameter usage jumps to parameter declaration."""
        nav = self._txt("NavRunner.java")
        nav_uri = self._uri("NavRunner.java")
        decl_rng = range_of(nav, "inputName", occurrence=1)
        use_pos = pos_of(nav, "inputName", occurrence=2)
        res = _safe_req(
            self,
            self.client,
            "textDocument/definition",
            {"textDocument": {"uri": nav_uri}, "position": use_pos},
        )
        _assert_loc_matches(
            self,
            "definition(inputName param)",
            res,
            nav_uri,
            expected_line=decl_rng["start"]["line"],
        )

    def test_nav_03_field_definition_this_and_unqualified(self) -> None:
        """[NAV-03] definition on field usage (this.title and unqualified title) jumps to field declaration."""
        w = self._txt("Widget.java")
        w_uri = self._uri("Widget.java")
        decl_line = pos_of(w, "private final String title")["line"]

        res_this = _safe_req(
            self,
            self.client,
            "textDocument/definition",
            {"textDocument": {"uri": w_uri}, "position": pos_of(w, "this.title", offset=5)},
        )
        res_title = _safe_req(
            self,
            self.client,
            "textDocument/definition",
            {"textDocument": {"uri": w_uri}, "position": pos_of(w, "return title +", offset=7)},
        )
        failures: list[str] = []
        for lbl, res in (("this.title", res_this), ("unqualified title", res_title)):
            locs = extract_locations(res)
            if not any(location_uri(l) == w_uri and (location_range(l) or {}).get("start", {}).get("line") == decl_line for l in locs):
                failures.append(f"definition({lbl}) expected line={decl_line}, got {res!r}")
        self.assertFalse(failures, "; ".join(failures))

    def test_nav_04_constructor_definition_and_overload_selection(self) -> None:
        """[NAV-04] definition on 'new Widget(...)' jumps to matching overloaded constructor."""
        nav = self._txt("NavRunner.java")
        nav_uri = self._uri("NavRunner.java")
        w = self._txt("Widget.java")
        w_uri = self._uri("Widget.java")

        ctor1_line = pos_of(w, "public Widget(String title)")["line"]
        ctor2_line = pos_of(w, "public Widget(String title, int count)")["line"]

        res1 = _safe_req(
            self,
            self.client,
            "textDocument/definition",
            {"textDocument": {"uri": nav_uri}, "position": pos_of(nav, "new Widget(inputName)", offset=4)},
        )
        _assert_loc_matches(self, "definition(new Widget(1-arg))", res1, w_uri, expected_line=ctor1_line)

        res2 = _safe_req(
            self,
            self.client,
            "textDocument/definition",
            {
                "textDocument": {"uri": nav_uri},
                "position": pos_of(nav, "new Widget(inputName, doubled)", offset=4),
            },
        )
        _assert_loc_matches(self, "definition(new Widget(2-arg))", res2, w_uri, expected_line=ctor2_line)

    def test_nav_05_overloaded_method_definition_distinguishes_arg_types(self) -> None:
        """[NAV-05] definition distinguishes overloaded methods by argument types and arity."""
        nav = self._txt("NavRunner.java")
        nav_uri = self._uri("NavRunner.java")
        w = self._txt("Widget.java")
        w_uri = self._uri("Widget.java")

        int_line = pos_of(w, "public String compute(int n)")["line"]
        str_line = pos_of(w, "public String compute(String s)")["line"]
        pair_line = pos_of(w, "public String compute(int a, int b)")["line"]

        r_int = _safe_req(
            self,
            self.client,
            "textDocument/definition",
            {"textDocument": {"uri": nav_uri}, "position": pos_of(nav, "w1.compute(10)", offset=3)},
        )
        _assert_loc_matches(self, "definition(compute(int))", r_int, w_uri, expected_line=int_line)

        r_str = _safe_req(
            self,
            self.client,
            "textDocument/definition",
            {"textDocument": {"uri": nav_uri}, "position": pos_of(nav, 'w1.compute("ten")', offset=3)},
        )
        _assert_loc_matches(self, "definition(compute(String))", r_str, w_uri, expected_line=str_line)

        r_pair = _safe_req(
            self,
            self.client,
            "textDocument/definition",
            {"textDocument": {"uri": nav_uri}, "position": pos_of(nav, "w1.compute(1, 2)", offset=3)},
        )
        _assert_loc_matches(self, "definition(compute(int, int))", r_pair, w_uri, expected_line=pair_line)

    def test_nav_06_this_and_super_method_navigation(self) -> None:
        """[NAV-06] definition on super.describe() jumps to BaseComponent and this.describe() jumps to Widget."""
        w = self._txt("Widget.java")
        w_uri = self._uri("Widget.java")
        base = self._txt("BaseComponent.java")
        base_uri = self._uri("BaseComponent.java")

        base_line = pos_of(base, "public String describe()")["line"]
        widget_line = pos_of(w, "public String describe()")["line"]

        res_super = _safe_req(
            self,
            self.client,
            "textDocument/definition",
            {"textDocument": {"uri": w_uri}, "position": pos_of(w, "super.describe()", offset=6)},
        )
        _assert_loc_matches(self, "definition(super.describe())", res_super, base_uri, expected_line=base_line)

        res_this = _safe_req(
            self,
            self.client,
            "textDocument/definition",
            {"textDocument": {"uri": w_uri}, "position": pos_of(w, "this.describe()", offset=5)},
        )
        _assert_loc_matches(self, "definition(this.describe())", res_this, w_uri, expected_line=widget_line)

    def test_nav_07_chained_method_calls_navigation(self) -> None:
        """[NAV-07] definition resolves each segment of chained call w2.withCount(5).asBase().describe()."""
        nav = self._txt("NavRunner.java")
        nav_uri = self._uri("NavRunner.java")
        w = self._txt("Widget.java")
        w_uri = self._uri("Widget.java")
        base = self._txt("BaseComponent.java")
        base_uri = self._uri("BaseComponent.java")

        chain_anchor = "w2.withCount(5).asBase().describe()"
        res_with = _safe_req(
            self,
            self.client,
            "textDocument/definition",
            {"textDocument": {"uri": nav_uri}, "position": pos_of(nav, chain_anchor, offset=3)},
        )
        _assert_loc_matches(
            self,
            "definition(chained .withCount)",
            res_with,
            w_uri,
            expected_line=pos_of(w, "public Widget withCount")["line"],
        )

        res_as_base = _safe_req(
            self,
            self.client,
            "textDocument/definition",
            {"textDocument": {"uri": nav_uri}, "position": pos_of(nav, "asBase().describe()")},
        )
        _assert_loc_matches(
            self,
            "definition(chained .asBase)",
            res_as_base,
            w_uri,
            expected_line=pos_of(w, "public BaseComponent asBase")["line"],
        )

        res_desc = _safe_req(
            self,
            self.client,
            "textDocument/definition",
            {"textDocument": {"uri": nav_uri}, "position": pos_of(nav, "asBase().describe()", offset=9)},
        )
        _assert_loc_matches(
            self,
            "definition(chained .describe)",
            res_desc,
            base_uri,
            expected_line=pos_of(base, "public String describe()")["line"],
        )

    def test_nav_08_static_import_navigation(self) -> None:
        """[NAV-08] definition on statically imported method, constant, and enum constant resolves to source."""
        nav = self._txt("NavRunner.java")
        nav_uri = self._uri("NavRunner.java")
        mh = self._txt("MathHelper.java")
        mh_uri = self._uri("MathHelper.java")
        col = self._txt("Color.java")
        col_uri = self._uri("Color.java")

        res_fn = _safe_req(
            self,
            self.client,
            "textDocument/definition",
            {"textDocument": {"uri": nav_uri}, "position": pos_of(nav, "computeMagic(localVal)")},
        )
        _assert_loc_matches(
            self,
            "definition(static import computeMagic)",
            res_fn,
            mh_uri,
            expected_line=pos_of(mh, "public static int computeMagic")["line"],
        )

        res_const = _safe_req(
            self,
            self.client,
            "textDocument/definition",
            {"textDocument": {"uri": nav_uri}, "position": pos_of(nav, "MAGIC_FACTOR", occurrence=2)},
        )
        _assert_loc_matches(
            self,
            "definition(static import MAGIC_FACTOR)",
            res_const,
            mh_uri,
            expected_line=pos_of(mh, "public static final int MAGIC_FACTOR")["line"],
        )

        res_enum = _safe_req(
            self,
            self.client,
            "textDocument/definition",
            {"textDocument": {"uri": nav_uri}, "position": pos_of(nav, "CRIMSON.getCode()")},
        )
        _assert_loc_matches(
            self,
            "definition(static import enum CRIMSON)",
            res_enum,
            col_uri,
            expected_line=pos_of(col, "CRIMSON(3)")["line"],
        )

    def test_nav_09_enum_constant_and_method_navigation(self) -> None:
        """[NAV-09] definition on Color.RED.getCode() resolves both enum constant and enum method."""
        nav = self._txt("NavRunner.java")
        nav_uri = self._uri("NavRunner.java")
        col = self._txt("Color.java")
        col_uri = self._uri("Color.java")

        res_red = _safe_req(
            self,
            self.client,
            "textDocument/definition",
            {"textDocument": {"uri": nav_uri}, "position": pos_of(nav, "Color.RED.getCode()", offset=6)},
        )
        _assert_loc_matches(
            self,
            "definition(Color.RED)",
            res_red,
            col_uri,
            expected_line=pos_of(col, "RED(1)")["line"],
        )

        res_code = _safe_req(
            self,
            self.client,
            "textDocument/definition",
            {"textDocument": {"uri": nav_uri}, "position": pos_of(nav, "Color.RED.getCode()", offset=10)},
        )
        _assert_loc_matches(
            self,
            "definition(Color.RED.getCode())",
            res_code,
            col_uri,
            expected_line=pos_of(col, "public int getCode()")["line"],
        )

    def test_nav_10_inner_nested_and_anonymous_classes(self) -> None:
        """[NAV-10] definition resolves static nested class, inner class, and enclosing field inside anonymous class."""
        nav = self._txt("NavRunner.java")
        nav_uri = self._uri("NavRunner.java")
        w = self._txt("Widget.java")
        w_uri = self._uri("Widget.java")

        res_static = _safe_req(
            self,
            self.client,
            "textDocument/definition",
            {"textDocument": {"uri": nav_uri}, "position": pos_of(nav, "StaticNested().tag()", offset=15)},
        )
        _assert_loc_matches(
            self,
            "definition(StaticNested.tag())",
            res_static,
            w_uri,
            expected_line=pos_of(w, "public String tag()")["line"],
        )

        res_inner = _safe_req(
            self,
            self.client,
            "textDocument/definition",
            {"textDocument": {"uri": nav_uri}, "position": pos_of(nav, "InnerHelper().readTitle()", offset=14)},
        )
        _assert_loc_matches(
            self,
            "definition(InnerHelper.readTitle())",
            res_inner,
            w_uri,
            expected_line=pos_of(w, "public String readTitle()")["line"],
        )

        res_anon_field = _safe_req(
            self,
            self.client,
            "textDocument/definition",
            {"textDocument": {"uri": nav_uri}, "position": pos_of(nav, "return prefix + target", offset=7)},
        )
        _assert_loc_matches(
            self,
            "definition(enclosing 'prefix' inside anonymous Greeter)",
            res_anon_field,
            nav_uri,
            expected_line=pos_of(nav, "private final String prefix")["line"],
        )

    def test_nav_17_references_include_declaration_true_vs_false(self) -> None:
        """[NAV-17] textDocument/references honors includeDeclaration=True vs False across files."""
        w = self._txt("Widget.java")
        w_uri = self._uri("Widget.java")
        nav_uri = self._uri("NavRunner.java")
        decl_pos = pos_of(w, "public String compute(int n)", offset=14)
        decl_line = decl_pos["line"]

        refs_inc = _safe_req(
            self,
            self.client,
            "textDocument/references",
            {
                "textDocument": {"uri": w_uri},
                "position": decl_pos,
                "context": {"includeDeclaration": True},
            },
        )
        locs_inc = extract_locations(refs_inc)
        self.assertTrue(
            any(location_uri(l) == nav_uri for l in locs_inc),
            f"references(Widget.compute(int), includeDeclaration=True) missing NavRunner.java call site; got {locs_inc!r}",
        )
        self.assertTrue(
            any(
                location_uri(l) == w_uri and (location_range(l) or {}).get("start", {}).get("line") == decl_line
                for l in locs_inc
            ),
            f"references(includeDeclaration=True) missing declaration at line {decl_line}; got {locs_inc!r}",
        )

        refs_exc = _safe_req(
            self,
            self.client,
            "textDocument/references",
            {
                "textDocument": {"uri": w_uri},
                "position": decl_pos,
                "context": {"includeDeclaration": False},
            },
        )
        locs_exc = extract_locations(refs_exc)
        self.assertTrue(
            any(location_uri(l) == nav_uri for l in locs_exc),
            f"references(includeDeclaration=False) missing NavRunner.java call site; got {locs_exc!r}",
        )
        self.assertFalse(
            any(
                location_uri(l) == w_uri and (location_range(l) or {}).get("start", {}).get("line") == decl_line
                for l in locs_exc
            ),
            f"references(includeDeclaration=False) still included declaration line {decl_line}; got {locs_exc!r}",
        )

    def test_nav_18_references_scoped_local_shadow_and_overloads(self) -> None:
        """[NAV-18] references on local 'shadow' in methodA() does not leak into methodB()."""
        nav = self._txt("NavRunner.java")
        nav_uri = self._uri("NavRunner.java")
        pos_a = pos_of(nav, "int shadow = 1;", offset=4)
        line_b_decl = pos_of(nav, "int shadow = 2;")["line"]
        line_b_use = pos_of(nav, "return shadow + 20;")["line"]

        refs = _safe_req(
            self,
            self.client,
            "textDocument/references",
            {
                "textDocument": {"uri": nav_uri},
                "position": pos_a,
                "context": {"includeDeclaration": True},
            },
        )
        locs = extract_locations(refs)
        self.assertTrue(locs, f"references on local 'shadow' in methodA returned {refs!r}")
        bad = [
            l
            for l in locs
            if (location_range(l) or {}).get("start", {}).get("line") in (line_b_decl, line_b_use)
        ]
        self.assertFalse(
            bad,
            f"references on local 'shadow' in methodA leaked into methodB lines ({line_b_decl}, {line_b_use}): {bad!r}",
        )

    def test_sym_01_document_symbol_hierarchy(self) -> None:
        """[SYM-01] textDocument/documentSymbol returns hierarchical DocumentSymbol tree with constructors, fields, methods, inner classes."""
        w_uri = self._uri("Widget.java")
        res = _safe_req(
            self,
            self.client,
            "textDocument/documentSymbol",
            {"textDocument": {"uri": w_uri}},
        )
        self.assertIsInstance(res, list)
        self.assertTrue(res, f"documentSymbol on Widget.java returned {res!r}")
        top = res[0]
        self.assertIn(
            "children",
            top,
            f"documentSymbol on Widget.java expected hierarchical DocumentSymbol with 'children', got {top!r}",
        )
        flat = flatten_document_symbols(res)
        names = [s.get("name", "") for s in flat]
        for expected in ("Widget", "title", "count", "describe", "compute", "StaticNested", "InnerHelper", "tag", "readTitle"):
            self.assertTrue(
                any(expected in n for n in names),
                f"documentSymbol on Widget.java missing {expected!r}; found names={names!r}",
            )
        # Validate nested hierarchy: StaticNested must contain tag.
        static_nested = [s for s in flat if s.get("name") == "StaticNested"]
        self.assertTrue(
            static_nested and any("tag" in c.get("name", "") for c in static_nested[0].get("children", [])),
            f"StaticNested expected child symbol 'tag', got {static_nested!r}",
        )

    def test_sym_02_document_symbol_record_and_enum(self) -> None:
        """[SYM-02] textDocument/documentSymbol captures record components/methods and enum constants/methods."""
        rec_uri = self._uri("RecordSample.java")
        rec_res = _safe_req(self, self.client, "textDocument/documentSymbol", {"textDocument": {"uri": rec_uri}})
        rec_names = [s.get("name", "") for s in flatten_document_symbols(rec_res)]
        self.assertTrue(
            any("Point" in n for n in rec_names) and any("sum" in n for n in rec_names),
            f"documentSymbol on record Point missing 'Point' or 'sum'; got {rec_names!r}",
        )

        col_uri = self._uri("Color.java")
        col_res = _safe_req(self, self.client, "textDocument/documentSymbol", {"textDocument": {"uri": col_uri}})
        col_names = [s.get("name", "") for s in flatten_document_symbols(col_res)]
        for expected in ("Color", "RED", "GREEN", "CRIMSON", "getCode"):
            self.assertTrue(
                any(expected in n for n in col_names),
                f"documentSymbol on enum Color missing {expected!r}; got {col_names!r}",
            )

    def test_sym_03_workspace_symbol_search(self) -> None:
        """[SYM-03] workspace/symbol finds project classes and methods by query."""
        res = _safe_req(self, self.client, "workspace/symbol", {"query": "Widget"})
        self.assertIsInstance(res, list, f"workspace/symbol expected list, got {res!r}")
        names = [s.get("name", "") for s in res if isinstance(s, dict)]
        self.assertTrue(
            any("Widget" in n for n in names),
            f"workspace/symbol(query='Widget') returned {res!r}; expected Widget symbol",
        )

    def test_sym_04_document_highlight(self) -> None:
        """[SYM-04] textDocument/documentHighlight highlights all occurrences of local variable in file."""
        nav = self._txt("NavRunner.java")
        nav_uri = self._uri("NavRunner.java")
        res = _safe_req(
            self,
            self.client,
            "textDocument/documentHighlight",
            {"textDocument": {"uri": nav_uri}, "position": pos_of(nav, "localVal", occurrence=1)},
        )
        self.assertIsInstance(res, list, f"documentHighlight expected list, got {res!r}")
        self.assertGreaterEqual(
            len(res),
            2,
            f"documentHighlight on 'localVal' expected >= 2 highlights (decl + use), got {res!r}",
        )

    def test_sym_05_folding_range(self) -> None:
        """[SYM-05] textDocument/foldingRange returns folding ranges for class and methods."""
        w_uri = self._uri("Widget.java")
        res = _safe_req(self, self.client, "textDocument/foldingRange", {"textDocument": {"uri": w_uri}})
        self.assertIsInstance(res, list, f"foldingRange expected list, got {res!r}")
        self.assertGreaterEqual(
            len(res),
            5,
            f"foldingRange on Widget.java expected >= 5 foldable ranges, got {res!r}",
        )

    def test_sym_06_selection_range(self) -> None:
        """[SYM-06] textDocument/selectionRange returns nested SelectionRange chain around identifier."""
        nav = self._txt("NavRunner.java")
        nav_uri = self._uri("NavRunner.java")
        res = _safe_req(
            self,
            self.client,
            "textDocument/selectionRange",
            {"textDocument": {"uri": nav_uri}, "positions": [pos_of(nav, "localVal", occurrence=2)]},
        )
        self.assertIsInstance(res, list, f"selectionRange expected list, got {res!r}")
        self.assertTrue(res and isinstance(res[0], dict), f"selectionRange expected non-empty list of dicts, got {res!r}")
        self.assertIn("parent", res[0], f"selectionRange expected nested 'parent' chain, got {res[0]!r}")

    def test_sym_07_semantic_tokens_full(self) -> None:
        """[SYM-07] textDocument/semanticTokens/full returns valid delta-encoded 5-tuple integer array."""
        w_uri = self._uri("Widget.java")
        res = _safe_req(self, self.client, "textDocument/semanticTokens/full", {"textDocument": {"uri": w_uri}})
        self.assertIsInstance(res, dict, f"semanticTokens/full expected dict, got {res!r}")
        data = res.get("data")
        self.assertTrue(
            isinstance(data, list) and len(data) > 0 and len(data) % 5 == 0,
            f"semanticTokens/full expected non-empty data list with len % 5 == 0, got {res!r}",
        )

    def test_sym_08_inlay_hint(self) -> None:
        """[SYM-08] textDocument/inlayHint returns parameter or inferred type hints in range."""
        nav = self._txt("NavRunner.java")
        nav_uri = self._uri("NavRunner.java")
        full_rng = {"start": {"line": 0, "character": 0}, "end": {"line": len(nav.splitlines()), "character": 0}}
        res = _safe_req(
            self,
            self.client,
            "textDocument/inlayHint",
            {"textDocument": {"uri": nav_uri}, "range": full_rng},
        )
        self.assertIsInstance(res, list, f"inlayHint expected list, got {res!r}")
        self.assertTrue(res, f"inlayHint on NavRunner.java expected non-empty hints, got {res!r}")

    def test_sym_09_call_hierarchy(self) -> None:
        """[SYM-09] prepareCallHierarchy + incomingCalls/outgoingCalls trace Widget.describe()."""
        w = self._txt("Widget.java")
        w_uri = self._uri("Widget.java")
        items = _safe_req(
            self,
            self.client,
            "textDocument/prepareCallHierarchy",
            {"textDocument": {"uri": w_uri}, "position": pos_of(w, "public String describe()", offset=14)},
        )
        self.assertTrue(
            isinstance(items, list) and len(items) > 0,
            f"prepareCallHierarchy on Widget.describe() returned {items!r}; expected CallHierarchyItem[]",
        )
        incoming = _safe_req(self, self.client, "callHierarchy/incomingCalls", {"item": items[0]})
        self.assertTrue(
            isinstance(incoming, list) and len(incoming) > 0,
            f"callHierarchy/incomingCalls for Widget.describe() returned {incoming!r}",
        )
        outgoing = _safe_req(self, self.client, "callHierarchy/outgoingCalls", {"item": items[0]})
        self.assertTrue(
            isinstance(outgoing, list) and len(outgoing) > 0,
            f"callHierarchy/outgoingCalls for Widget.describe() returned {outgoing!r}",
        )

    def test_sym_10_type_hierarchy(self) -> None:
        """[SYM-10] prepareTypeHierarchy + supertypes/subtypes trace BaseComponent <-> Widget."""
        w = self._txt("Widget.java")
        w_uri = self._uri("Widget.java")
        items = _safe_req(
            self,
            self.client,
            "textDocument/prepareTypeHierarchy",
            {"textDocument": {"uri": w_uri}, "position": pos_of(w, "class Widget", offset=6)},
        )
        self.assertTrue(
            isinstance(items, list) and len(items) > 0,
            f"prepareTypeHierarchy on Widget returned {items!r}; expected TypeHierarchyItem[]",
        )
        supers = _safe_req(self, self.client, "typeHierarchy/supertypes", {"item": items[0]})
        super_names = [s.get("name", "") for s in (supers or []) if isinstance(s, dict)]
        self.assertTrue(
            any("BaseComponent" in n or "Greeter" in n for n in super_names),
            f"typeHierarchy/supertypes for Widget expected BaseComponent/Greeter, got {supers!r}",
        )

    def test_sym_11_code_lens(self) -> None:
        """[SYM-11] textDocument/codeLens returns CodeLens entries for class/methods."""
        w_uri = self._uri("Widget.java")
        res = _safe_req(self, self.client, "textDocument/codeLens", {"textDocument": {"uri": w_uri}})
        self.assertIsInstance(res, list, f"codeLens expected list, got {res!r}")
        self.assertTrue(res, f"codeLens on Widget.java returned empty list {res!r}")


class TestSpringDemoNavigationAndDependencies(unittest.TestCase):
    """Cross-module, interface/impl, Lombok, JDK stdlib, and Maven dep navigation on isolated java17-spring-demo."""

    project: TempJavaProject
    client: LspClient
    bridge_rel = "core/src/main/java/com/example/demo/controller/FeignBridgeProbe.java"
    bridge_src = (
        "package com.example.demo.controller;\n\n"
        "import com.example.demo.api.UserFeignClient;\n\n"
        "public class FeignBridgeProbe {\n"
        "  private UserFeignClient feignClient;\n\n"
        "  public String fetch(Long id) {\n"
        "    return feignClient.getUserName(id);\n"
        "  }\n"
        "}\n"
    )

    @classmethod
    def setUpClass(cls) -> None:
        cls.project = TempJavaProject(prefix="java_lsp_nav_spring_")
        try:
            cls.project.copy_from(SPRING_DEMO_DIR)
            # Remove any pre-existing .classpath_m2.txt so this isolated copy generates fresh paths.
            for cp in cls.project.root.rglob(".classpath_m2.txt"):
                cp.unlink(missing_ok=True)
            cls.project.write_file(cls.bridge_rel, cls.bridge_src)

            cls.client = LspClient(root_path=cls.project.root, default_timeout=25.0)
            cls.client.initialize_and_ready(timeout=60.0)
            cls._open_demo_files()
        except Exception:
            if hasattr(cls, "client"):
                cls.client.close()
            cls.project.close()
            raise

    @classmethod
    def _open_demo_files(cls) -> None:
        for rel in (
            "api/src/main/java/com/example/demo/api/UserFeignClient.java",
            "core/src/main/java/com/example/demo/controller/UserController.java",
            "core/src/main/java/com/example/demo/service/UserService.java",
            "core/src/main/java/com/example/demo/service/impl/UserServiceImpl.java",
            "core/src/main/java/com/example/demo/model/User.java",
            "core/src/main/java/com/example/demo/mapper/UserMapper.java",
            "core/src/test/java/com/example/demo/service/UserServiceSmokeTest.java",
            cls.bridge_rel,
        ):
            cls.client.did_open(cls.project.uri(rel), cls.project.read_file(rel))

    @classmethod
    def tearDownClass(cls) -> None:
        try:
            cls.client.close()
        finally:
            cls.project.close()

    def setUp(self) -> None:
        if not self.client.is_alive():
            self.client.restart(clean_storage=False, clear_history=True)
            self.client.initialize_and_ready(timeout=60.0)
            self._open_demo_files()

    def test_nav_11_cross_file_interface_definition(self) -> None:
        """[NAV-11] definition on userService.listUsers() in UserController resolves to UserService/UserServiceImpl."""
        ctrl_rel = "core/src/main/java/com/example/demo/controller/UserController.java"
        svc_rel = "core/src/main/java/com/example/demo/service/UserService.java"
        impl_rel = "core/src/main/java/com/example/demo/service/impl/UserServiceImpl.java"
        ctrl_txt = self.project.read_file(ctrl_rel)
        ctrl_uri = self.project.uri(ctrl_rel)
        svc_uri = self.project.uri(svc_rel)
        impl_uri = self.project.uri(impl_rel)

        def_res = _safe_req(
            self,
            self.client,
            "textDocument/definition",
            {
                "textDocument": {"uri": ctrl_uri},
                "position": pos_of(ctrl_txt, "userService.listUsers()", offset=12),
            },
        )
        def_locs = extract_locations(def_res)
        self.assertTrue(
            any(location_uri(l) in (svc_uri, impl_uri) for l in def_locs),
            f"definition(userService.listUsers()) expected {svc_uri} or {impl_uri}, got {def_res!r}",
        )

    def test_nav_20_interface_implementation_navigation(self) -> None:
        """[NAV-20] textDocument/implementation on UserService interface & method jumps to UserServiceImpl."""
        svc_rel = "core/src/main/java/com/example/demo/service/UserService.java"
        impl_rel = "core/src/main/java/com/example/demo/service/impl/UserServiceImpl.java"
        svc_txt = self.project.read_file(svc_rel)
        impl_txt = self.project.read_file(impl_rel)
        svc_uri = self.project.uri(svc_rel)
        impl_uri = self.project.uri(impl_rel)

        impl_res = _safe_req(
            self,
            self.client,
            "textDocument/implementation",
            {
                "textDocument": {"uri": svc_uri},
                "position": pos_of(svc_txt, "listUsers"),
            },
        )
        _assert_loc_matches(
            self,
            "implementation(UserService.listUsers)",
            impl_res,
            impl_uri,
            expected_line=pos_of(impl_txt, "public List<User> listUsers()")["line"],
        )

        impl_cls = _safe_req(
            self,
            self.client,
            "textDocument/implementation",
            {
                "textDocument": {"uri": svc_uri},
                "position": pos_of(svc_txt, "interface UserService", offset=10),
            },
        )
        _assert_loc_matches(
            self,
            "implementation(interface UserService)",
            impl_cls,
            impl_uri,
            expected_line=pos_of(impl_txt, "class UserServiceImpl")["line"],
        )

    def test_nav_12_cross_module_api_core_navigation(self) -> None:
        """[NAV-12] Cross-module core <-> api navigation (FeignBridgeProbe <-> UserFeignClient)."""
        api_rel = "api/src/main/java/com/example/demo/api/UserFeignClient.java"
        api_txt = self.project.read_file(api_rel)
        api_uri = self.project.uri(api_rel)
        bridge_uri = self.project.uri(self.bridge_rel)

        # core -> api definition on UserFeignClient type and getUserName method
        res_type = _safe_req(
            self,
            self.client,
            "textDocument/definition",
            {
                "textDocument": {"uri": bridge_uri},
                "position": pos_of(self.bridge_src, "UserFeignClient feignClient"),
            },
        )
        _assert_loc_matches(
            self,
            "cross-module definition(UserFeignClient)",
            res_type,
            api_uri,
            expected_line=pos_of(api_txt, "interface UserFeignClient")["line"],
        )

        res_method = _safe_req(
            self,
            self.client,
            "textDocument/definition",
            {
                "textDocument": {"uri": bridge_uri},
                "position": pos_of(self.bridge_src, "feignClient.getUserName(id)", offset=12),
            },
        )
        _assert_loc_matches(
            self,
            "cross-module definition(UserFeignClient.getUserName)",
            res_method,
            api_uri,
            expected_line=pos_of(api_txt, "getUserName")["line"],
        )

        # api -> core references from UserFeignClient.getUserName
        refs = _safe_req(
            self,
            self.client,
            "textDocument/references",
            {
                "textDocument": {"uri": api_uri},
                "position": pos_of(api_txt, "getUserName"),
                "context": {"includeDeclaration": False},
            },
        )
        ref_locs = extract_locations(refs)
        self.assertTrue(
            any(location_uri(l) == bridge_uri for l in ref_locs),
            f"cross-module references(UserFeignClient.getUserName) missing core usage in {bridge_uri}; got {refs!r}",
        )

    def test_nav_13_type_definition_on_variables_and_methods(self) -> None:
        """[NAV-13] textDocument/typeDefinition on userService.listUsers() -> List.java and User -> User.java."""
        ctrl_rel = "core/src/main/java/com/example/demo/controller/UserController.java"
        user_rel = "core/src/main/java/com/example/demo/model/User.java"
        ctrl_txt = self.project.read_file(ctrl_rel)
        ctrl_uri = self.project.uri(ctrl_rel)
        user_uri = self.project.uri(user_rel)

        # userService.listUsers() -> List.java
        td_list = _safe_req(
            self,
            self.client,
            "textDocument/typeDefinition",
            {
                "textDocument": {"uri": ctrl_uri},
                "position": pos_of(ctrl_txt, "userService.listUsers()", offset=12),
            },
        )
        locs_list = extract_locations(td_list)
        self.assertTrue(
            locs_list
            and (location_uri(locs_list[0]) or "").endswith("/List.java")
            and uri_exists_on_disk(location_uri(locs_list[0]) or ""),
            f"typeDefinition(userService.listUsers()) expected existing List.java URI, got {td_list!r}",
        )

        # request parameter in createUser -> CreateUserRequest.java
        req_uri = self.project.uri("core/src/main/java/com/example/demo/model/CreateUserRequest.java")
        td_req = _safe_req(
            self,
            self.client,
            "textDocument/typeDefinition",
            {
                "textDocument": {"uri": ctrl_uri},
                "position": pos_of(ctrl_txt, "request.getEmail()"),
            },
        )
        _assert_loc_matches(self, "typeDefinition(request)", td_req, req_uri)

        # User variable in UserServiceSmokeTest -> User.java
        smoke_rel = "core/src/test/java/com/example/demo/service/UserServiceSmokeTest.java"
        smoke_txt = self.project.read_file(smoke_rel)
        smoke_uri = self.project.uri(smoke_rel)
        td_users = _safe_req(
            self,
            self.client,
            "textDocument/typeDefinition",
            {
                "textDocument": {"uri": smoke_uri},
                "position": pos_of(smoke_txt, "assertThat(users)", offset=11),
            },
        )
        locs_u = extract_locations(td_users)
        self.assertTrue(
            locs_u and ((location_uri(locs_u[0]) or "").endswith("/List.java") or location_uri(locs_u[0]) == user_uri),
            f"typeDefinition(users) expected List.java (or User.java), got {td_users!r}",
        )

    def test_nav_14_lombok_slf4j_declaration_and_implementation(self) -> None:
        """[NAV-14] Lombok @Slf4j log: declaration -> org.slf4j.Logger.java; implementation -> logback Logger.java."""
        ctrl_rel = "core/src/main/java/com/example/demo/controller/UserController.java"
        ctrl_txt = self.project.read_file(ctrl_rel)
        ctrl_uri = self.project.uri(ctrl_rel)
        log_pos = pos_of(ctrl_txt, "log.info(")

        decl = _safe_req(
            self,
            self.client,
            "textDocument/declaration",
            {"textDocument": {"uri": ctrl_uri}, "position": log_pos},
        )
        decl_locs = extract_locations(decl)
        self.assertTrue(
            decl_locs
            and "slf4j" in (location_uri(decl_locs[0]) or "").lower()
            and (location_uri(decl_locs[0]) or "").endswith("Logger.java")
            and uri_exists_on_disk(location_uri(decl_locs[0]) or ""),
            f"declaration(@Slf4j log) expected existing org/slf4j/.../Logger.java, got {decl!r}",
        )

        impl = _safe_req(
            self,
            self.client,
            "textDocument/implementation",
            {"textDocument": {"uri": ctrl_uri}, "position": log_pos},
        )
        impl_locs = extract_locations(impl)
        self.assertTrue(
            impl_locs
            and "logback" in (location_uri(impl_locs[0]) or "").lower()
            and (location_uri(impl_locs[0]) or "").endswith("Logger.java")
            and uri_exists_on_disk(location_uri(impl_locs[0]) or ""),
            f"implementation(@Slf4j log) expected existing ch/qos/logback/.../Logger.java, got {impl!r}",
        )

    def test_nav_15_jdk_stdlib_source_navigation(self) -> None:
        """[NAV-15] definition on JDK types (List, String, RuntimeException) jumps to extracted JDK .java sources."""
        ctrl_rel = "core/src/main/java/com/example/demo/controller/UserController.java"
        ctrl_txt = self.project.read_file(ctrl_rel)
        ctrl_uri = self.project.uri(ctrl_rel)

        cases = [
            ("List<User>", "List.java"),
            ("RuntimeException", "RuntimeException.java"),
            ("String.format", "String.java"),
        ]
        failures: list[str] = []
        for anchor, expected_file in cases:
            res = _safe_req(
                self,
                self.client,
                "textDocument/definition",
                {"textDocument": {"uri": ctrl_uri}, "position": pos_of(ctrl_txt, anchor)},
            )
            locs = extract_locations(res)
            if not locs:
                failures.append(f"definition(JDK {anchor}) returned {res!r}; expected {expected_file}")
                continue
            u = location_uri(locs[0]) or ""
            if not (u.endswith(f"/{expected_file}") and uri_exists_on_disk(u)):
                failures.append(f"definition(JDK {anchor}) returned URI {u!r}; expected existing {expected_file}")
                continue
            text = uri_to_path(u).read_text(encoding="utf-8", errors="replace")
            if len(text) <= 100:
                failures.append(f"Extracted JDK source {u} is unexpectedly empty")
        self.assertFalse(failures, "; ".join(failures))

    def test_nav_16_maven_dependency_source_or_decompiled_navigation(self) -> None:
        """[NAV-16] definition on Maven deps (@RestController, @GetMapping, @Param, @Data) jumps to sources/CFR .java."""
        ctrl_rel = "core/src/main/java/com/example/demo/controller/UserController.java"
        mapper_rel = "core/src/main/java/com/example/demo/mapper/UserMapper.java"
        user_rel = "core/src/main/java/com/example/demo/model/User.java"
        ctrl_txt = self.project.read_file(ctrl_rel)
        mapper_txt = self.project.read_file(mapper_rel)
        user_txt = self.project.read_file(user_rel)

        checks = [
            (self.project.uri(ctrl_rel), pos_of(ctrl_txt, "@RestController", offset=1), "RestController.java"),
            (self.project.uri(ctrl_rel), pos_of(ctrl_txt, "@GetMapping", offset=1), "GetMapping.java"),
            (self.project.uri(mapper_rel), pos_of(mapper_txt, "@Param", offset=1), "Param.java"),
            (self.project.uri(user_rel), pos_of(user_txt, "@Data", offset=1), "Data.java"),
        ]
        failures: list[str] = []
        for doc_uri, pos, expected_name in checks:
            res = _safe_req(
                self,
                self.client,
                "textDocument/definition",
                {"textDocument": {"uri": doc_uri}, "position": pos},
            )
            locs = extract_locations(res)
            if not locs:
                failures.append(f"definition(Maven {expected_name} at {pos}) returned {res!r}")
                continue
            u = location_uri(locs[0]) or ""
            if not (u.endswith(f"/{expected_name}") and uri_exists_on_disk(u)):
                failures.append(f"definition(Maven {expected_name}) returned URI {u!r}; expected {expected_name}")
        self.assertFalse(failures, "; ".join(failures))

    def test_nav_19_cross_file_references_spring_demo(self) -> None:
        """[NAV-19] textDocument/references on UserService.getUser finds UserController and UserServiceImpl."""
        svc_rel = "core/src/main/java/com/example/demo/service/UserService.java"
        ctrl_rel = "core/src/main/java/com/example/demo/controller/UserController.java"
        impl_rel = "core/src/main/java/com/example/demo/service/impl/UserServiceImpl.java"
        svc_txt = self.project.read_file(svc_rel)
        svc_uri = self.project.uri(svc_rel)
        ctrl_uri = self.project.uri(ctrl_rel)
        impl_uri = self.project.uri(impl_rel)

        refs = _safe_req(
            self,
            self.client,
            "textDocument/references",
            {
                "textDocument": {"uri": svc_uri},
                "position": pos_of(svc_txt, "getUser"),
                "context": {"includeDeclaration": True},
            },
        )
        locs = extract_locations(refs)
        uris = {location_uri(l) for l in locs}
        self.assertIn(
            ctrl_uri,
            uris,
            f"references(UserService.getUser) missing call site in UserController.java; got {locs!r}",
        )
        self.assertIn(
            impl_uri,
            uris,
            f"references(UserService.getUser) missing implementation in UserServiceImpl.java; got {locs!r}",
        )


if __name__ == "__main__":
    unittest.main()
