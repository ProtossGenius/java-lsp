"""Black-box LSP 3.17 hover and signatureHelp tests."""

from __future__ import annotations

import unittest
from typing import Any

from tests.blackbox.lsp_client import (
    REPO_ROOT,
    LspClient,
    LspClientError,
    LspResponseError,
    ServerCrashedError,
    TempJavaProject,
    after_of,
    extract_hover_text,
    pos_of,
    utf16_len,
)

SPRING_DEMO_DIR = REPO_ROOT / "java17-spring-demo"


def _minimal_pom(artifact: str = "hover-sig-demo") -> str:
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


def _assert_valid_signature_help(
    case: unittest.TestCase,
    label: str,
    result: Any,
    expected_param_index: int | None = None,
    min_params: int | None = None,
    expected_name: str | None = None,
) -> dict[str, Any]:
    case.assertIsInstance(
        result,
        dict,
        f"{label}: signatureHelp expected dict response, got {result!r}",
    )
    sigs = result.get("signatures")
    case.assertTrue(
        isinstance(sigs, list) and len(sigs) > 0,
        f"{label}: expected non-empty signatures list, got {result!r}",
    )
    active_sig_idx = result.get("activeSignature", 0)
    case.assertTrue(
        isinstance(active_sig_idx, int) and 0 <= active_sig_idx < len(sigs),
        f"{label}: activeSignature {active_sig_idx!r} out of range for {len(sigs)} signatures: {result!r}",
    )
    active_sig = sigs[active_sig_idx]
    sig_label = active_sig.get("label", "")
    if expected_name is not None:
        case.assertIn(
            expected_name,
            sig_label,
            f"{label}: active signature {sig_label!r} does not match expected {expected_name!r} (all={sigs!r})",
        )
    params = active_sig.get("parameters") or []
    if min_params is not None:
        case.assertGreaterEqual(
            len(params),
            min_params,
            f"{label}: active signature {sig_label!r} has only {len(params)} params, expected >= {min_params}",
        )
    # Validate activeParameter (either top-level or on activeSignature per LSP 3.17).
    active_param = result.get("activeParameter")
    if active_param is None:
        active_param = active_sig.get("activeParameter")
    if expected_param_index is not None:
        case.assertEqual(
            active_param,
            expected_param_index,
            f"{label}: expected activeParameter={expected_param_index}, got {active_param!r} in {result!r}",
        )
    if params and active_param is not None:
        case.assertTrue(
            isinstance(active_param, int) and 0 <= active_param < len(params),
            f"{label}: activeParameter {active_param!r} out of bounds for params={params!r}",
        )
    # Validate ParameterInformation labels against SignatureInformation.label.
    sig_u16_len = utf16_len(sig_label)
    for p in params:
        p_label = p.get("label")
        if isinstance(p_label, str):
            case.assertTrue(
                p_label and p_label in sig_label,
                f"{label}: parameter label {p_label!r} not found in signature label {sig_label!r}",
            )
        elif isinstance(p_label, list) and len(p_label) == 2:
            start, end = p_label
            case.assertTrue(
                isinstance(start, int) and isinstance(end, int) and 0 <= start < end <= sig_u16_len,
                f"{label}: parameter offset pair {p_label!r} invalid for sig_label {sig_label!r} (u16_len={sig_u16_len})",
            )
        else:
            case.fail(f"{label}: invalid parameter label format {p_label!r} in {active_sig!r}")
    return result


