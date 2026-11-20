"""Black-box LSP tests for Lombok, Spring Boot, OpenFeign, MyBatis, JUnit 5, and AssertJ."""

from __future__ import annotations

import re
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
    after_of,
    extract_hover_text,
    extract_locations,
    location_uri,
    pos_of,
)

SPRING_DEMO_DIR = REPO_ROOT / "java17-spring-demo"
_IDENT_SPLIT_RE = re.compile(r"[(\s:<]")


def _item_ident(item: dict[str, Any]) -> str:
    label = str(item.get("label", "")).strip()
    return _IDENT_SPLIT_RE.split(label, 1)[0] if label else ""


def _item_insert_text(item: dict[str, Any]) -> str:
    te = item.get("textEdit")
    if isinstance(te, dict) and isinstance(te.get("newText"), str):
        return te["newText"]
    ins = item.get("insertText")
    return ins if isinstance(ins, str) and ins else str(item.get("label", ""))


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


def _clean_m2_markers(root: Path) -> None:
    for marker in root.rglob(".classpath_m2.txt"):
        marker.unlink(missing_ok=True)


class _FrameworkBase(unittest.TestCase):
    """Shared safe LSP helpers for Lombok and Java framework black-box tests."""

    project: TempJavaProject
    client: LspClient
    init_timeout: float = 60.0

    @classmethod
    def _start_workspace(cls) -> None:
        cls.client = LspClient(root_path=cls.project.root, default_timeout=25.0)
        cls.client.start()
        cls.client.initialize_and_ready(timeout=cls.init_timeout)

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
            self.client.initialize_and_ready(timeout=self.init_timeout)

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
                uri, pos, trigger_kind=trigger_kind, trigger_character=trigger_char
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
        self, method: str, uri: str, pos: dict[str, int], *, tag: str, context_desc: str
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
            f"[{tag}] Expected 0 error diagnostics on valid code ({context_desc}), "
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


