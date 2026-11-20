"""Black-box tests for java-lsp project layouts, classpath scoping, Maven/Gradle, external libs, and dynamic updates."""

from __future__ import annotations

import os
import time
import unittest
from pathlib import Path
from typing import Any

from tests.blackbox.lsp_client import (
    LspClient,
    LspClientError,
    LspResponseError,
    REPO_ROOT,
    TempJavaProject,
    extract_completion_items,
    extract_hover_text,
    extract_locations,
    location_uri,
    pos_of,
    uri_to_path,
)

SPRING_DEMO_DIR = REPO_ROOT / "java17-spring-demo"


def _is_error_diag(diag: dict[str, Any]) -> bool:
    sev = diag.get("severity")
    return sev is None or sev == 1


def _format_diags(diags: list[dict[str, Any]]) -> str:
    return str(
        [
            (
                d.get("range", {}).get("start", {}).get("line"),
                d.get("severity"),
                d.get("message"),
            )
            for d in diags
        ]
    )


class _BaseProjTestCase(unittest.TestCase):
    client: LspClient
    project: TempJavaProject

    def _start_client(self, root_path: Path, init_timeout: float = 60.0) -> LspClient:
        client = LspClient(root_path=root_path, default_timeout=20.0)
        self.addCleanup(client.close)
        client.start()
        try:
            client.initialize_and_ready(root_path=root_path, timeout=init_timeout)
        except LspClientError as exc:
            self.fail(f"LSP initialize_and_ready failed for {root_path}: {exc}")
        return client

    def _safe_request(self, tag: str, client: LspClient, fn: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except LspResponseError as err:
            self.fail(
                f"[{tag}] Server returned JSON-RPC error for {err.method}: "
                f"code={err.code}, message={err.message!r}, data={err.data!r}"
            )
        except TimeoutError as err:
            self.fail(f"[{tag}] Request timed out: {err}")
        except LspClientError as err:
            self.fail(f"[{tag}] LSP client error: {err}")

    def _open_and_await_diags(
        self,
        client: LspClient,
        uri: str,
        text: str,
        timeout: float = 5.0,
    ) -> list[dict[str, Any]]:
        try:
            seq = client.did_open(uri, text)
            return client.wait_for_diagnostics_quiescent(
                uri, timeout=timeout, quiet_period=0.25, after_seq=seq
            )
        except TimeoutError:
            return client.get_diagnostics(uri)
        except LspClientError as exc:
            self.fail(f"LSP error waiting for diagnostics on {uri}: {exc}")


class TestMultiModuleMavenSpringDemo(_BaseProjTestCase):
    """Multi-module Maven (api <-> core) and src/test/java support on isolated java17-spring-demo copy."""

    _init_error: str | None = None

    @classmethod
    def setUpClass(cls) -> None:
        cls.project = TempJavaProject(prefix="proj_spring_")
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
            self.fail(f"[PROJ-SPRING] {self._init_error}")

    def test_multimodule_cross_module_core_to_api(self) -> None:
        """[PROJ-01] Core module must resolve api module UserFeignClient (definition, completion, hover, 0 errors)."""
        bridge_rel = "core/src/main/java/com/example/demo/service/UserRemoteBridge.java"
        bridge_code = """\
package com.example.demo.service;

import com.example.demo.api.UserFeignClient;

public class UserRemoteBridge {
  private final UserFeignClient feignClient;

  public UserRemoteBridge(UserFeignClient feignClient) {
    this.feignClient = feignClient;
  }

  public String fetch(Long id) {
    return feignClient.getUserName(id);
  }
}
"""
        self.project.write_file(bridge_rel, bridge_code)
        bridge_uri = self.project.uri(bridge_rel)
        api_uri = self.project.uri("api/src/main/java/com/example/demo/api/UserFeignClient.java")

        def _cleanup() -> None:
            try:
                self.client.did_close(bridge_uri)
            except LspClientError:
                pass
            self.project.remove_file(bridge_rel)

        self.addCleanup(_cleanup)

        # Inform server about new file + open it.
        self.client.workspace_did_create_files([bridge_uri])
        self.client.workspace_did_change_watched_files([{"uri": bridge_uri, "type": 1}])
        diags = self._open_and_await_diags(self.client, bridge_uri, bridge_code, timeout=5.0)
        errors = [d for d in diags if _is_error_diag(d)]
        self.assertEqual(
            [],
            errors,
            f"[PROJ-01] Expected 0 Error diagnostics when core class imports api UserFeignClient, got: {_format_diags(errors)}",
        )

        # Definition on UserFeignClient type and getUserName method must jump to api module.
        locs = extract_locations(
            self._safe_request(
                "PROJ-01-def-type",
                self.client,
                self.client.definition,
                bridge_uri,
                pos_of(bridge_code, "UserFeignClient", occurrence=2),
            )
        )
        uris = [location_uri(l) for l in locs]
        self.assertIn(
            api_uri,
            uris,
            f"[PROJ-01] Expected definition on UserFeignClient in core to jump to {api_uri}, got: {locs!r}",
        )

        method_locs = extract_locations(
            self._safe_request(
                "PROJ-01-def-method",
                self.client,
                self.client.definition,
                bridge_uri,
                pos_of(bridge_code, "getUserName"),
            )
        )
        self.assertIn(
            api_uri,
            [location_uri(l) for l in method_locs],
            f"[PROJ-01] Expected definition on feignClient.getUserName to jump to {api_uri}, got: {method_locs!r}",
        )

        # Hover on getUserName.
        hover_res = self._safe_request(
            "PROJ-01-hover",
            self.client,
            self.client.hover,
            bridge_uri,
            pos_of(bridge_code, "getUserName"),
        )
        hover_txt = extract_hover_text(hover_res)
        self.assertIn(
            "getUserName",
            hover_txt,
            f"[PROJ-01] Expected hover on cross-module feignClient.getUserName to contain 'getUserName', got: {hover_txt!r}",
        )

        # Completion after feignClient. via didChange.
        comp_code = bridge_code.replace(
            "return feignClient.getUserName(id);",
            "return feignClient.",
        )
        self.client.did_change(bridge_uri, text=comp_code)
        comp_res = self._safe_request(
            "PROJ-01-comp",
            self.client,
            self.client.completion,
            bridge_uri,
            pos_of(comp_code, "feignClient.", offset=len("feignClient.")),
            trigger_kind=2,
            trigger_character=".",
        )
        labels = [item.get("label") for item in extract_completion_items(comp_res)]
        self.assertTrue(
            any("getUserName" in str(lbl) for lbl in labels),
            f"[PROJ-01] Expected 'getUserName' in cross-module completion on feignClient., got labels={labels[:20]}",
        )

    def test_spring_demo_test_scope_and_main_resolution(self) -> None:
        """[PROJ-05] Test class UserServiceSmokeTest.java sees main classes and test-scoped JUnit/AssertJ/SpringBootTest."""
        test_rel = "core/src/test/java/com/example/demo/service/UserServiceSmokeTest.java"
        test_code = self.project.read_file(test_rel)
        test_uri = self.project.uri(test_rel)
        service_uri = self.project.uri("core/src/main/java/com/example/demo/service/UserService.java")

        self.addCleanup(lambda: self.client.did_close(test_uri))
        diags = self._open_and_await_diags(self.client, test_uri, test_code, timeout=6.0)
        errors = [d for d in diags if _is_error_diag(d)]
        self.assertEqual(
            [],
            errors,
            f"[PROJ-05] Expected 0 Error diagnostics on UserServiceSmokeTest.java, got: {_format_diags(errors)}",
        )

        # Definition on UserService in test jumps to main source.
        svc_locs = extract_locations(
            self._safe_request(
                "PROJ-05-def-svc",
                self.client,
                self.client.definition,
                test_uri,
                pos_of(test_code, "UserService", occurrence=2),
            )
        )
        self.assertIn(
            service_uri,
            [location_uri(l) for l in svc_locs],
            f"[PROJ-05] Expected UserService definition from test to resolve to {service_uri}, got: {svc_locs!r}",
        )

        # Test-scoped dependency symbols (@Test, @SpringBootTest, assertThat) resolve.
        for sym, pos in (
            ("SpringBootTest", pos_of(test_code, "@SpringBootTest", offset=1)),
            ("Test", pos_of(test_code, "@Test", offset=1)),
            ("assertThat", pos_of(test_code, "assertThat(")),
        ):
            locs = extract_locations(
                self._safe_request(
                    f"PROJ-05-def-{sym}",
                    self.client,
                    self.client.definition,
                    test_uri,
                    pos,
                )
            )
            self.assertTrue(
                locs and location_uri(locs[0]),
                f"[PROJ-05] Expected test-scoped symbol {sym!r} to resolve via definition, got: {locs!r}",
            )


class TestProjectLayoutsAndIsolation(_BaseProjTestCase):
    """Single-module Maven, Gradle, standalone Java, main/test isolation, and BOM/parent/transitive deps."""

    def test_single_module_maven_with_dependency(self) -> None:
        """[PROJ-02] Single-module Maven with gson dependency resolves types, completion, and 0 errors."""
        pom = """\
<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0"
         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0 https://maven.apache.org/xsd/maven-4.0.0.xsd">
  <modelVersion>4.0.0</modelVersion>
  <groupId>com.demo</groupId>
  <artifactId>single-mvn</artifactId>
  <version>1.0.0</version>
  <dependencies>
    <dependency>
      <groupId>com.google.code.gson</groupId>
      <artifactId>gson</artifactId>
      <version>2.10</version>
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
        app = """\
package com.demo;

import com.google.gson.Gson;

public class SingleMavenApp {
  public String encode(Object obj) {
    Gson gson = new Gson();
    return gson.toJson(obj);
  }
}
"""
        proj = TempJavaProject(
            files={"pom.xml": pom, "src/main/java/com/demo/SingleMavenApp.java": app},
            prefix="proj_single_mvn_",
        )
        self.addCleanup(proj.close)
        client = self._start_client(proj.root)

        uri = proj.uri("src/main/java/com/demo/SingleMavenApp.java")
        diags = self._open_and_await_diags(client, uri, app)
        errors = [d for d in diags if _is_error_diag(d)]
        self.assertEqual([], errors, f"[PROJ-02] Expected 0 errors with gson dep, got: {_format_diags(errors)}")

        locs = extract_locations(
            self._safe_request("PROJ-02-def", client, client.definition, uri, pos_of(app, "Gson", occurrence=2))
        )
        self.assertTrue(locs, "[PROJ-02] Expected definition on Gson to resolve.")

        comp_app = app.replace("return gson.toJson(obj);", "return gson.")
        client.did_change(uri, text=comp_app)
        comp = self._safe_request(
            "PROJ-02-comp",
            client,
            client.completion,
            uri,
            pos_of(comp_app, "gson.", offset=len("gson.")),
            trigger_kind=2,
            trigger_character=".",
        )
        labels = [i.get("label") for i in extract_completion_items(comp)]
        self.assertTrue(
            any("toJson" in str(lbl) for lbl in labels),
            f"[PROJ-02] Expected 'toJson' in completion on Gson, got labels={labels[:20]}",
        )

    def test_gradle_groovy_and_kotlin_dsl_projects(self) -> None:
        """[PROJ-03] Gradle projects (build.gradle and build.gradle.kts) resolve project sources and dependencies."""
        helper = """\
package com.demo.grad;

public class GradHelper {
  public String value() { return "gradle-ok"; }
}
"""
        main = """\
package com.demo.grad;

import com.google.gson.Gson;

public class GradMain {
  public String run() {
    GradHelper h = new GradHelper();
    return new Gson().toJson(h.value());
  }
}
"""
        variants = [
            (
                "groovy",
                "build.gradle",
                """\
plugins {
    id 'java'
}
repositories {
    mavenLocal()
}
dependencies {
    implementation 'com.google.code.gson:gson:2.10'
}
""",
            ),
            (
                "kotlin",
                "build.gradle.kts",
                """\
plugins {
    java
}
repositories {
    mavenLocal()
}
dependencies {
    implementation("com.google.code.gson:gson:2.10")
}
""",
            ),
        ]

        failures: list[str] = []
        for label, build_file, build_text in variants:
            proj = TempJavaProject(
                files={
                    build_file: build_text,
                    "src/main/java/com/demo/grad/GradHelper.java": helper,
                    "src/main/java/com/demo/grad/GradMain.java": main,
                },
                prefix=f"proj_gradle_{label}_",
            )
            self.addCleanup(proj.close)
            client = self._start_client(proj.root)

            h_uri = proj.uri("src/main/java/com/demo/grad/GradHelper.java")
            m_uri = proj.uri("src/main/java/com/demo/grad/GradMain.java")
            diags = self._open_and_await_diags(client, m_uri, main)
            errors = [d for d in diags if _is_error_diag(d)]
            if errors:
                failures.append(
                    f"[PROJ-03:{label}] Expected 0 errors in Gradle ({build_file}) project, got: {_format_diags(errors)}"
                )
            locs = extract_locations(
                self._safe_request(
                    f"PROJ-03:{label}-def-local",
                    client,
                    client.definition,
                    m_uri,
                    pos_of(main, "GradHelper", occurrence=1),
                )
            )
            if h_uri not in [location_uri(l) for l in locs]:
                failures.append(
                    f"[PROJ-03:{label}] Expected GradHelper definition to resolve to {h_uri}, got: {locs!r}"
                )
            gson_locs = extract_locations(
                self._safe_request(
                    f"PROJ-03:{label}-def-dep",
                    client,
                    client.definition,
                    m_uri,
                    pos_of(main, "Gson", occurrence=2),
                )
            )
            if not gson_locs:
                failures.append(
                    f"[PROJ-03:{label}] Expected Gradle dependency Gson definition to resolve, got: {gson_locs!r}"
                )
        self.assertEqual([], failures, "\n".join(failures))

    def test_standalone_java_file_without_build_descriptor(self) -> None:
        """[PROJ-04] Standalone .java file without pom.xml/build.gradle resolves JDK types with 0 errors."""
        code = """\
import java.util.ArrayList;
import java.util.List;

public class StandaloneFile {
  public int count() {
    List<String> items = new ArrayList<>();
    items.add("one");
    return items.size();
  }
}
"""
        proj = TempJavaProject(files={"StandaloneFile.java": code}, prefix="proj_standalone_")
        self.addCleanup(proj.close)
        client = self._start_client(proj.root)

        uri = proj.uri("StandaloneFile.java")
        diags = self._open_and_await_diags(client, uri, code)
        errors = [d for d in diags if _is_error_diag(d)]
        self.assertEqual(
            [],
            errors,
            f"[PROJ-04] Expected 0 errors on standalone Java file, got: {_format_diags(errors)}",
        )

        locs = extract_locations(
            self._safe_request("PROJ-04-def", client, client.definition, uri, pos_of(code, "List", occurrence=2))
        )
        self.assertTrue(locs, "[PROJ-04] Expected definition on JDK List in standalone file to resolve.")

        hover_txt = extract_hover_text(
            self._safe_request("PROJ-04-hover", client, client.hover, uri, pos_of(code, "size"))
        )
        self.assertIn(
            "size",
            hover_txt,
            f"[PROJ-04] Expected hover on items.size() in standalone file, got: {hover_txt!r}",
        )

    def test_main_vs_test_classpath_isolation(self) -> None:
        """[PROJ-06] Test code sees main + test deps; main code must NOT see test classes or test-scoped deps."""
        pom = """\
<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0"
         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0 https://maven.apache.org/xsd/maven-4.0.0.xsd">
  <modelVersion>4.0.0</modelVersion>
  <groupId>com.demo</groupId>
  <artifactId>scope-isolation</artifactId>
  <version>1.0.0</version>
  <dependencies>
    <dependency>
      <groupId>org.junit.jupiter</groupId>
      <artifactId>junit-jupiter-api</artifactId>
      <version>5.10.3</version>
      <scope>test</scope>
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
        valid_main = """\
package com.demo;

public class ValidMain {
  public int two() { return 2; }
}
"""
        leaky_main = """\
package com.demo;

import org.junit.jupiter.api.Test;

public class LeakyMain {
  @Test
  public void illegalFromMain() {
    TestHelper.assist();
  }
}
"""
        test_helper = """\
package com.demo;

public class TestHelper {
  public static int assist() { return 9; }
}
"""
        real_test = """\
package com.demo;

import org.junit.jupiter.api.Test;

public class RealTest {
  @Test
  void ok() {
    int sum = new ValidMain().two() + TestHelper.assist();
  }
}
"""
        proj = TempJavaProject(
            files={
                "pom.xml": pom,
                "src/main/java/com/demo/ValidMain.java": valid_main,
                "src/main/java/com/demo/LeakyMain.java": leaky_main,
                "src/test/java/com/demo/TestHelper.java": test_helper,
                "src/test/java/com/demo/RealTest.java": real_test,
            },
            prefix="proj_scope_iso_",
        )
        self.addCleanup(proj.close)
        client = self._start_client(proj.root)

        u_valid = proj.uri("src/main/java/com/demo/ValidMain.java")
        u_leaky = proj.uri("src/main/java/com/demo/LeakyMain.java")
        u_test = proj.uri("src/test/java/com/demo/RealTest.java")

        d_valid = self._open_and_await_diags(client, u_valid, valid_main)
        self.assertEqual(
            [],
            [d for d in d_valid if _is_error_diag(d)],
            f"[PROJ-06] ValidMain should have 0 errors, got: {_format_diags(d_valid)}",
        )

        d_test = self._open_and_await_diags(client, u_test, real_test)
        self.assertEqual(
            [],
            [d for d in d_test if _is_error_diag(d)],
            f"[PROJ-06] RealTest should see ValidMain + TestHelper + JUnit @Test with 0 errors, got: {_format_diags(d_test)}",
        )

        d_leaky = self._open_and_await_diags(client, u_leaky, leaky_main)
        leaky_errors = [d for d in d_leaky if _is_error_diag(d)]
        self.assertGreaterEqual(
            len(leaky_errors),
            2,
            "[PROJ-06] Main source LeakyMain illegally uses test-scoped JUnit @Test and test class TestHelper; "
            f"expected >=2 Error diagnostics due to main/test classpath isolation, got: {_format_diags(d_leaky)}",
        )

    def test_maven_parent_bom_properties_and_transitive_deps(self) -> None:
        """[PROJ-07] Maven parent POM, <dependencyManagement>, ${property} versions, and transitive deps must resolve."""
        parent_pom = """\
<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0"
         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0 https://maven.apache.org/xsd/maven-4.0.0.xsd">
  <modelVersion>4.0.0</modelVersion>
  <groupId>com.demo</groupId>
  <artifactId>bom-parent</artifactId>
  <version>1.0.0</version>
  <packaging>pom</packaging>
  <modules>
    <module>child</module>
  </modules>
  <properties>
    <jackson.version>2.17.2</jackson.version>
  </properties>
  <dependencyManagement>
    <dependencies>
      <dependency>
        <groupId>com.fasterxml.jackson.core</groupId>
        <artifactId>jackson-databind</artifactId>
        <version>${jackson.version}</version>
      </dependency>
    </dependencies>
  </dependencyManagement>
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
        child_pom = """\
<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0"
         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0 https://maven.apache.org/xsd/maven-4.0.0.xsd">
  <modelVersion>4.0.0</modelVersion>
  <parent>
    <groupId>com.demo</groupId>
    <artifactId>bom-parent</artifactId>
    <version>1.0.0</version>
    <relativePath>../pom.xml</relativePath>
  </parent>
  <artifactId>bom-child</artifactId>
  <dependencies>
    <dependency>
      <groupId>com.fasterxml.jackson.core</groupId>
      <artifactId>jackson-databind</artifactId>
    </dependency>
  </dependencies>
</project>
"""
        app = """\
package com.demo.bom;

import com.fasterxml.jackson.annotation.JsonProperty;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;

public class JacksonConsumer {
  public static class Payload {
    @JsonProperty("user_name")
    public String userName;
  }

  public String write(Payload p) throws JsonProcessingException {
    ObjectMapper mapper = new ObjectMapper();
    return mapper.writeValueAsString(p);
  }
}
"""
        proj = TempJavaProject(
            files={
                "pom.xml": parent_pom,
                "child/pom.xml": child_pom,
                "child/src/main/java/com/demo/bom/JacksonConsumer.java": app,
            },
            prefix="proj_bom_",
        )
        self.addCleanup(proj.close)
        client = self._start_client(proj.root)

        uri = proj.uri("child/src/main/java/com/demo/bom/JacksonConsumer.java")
        diags = self._open_and_await_diags(client, uri, app)
        errors = [d for d in diags if _is_error_diag(d)]
        self.assertEqual(
            [],
            errors,
            f"[PROJ-07] Expected 0 errors resolving parent POM + <dependencyManagement> + ${{jackson.version}} + "
            f"transitive jackson-core/jackson-annotations, got: {_format_diags(errors)}",
        )

        unresolved_syms: list[str] = []
        for sym, occ in (("ObjectMapper", 2), ("JsonProcessingException", 2), ("JsonProperty", 2)):
            locs = extract_locations(
                self._safe_request(
                    f"PROJ-07-def-{sym}",
                    client,
                    client.definition,
                    uri,
                    pos_of(app, sym, occurrence=occ),
                )
            )
            if not (locs and location_uri(locs[0])):
                unresolved_syms.append(f"{sym}->{locs!r}")
        self.assertEqual(
            [],
            unresolved_syms,
            f"[PROJ-07] Expected parent-managed/transitive symbols to resolve, failed: {unresolved_syms}",
        )


class TestExternalLibraryInspection(_BaseProjTestCase):
    """Tests for JDK src.zip, Maven *-sources.jar, and CFR bytecode decompilation via lib/cfr.jar."""

    def _assert_definition_file_contains(
        self,
        tag: str,
        client: LspClient,
        uri: str,
        pos: dict[str, int],
        expected_substrings: list[str],
        method_name: str = "definition",
    ) -> Path:
        fn = client.definition if method_name == "definition" else client.implementation
        locs = extract_locations(self._safe_request(tag, client, fn, uri, pos))
        self.assertTrue(locs, f"[{tag}] Expected non-empty {method_name} locations, got: {locs!r}")
        target_uri = location_uri(locs[0])
        self.assertTrue(
            target_uri and target_uri.startswith("file://"),
            f"[{tag}] Expected file:// target URI from {method_name}, got: {locs!r}",
        )
        target_path = uri_to_path(target_uri)
        self.assertTrue(
            target_path.exists() and target_path.suffix == ".java",
            f"[{tag}] Target {target_path} does not exist or is not a .java file (locs={locs!r})",
        )
        content = target_path.read_text(encoding="utf-8", errors="replace")
        for sub in expected_substrings:
            self.assertIn(
                sub,
                content,
                f"[{tag}] Target file {target_path} did not contain expected text {sub!r}.\nPreview:\n{content[:600]}",
            )
        return target_path

    def test_jdk_src_zip_navigation(self) -> None:
        """[PROJ-08] Navigating to JDK types (List, ArrayList, RuntimeException) extracts JDK src.zip."""
        code = """\
package com.demo.ext;

import java.util.ArrayList;
import java.util.List;

public class JdkNav {
  public void demo() {
    List<String> list = new ArrayList<>();
    if (list.isEmpty()) {
      throw new RuntimeException("empty");
    }
  }
}
"""
        proj = TempJavaProject(files={"src/com/demo/ext/JdkNav.java": code}, prefix="proj_jdk_")
        self.addCleanup(proj.close)
        client = self._start_client(proj.root)
        uri = proj.uri("src/com/demo/ext/JdkNav.java")
        self._open_and_await_diags(client, uri, code)

        self._assert_definition_file_contains(
            "PROJ-08-List", client, uri, pos_of(code, "List<String>"), ["interface List"]
        )
        self._assert_definition_file_contains(
            "PROJ-08-ArrayList", client, uri, pos_of(code, "ArrayList<>()"), ["class ArrayList"]
        )
        self._assert_definition_file_contains(
            "PROJ-08-RuntimeException", client, uri, pos_of(code, 'RuntimeException("empty")'), ["class RuntimeException"]
        )
        hover_txt = extract_hover_text(
            self._safe_request("PROJ-08-hover", client, client.hover, uri, pos_of(code, "isEmpty"))
        )
        self.assertTrue(hover_txt.strip(), "[PROJ-08] Expected non-empty hover on JDK List.isEmpty().")

    def test_maven_sources_jar_navigation(self) -> None:
        """[PROJ-09] Navigating to Maven dependencies with *-sources.jar extracts real source files."""
        pom = """\
<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0"
         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0 https://maven.apache.org/xsd/maven-4.0.0.xsd">
  <modelVersion>4.0.0</modelVersion>
  <groupId>com.demo</groupId>
  <artifactId>sources-jar-test</artifactId>
  <version>1.0.0</version>
  <dependencies>
    <dependency>
      <groupId>org.slf4j</groupId>
      <artifactId>slf4j-api</artifactId>
      <version>2.0.13</version>
    </dependency>
    <dependency>
      <groupId>commons-io</groupId>
      <artifactId>commons-io</artifactId>
      <version>2.22.0</version>
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
        code = """\
package com.demo.ext;

import org.apache.commons.io.IOUtils;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

public class SourcesJarNav {
  private static final Logger log = LoggerFactory.getLogger(SourcesJarNav.class);

  public String dump(byte[] data) {
    log.info("dump");
    return new String(IOUtils.toByteArray(data));
  }
}
"""
        proj = TempJavaProject(
            files={"pom.xml": pom, "src/main/java/com/demo/ext/SourcesJarNav.java": code},
            prefix="proj_sources_jar_",
        )
        self.addCleanup(proj.close)
        client = self._start_client(proj.root)
        uri = proj.uri("src/main/java/com/demo/ext/SourcesJarNav.java")
        self._open_and_await_diags(client, uri, code)

        logger_path = self._assert_definition_file_contains(
            "PROJ-09-slf4j", client, uri, pos_of(code, "Logger log"), ["interface Logger"]
        )
        self.assertNotIn(
            "Decompiled with CFR",
            logger_path.read_text(encoding="utf-8", errors="replace"),
            f"[PROJ-09] slf4j-api-2.0.13 has -sources.jar; expected real source rather than CFR decompilation ({logger_path}).",
        )
        ioutils_path = self._assert_definition_file_contains(
            "PROJ-09-commons-io", client, uri, pos_of(code, "IOUtils.toByteArray"), ["class IOUtils"]
        )
        self.assertNotIn(
            "Decompiled with CFR",
            ioutils_path.read_text(encoding="utf-8", errors="replace"),
            f"[PROJ-09] commons-io-2.22.0 has -sources.jar; expected real source rather than CFR decompilation ({ioutils_path}).",
        )

    def test_cfr_bytecode_decompilation_without_sources_jar(self) -> None:
        """[PROJ-10] Dependencies without *-sources.jar must fall back to CFR bytecode decompilation (lib/cfr.jar)."""
        pom = """\
<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0"
         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0 https://maven.apache.org/xsd/maven-4.0.0.xsd">
  <modelVersion>4.0.0</modelVersion>
  <groupId>com.demo</groupId>
  <artifactId>cfr-decompile-test</artifactId>
  <version>1.0.0</version>
  <dependencies>
    <dependency>
      <groupId>com.google.code.gson</groupId>
      <artifactId>gson</artifactId>
      <version>2.10</version>
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
        code = """\
package com.demo.ext;

import com.google.gson.Gson;

public class CfrNav {
  public String encode(Object v) {
    return new Gson().toJson(v);
  }
}
"""
        proj = TempJavaProject(
            files={"pom.xml": pom, "src/main/java/com/demo/ext/CfrNav.java": code},
            prefix="proj_cfr_",
        )
        self.addCleanup(proj.close)
        client = self._start_client(proj.root)
        uri = proj.uri("src/main/java/com/demo/ext/CfrNav.java")
        self._open_and_await_diags(client, uri, code)

        decompiled_path = self._assert_definition_file_contains(
            "PROJ-10-cfr",
            client,
            uri,
            pos_of(code, "Gson", occurrence=2),
            ["class Gson", "toJson"],
        )
        content = decompiled_path.read_text(encoding="utf-8", errors="replace")
        self.assertTrue(
            len(content) > 200,
            f"[PROJ-10] Expected substantial CFR-decompiled Gson.java content, got {len(content)} chars at {decompiled_path}",
        )


class TestDynamicProjectUpdates(_BaseProjTestCase):
    """Dynamic project update tests: adding new .java files and modifying pom.xml dependencies."""

    def test_add_new_java_file_dynamically(self) -> None:
        """[PROJ-11] Adding a brand-new .java file on disk + didCreateFiles/didChangeWatchedFiles updates resolution & diagnostics."""
        app_code = """\
package com.demo.dyn;

public class DynApp {
  public String callNew() {
    return new NewHelper().ping();
  }
}
"""
        helper_code = """\
package com.demo.dyn;

public class NewHelper {
  public String ping() {
    return "alive";
  }
}
"""
        proj = TempJavaProject(
            files={"src/com/demo/dyn/DynApp.java": app_code},
            prefix="proj_dyn_file_",
        )
        self.addCleanup(proj.close)
        client = self._start_client(proj.root)

        app_uri = proj.uri("src/com/demo/dyn/DynApp.java")
        init_diags = self._open_and_await_diags(client, app_uri, app_code)
        self.assertTrue(
            any(_is_error_diag(d) for d in init_diags),
            f"[PROJ-11] Expected unresolved 'NewHelper' error before creating NewHelper.java, got: {_format_diags(init_diags)}",
        )

        # Write NewHelper.java on disk and notify LSP server.
        seq = client.diagnostics_seq()
        proj.write_file("src/com/demo/dyn/NewHelper.java", helper_code)
        helper_uri = proj.uri("src/com/demo/dyn/NewHelper.java")
        try:
            client.workspace_did_create_files([helper_uri])
            client.workspace_did_change_watched_files([{"uri": helper_uri, "type": 1}])
        except LspClientError as exc:
            self.fail(f"[PROJ-11] Server error notifying new file creation: {exc}")

        # Wait briefly for DynApp diagnostics to clear automatically; if not, touch via didSave and verify.
        try:
            after_diags = client.wait_for_diagnostics(
                app_uri,
                timeout=4.0,
                after_seq=seq,
                predicate=lambda ds: len([d for d in ds if _is_error_diag(d)]) == 0,
                settle_time=0.2,
            )
        except TimeoutError:
            after_diags = client.get_diagnostics(app_uri)

        locs = extract_locations(
            self._safe_request("PROJ-11-def", client, client.definition, app_uri, pos_of(app_code, "NewHelper"))
        )
        self.assertIn(
            helper_uri,
            [location_uri(l) for l in locs],
            f"[PROJ-11] Expected definition on NewHelper to resolve to newly created {helper_uri}, got: {locs!r}",
        )
        self.assertEqual(
            [],
            [d for d in after_diags if _is_error_diag(d)],
            f"[PROJ-11] Expected DynApp.java errors to clear after NewHelper.java was created, got: {_format_diags(after_diags)}",
        )

    def test_modify_pom_xml_dependencies_dynamically(self) -> None:
        """[PROJ-12] Adding a dependency to pom.xml dynamically updates classpath and clears unresolved errors."""
        pom_v1 = """\
<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0"
         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0 https://maven.apache.org/xsd/maven-4.0.0.xsd">
  <modelVersion>4.0.0</modelVersion>
  <groupId>com.demo</groupId>
  <artifactId>dyn-pom</artifactId>
  <version>1.0.0</version>
  <dependencies>
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
        pom_v2 = pom_v1.replace(
            "  <dependencies>\n  </dependencies>",
            """  <dependencies>
    <dependency>
      <groupId>com.google.code.gson</groupId>
      <artifactId>gson</artifactId>
      <version>2.10</version>
    </dependency>
  </dependencies>""",
        )
        app_code = """\
package com.demo.dynpom;

import com.google.gson.Gson;

public class DynPomApp {
  public String encode(Object obj) {
    return new Gson().toJson(obj);
  }
}
"""
        proj = TempJavaProject(
            files={"pom.xml": pom_v1, "src/main/java/com/demo/dynpom/DynPomApp.java": app_code},
            prefix="proj_dyn_pom_",
        )
        self.addCleanup(proj.close)
        client = self._start_client(proj.root)

        app_uri = proj.uri("src/main/java/com/demo/dynpom/DynPomApp.java")
        pom_uri = proj.uri("pom.xml")
        init_diags = self._open_and_await_diags(client, app_uri, app_code)
        self.assertTrue(
            any(_is_error_diag(d) for d in init_diags),
            f"[PROJ-12] Expected unresolved Gson error before adding gson to pom.xml, got: {_format_diags(init_diags)}",
        )

        # Update pom.xml on disk + notify watched file change + didSave.
        seq = client.diagnostics_seq()
        proj.write_file("pom.xml", pom_v2)
        try:
            client.workspace_did_change_watched_files([{"uri": pom_uri, "type": 2}])
            client.did_open(pom_uri, pom_v2, language_id="xml")
            client.did_save(pom_uri, text=pom_v2)
        except LspClientError as exc:
            self.fail(f"[PROJ-12] Server error notifying pom.xml modification: {exc}")

        # Give server a few seconds to reload pom.xml & republish diagnostics.
        try:
            after_diags = client.wait_for_diagnostics(
                app_uri,
                timeout=6.0,
                after_seq=seq,
                predicate=lambda ds: len([d for d in ds if _is_error_diag(d)]) == 0,
                settle_time=0.25,
            )
        except TimeoutError:
            after_diags = client.get_diagnostics(app_uri)

        locs = extract_locations(
            self._safe_request("PROJ-12-def", client, client.definition, app_uri, pos_of(app_code, "Gson", occurrence=2))
        )
        self.assertTrue(
            locs,
            f"[PROJ-12] Expected Gson definition to resolve after adding dependency to pom.xml, got: {locs!r}",
        )
        self.assertEqual(
            [],
            [d for d in after_diags if _is_error_diag(d)],
            f"[PROJ-12] Expected DynPomApp.java errors to clear after adding gson to pom.xml, got: {_format_diags(after_diags)}",
        )


if __name__ == "__main__":
    unittest.main()