SYNTH_FILES: dict[str, str] = {
    "pom.xml": _minimal_pom("hover-sig-synth"),
    "src/main/java/com/acme/hs/Marker.java": (
        "package com.acme.hs;\n\n"
        "import java.lang.annotation.Retention;\n"
        "import java.lang.annotation.RetentionPolicy;\n\n"
        "/** Marks sample elements. */\n"
        "@Retention(RetentionPolicy.RUNTIME)\n"
        "public @interface Marker {\n"
        "  String tag() default \"default\";\n"
        "}\n"
    ),
    "src/main/java/com/acme/hs/Person.java": (
        "package com.acme.hs;\n\n"
        "/** Represents a registered person. */\n"
        "public class Person {\n"
        "  private final String fullName;\n"
        "  private final int age;\n\n"
        "  /**\n"
        "   * Creates a new Person.\n"
        "   * @param fullName display name\n"
        "   * @param age age in years\n"
        "   */\n"
        "  public Person(String fullName, int age) {\n"
        "    this.fullName = fullName;\n"
        "    this.age = age;\n"
        "  }\n\n"
        "  public String getFullName() {\n"
        "    return fullName;\n"
        "  }\n\n"
        "  public int getAge() {\n"
        "    return age;\n"
        "  }\n"
        "}\n"
    ),
    "src/main/java/com/acme/hs/Showcase.java": (
        "package com.acme.hs;\n\n"
        "import java.util.HashMap;\n"
        "import java.util.List;\n"
        "import java.util.Map;\n"
        "import java.util.function.BiFunction;\n\n"
        "@Marker(tag = \"showcase\")\n"
        "public class Showcase {\n"
        "  /** Maximum allowed count. */\n"
        "  public static final int MAX_LIMIT = 99;\n"
        "  private final String prefix = \"Hi\";\n\n"
        "  /**\n"
        "   * Formats a greeting for the target.\n"
        "   * @param target recipient name\n"
        "   * @param times number of repetitions\n"
        "   * @return formatted greeting string\n"
        "   * @throws IllegalArgumentException if times is non-positive\n"
        "   */\n"
        "  public String greet(String target, int times) throws IllegalArgumentException {\n"
        "    if (times <= 0) {\n"
        "      throw new IllegalArgumentException(\"bad times\");\n"
        "    }\n"
        "    return prefix + \" \" + target + \" x\" + times;\n"
        "  }\n\n"
        "  public String format(String text) {\n"
        "    return text;\n"
        "  }\n\n"
        "  public String format(String text, int width) {\n"
        "    return text + width;\n"
        "  }\n\n"
        "  public String format(String text, int width, boolean upper) {\n"
        "    return upper ? text.toUpperCase() : text;\n"
        "  }\n\n"
        "  public String joinAll(String sep, String... items) {\n"
        "    return String.join(sep, items);\n"
        "  }\n\n"
        "  public int pair(int left, int right) {\n"
        "    return left + right;\n"
        "  }\n\n"
        "  public int outer(int val, String tag) {\n"
        "    return val + tag.length();\n"
        "  }\n\n"
        "  public String takeThree(Object first, int second, boolean flag) {\n"
        "    return String.valueOf(first) + second + flag;\n"
        "  }\n\n"
        "  public String processWithLambda(List<Person> people, BiFunction<Person, Person, String> fn, boolean active) {\n"
        "    return fn.apply(people.get(0), people.get(0)) + active;\n"
        "  }\n\n"
        "  public String takeGeneric(Map<String, Integer> counts, int fallback) {\n"
        "    return counts.size() + \":\" + fallback;\n"
        "  }\n\n"
        "  public void exercise() {\n"
        "    int localCounter = 42;\n"
        "    Person ada = new Person(\"Ada\", 30);\n"
        "    List<Person> people = List.of(ada);\n"
        "    Person firstPerson = people.get(0);\n"
        "    var inferredList = List.of(\"alpha\", \"beta\");\n"
        "    var inferredPerson = new Person(\"Grace\", 40);\n"
        "    for (var loopPerson : people) {\n"
        "      loopPerson.getFullName();\n"
        "    }\n"
        "    List<String> tags = List.of(\"Ada\");\n"
        "    boolean any = people.stream().filter(p -> tags.stream().anyMatch(t -> t.equals(p.getFullName()))).count() > 0;\n"
        "    String g = greet(\"World\", localCounter);\n"
        "    String f1 = format(\"one\");\n"
        "    String f2 = format(\"two\", 12);\n"
        "    String f3 = format(\"three\", 12, true);\n"
        "    String j = joinAll(\",\", \"a\", \"b\", \"c\");\n"
        "    int nested = outer(pair(10, 20), \"ok\");\n"
        "    String sComma = takeThree(\"a, b, c\", 42, true);\n"
        "    String pComma = takeThree(pair(1, 2), 42, true);\n"
        "    String lComma = processWithLambda(people, (a, b) -> a.getFullName() + b.getAge(), true);\n"
        "    String gComma = takeGeneric(new HashMap<String, Integer>(), 99);\n"
        "    System.out.println(firstPerson.getFullName() + inferredList.size() + inferredPerson.getAge() + any + g + f1 + f2 + f3 + j + nested + sComma + pComma + lComma + gComma + MAX_LIMIT);\n"
        "  }\n"
        "}\n"
    ),
}