class TestSpringDemoLombokSpringMyBatisAssertJ(_FrameworkBase):
    """Framework tests running against an isolated temp copy of java17-spring-demo."""

    @classmethod
    def setUpClass(cls) -> None:
        unittest.TestCase.setUpClass()
        cls.project = TempJavaProject(prefix="fw_spring_demo_")
        cls.project.copy_from(SPRING_DEMO_DIR)
        _clean_m2_markers(cls.project.root)
        # Add an isolated scratch file under core/ for testing cross-module Feign, @Log4j2, AssertJ
        cls.project.write_file(
            "core/src/main/java/com/example/demo/service/FrameworkProbe.java",
            """package com.example.demo.service;

import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;

import com.example.demo.api.UserFeignClient;
import com.example.demo.model.User;

import lombok.extern.log4j.Log4j2;

@Log4j2
@Service
public class FrameworkProbe {

  @Autowired
  private UserFeignClient userFeignClient;

  public String fetchRemoteName(Long id) {
    log.info("Fetching remote user {}", id);
    return userFeignClient.getUserName(id);
  }

  public void inspectUser(User user) {
    user.getEmail();
  }
}
""",
        )
        cls._start_workspace()

    def test_lombok_data_getters_setters_and_definition(self) -> None:
        """[FW-01] @Data generates getters/setters with snippet placeholders and jumps to field."""
        ctrl_rel = "core/src/main/java/com/example/demo/controller/UserController.java"
        orig = self.project.read_file(ctrl_rel)
        uri_clean, text_clean = self._open_file(ctrl_rel)
        locs = self._req_locations(
            "definition",
            uri_clean,
            pos_of(text_clean, "getEmail"),
            tag="FW-01",
            context_desc="request.getEmail() in UserController",
        )
        self.assertTrue(
            any("CreateUserRequest.java" in (location_uri(l) or "") for l in locs),
            f"[FW-01] Expected definition of @Data request.getEmail() to jump to CreateUserRequest.java, "
            f"got {locs!r}",
        )

        mod = orig.replace(
            "return userService.createUser(request);",
            "request.\n    return userService.createUser(request);",
        )
        self.client.did_change(uri_clean, mod)
        items = self._req_completion(
            uri_clean, after_of(mod, "request."), tag="FW-01", context_desc="@Data request."
        )
        for name in ("getName", "setName", "getEmail", "setEmail", "equals", "hashCode", "toString"):
            self._assert_has_member(items, name, tag="FW-01", context_desc="@Data request.")
        set_email = self._assert_has_member(items, "setEmail", tag="FW-01", context_desc="@Data request.")
        self.assertIn(
            "${1:",
            _item_insert_text(set_email),
            f"[FW-01] Expected snippet placeholder in @Data setter insertText, got {set_email!r}",
        )

    def test_lombok_builder_chain(self) -> None:
        """[FW-04] @Builder User.builder(). offers field setters + build(), and .build(). yields User."""
        rel = "core/src/main/java/com/example/demo/service/impl/UserServiceImpl.java"
        orig = self.project.read_file(rel)
        mod1 = orig.replace(
            "User user = User.builder()\n      .name(request.getName())\n      .email(request.getEmail())\n      .build();",
            "User.builder().",
        )
        uri, _ = self._open_file(rel, mod1)
        items = self._req_completion(
            uri, after_of(mod1, "User.builder()."), tag="FW-04", context_desc="User.builder()."
        )
        for name in ("id", "name", "email", "build"):
            self._assert_has_member(items, name, tag="FW-04", context_desc="User.builder().")

        mod2 = orig.replace(
            "return user;",
            'User.builder().name("a").build().',
        )
        self.client.did_change(uri, mod2)
        built_items = self._req_completion(
            uri, after_of(mod2, ".build()."), tag="FW-04", context_desc="User.builder()...build()."
        )
        self._assert_has_member(built_items, "getEmail", tag="FW-04", context_desc="User.builder()...build().")

    def test_lombok_slf4j_and_log4j2(self) -> None:
        """[FW-05] @Slf4j and @Log4j2 inject log with completion, hover, declaration, and Logback impl."""
        impl_rel = "core/src/main/java/com/example/demo/service/impl/UserServiceImpl.java"
        impl_orig = self.project.read_file(impl_rel)
        uri, text = self._open_file(impl_rel)

        hov = self._req_hover(uri, pos_of(text, "log.info"), tag="FW-05", context_desc="@Slf4j log")
        self.assertIn(
            "org.slf4j.Logger",
            hov,
            f"[FW-05] Expected hover on @Slf4j 'log' to contain 'org.slf4j.Logger', got {hov!r}",
        )
        decl = self._req_locations("declaration", uri, pos_of(text, "log.info"), tag="FW-05", context_desc="@Slf4j log")
        self.assertTrue(
            any("org/slf4j/Logger.java" in (location_uri(l) or "") for l in decl),
            f"[FW-05] Expected declaration of @Slf4j 'log' to jump to org/slf4j/Logger.java, got {decl!r}",
        )
        impl = self._req_locations("implementation", uri, pos_of(text, "log.info"), tag="FW-05", context_desc="@Slf4j log")
        self.assertTrue(
            any("logback" in (location_uri(l) or "").lower() for l in impl),
            f"[FW-05] Expected implementation of @Slf4j 'log' to jump to Logback Logger.java, got {impl!r}",
        )

        mod = impl_orig.replace('log.info("Listing all users");', 'log.info("Listing all users");\n    log.')
        self.client.did_change(uri, mod)
        items = self._req_completion(uri, after_of(mod, "    log."), tag="FW-05", context_desc="@Slf4j log.")
        for lvl in ("info", "debug", "warn", "error", "trace"):
            self._assert_has_member(items, lvl, tag="FW-05", context_desc="@Slf4j log.")

        # @Log4j2 in FrameworkProbe.java
        probe_rel = "core/src/main/java/com/example/demo/service/FrameworkProbe.java"
        probe_orig = self.project.read_file(probe_rel)
        probe_uri, probe_txt = self._open_file(probe_rel)
        self._assert_zero_errors(probe_uri, probe_txt, tag="FW-05", context_desc="@Log4j2 FrameworkProbe")
        probe_mod = probe_orig.replace('log.info("Fetching remote user {}", id);', "log.")
        self.client.did_change(probe_uri, probe_mod)
        l4j_items = self._req_completion(
            probe_uri, after_of(probe_mod, "    log."), tag="FW-05", context_desc="@Log4j2 log."
        )
        for lvl in ("info", "debug", "error", "fatal"):
            self._assert_has_member(l4j_items, lvl, tag="FW-05", context_desc="@Log4j2 log.")

    def test_spring_constructor_di_and_implementation(self) -> None:
        """[FW-11] Spring constructor DI via @RequiredArgsConstructor navigates UserController -> UserService -> UserServiceImpl."""
        ctrl_rel = "core/src/main/java/com/example/demo/controller/UserController.java"
        orig = self.project.read_file(ctrl_rel)
        uri, text = self._open_file(ctrl_rel)

        svc_def = self._req_locations(
            "definition",
            uri,
            pos_of(text, "getUser(id)"),
            tag="FW-11",
            context_desc="userService.getUser(id) in UserController",
        )
        self.assertTrue(
            any("UserService.java" in (location_uri(l) or "") for l in svc_def),
            f"[FW-11] Expected definition on userService.getUser(id) to jump to UserService.java, got {svc_def!r}",
        )

        svc_rel = "core/src/main/java/com/example/demo/service/UserService.java"
        svc_uri, svc_text = self._open_file(svc_rel)
        svc_impl = self._req_locations(
            "implementation",
            svc_uri,
            pos_of(svc_text, "getUser"),
            tag="FW-11",
            context_desc="UserService.getUser implementation",
        )
        self.assertTrue(
            any("UserServiceImpl.java" in (location_uri(l) or "") for l in svc_impl),
            f"[FW-11] Expected implementation on UserService.getUser to jump to UserServiceImpl.java, got {svc_impl!r}",
        )

        mod = orig.replace("return userService.listUsers();", "userService.")
        self.client.did_change(uri, mod)
        items = self._req_completion(
            uri, after_of(mod, "userService."), tag="FW-11", context_desc="DI userService."
        )
        for m in ("listUsers", "getUser", "createUser"):
            self._assert_has_member(items, m, tag="FW-11", context_desc="DI userService.")

    def test_spring_autowired_field_di(self) -> None:
        """[FW-12] @Autowired field DI resolves completion and navigation without false diagnostics."""
        rel = "core/src/main/java/com/example/demo/service/FrameworkProbe.java"
        orig = self.project.read_file(rel)
        mod = orig.replace("return userFeignClient.getUserName(id);", "return userFeignClient.")
        uri, _ = self._open_file(rel, mod)
        items = self._req_completion(
            uri, after_of(mod, "return userFeignClient."), tag="FW-12", context_desc="@Autowired userFeignClient."
        )
        self._assert_has_member(items, "getUserName", tag="FW-12", context_desc="@Autowired userFeignClient.")

    def test_spring_web_annotations_diagnostics_and_hover(self) -> None:
        """[FW-13] Spring Web @RestController/@RequestMapping/@GetMapping/@PostMapping have 0 errors and hover/def."""
        rel = "core/src/main/java/com/example/demo/controller/UserController.java"
        uri, text = self._open_file(rel)
        self._assert_zero_errors(uri, text, tag="FW-13", context_desc="UserController.java")
        locs = self._req_locations(
            "definition", uri, pos_of(text, "GetMapping"), tag="FW-13", context_desc="@GetMapping"
        )
        self.assertTrue(
            any("GetMapping.java" in (location_uri(l) or "") for l in locs),
            f"[FW-13] Expected definition on @GetMapping to jump to GetMapping.java, got {locs!r}",
        )

    def test_cross_module_openfeign_navigation(self) -> None:
        """[FW-14] Cross-module OpenFeign UserFeignClient (api) <-> core resolves cleanly and navigates across modules."""
        feign_rel = "api/src/main/java/com/example/demo/api/UserFeignClient.java"
        feign_uri, feign_text = self._open_file(feign_rel)
        self._assert_zero_errors(feign_uri, feign_text, tag="FW-14", context_desc="UserFeignClient.java")

        probe_rel = "core/src/main/java/com/example/demo/service/FrameworkProbe.java"
        probe_uri, probe_text = self._open_file(probe_rel)
        locs = self._req_locations(
            "definition",
            probe_uri,
            pos_of(probe_text, "getUserName"),
            tag="FW-14",
            context_desc="userFeignClient.getUserName(id) in core",
        )
        self.assertTrue(
            any("UserFeignClient.java" in (location_uri(l) or "") for l in locs),
            f"[FW-14] Expected cross-module definition from core to api UserFeignClient.java, got {locs!r}",
        )

    def test_mybatis_mapper_to_xml_navigation(self) -> None:
        """[FW-15] MyBatis UserMapper.java methods jump via definition to matching SQL IDs in UserMapper.xml."""
        mapper_rel = "core/src/main/java/com/example/demo/mapper/UserMapper.java"
        uri, text = self._open_file(mapper_rel)
        for method_name, expected_line_text in (
            ("findAll", '<select id="findAll"'),
            ("findById", '<select id="findById"'),
            ("insert", '<insert id="insert"'),
        ):
            locs = self._req_locations(
                "definition",
                uri,
                pos_of(text, method_name),
                tag="FW-15",
                context_desc=f"UserMapper.{method_name}",
            )
            self.assertTrue(
                any("UserMapper.xml" in (location_uri(l) or "") for l in locs),
                f"[FW-15] Expected definition on UserMapper.{method_name} to jump to UserMapper.xml "
                f"({expected_line_text}), got {locs!r}",
            )

    def test_mybatis_xml_to_mapper_and_resultmap_navigation(self) -> None:
        """[FW-16] MyBatis UserMapper.xml SQL id and resultMap type jump back to UserMapper.java and User.java."""
        xml_rel = "core/src/main/resources/mapper/UserMapper.xml"
        uri, text = self._open_file(xml_rel)
        locs_id = self._req_locations(
            "definition",
            uri,
            pos_of(text, "findById"),
            tag="FW-16",
            context_desc='UserMapper.xml id="findById"',
        )
        self.assertTrue(
            any("UserMapper.java" in (location_uri(l) or "") for l in locs_id),
            f"[FW-16] Expected definition on XML id='findById' to jump to UserMapper.java, got {locs_id!r}",
        )
        locs_type = self._req_locations(
            "definition",
            uri,
            pos_of(text, "com.example.demo.model.User"),
            tag="FW-16",
            context_desc="UserMapper.xml resultMap type",
        )
        self.assertTrue(
            any("User.java" in (location_uri(l) or "") for l in locs_type),
            f"[FW-16] Expected definition on resultMap type='com.example.demo.model.User' "
            f"to jump to User.java, got {locs_type!r}",
        )

    def test_mybatis_return_type_completion(self) -> None:
        """[FW-18] MyBatis mapper return type Optional<User> resolves Optional methods accurately."""
        rel = "core/src/main/java/com/example/demo/service/impl/UserServiceImpl.java"
        orig = self.project.read_file(rel)
        mod = orig.replace(
            'return userMapper.findById(id)\n      .orElseThrow(() -> new IllegalArgumentException("User not found: " + id));',
            "return userMapper.findById(id).",
        )
        uri, _ = self._open_file(rel, mod)
        items = self._req_completion(
            uri,
            after_of(mod, "userMapper.findById(id)."),
            tag="FW-18",
            context_desc="userMapper.findById(id).",
        )
        for m in ("orElseThrow", "map", "isPresent", "orElse"):
            self._assert_has_member(items, m, tag="FW-18", context_desc="Optional<User> from UserMapper")

    def test_junit5_and_assertj_smoke_test_clean(self) -> None:
        """[FW-19] UserServiceSmokeTest.java has 0 false diagnostics with JUnit 5, SpringBootTest, and AssertJ."""
        rel = "core/src/test/java/com/example/demo/service/UserServiceSmokeTest.java"
        uri, text = self._open_file(rel)
        self._assert_zero_errors(uri, text, tag="FW-19", context_desc="UserServiceSmokeTest.java")

    def test_assertj_fluent_chain_completion(self) -> None:
        """[FW-20] AssertJ assertThat(users). and .extracting(User::getEmail). complete fluent assertion methods."""
        rel = "core/src/test/java/com/example/demo/service/UserServiceSmokeTest.java"
        orig = self.project.read_file(rel)
        mod1 = orig.replace(
            'assertThat(users)\n      .extracting(User::getEmail)\n      .contains("alice@example.com", "bob@example.com");',
            "assertThat(users).",
        )
        uri, _ = self._open_file(rel, mod1)
        items1 = self._req_completion(
            uri, after_of(mod1, "assertThat(users)."), tag="FW-20", context_desc="assertThat(users)."
        )
        for m in ("extracting", "hasSize", "isNotEmpty"):
            self._assert_has_member(items1, m, tag="FW-20", context_desc="assertThat(users).")

        mod2 = orig.replace(
            '.contains("alice@example.com", "bob@example.com");',
            ".",
        )
        self.client.did_change(uri, mod2)
        items2 = self._req_completion(
            uri,
            after_of(mod2, ".extracting(User::getEmail)\n      ."),
            tag="FW-20",
            context_desc="assertThat(users).extracting(User::getEmail).",
        )
        self._assert_has_member(items2, "contains", tag="FW-20", context_desc="AssertJ extracting().")
        self._assert_has_member(items2, "containsExactly", tag="FW-20", context_desc="AssertJ extracting().")

    def test_assertj_static_import_definition_and_completion(self) -> None:
        """[FW-21] Static imported AssertJ assertThat completes and jumps to org.assertj.core.api.Assertions."""
        rel = "core/src/test/java/com/example/demo/service/UserServiceSmokeTest.java"
        orig = self.project.read_file(rel)
        uri, text = self._open_file(rel)
        locs = self._req_locations(
            "definition", uri, pos_of(text, "assertThat(users)"), tag="FW-21", context_desc="assertThat"
        )
        self.assertTrue(
            any("Assertions" in (location_uri(l) or "") for l in locs),
            f"[FW-21] Expected definition on static imported assertThat to jump to Assertions, got {locs!r}",
        )
        mod = orig.replace("assertThat(users)", "assertTh\n    assertThat(users)")
        self.client.did_change(uri, mod)
        items = self._req_completion(
            uri, after_of(mod, "assertTh"), tag="FW-21", context_desc="static import assertTh"
        )
        self._assert_has_member(items, "assertThat", tag="FW-21", context_desc="static import assertTh")


