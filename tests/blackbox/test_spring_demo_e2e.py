"""End-to-end black-box tests exercising java-lsp against an isolated copy of java17-spring-demo."""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests.blackbox.lsp_client import (
    LspClient,
    LspResponseError,
    ServerCrashedError,
    TempJavaProject,
    apply_text_edits,
    extract_completion_items,
    extract_hover_text,
    extract_locations,
    flatten_document_symbols,
    is_file_uri,
    location_range,
    location_uri,
    offset_to_position,
    pos_of,
    uri_exists_on_disk,
)

SPRING_DEMO_DIR = REPO_ROOT / "java17-spring-demo"

JAVA_FILES = [
    "api/src/main/java/com/example/demo/api/UserFeignClient.java",
    "core/src/main/java/com/example/demo/DemoApplication.java",
    "core/src/main/java/com/example/demo/controller/UserController.java",
    "core/src/main/java/com/example/demo/es/EsInitconstant.java",
    "core/src/main/java/com/example/demo/mapper/UserMapper.java",
    "core/src/main/java/com/example/demo/model/CreateUserRequest.java",
    "core/src/main/java/com/example/demo/model/User.java",
    "core/src/main/java/com/example/demo/model/UserEnum.java",
    "core/src/main/java/com/example/demo/service/UserService.java",
    "core/src/main/java/com/example/demo/service/impl/UserServiceImpl.java",
    "core/src/test/java/com/example/demo/service/UserServiceSmokeTest.java",
]


def _completion_labels(items: list[dict]) -> list[str]:
    return [re.split(r"[\s(<:\[]", str(it.get("label", "")), maxsplit=1)[0] for it in items]


def _workspace_edit_uris(ws_edit: dict | None) -> set[str]:
    uris: set[str] = set()
    if not isinstance(ws_edit, dict):
        return uris
    changes = ws_edit.get("changes")
    if isinstance(changes, dict):
        uris.update(changes.keys())
    doc_changes = ws_edit.get("documentChanges")
    if isinstance(doc_changes, list):
        for dc in doc_changes:
            if isinstance(dc, dict) and "textDocument" in dc:
                u = dc["textDocument"].get("uri")
                if u:
                    uris.add(u)
    return uris