class TestSyntheticHoverAndSignatureHelp(unittest.TestCase):
    """[HOVER/SIG] Hover and signatureHelp tests on synthetic Java 17 fixtures."""

    project: TempJavaProject
    client: LspClient

    @classmethod
    def setUpClass(cls) -> None:
        cls.project = TempJavaProject(files=SYNTH_FILES, create_root_marker=True)
        cls.client = LspClient(root_path=cls.project.root, default_timeout=20.0)
        try:
            cls.client.initialize_and_ready(timeout=60.0)
            for rel, content in SYNTH_FILES.items():
                if rel.endswith(".java"):
                    cls.client.did_open(cls.project.uri(rel), content)
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
            for rel, content in SYNTH_FILES.items():
                if rel.endswith(".java"):
                    self.client.did_open(self.project.uri(rel), content)

    def _uri(self, name: str) -> str:
        return self.project.uri(f"src/main/java/com/acme/hs/{name}")

    def _txt(self, name: str) -> str:
        return SYNTH_FILES[f"src/main/java/com/acme/hs/{name}"]

    def test_hover_01_local_variable(self) -> None:
        """[HOVER-01] hover on local variable displays type and identifier."""
        src = self._txt("Showcase.java")
        uri = self._uri("Showcase.java")
        res = _safe_req(
            self,
            self.client,
            "textDocument/hover",
            {"textDocument": {"uri": uri}, "position": pos_of(src, "localCounter", occurrence=2)},
        )
        txt = extract_hover_text(res)
        self.assertTrue(
            "int" in txt and "localCounter" in txt,
            f"hover(localCounter) expected 'int' and 'localCounter', got {res!r}",
        )

    def test_hover_02_field_and_constant_with_javadoc(self) -> None:
        """[HOVER-02] hover on field and static final constant shows type, modifiers, and Javadoc."""
        src = self._txt("Showcase.java")
        uri = self._uri("Showcase.java")
        res_const = _safe_req(
            self,
            self.client,
            "textDocument/hover",
            {"textDocument": {"uri": uri}, "position": pos_of(src, "MAX_LIMIT", occurrence=2)},
        )
        txt_const = extract_hover_text(res_const)
        self.assertIn("int", txt_const, f"hover(MAX_LIMIT) expected 'int', got {res_const!r}")
        self.assertIn(
            "Maximum allowed count",
            txt_const,
            f"hover(MAX_LIMIT) expected Javadoc 'Maximum allowed count', got {res_const!r}",
        )

        res_field = _safe_req(
            self,
            self.client,
            "textDocument/hover",
            {"textDocument": {"uri": uri}, "position": pos_of(src, "prefix", occurrence=2)},
        )
        txt_field = extract_hover_text(res_field)
        self.assertIn("String", txt_field, f"hover(prefix) expected 'String', got {res_field!r}")

    def test_hover_03_method_signature_and_javadoc_tags(self) -> None:
        """[HOVER-03] hover on method shows signature plus Javadoc @param/@return/@throws."""
        src = self._txt("Showcase.java")
        uri = self._uri("Showcase.java")
        res = _safe_req(
            self,
            self.client,
            "textDocument/hover",
            {"textDocument": {"uri": uri}, "position": pos_of(src, 'greet("World"')},
        )
        txt = extract_hover_text(res)
        for token in ("String", "greet", "target", "times", "Formats a greeting", "recipient name", "formatted greeting", "IllegalArgumentException"):
            self.assertIn(
                token,
                txt,
                f"hover(greet) missing {token!r} in hover text; actual={txt!r}",
            )

    def test_hover_04_class_and_javadoc(self) -> None:
        """[HOVER-04] hover on class usage displays class signature and Javadoc."""
        src = self._txt("Showcase.java")
        uri = self._uri("Showcase.java")
        res = _safe_req(
            self,
            self.client,
            "textDocument/hover",
            {"textDocument": {"uri": uri}, "position": pos_of(src, "Person ada")},
        )
        txt = extract_hover_text(res)
        self.assertIn("Person", txt, f"hover(Person) expected 'Person', got {res!r}")
        self.assertIn(
            "registered person",
            txt,
            f"hover(Person) expected class Javadoc 'Represents a registered person', got {res!r}",
        )

    def test_hover_05_constructor_with_javadoc(self) -> None:
        """[HOVER-05] hover on constructor call 'new Person(...)' shows constructor signature and @param docs."""
        src = self._txt("Showcase.java")
        uri = self._uri("Showcase.java")
        res = _safe_req(
            self,
            self.client,
            "textDocument/hover",
            {"textDocument": {"uri": uri}, "position": pos_of(src, 'new Person("Ada"', offset=4)},
        )
        txt = extract_hover_text(res)
        self.assertIn("Person", txt, f"hover(new Person) expected 'Person', got {res!r}")
        self.assertIn("fullName", txt, f"hover(new Person) expected parameter 'fullName', got {res!r}")

    def test_hover_06_generics_instantiation_substitutes_type_parameter(self) -> None:
        """[HOVER-06] hover on List<Person>.get(0) shows substituted 'Person', not raw type variable 'E'."""
        src = self._txt("Showcase.java")
        uri = self._uri("Showcase.java")
        res_get = _safe_req(
            self,
            self.client,
            "textDocument/hover",
            {"textDocument": {"uri": uri}, "position": pos_of(src, "people.get(0)", offset=7)},
        )
        txt_get = extract_hover_text(res_get)
        self.assertIn(
            "Person",
            txt_get,
            f"hover(people.get(0)) expected substituted return type 'Person' instead of 'E', got {res_get!r}",
        )

        res_var = _safe_req(
            self,
            self.client,
            "textDocument/hover",
            {"textDocument": {"uri": uri}, "position": pos_of(src, "people.get(0)")},
        )
        txt_var = extract_hover_text(res_var)
        self.assertIn("List", txt_var, f"hover(people) expected 'List<Person>', got {res_var!r}")
        self.assertIn("Person", txt_var, f"hover(people) expected 'List<Person>', got {res_var!r}")

    def test_hover_07_var_local_type_inference(self) -> None:
        """[HOVER-07] hover on 'var' inferred locals and enhanced-for 'var' shows inferred concrete types."""
        src = self._txt("Showcase.java")
        uri = self._uri("Showcase.java")

        r1 = _safe_req(
            self,
            self.client,
            "textDocument/hover",
            {"textDocument": {"uri": uri}, "position": pos_of(src, "inferredList.size()")},
        )
        t1 = extract_hover_text(r1)
        self.assertTrue(
            "List" in t1 and "String" in t1,
            f"hover(var inferredList) expected 'List<String>', got {r1!r}",
        )

        r2 = _safe_req(
            self,
            self.client,
            "textDocument/hover",
            {"textDocument": {"uri": uri}, "position": pos_of(src, "inferredPerson.getAge()")},
        )
        self.assertIn("Person", extract_hover_text(r2), f"hover(var inferredPerson) expected 'Person', got {r2!r}")

        r3 = _safe_req(
            self,
            self.client,
            "textDocument/hover",
            {"textDocument": {"uri": uri}, "position": pos_of(src, "loopPerson.getFullName()")},
        )
        self.assertIn("Person", extract_hover_text(r3), f"hover(for var loopPerson) expected 'Person', got {r3!r}")

    def test_hover_08_nested_lambda_parameters(self) -> None:
        """[HOVER-08] hover on outer and inner lambda parameters 'p' and 't' shows inferred element types."""
        src = self._txt("Showcase.java")
        uri = self._uri("Showcase.java")

        r_p = _safe_req(
            self,
            self.client,
            "textDocument/hover",
            {"textDocument": {"uri": uri}, "position": pos_of(src, "p.getFullName()")},
        )
        self.assertIn(
            "Person",
            extract_hover_text(r_p),
            f"hover(outer lambda param p) expected 'Person', got {r_p!r}",
        )

        r_t = _safe_req(
            self,
            self.client,
            "textDocument/hover",
            {"textDocument": {"uri": uri}, "position": pos_of(src, "t.equals(")},
        )
        self.assertIn(
            "String",
            extract_hover_text(r_t),
            f"hover(inner lambda param t) expected 'String', got {r_t!r}",
        )

    def test_hover_09_annotation_hover(self) -> None:
        """[HOVER-09] hover on @Marker annotation shows annotation type and Javadoc."""
        src = self._txt("Showcase.java")
        uri = self._uri("Showcase.java")
        res = _safe_req(
            self,
            self.client,
            "textDocument/hover",
            {"textDocument": {"uri": uri}, "position": pos_of(src, "@Marker", offset=1)},
        )
        txt = extract_hover_text(res)
        self.assertIn("Marker", txt, f"hover(@Marker) expected 'Marker', got {res!r}")

    def test_hover_12_keywords_and_whitespace_return_null(self) -> None:
        """[HOVER-12] hover on keywords or whitespace returns null cleanly."""
        src = self._txt("Showcase.java")
        uri = self._uri("Showcase.java")
        res = _safe_req(
            self,
            self.client,
            "textDocument/hover",
            {"textDocument": {"uri": uri}, "position": pos_of(src, "return text;")},
        )
        self.assertIsNone(res, f"hover on 'return' keyword expected None, got {res!r}")

    def test_hover_13_markup_content_format(self) -> None:
        """[HOVER-13] hover contents uses MarkupContent with kind='markdown' when client requests markdown."""
        src = self._txt("Showcase.java")
        uri = self._uri("Showcase.java")
        res = _safe_req(
            self,
            self.client,
            "textDocument/hover",
            {"textDocument": {"uri": uri}, "position": pos_of(src, 'greet("World"')},
        )
        self.assertIsInstance(res, dict, f"hover expected dict, got {res!r}")
        contents = res.get("contents")
        self.assertIsInstance(contents, dict, f"hover.contents expected MarkupContent dict, got {contents!r}")
        self.assertEqual(
            contents.get("kind"),
            "markdown",
            f"hover.contents.kind expected 'markdown', got {contents!r}",
        )

    def test_sig_01_single_method_signature_help(self) -> None:
        """[SIG-01] signatureHelp on single method greet(\"World\", localCounter) shows params and activeParameter."""
        src = self._txt("Showcase.java")
        uri = self._uri("Showcase.java")
        res0 = _safe_req(
            self,
            self.client,
            "textDocument/signatureHelp",
            {"textDocument": {"uri": uri}, "position": after_of(src, 'greet(')},
        )
        _assert_valid_signature_help(
            self, "signatureHelp(greet param 0)", res0, expected_param_index=0, min_params=2, expected_name="greet"
        )

        res1 = _safe_req(
            self,
            self.client,
            "textDocument/signatureHelp",
            {"textDocument": {"uri": uri}, "position": after_of(src, 'greet("World", ')},
        )
        _assert_valid_signature_help(
            self, "signatureHelp(greet param 1)", res1, expected_param_index=1, min_params=2, expected_name="greet"
        )

    def test_sig_02_overloaded_methods_signature_help(self) -> None:
        """[SIG-02] signatureHelp on overloaded format(...) lists overloads and selects matching arity."""
        src = self._txt("Showcase.java")
        uri = self._uri("Showcase.java")

        res2 = _safe_req(
            self,
            self.client,
            "textDocument/signatureHelp",
            {"textDocument": {"uri": uri}, "position": after_of(src, 'format("two", ')},
        )
        _assert_valid_signature_help(
            self, "signatureHelp(format 2-arg)", res2, expected_param_index=1, min_params=2, expected_name="format"
        )
        self.assertGreaterEqual(
            len(res2.get("signatures", [])),
            2,
            f"signatureHelp(format) expected multiple overload signatures, got {res2!r}",
        )

        res3 = _safe_req(
            self,
            self.client,
            "textDocument/signatureHelp",
            {"textDocument": {"uri": uri}, "position": after_of(src, 'format("three", 12, ')},
        )
        _assert_valid_signature_help(
            self, "signatureHelp(format 3-arg)", res3, expected_param_index=2, min_params=3, expected_name="format"
        )

    def test_sig_03_varargs_active_parameter_clamped(self) -> None:
        """[SIG-03] signatureHelp on varargs joinAll(\",\", \"a\", \"b\", \"c\") clamps activeParameter to varargs slot."""
        src = self._txt("Showcase.java")
        uri = self._uri("Showcase.java")
        res = _safe_req(
            self,
            self.client,
            "textDocument/signatureHelp",
            {"textDocument": {"uri": uri}, "position": after_of(src, 'joinAll(",", "a", "b", ')},
        )
        _assert_valid_signature_help(
            self,
            "signatureHelp(varargs joinAll arg 3)",
            res,
            expected_param_index=1,
            min_params=2,
            expected_name="joinAll",
        )

    def test_sig_04_constructor_signature_help(self) -> None:
        """[SIG-04] signatureHelp inside 'new Person(\"Ada\", 30)' shows constructor parameters."""
        src = self._txt("Showcase.java")
        uri = self._uri("Showcase.java")
        res0 = _safe_req(
            self,
            self.client,
            "textDocument/signatureHelp",
            {"textDocument": {"uri": uri}, "position": after_of(src, 'new Person(')},
        )
        _assert_valid_signature_help(
            self, "signatureHelp(new Person arg 0)", res0, expected_param_index=0, min_params=2, expected_name="Person"
        )
        res1 = _safe_req(
            self,
            self.client,
            "textDocument/signatureHelp",
            {"textDocument": {"uri": uri}, "position": after_of(src, 'new Person("Ada", ')},
        )
        _assert_valid_signature_help(
            self, "signatureHelp(new Person arg 1)", res1, expected_param_index=1, min_params=2, expected_name="Person"
        )

    def test_sig_05_nested_calls_active_signature_and_param(self) -> None:
        """[SIG-05] signatureHelp distinguishes inner vs outer call in outer(pair(10, 20), \"ok\")."""
        src = self._txt("Showcase.java")
        uri = self._uri("Showcase.java")

        res_inner = _safe_req(
            self,
            self.client,
            "textDocument/signatureHelp",
            {"textDocument": {"uri": uri}, "position": after_of(src, "pair(10, ")},
        )
        _assert_valid_signature_help(
            self, "signatureHelp(nested inner pair)", res_inner, expected_param_index=1, min_params=2, expected_name="pair"
        )

        res_outer = _safe_req(
            self,
            self.client,
            "textDocument/signatureHelp",
            {"textDocument": {"uri": uri}, "position": after_of(src, "outer(pair(10, 20), ")},
        )
        _assert_valid_signature_help(
            self, "signatureHelp(nested outer after pair)", res_outer, expected_param_index=1, min_params=2, expected_name="outer"
        )

    def test_sig_06_commas_inside_string_literal_ignored(self) -> None:
        """[SIG-06] Commas inside string literal \"a, b, c\" do not inflate activeParameter."""
        src = self._txt("Showcase.java")
        uri = self._uri("Showcase.java")
        res = _safe_req(
            self,
            self.client,
            "textDocument/signatureHelp",
            {"textDocument": {"uri": uri}, "position": after_of(src, 'takeThree("a, b, c", ')},
        )
        _assert_valid_signature_help(
            self, "signatureHelp(string commas)", res, expected_param_index=1, min_params=3, expected_name="takeThree"
        )

    def test_sig_07_commas_inside_nested_parens_ignored(self) -> None:
        """[SIG-07] Commas inside nested pair(1, 2) do not inflate outer takeThree activeParameter."""
        src = self._txt("Showcase.java")
        uri = self._uri("Showcase.java")
        res = _safe_req(
            self,
            self.client,
            "textDocument/signatureHelp",
            {"textDocument": {"uri": uri}, "position": after_of(src, "takeThree(pair(1, 2), ")},
        )
        _assert_valid_signature_help(
            self, "signatureHelp(nested paren commas)", res, expected_param_index=1, min_params=3, expected_name="takeThree"
        )

    def test_sig_08_commas_inside_lambda_ignored(self) -> None:
        """[SIG-08] Commas inside lambda (a, b) -> ... do not inflate outer processWithLambda activeParameter."""
        src = self._txt("Showcase.java")
        uri = self._uri("Showcase.java")
        res = _safe_req(
            self,
            self.client,
            "textDocument/signatureHelp",
            {
                "textDocument": {"uri": uri},
                "position": after_of(src, "processWithLambda(people, (a, b) -> a.getFullName() + b.getAge(), "),
            },
        )
        _assert_valid_signature_help(
            self,
            "signatureHelp(lambda commas)",
            res,
            expected_param_index=2,
            min_params=3,
            expected_name="processWithLambda",
        )

    def test_sig_09_commas_inside_generic_angles_ignored(self) -> None:
        """[SIG-09] Commas inside generic type args <String, Integer> do not inflate activeParameter."""
        src = self._txt("Showcase.java")
        uri = self._uri("Showcase.java")
        res = _safe_req(
            self,
            self.client,
            "textDocument/signatureHelp",
            {"textDocument": {"uri": uri}, "position": after_of(src, "takeGeneric(new HashMap<String, Integer>(), ")},
        )
        _assert_valid_signature_help(
            self, "signatureHelp(generic angle commas)", res, expected_param_index=1, min_params=2, expected_name="takeGeneric"
        )

    def test_sig_10_retrigger_after_comma(self) -> None:
        """[SIG-10] signatureHelp retrigger after ',' with activeSignatureHelp increments activeParameter."""
        src = self._txt("Showcase.java")
        uri = self._uri("Showcase.java")
        first = _safe_req(
            self,
            self.client,
            "textDocument/signatureHelp",
            {
                "textDocument": {"uri": uri},
                "position": after_of(src, 'format("three", '),
                "context": {"triggerKind": 2, "triggerCharacter": ",", "isRetrigger": False},
            },
        )
        _assert_valid_signature_help(self, "signatureHelp(retrigger step 1)", first, expected_param_index=1, expected_name="format")

        second = _safe_req(
            self,
            self.client,
            "textDocument/signatureHelp",
            {
                "textDocument": {"uri": uri},
                "position": after_of(src, 'format("three", 12, '),
                "context": {
                    "triggerKind": 2,
                    "triggerCharacter": ",",
                    "isRetrigger": True,
                    "activeSignatureHelp": first,
                },
            },
        )
        _assert_valid_signature_help(
            self, "signatureHelp(retrigger step 2)", second, expected_param_index=2, min_params=3, expected_name="format"
        )

    def test_sig_13_string_format_comma_and_varargs_bounds(self) -> None:
        """[SIG-13] signatureHelp on String.format ignores commas inside string literals and nested calls and clamps varargs activeParameter."""
        src = self._txt("Showcase.java")
        uri = self._uri("Showcase.java")
        live = src.replace(
            'String f1 = format("one");',
            'String f1 = String.format("a, b", String.join(",", "x", "y"), 1);',
        )
        self.client.did_change(uri, text=live)
        self.addCleanup(lambda: self.client.did_change(uri, text=src) if self.client.is_alive() else None)

        failures: list[str] = []
        # Inside arg 0 string literal "a, |b" -> activeParameter must be 0
        r_str = _safe_req(
            self,
            self.client,
            "textDocument/signatureHelp",
            {"textDocument": {"uri": uri}, "position": after_of(live, 'String.format("a, ')},
        )
        ap_str = r_str.get("activeParameter") if isinstance(r_str, dict) else None
        if ap_str != 0:
            failures.append(f'inside "a, |b": expected activeParameter=0, got {r_str!r}')

        # Inside arg 1 after nested String.join(",", "x", "y"), |1 -> activeParameter must clamp to 1 (varargs)
        r_after = _safe_req(
            self,
            self.client,
            "textDocument/signatureHelp",
            {"textDocument": {"uri": uri}, "position": after_of(live, 'String.join(",", "x", "y"), ')},
        )
        ap_after = r_after.get("activeParameter") if isinstance(r_after, dict) else None
        if ap_after != 1:
            failures.append(f'after nested String.join(...) comma: expected activeParameter=1 (varargs clamp), got {r_after!r}')

        self.assertFalse(failures, "; ".join(failures))

    def test_sig_12_incomplete_mid_typing_call_signature_help(self) -> None:
        """[SIG-12] signatureHelp works mid-typing on unclosed call 'greet(\"Ada\", ' via didChange."""
        base = (
            "package com.acme.hs;\n\n"
            "public class LiveSig {\n"
            "  public String greet(String who, int count) { return who + count; }\n"
            "  public void run() {\n"
            "    greet(\"Ada\", \n"
            "  }\n"
            "}\n"
        )
        uri = self.project.uri("src/main/java/com/acme/hs/LiveSig.java")
        self.project.write_file("src/main/java/com/acme/hs/LiveSig.java", base)
        self.client.did_open(uri, base)
        self.addCleanup(lambda: self.client.did_close(uri) if self.client.is_alive() else None)

        res = _safe_req(
            self,
            self.client,
            "textDocument/signatureHelp",
            {
                "textDocument": {"uri": uri},
                "position": after_of(base, 'greet("Ada", '),
                "context": {"triggerKind": 2, "triggerCharacter": ",", "isRetrigger": False},
            },
        )
        _assert_valid_signature_help(
            self,
            "signatureHelp(incomplete unclosed call)",
            res,
            expected_param_index=1,
            min_params=2,
            expected_name="greet",
        )