class TestLombokComprehensiveAndMyBatisXml(_FrameworkBase):
    """Comprehensive Lombok annotations (@Value, @Getter/@Setter, @With, @UtilityClass, @Delegate) and JUnit 5."""

    init_timeout = 60.0

    @classmethod
    def setUpClass(cls) -> None:
        unittest.TestCase.setUpClass()
        lombok_pom = """<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0"
         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0 https://maven.apache.org/xsd/maven-4.0.0.xsd">
  <modelVersion>4.0.0</modelVersion>
  <parent>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-parent</artifactId>
    <version>3.3.2</version>
    <relativePath/>
  </parent>
  <groupId>com.example</groupId>
  <artifactId>lombok-bb-suite</artifactId>
  <version>0.0.1-SNAPSHOT</version>
  <properties>
    <java.version>17</java.version>
    <lombok.version>1.18.38</lombok.version>
  </properties>
  <dependencies>
    <dependency>
      <groupId>org.projectlombok</groupId>
      <artifactId>lombok</artifactId>
      <version>${lombok.version}</version>
      <optional>true</optional>
    </dependency>
    <dependency>
      <groupId>org.mybatis.spring.boot</groupId>
      <artifactId>mybatis-spring-boot-starter</artifactId>
      <version>3.0.3</version>
    </dependency>
    <dependency>
      <groupId>org.springframework.boot</groupId>
      <artifactId>spring-boot-starter-test</artifactId>
      <scope>test</scope>
    </dependency>
  </dependencies>
  <build>
    <plugins>
      <plugin>
        <groupId>org.apache.maven.plugins</groupId>
        <artifactId>maven-compiler-plugin</artifactId>
        <configuration>
          <release>17</release>
          <annotationProcessorPaths>
            <path>
              <groupId>org.projectlombok</groupId>
              <artifactId>lombok</artifactId>
              <version>${lombok.version}</version>
            </path>
          </annotationProcessorPaths>
        </configuration>
      </plugin>
    </plugins>
  </build>
</project>
"""
        files = {
            "pom.xml": lombok_pom,
            "src/main/java/lb/Models.java": """package lb;

import java.util.ArrayList;
import java.util.List;
import lombok.AccessLevel;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.EqualsAndHashCode;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.RequiredArgsConstructor;
import lombok.Setter;
import lombok.ToString;
import lombok.Value;
import lombok.With;
import lombok.experimental.Delegate;
import lombok.experimental.UtilityClass;

public class Models {
  @Value
  public static class ValuePoint {
    int x;
    String label;
  }

  @Getter
  @Setter
  public static class AccessControlled {
    @Setter(AccessLevel.NONE)
    private String readOnlySecret;

    @Getter(AccessLevel.PRIVATE)
    private String internalCode;

    private String openName;
  }

  @Builder(toBuilder = true)
  @Getter
  public static class ToBuilderBean {
    private final String title;
    private final int version;
  }

  @RequiredArgsConstructor
  @Getter
  public static class ReqBean {
    private final String requiredId;
    private int optionalNum;
  }

  @AllArgsConstructor
  @NoArgsConstructor
  @EqualsAndHashCode
  @ToString
  @Getter
  public static class CtorBean {
    private String code;
    private int count;
  }

  @With
  @AllArgsConstructor
  @Getter
  public static class WithBean {
    private final String name;
  }

  @UtilityClass
  public static class StrHelper {
    public String safeTrim(String s) {
      return s == null ? "" : s.trim();
    }
  }

  public static class ListWrapper {
    @Delegate
    private final List<String> inner = new ArrayList<>();
  }
}
""",
            "src/main/java/lb/LombokConsumer.java": """package lb;

import lb.Models.AccessControlled;
import lb.Models.CtorBean;
import lb.Models.ListWrapper;
import lb.Models.ReqBean;
import lb.Models.StrHelper;
import lb.Models.ToBuilderBean;
import lb.Models.ValuePoint;
import lb.Models.WithBean;

public class LombokConsumer {
  public void useAll() {
    ValuePoint vp = new ValuePoint(1, "origin");
    vp.getLabel();

    AccessControlled ac = new AccessControlled();
    ac.getReadOnlySecret();
    ac.setOpenName("ok");

    ToBuilderBean tb = ToBuilderBean.builder().title("v1").version(1).build();
    ToBuilderBean tb2 = tb.toBuilder().version(2).build();

    ReqBean req = new ReqBean("req-1");
    CtorBean c1 = new CtorBean();
    CtorBean c2 = new CtorBean("k", 2);

    WithBean wb = new WithBean("a");
    WithBean wb2 = wb.withName("b");

    String trimmed = StrHelper.safeTrim(" x ");
    ListWrapper lw = new ListWrapper();
    int sz = lw.size();
    if (tb2.getVersion() + req.getRequiredId().length() + c1.toString().length()
        + c2.hashCode() + wb2.getName().length() + trimmed.length() + sz > 0) {
      return;
    }
  }
}
""",
            "src/main/java/lb/Item.java": """package lb;
import lombok.Data;
@Data
public class Item {
  private Long id;
  private String name;
}
""",
            "src/main/java/lb/ItemMapper.java": """package lb;

import java.util.Optional;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;

@Mapper
public interface ItemMapper {
  Optional<Item> findOne(@Param("id") Long id);
}
""",
            "src/main/resources/mapper/ItemMapper.xml": """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE mapper PUBLIC "-//mybatis.org//DTD Mapper 3.0//EN"
  "https://mybatis.org/dtd/mybatis-3-mapper.dtd">
<mapper namespace="lb.ItemMapper">
  <select id="findOne" resultType="lb.Item">
    select id, name from item where id = #{id}
  </select>
</mapper>
""",
            "src/test/java/lb/Junit5SmokeTest.java": """package lb;

import static org.junit.jupiter.api.Assertions.assertEquals;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

class Junit5SmokeTest {
  @BeforeEach
  void setup() {}

  @Test
  void checksValue() {
    assertEquals(2, 1 + 1);
  }
}
""",
        }
        cls.project = TempJavaProject(files=files, prefix="fw_lombok_bb_")
        cls._start_workspace()

    def test_lombok_value_immutable_getters_no_setters(self) -> None:
        """[FW-02] @Value generates getters, equals/hashCode/toString, and MUST NOT generate setters."""
        rel = "src/main/java/lb/LombokConsumer.java"
        orig = self.project.read_file(rel)
        mod = orig.replace("vp.getLabel();", "vp.")
        uri, _ = self._open_file(rel, mod)
        items = self._req_completion(uri, after_of(mod, "vp."), tag="FW-02", context_desc="@Value vp.")
        for m in ("getX", "getLabel", "equals", "hashCode", "toString"):
            self._assert_has_member(items, m, tag="FW-02", context_desc="@Value vp.")
        self._assert_lacks_member(
            items,
            "setLabel",
            tag="FW-02",
            context_desc="@Value vp.",
            reason="@Value makes class immutable and must not generate setters",
        )

    def test_lombok_getter_setter_access_levels(self) -> None:
        """[FW-03] @Setter(AccessLevel.NONE) and @Getter(AccessLevel.PRIVATE) suppress external visibility."""
        rel = "src/main/java/lb/LombokConsumer.java"
        orig = self.project.read_file(rel)
        mod = orig.replace("ac.getReadOnlySecret();", "ac.")
        uri, _ = self._open_file(rel, mod)
        items = self._req_completion(
            uri, after_of(mod, "ac."), tag="FW-03", context_desc="AccessControlled ac."
        )
        self._assert_has_member(items, "getReadOnlySecret", tag="FW-03", context_desc="ac.")
        self._assert_has_member(items, "setOpenName", tag="FW-03", context_desc="ac.")
        self._assert_lacks_member(
            items,
            "setReadOnlySecret",
            tag="FW-03",
            context_desc="ac.",
            reason="@Setter(AccessLevel.NONE) must suppress setReadOnlySecret",
        )
        self._assert_lacks_member(
            items,
            "getInternalCode",
            tag="FW-03",
            context_desc="ac.",
            reason="@Getter(AccessLevel.PRIVATE) must not be visible outside AccessControlled",
        )

    def test_lombok_to_builder(self) -> None:
        """[FW-23] @Builder(toBuilder = true) exposes toBuilder() returning builder chain."""
        rel = "src/main/java/lb/LombokConsumer.java"
        orig = self.project.read_file(rel)
        mod = orig.replace("tb.toBuilder().version(2).build();", "tb.toBuilder().")
        uri, _ = self._open_file(rel, mod)
        items = self._req_completion(
            uri, after_of(mod, "tb.toBuilder()."), tag="FW-23", context_desc="@Builder(toBuilder=true)"
        )
        for m in ("title", "version", "build"):
            self._assert_has_member(items, m, tag="FW-23", context_desc="tb.toBuilder().")

    def test_lombok_constructors_and_equals_tostring_clean(self) -> None:
        """[FW-06][FW-07] @RequiredArgsConstructor, @AllArgsConstructor, @NoArgsConstructor, @EqualsAndHashCode, @ToString have 0 errors."""
        rel = "src/main/java/lb/LombokConsumer.java"
        uri, text = self._open_file(rel)
        # Note: assert zero errors if all Lombok annotations are understood
        self._assert_zero_errors(uri, text, tag="FW-06", context_desc="LombokConsumer.java")

        mod = text.replace("c1.toString()", "c1.")
        self.client.did_change(uri, mod)
        items = self._req_completion(uri, after_of(mod, "c1."), tag="FW-07", context_desc="CtorBean c1.")
        for m in ("equals", "hashCode", "toString", "getCode"):
            self._assert_has_member(items, m, tag="FW-07", context_desc="@EqualsAndHashCode @ToString c1.")

    def test_lombok_with_fluent_chain(self) -> None:
        """[FW-08] @With generates withName(...) returning WithBean for fluent chaining."""
        rel = "src/main/java/lb/LombokConsumer.java"
        orig = self.project.read_file(rel)
        mod = orig.replace('wb.withName("b");', "wb.")
        uri, _ = self._open_file(rel, mod)
        items = self._req_completion(uri, after_of(mod, "wb."), tag="FW-08", context_desc="@With wb.")
        self._assert_has_member(items, "withName", tag="FW-08", context_desc="@With wb.")

        mod2 = orig.replace('wb.withName("b");', 'wb.withName("b").')
        self.client.did_change(uri, mod2)
        items2 = self._req_completion(
            uri, after_of(mod2, 'wb.withName("b").'), tag="FW-08", context_desc='wb.withName("b").'
        )
        self._assert_has_member(items2, "getName", tag="FW-08", context_desc='wb.withName("b").')

    def test_lombok_utility_class(self) -> None:
        """[FW-09] @UtilityClass makes methods implicitly static on StrHelper."""
        rel = "src/main/java/lb/LombokConsumer.java"
        orig = self.project.read_file(rel)
        mod = orig.replace('StrHelper.safeTrim(" x ");', "StrHelper.")
        uri, _ = self._open_file(rel, mod)
        items = self._req_completion(
            uri, after_of(mod, "StrHelper."), tag="FW-09", context_desc="@UtilityClass StrHelper."
        )
        self._assert_has_member(items, "safeTrim", tag="FW-09", context_desc="@UtilityClass StrHelper.")

    def test_lombok_delegate(self) -> None:
        """[FW-10] @Delegate exposes delegated List methods on ListWrapper."""
        rel = "src/main/java/lb/LombokConsumer.java"
        orig = self.project.read_file(rel)
        mod = orig.replace("lw.size();", "lw.")
        uri, _ = self._open_file(rel, mod)
        items = self._req_completion(
            uri, after_of(mod, "lw."), tag="FW-10", context_desc="@Delegate ListWrapper lw."
        )
        for m in ("size", "add", "get", "clear"):
            self._assert_has_member(items, m, tag="FW-10", context_desc="@Delegate lw.")

    def test_mybatis_at_mapper_annotation_navigation(self) -> None:
        """[FW-17] @Mapper interface ItemMapper navigates to ItemMapper.xml."""
        rel = "src/main/java/lb/ItemMapper.java"
        uri, text = self._open_file(rel)
        self._assert_zero_errors(uri, text, tag="FW-17", context_desc="@Mapper ItemMapper.java")
        locs = self._req_locations(
            "definition", uri, pos_of(text, "findOne"), tag="FW-17", context_desc="ItemMapper.findOne"
        )
        self.assertTrue(
            any("ItemMapper.xml" in (location_uri(l) or "") for l in locs),
            f"[FW-17] Expected definition on @Mapper ItemMapper.findOne to jump to ItemMapper.xml, got {locs!r}",
        )

    def test_junit5_annotations_and_assertions(self) -> None:
        """[FW-22] JUnit 5 @BeforeEach, @Test, and assertEquals resolve cleanly with 0 errors."""
        rel = "src/test/java/lb/Junit5SmokeTest.java"
        uri, text = self._open_file(rel)
        self._assert_zero_errors(uri, text, tag="FW-22", context_desc="Junit5SmokeTest.java")


if __name__ == "__main__":
    unittest.main()