class TestSpringDemoE2E(unittest.TestCase):
    """End-to-end LSP scenarios on an isolated copy of java17-spring-demo."""

    proj: TempJavaProject
    client: LspClient
    texts: dict[str, str]
    uris: dict[str, str]

    @classmethod
    def setUpClass(cls) -> None:
        if not SPRING_DEMO_DIR.is_dir():
            raise unittest.SkipTest(f"Missing {SPRING_DEMO_DIR}")
        cls.proj = TempJavaProject(prefix="spring_demo_e2e_")
        cls.proj.copy_from(SPRING_DEMO_DIR)
        cls.texts = {rel: cls.proj.read_file(rel) for rel in JAVA_FILES}
        cls.uris = {rel: cls.proj.uri(rel) for rel in JAVA_FILES}

        cls.client = LspClient(root_path=cls.proj.root, default_timeout=20.0)
        cls.client.start()
        cls.client.initialize_and_ready(root_path=cls.proj.root, timeout=60.0)

        for idx, rel in enumerate(JAVA_FILES):
            seq = cls.client.did_open(cls.uris[rel], cls.texts[rel])
            wait_sec = 10.0 if idx == 0 else 4.0
            try:
                cls.client.wait_for_diagnostics(
                    cls.uris[rel], timeout=wait_sec, after_seq=seq, settle_time=0.1
                )
            except TimeoutError:
                pass

    @classmethod
    def tearDownClass(cls) -> None:
        try:
            cls.client.close()
        finally:
            cls.proj.close()

    def _safe_call(self, label: str, fn):
        try:
            return fn()
        except (LspResponseError, ServerCrashedError, TimeoutError) as exc:
            self.fail(f"{label} failed: {exc}")

    def _restore_file(self, rel: str) -> None:
        seq = self.client.did_change(self.uris[rel], text=self.texts[rel])
        try:
            self.client.wait_for_diagnostics(
                self.uris[rel], timeout=5.0, after_seq=seq, settle_time=0.1
            )
        except TimeoutError:
            pass

    def test_e2e_01_clean_diagnostics_and_document_symbols(self) -> None:
        """[E2E-01] All 11 compiling Java files report 0 error diagnostics and non-empty documentSymbol outlines."""
        for rel in JAVA_FILES:
            diags = self.client.get_diagnostics(self.uris[rel])
            errors = [d for d in diags if d.get("severity", 1) == 1]
            self.assertEqual(
                errors,
                [],
                f"[E2E-01] Expected 0 error diagnostics on valid compiling {rel}, got: {errors}",
            )
            syms = self._safe_call(
                f"documentSymbol({rel})",
                lambda r=rel: self.client.document_symbol(self.uris[r]),
            )
            flat = flatten_document_symbols(syms)
            self.assertGreater(
                len(flat),
                0,
                f"[E2E-01] Expected non-empty documentSymbol for {rel}, got: {syms!r}",
            )

    def test_e2e_02_slf4j_lombok_logger_workflow(self) -> None:
        """[E2E-02] @Slf4j log field supports completion, hover, declaration (slf4j), and implementation (logback)."""
        rel = "core/src/main/java/com/example/demo/controller/UserController.java"
        uri = self.uris[rel]
        txt = self.texts[rel]

        # Completion on log.
        edited = txt.replace('log.error("Sample stack trace', 'log.("Sample stack trace', 1)
        try:
            self.client.did_change(uri, text=edited)
            pos = pos_of(edited, 'log.("Sample stack trace', offset=4)
            comp = self._safe_call(
                "completion(log.)",
                lambda: self.client.completion(uri, pos, trigger_kind=2, trigger_character="."),
            )
            labels = set(_completion_labels(extract_completion_items(comp)))
            for expected in ("info", "debug", "warn", "error"):
                self.assertIn(
                    expected,
                    labels,
                    f"[E2E-02] Expected '{expected}' in @Slf4j log. completion, got {sorted(labels)[:15]}",
                )
        finally:
            self._restore_file(rel)

        log_pos = pos_of(txt, "log.error", offset=1)

        # Declaration should resolve to org.slf4j.Logger.java
        decl = self._safe_call("declaration(log)", lambda: self.client.declaration(uri, log_pos))
        decl_locs = extract_locations(decl)
        self.assertTrue(
            decl_locs and "Logger" in (location_uri(decl_locs[0]) or ""),
            f"[E2E-02] Expected declaration(log) -> org/slf4j/Logger.java, got {decl!r}",
        )
        self.assertTrue(
            uri_exists_on_disk(location_uri(decl_locs[0]) or ""),
            f"[E2E-02] declaration(log) URI does not exist on disk: {decl_locs[0]!r}",
        )

        # Hover on log should describe org.slf4j.Logger
        hov = self._safe_call("hover(log)", lambda: self.client.hover(uri, log_pos))
        hov_txt = extract_hover_text(hov)
        self.assertIn(
            "Logger",
            hov_txt,
            f"[E2E-02] Expected hover(log) to show Logger type, got {hov!r}",
        )

        # Implementation on log should resolve to Logback Logger (ch.qos.logback.classic.Logger)
        impl = self._safe_call("implementation(log)", lambda: self.client.implementation(uri, log_pos))
        impl_locs = extract_locations(impl)
        self.assertTrue(
            impl_locs and any("logback" in (location_uri(l) or "").lower() for l in impl_locs),
            f"[E2E-02] Expected implementation(log) -> Logback Logger, got {impl!r}",
        )

    def test_e2e_03_lombok_builder_and_data_accessors(self) -> None:
        """[E2E-03] Lombok @Builder and @Data generate completion, hover, and navigation in UserServiceImpl & UserController."""
        impl_rel = "core/src/main/java/com/example/demo/service/impl/UserServiceImpl.java"
        impl_uri = self.uris[impl_rel]
        impl_txt = self.texts[impl_rel]

        # Completion after User.builder().
        edited = impl_txt.replace(".name(request.getName())", ".", 1)
        try:
            self.client.did_change(impl_uri, text=edited)
            pos = pos_of(edited, "User.builder()\n      .", offset=len("User.builder()\n      ."))
            comp = self._safe_call(
                "completion(User.builder().)",
                lambda: self.client.completion(impl_uri, pos, trigger_kind=2, trigger_character="."),
            )
            labels = set(_completion_labels(extract_completion_items(comp)))
            for expected in ("name", "email", "build"):
                self.assertIn(
                    expected,
                    labels,
                    f"[E2E-03] Expected Lombok builder method '{expected}' in completion, got {sorted(labels)[:20]}",
                )
        finally:
            self._restore_file(impl_rel)

        # Completion on @Data CreateUserRequest request.getEmail() -> request.
        ctrl_rel = "core/src/main/java/com/example/demo/controller/UserController.java"
        ctrl_uri = self.uris[ctrl_rel]
        ctrl_txt = self.texts[ctrl_rel]
        edited_ctrl = ctrl_txt.replace("request.getEmail()", "request.", 1)
        try:
            self.client.did_change(ctrl_uri, text=edited_ctrl)
            pos = pos_of(edited_ctrl, "request.)", offset=len("request."))
            comp = self._safe_call(
                "completion(request.)",
                lambda: self.client.completion(ctrl_uri, pos, trigger_kind=2, trigger_character="."),
            )
            labels = set(_completion_labels(extract_completion_items(comp)))
            for expected in ("getEmail", "getName"):
                self.assertIn(
                    expected,
                    labels,
                    f"[E2E-03] Expected Lombok @Data getter '{expected}' on request., got {sorted(labels)[:20]}",
                )
        finally:
            self._restore_file(ctrl_rel)

        # Definition on request.getEmail() should jump to CreateUserRequest.java (not empty)
        req_email_pos = pos_of(ctrl_txt, "request.getEmail()", offset=len("request.") + 2)
        defn = self._safe_call(
            "definition(request.getEmail)",
            lambda: self.client.definition(ctrl_uri, req_email_pos),
        )
        locs = extract_locations(defn)
        self.assertTrue(
            locs and any("CreateUserRequest.java" in (location_uri(l) or "") for l in locs),
            f"[E2E-03] Expected definition(request.getEmail) -> CreateUserRequest.java, got {defn!r}",
        )

    def test_e2e_04_cross_file_service_navigation_and_type_definition(self) -> None:
        """[E2E-04] UserController -> UserService -> UserServiceImpl navigation & typeDefinition(listUsers -> List)."""
        ctrl_rel = "core/src/main/java/com/example/demo/controller/UserController.java"
        svc_rel = "core/src/main/java/com/example/demo/service/UserService.java"
        ctrl_uri = self.uris[ctrl_rel]
        svc_uri = self.uris[svc_rel]
        ctrl_txt = self.texts[ctrl_rel]
        svc_txt = self.texts[svc_rel]

        call_pos = pos_of(ctrl_txt, "userService.listUsers()", offset=len("userService.") + 2)

        # Definition on userService.listUsers() must jump to UserService.java or UserServiceImpl.java (NOT UserController.listUsers!)
        defn = self._safe_call(
            "definition(userService.listUsers)",
            lambda: self.client.definition(ctrl_uri, call_pos),
        )
        locs = extract_locations(defn)
        self.assertTrue(
            locs
            and any(
                "UserService.java" in (location_uri(l) or "")
                or "UserServiceImpl.java" in (location_uri(l) or "")
                for l in locs
            ),
            f"[E2E-04] Expected definition(userService.listUsers) -> UserService/UserServiceImpl, got {defn!r}",
        )

        # typeDefinition on userService.listUsers() should jump to java.util.List
        tdef = self._safe_call(
            "typeDefinition(userService.listUsers)",
            lambda: self.client.type_definition(ctrl_uri, call_pos),
        )
        tlocs = extract_locations(tdef)
        self.assertTrue(
            tlocs and any("List.java" in (location_uri(l) or "") for l in tlocs),
            f"[E2E-04] Expected typeDefinition(userService.listUsers) -> List.java, got {tdef!r}",
        )

        # implementation on UserService.listUsers in interface must jump to UserServiceImpl.java listUsers()
        iface_pos = pos_of(svc_txt, "listUsers()", offset=2)
        impl = self._safe_call(
            "implementation(UserService.listUsers)",
            lambda: self.client.implementation(svc_uri, iface_pos),
        )
        ilocs = extract_locations(impl)
        self.assertTrue(
            ilocs and any("UserServiceImpl.java" in (location_uri(l) or "") for l in ilocs),
            f"[E2E-04] Expected implementation(UserService.listUsers) -> UserServiceImpl.java, got {impl!r}",
        )

    def test_e2e_05_cross_module_api_to_core_feign_navigation(self) -> None:
        """[E2E-05] Cross-module resolution from core UserController to api UserFeignClient."""
        ctrl_rel = "core/src/main/java/com/example/demo/controller/UserController.java"
        ctrl_uri = self.uris[ctrl_rel]
        ctrl_txt = self.texts[ctrl_rel]

        injected = ctrl_txt.replace(
            "import com.example.demo.service.UserService;",
            "import com.example.demo.api.UserFeignClient;\nimport com.example.demo.service.UserService;",
            1,
        ).replace(
            "private final UserService userService;",
            "private final UserService userService;\n  private UserFeignClient feignClient;",
            1,
        ).replace(
            "return userService.listUsers();",
            "feignClient.getUserName(1L);\n    return userService.listUsers();",
            1,
        )
        try:
            self.client.did_change(ctrl_uri, text=injected)
            call_pos = pos_of(injected, "feignClient.getUserName", offset=len("feignClient.") + 2)
            defn = self._safe_call(
                "definition(cross-module UserFeignClient.getUserName)",
                lambda: self.client.definition(ctrl_uri, call_pos),
            )
            locs = extract_locations(defn)
            self.assertTrue(
                locs and any("UserFeignClient.java" in (location_uri(l) or "") for l in locs),
                f"[E2E-05] Expected cross-module definition -> api/UserFeignClient.java, got {defn!r}",
            )

            # Completion after feignClient.
            comp_txt = injected.replace("feignClient.getUserName(1L);", "feignClient.;", 1)
            self.client.did_change(ctrl_uri, text=comp_txt)
            cpos = pos_of(comp_txt, "feignClient.;", offset=len("feignClient."))
            comp = self._safe_call(
                "completion(cross-module feignClient.)",
                lambda: self.client.completion(ctrl_uri, cpos, trigger_kind=2, trigger_character="."),
            )
            labels = set(_completion_labels(extract_completion_items(comp)))
            self.assertIn(
                "getUserName",
                labels,
                f"[E2E-05] Expected 'getUserName' in cross-module feignClient. completion, got {sorted(labels)[:15]}",
            )
        finally:
            self._restore_file(ctrl_rel)

    def test_e2e_06_mybatis_mapper_xml_and_completion(self) -> None:
        """[E2E-06] MyBatis UserMapper navigates to UserMapper.xml and completes mapper methods."""
        impl_rel = "core/src/main/java/com/example/demo/service/impl/UserServiceImpl.java"
        impl_uri = self.uris[impl_rel]
        impl_txt = self.texts[impl_rel]

        # Definition on userMapper.findAll() in UserServiceImpl resolves to UserMapper.java or UserMapper.xml
        pos = pos_of(impl_txt, "userMapper.findAll()", offset=len("userMapper.") + 2)
        defn = self._safe_call("definition(userMapper.findAll)", lambda: self.client.definition(impl_uri, pos))
        locs = extract_locations(defn)
        self.assertTrue(
            locs
            and any(
                "UserMapper.xml" in (location_uri(l) or "")
                or "UserMapper.java" in (location_uri(l) or "")
                for l in locs
            ),
            f"[E2E-06] Expected definition(userMapper.findAll) -> UserMapper.xml/java, got {defn!r}",
        )

        # Completion on userMapper.
        edited = impl_txt.replace("userMapper.findAll()", "userMapper.", 1)
        try:
            self.client.did_change(impl_uri, text=edited)
            cpos = pos_of(edited, "return userMapper.;", offset=len("return userMapper."))
            comp = self._safe_call(
                "completion(userMapper.)",
                lambda: self.client.completion(impl_uri, cpos, trigger_kind=2, trigger_character="."),
            )
            labels = set(_completion_labels(extract_completion_items(comp)))
            for m in ("findAll", "findById", "insert", "updateStatus", "chaosFindAll"):
                self.assertIn(
                    m,
                    labels,
                    f"[E2E-06] Expected UserMapper method '{m}' in userMapper. completion, got {sorted(labels)}",
                )
        finally:
            self._restore_file(impl_rel)

    def test_e2e_07_stream_optional_and_assertj_static_import(self) -> None:
        """[E2E-07] Optional chaining in UserServiceImpl and AssertJ static import + fluent chain in UserServiceSmokeTest."""
        test_rel = "core/src/test/java/com/example/demo/service/UserServiceSmokeTest.java"
        test_uri = self.uris[test_rel]
        test_txt = self.texts[test_rel]

        # 1. Static import assertThat completion
        edited_static = test_txt.replace("assertThat(users)", "assertTh(users)", 1)
        try:
            self.client.did_change(test_uri, text=edited_static)
            pos = pos_of(edited_static, "assertTh(users)", offset=len("assertTh"))
            comp = self._safe_call(
                "completion(static import assertTh)",
                lambda: self.client.completion(test_uri, pos),
            )
            labels = set(_completion_labels(extract_completion_items(comp)))
            self.assertIn(
                "assertThat",
                labels,
                f"[E2E-07] Expected static import 'assertThat' in completion, got {sorted(labels)[:20]}",
            )
        finally:
            self._restore_file(test_rel)

        # 2. Fluent AssertJ chain assertThat(users).extracting
        edited_chain = test_txt.replace(".extracting(User::getEmail)", ".", 1)
        try:
            self.client.did_change(test_uri, text=edited_chain)
            pos = pos_of(edited_chain, "assertThat(users)\n      .", offset=len("assertThat(users)\n      ."))
            comp = self._safe_call(
                "completion(assertThat(users).)",
                lambda: self.client.completion(test_uri, pos, trigger_kind=2, trigger_character="."),
            )
            labels = set(_completion_labels(extract_completion_items(comp)))
            self.assertIn(
                "extracting",
                labels,
                f"[E2E-07] Expected AssertJ 'extracting' on assertThat(users)., got {sorted(labels)[:20]}",
            )
        finally:
            self._restore_file(test_rel)

    def test_e2e_08_nested_generic_lambda_builder_es_initconstant(self) -> None:
        """[E2E-08] Nested generic lambda builder in EsInitconstant resolves TypeMapping.Builder (not UserBuilder)."""
        rel = "core/src/main/java/com/example/demo/es/EsInitconstant.java"
        uri = self.uris[rel]
        txt = self.texts[rel]

        b_pos = pos_of(txt, "builder -> builder", offset=len("builder -> ") + 2)
        hov = self._safe_call("hover(EsInitconstant lambda builder)", lambda: self.client.hover(uri, b_pos))
        hov_txt = extract_hover_text(hov)
        self.assertTrue(
            "TypeMapping" in hov_txt or "Builder" in hov_txt,
            f"[E2E-08] Expected TypeMapping.Builder in hover, got {hov!r}",
        )
        self.assertNotIn(
            "UserBuilder",
            hov_txt,
            f"[E2E-08] Incorrectly resolved EsInitconstant builder hover to UserBuilder: {hov_txt!r}",
        )

        # Completion on builder.properties
        edited = txt.replace(".properties(", ".", 1)
        try:
            self.client.did_change(uri, text=edited)
            cpos = pos_of(edited, "builder\n      .", offset=len("builder\n      ."))
            comp = self._safe_call(
                "completion(EsInitconstant builder.)",
                lambda: self.client.completion(uri, cpos, trigger_kind=2, trigger_character="."),
            )
            labels = set(_completion_labels(extract_completion_items(comp)))
            self.assertIn(
                "properties",
                labels,
                f"[E2E-08] Expected 'properties' on TypeMapping.Builder completion, got {sorted(labels)[:20]}",
            )
        finally:
            self._restore_file(rel)

    def test_e2e_09_live_signature_help_on_spring_demo_calls(self) -> None:
        """[E2E-09] signatureHelp provides parameter hints & activeParameter for String.format and userService.getUser."""
        ctrl_rel = "core/src/main/java/com/example/demo/controller/UserController.java"
        ctrl_uri = self.uris[ctrl_rel]
        ctrl_txt = self.texts[ctrl_rel]

        # Test String.format("hello world %d", id) at param 0 and param 1
        edited0 = ctrl_txt.replace('String.format("hello world %d", id)', "String.format()", 1)
        try:
            self.client.did_change(ctrl_uri, text=edited0)
            p0 = pos_of(edited0, "String.format()", offset=len("String.format("))
            sig0 = self._safe_call(
                "signatureHelp(String.format()",
                lambda: self.client.signature_help(ctrl_uri, p0, trigger_kind=2, trigger_character="("),
            )
            sigs0 = sig0.get("signatures", []) if isinstance(sig0, dict) else []
            self.assertGreater(
                len(sigs0),
                0,
                f"[E2E-09] Expected non-empty signatureHelp for String.format(, got {sig0!r}",
            )
            self.assertEqual(
                sig0.get("activeParameter"),
                0,
                f"[E2E-09] Expected activeParameter=0 in String.format(, got {sig0!r}",
            )

            edited1 = ctrl_txt.replace('String.format("hello world %d", id)', 'String.format("hello", )', 1)
            self.client.did_change(ctrl_uri, text=edited1)
            p1 = pos_of(edited1, 'String.format("hello", )', offset=len('String.format("hello", '))
            sig1 = self._safe_call(
                "signatureHelp(String.format(arg0, )",
                lambda: self.client.signature_help(ctrl_uri, p1, trigger_kind=2, trigger_character=","),
            )
            sigs1 = sig1.get("signatures", []) if isinstance(sig1, dict) else []
            self.assertGreater(
                len(sigs1),
                0,
                f"[E2E-09] Expected non-empty signatureHelp after comma in String.format, got {sig1!r}",
            )
            self.assertEqual(
                sig1.get("activeParameter"),
                1,
                f"[E2E-09] Expected activeParameter=1 after comma in String.format, got {sig1!r}",
            )

            # Also test project interface method call userService.getUser(
            edited_svc = ctrl_txt.replace("userService.getUser(id)", "userService.getUser()", 1)
            self.client.did_change(ctrl_uri, text=edited_svc)
            psvc = pos_of(edited_svc, "userService.getUser()", offset=len("userService.getUser("))
            sig_svc = self._safe_call(
                "signatureHelp(userService.getUser()",
                lambda: self.client.signature_help(ctrl_uri, psvc, trigger_kind=2, trigger_character="("),
            )
            sigs_svc = sig_svc.get("signatures", []) if isinstance(sig_svc, dict) else []
            self.assertGreater(
                len(sigs_svc),
                0,
                f"[E2E-09] Expected non-empty signatureHelp for project method userService.getUser(, got {sig_svc!r}",
            )
        finally:
            self._restore_file(ctrl_rel)

    def test_e2e_10_live_error_diagnostics_and_code_action_quickfix(self) -> None:
        """[E2E-10] Injecting syntax/type/import errors triggers diagnostics, import codeAction quickfix, and clears on revert."""
        ctrl_rel = "core/src/main/java/com/example/demo/controller/UserController.java"
        ctrl_uri = self.uris[ctrl_rel]
        ctrl_txt = self.texts[ctrl_rel]

        # Missing import + type error + syntax error
        broken = ctrl_txt.replace(
            "return userService.listUsers();",
            "BigDecimal __missing = null;\n    int __badType = \"nope\";\n    return userService.listUsers();",
            1,
        )
        try:
            seq = self.client.did_change(ctrl_uri, text=broken)
            diags = self._safe_call(
                "wait_for_diagnostics(injected errors)",
                lambda: self.client.wait_for_diagnostics(ctrl_uri, timeout=8.0, after_seq=seq, settle_time=0.2),
            )
            msgs = [d.get("message", "") for d in diags]
            self.assertTrue(
                any("BigDecimal" in m for m in msgs),
                f"[E2E-10] Expected BigDecimal missing-import diagnostic, got: {msgs}",
            )
            self.assertTrue(
                any("int" in m or "String" in m or "type" in m.lower() or "convert" in m.lower() for m in msgs if "BigDecimal" not in m),
                f"[E2E-10] Expected incompatible assignment diagnostic for `int __badType = \"nope\"`, got: {msgs}",
            )

            # Check codeAction on BigDecimal line offers an import quickfix
            bd_pos = pos_of(broken, "BigDecimal __missing", offset=2)
            bd_end = pos_of(broken, "BigDecimal __missing", offset=len("BigDecimal"))
            actions = self._safe_call(
                "codeAction(missing import BigDecimal)",
                lambda: self.client.code_action(ctrl_uri, bd_pos, bd_end, diagnostics=diags),
            )
            titles = [str(a.get("title", "")) for a in (actions or []) if isinstance(a, dict)]
            self.assertTrue(
                any("BigDecimal" in t or ("import" in t.lower() and "organize" not in t.lower()) for t in titles),
                f"[E2E-10] Expected quickfix importing java.math.BigDecimal, got codeActions={titles}",
            )
        finally:
            self._restore_file(ctrl_rel)

        # Verify reverted file is clean again
        clean_errs = [d for d in self.client.get_diagnostics(ctrl_uri) if d.get("severity", 1) == 1]
        self.assertEqual(clean_errs, [], f"[E2E-10] Expected diagnostics to clear after revert, got {clean_errs}")

    def test_e2e_11_cross_file_rename_service_method_and_model(self) -> None:
        """[E2E-11] Renaming UserService.listUsers and CreateUserRequest updates all referencing files across project."""
        svc_rel = "core/src/main/java/com/example/demo/service/UserService.java"
        impl_rel = "core/src/main/java/com/example/demo/service/impl/UserServiceImpl.java"
        ctrl_rel = "core/src/main/java/com/example/demo/controller/UserController.java"
        test_rel = "core/src/test/java/com/example/demo/service/UserServiceSmokeTest.java"
        req_rel = "core/src/main/java/com/example/demo/model/CreateUserRequest.java"

        # 1. Rename UserService.listUsers -> fetchUsers
        svc_pos = pos_of(self.texts[svc_rel], "listUsers()", offset=2)
        ws_edit_meth = self._safe_call(
            "rename(UserService.listUsers)",
            lambda: self.client.rename(self.uris[svc_rel], svc_pos, "fetchUsers"),
        )
        edited_uris_meth = _workspace_edit_uris(ws_edit_meth)
        for expected_rel in (svc_rel, impl_rel, ctrl_rel, test_rel):
            self.assertIn(
                self.uris[expected_rel],
                edited_uris_meth,
                f"[E2E-11] Expected rename(UserService.listUsers) to edit {expected_rel}, edited={sorted(edited_uris_meth)}",
            )

        # 2. Rename CreateUserRequest -> NewUserRequest
        req_pos = pos_of(self.texts[req_rel], "class CreateUserRequest", offset=len("class ") + 2)
        ws_edit_cls = self._safe_call(
            "rename(CreateUserRequest)",
            lambda: self.client.rename(self.uris[req_rel], req_pos, "NewUserRequest"),
        )
        edited_uris_cls = _workspace_edit_uris(ws_edit_cls)
        for expected_rel in (req_rel, svc_rel, impl_rel, ctrl_rel):
            self.assertIn(
                self.uris[expected_rel],
                edited_uris_cls,
                f"[E2E-11] Expected rename(CreateUserRequest) to edit {expected_rel}, edited={sorted(edited_uris_cls)}",
            )

    def test_e2e_12_jdk_and_maven_source_extraction_uris(self) -> None:
        """[E2E-12] Navigation to JDK (RuntimeException, List) and Maven (GetMapping, Transactional) returns valid on-disk files."""
        ctrl_rel = "core/src/main/java/com/example/demo/controller/UserController.java"
        ctrl_uri = self.uris[ctrl_rel]
        ctrl_txt = self.texts[ctrl_rel]

        targets = [
            ("RuntimeException", pos_of(ctrl_txt, "new RuntimeException", offset=5)),
            ("List", pos_of(ctrl_txt, "List<User>", offset=1)),
            ("GetMapping", pos_of(ctrl_txt, "@GetMapping", offset=2)),
        ]
        for sym, pos in targets:
            defn = self._safe_call(f"definition({sym})", lambda p=pos: self.client.definition(ctrl_uri, p))
            locs = extract_locations(defn)
            if not locs:
                decl = self._safe_call(f"declaration({sym})", lambda p=pos: self.client.declaration(ctrl_uri, p))
                locs = extract_locations(decl)
            self.assertTrue(
                locs,
                f"[E2E-12] Expected definition/declaration for '{sym}' to resolve to JDK/Maven source, got empty",
            )
            target_uri = location_uri(locs[0]) or ""
            self.assertTrue(
                is_file_uri(target_uri) and uri_exists_on_disk(target_uri),
                f"[E2E-12] Resolved URI for '{sym}' is missing on disk: {target_uri!r}",
            )


if __name__ == "__main__":
    unittest.main()