class TestSpringDemoHoverAndSignatureHelp(unittest.TestCase):
    """Hover and signatureHelp on Lombok, JDK, and Maven symbols in isolated java17-spring-demo."""

    project: TempJavaProject
    client: LspClient

    @classmethod
    def setUpClass(cls) -> None:
        cls.project = TempJavaProject(prefix="java_lsp_hover_spring_")
        try:
            cls.project.copy_from(SPRING_DEMO_DIR)
            for cp in cls.project.root.rglob(".classpath_m2.txt"):
                cp.unlink(missing_ok=True)
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
            "core/src/main/java/com/example/demo/controller/UserController.java",
            "core/src/main/java/com/example/demo/service/UserService.java",
            "core/src/main/java/com/example/demo/service/impl/UserServiceImpl.java",
            "core/src/main/java/com/example/demo/model/User.java",
            "core/src/main/java/com/example/demo/model/CreateUserRequest.java",
            "core/src/test/java/com/example/demo/service/UserServiceSmokeTest.java",
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

    def test_hover_10_lombok_slf4j_log_hover(self) -> None:
        """[HOVER-10] hover on Lombok @Slf4j log displays 'org.slf4j.Logger log'."""
        ctrl_rel = "core/src/main/java/com/example/demo/controller/UserController.java"
        ctrl_txt = self.project.read_file(ctrl_rel)
        ctrl_uri = self.project.uri(ctrl_rel)
        hov_log = _safe_req(
            self,
            self.client,
            "textDocument/hover",
            {"textDocument": {"uri": ctrl_uri}, "position": pos_of(ctrl_txt, "log.info(")},
        )
        txt_log = extract_hover_text(hov_log)
        self.assertTrue(
            "Logger" in txt_log and "log" in txt_log,
            f"hover(@Slf4j log) expected 'org.slf4j.Logger log', got {hov_log!r}",
        )

    def test_hover_14_lombok_data_getter_hover(self) -> None:
        """[HOVER-14] hover on Lombok @Data generated getter displays method signature."""
        ctrl_rel = "core/src/main/java/com/example/demo/controller/UserController.java"
        ctrl_txt = self.project.read_file(ctrl_rel)
        ctrl_uri = self.project.uri(ctrl_rel)
        hov_get = _safe_req(
            self,
            self.client,
            "textDocument/hover",
            {"textDocument": {"uri": ctrl_uri}, "position": pos_of(ctrl_txt, "request.getEmail()", offset=8)},
        )
        txt_get = extract_hover_text(hov_get)
        self.assertTrue(
            "String" in txt_get and "getEmail" in txt_get,
            f"hover(@Data request.getEmail()) expected 'String getEmail()', got {hov_get!r}",
        )

    def test_hover_15_lombok_builder_chain_hover(self) -> None:
        """[HOVER-15] hover on Lombok @Builder User.builder().name(...).build() shows UserBuilder and User."""
        impl_rel = "core/src/main/java/com/example/demo/service/impl/UserServiceImpl.java"
        impl_txt = self.project.read_file(impl_rel)
        impl_uri = self.project.uri(impl_rel)

        failures: list[str] = []
        hov_builder = _safe_req(
            self,
            self.client,
            "textDocument/hover",
            {"textDocument": {"uri": impl_uri}, "position": pos_of(impl_txt, "User.builder()", offset=5)},
        )
        if "builder" not in extract_hover_text(hov_builder):
            failures.append(f"hover(User.builder()) expected builder signature, got {hov_builder!r}")

        hov_name = _safe_req(
            self,
            self.client,
            "textDocument/hover",
            {"textDocument": {"uri": impl_uri}, "position": pos_of(impl_txt, ".name(request", offset=1)},
        )
        if "name" not in extract_hover_text(hov_name):
            failures.append(f"hover(@Builder .name()) returned {hov_name!r}")

        hov_build = _safe_req(
            self,
            self.client,
            "textDocument/hover",
            {"textDocument": {"uri": impl_uri}, "position": pos_of(impl_txt, ".build()", offset=1)},
        )
        if "User" not in extract_hover_text(hov_build):
            failures.append(f"hover(@Builder .build()) expected return type 'User', got {hov_build!r}")

        self.assertFalse(failures, "; ".join(failures))

    def test_hover_11_jdk_and_maven_dependency_types_and_methods(self) -> None:
        """[HOVER-11] hover on JDK (String.format, RuntimeException) and Maven (log.info, assertThat) symbols."""
        ctrl_rel = "core/src/main/java/com/example/demo/controller/UserController.java"
        smoke_rel = "core/src/test/java/com/example/demo/service/UserServiceSmokeTest.java"
        ctrl_txt = self.project.read_file(ctrl_rel)
        smoke_txt = self.project.read_file(smoke_rel)
        ctrl_uri = self.project.uri(ctrl_rel)
        smoke_uri = self.project.uri(smoke_rel)

        failures: list[str] = []
        hov_fmt = _safe_req(
            self,
            self.client,
            "textDocument/hover",
            {"textDocument": {"uri": ctrl_uri}, "position": pos_of(ctrl_txt, "String.format", offset=7)},
        )
        if "String" not in extract_hover_text(hov_fmt):
            failures.append(f"hover(String.format) expected String signature, got {hov_fmt!r}")

        hov_re = _safe_req(
            self,
            self.client,
            "textDocument/hover",
            {"textDocument": {"uri": ctrl_uri}, "position": pos_of(ctrl_txt, "RuntimeException")},
        )
        if "RuntimeException" not in extract_hover_text(hov_re):
            failures.append(f"hover(RuntimeException) expected class info, got {hov_re!r}")

        hov_log_info = _safe_req(
            self,
            self.client,
            "textDocument/hover",
            {"textDocument": {"uri": ctrl_uri}, "position": pos_of(ctrl_txt, "log.info(", offset=4)},
        )
        if "info" not in extract_hover_text(hov_log_info):
            failures.append(f"hover(log.info) expected SLF4J Logger.info signature, got {hov_log_info!r}")

        hov_assert = _safe_req(
            self,
            self.client,
            "textDocument/hover",
            {"textDocument": {"uri": smoke_uri}, "position": pos_of(smoke_txt, "assertThat(users)")},
        )
        if "assertThat" not in extract_hover_text(hov_assert):
            failures.append(f"hover(assertThat) expected AssertJ signature, got {hov_assert!r}")

        self.assertFalse(failures, "; ".join(failures))

    def test_sig_11_jdk_and_maven_signature_help(self) -> None:
        """[SIG-11] signatureHelp on JDK String.format and Maven log.info in UserController."""
        ctrl_rel = "core/src/main/java/com/example/demo/controller/UserController.java"
        ctrl_txt = self.project.read_file(ctrl_rel)
        ctrl_uri = self.project.uri(ctrl_rel)

        sig_fmt = _safe_req(
            self,
            self.client,
            "textDocument/signatureHelp",
            {"textDocument": {"uri": ctrl_uri}, "position": after_of(ctrl_txt, 'String.format("hello world %d", ')},
        )
        _assert_valid_signature_help(
            self,
            "signatureHelp(JDK String.format)",
            sig_fmt,
            expected_param_index=1,
            min_params=2,
            expected_name="format",
        )

        sig_log = _safe_req(
            self,
            self.client,
            "textDocument/signatureHelp",
            {"textDocument": {"uri": ctrl_uri}, "position": after_of(ctrl_txt, 'log.info("Handling GET /api/users/{}", ')},
        )
        _assert_valid_signature_help(
            self,
            "signatureHelp(Maven SLF4J log.info)",
            sig_log,
            expected_param_index=1,
            min_params=2,
            expected_name="info",
        )


if __name__ == "__main__":
    unittest.main()
