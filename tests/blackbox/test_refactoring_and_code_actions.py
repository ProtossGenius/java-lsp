"""Black-box tests for java-lsp codeAction, rename, file rename, formatting, and JDTLS override commands."""

from __future__ import annotations

import unittest
from typing import Any

from tests.blackbox.lsp_client import (
    LspClient,
    LspClientError,
    LspResponseError,
    TempJavaProject,
    apply_text_edits,
    pos_of,
    range_of,
)


def _extract_edits_and_ops(
    workspace_edit: dict[str, Any] | None,
) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    """Extract per-URI TextEdits and resourceOperations from an LSP WorkspaceEdit."""
    edits_by_uri: dict[str, list[dict[str, Any]]] = {}
    resource_ops: list[dict[str, Any]] = []
    if not isinstance(workspace_edit, dict):
        return edits_by_uri, resource_ops

    changes = workspace_edit.get("changes")
    if isinstance(changes, dict):
        for uri, edits in changes.items():
            if isinstance(edits, list):
                edits_by_uri.setdefault(uri, []).extend(edits)

    doc_changes = workspace_edit.get("documentChanges")
    if isinstance(doc_changes, list):
        for entry in doc_changes:
            if not isinstance(entry, dict):
                continue
            if "kind" in entry:
                resource_ops.append(entry)
            elif "textDocument" in entry and isinstance(entry.get("edits"), list):
                uri = entry["textDocument"].get("uri")
                if uri:
                    edits_by_uri.setdefault(uri, []).extend(entry["edits"])
    return edits_by_uri, resource_ops


def _apply_workspace_edit(
    workspace_edit: dict[str, Any] | None,
    texts_by_uri: dict[str, str],
) -> tuple[dict[str, str], list[dict[str, Any]]]:
    edits_by_uri, resource_ops = _extract_edits_and_ops(workspace_edit)
    updated = dict(texts_by_uri)
    for uri, edits in edits_by_uri.items():
        base = updated.get(uri, "")
        updated[uri] = apply_text_edits(base, edits)
    return updated, resource_ops


class _BaseRefactorTestCase(unittest.TestCase):
    client: LspClient
    project: TempJavaProject

    def _safe_request(self, tag: str, fn: Any, *args: Any, **kwargs: Any) -> Any:
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

    def _open_and_await_diags(self, uri: str, text: str, timeout: float = 4.0) -> list[dict[str, Any]]:
        try:
            seq = self.client.did_open(uri, text)
            return self.client.wait_for_diagnostics_quiescent(
                uri, timeout=timeout, quiet_period=0.25, after_seq=seq
            )
        except TimeoutError:
            return self.client.get_diagnostics(uri)
        except LspClientError:
            return self.client.get_diagnostics(uri)

    def _safe_apply_text_edits(
        self,
        tag: str,
        text: str,
        edits: list[dict[str, Any]],
    ) -> str:
        try:
            return apply_text_edits(text, edits)
        except (ValueError, KeyError, TypeError) as exc:
            self.fail(
                f"[{tag}] Server returned invalid/out-of-bounds TextEdit range: {exc}; "
                f"doc_lines={len(text.splitlines())}, edits={edits!r}"
            )

    def _safe_apply_workspace_edit(
        self,
        tag: str,
        workspace_edit: dict[str, Any] | None,
        texts_by_uri: dict[str, str],
    ) -> tuple[dict[str, str], list[dict[str, Any]]]:
        edits_by_uri, resource_ops = _extract_edits_and_ops(workspace_edit)
        updated = dict(texts_by_uri)
        for uri, edits in edits_by_uri.items():
            base = updated.get(uri, "")
            updated[uri] = self._safe_apply_text_edits(tag, base, edits)
        return updated, resource_ops

    def _resolve_action_edit(
        self,
        tag: str,
        action: dict[str, Any],
    ) -> dict[str, Any] | None:
        if isinstance(action.get("edit"), dict):
            return action["edit"]
        # Try codeAction/resolve if edit isn't populated inline.
        resolved, err = self.client.try_request("codeAction/resolve", action)
        if err is not None:
            self.fail(
                f"[{tag}] CodeAction {action.get('title')!r} lacked inline 'edit' and "
                f"codeAction/resolve returned error code={err.code}, message={err.message!r}"
            )
        if isinstance(resolved, dict) and isinstance(resolved.get("edit"), dict):
            return resolved["edit"]
        return None


class TestCodeActions(_BaseRefactorTestCase):
    """Tests for LSP 3.17 textDocument/codeAction (quickfix, source, refactor, resolve)."""

    _init_error: str | None = None

    @classmethod
    def setUpClass(cls) -> None:
        cls.project = TempJavaProject(prefix="act_suite_")
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
            self.fail(f"[ACT] {self._init_error}")
        if not self.client.is_alive():
            try:
                self.client.restart(clean_storage=False)
                self.client.initialize_and_ready(root_path=self.project.root, timeout=60.0)
            except Exception as exc:
                self.fail(f"[ACT] Server restart failed: {exc}")

    def _open_temp_java(self, rel: str, code: str) -> tuple[str, list[dict[str, Any]]]:
        self.project.write_file(rel, code)
        uri = self.project.uri(rel)

        def _cleanup() -> None:
            try:
                self.client.did_close(uri)
            except LspClientError:
                pass
            self.project.remove_file(rel)

        self.addCleanup(_cleanup)
        diags = self._open_and_await_diags(uri, code, timeout=4.0)
        return uri, diags

    def _request_actions(
        self,
        tag: str,
        uri: str,
        rng: dict[str, Any],
        diagnostics: list[dict[str, Any]] | None = None,
        only: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        res = self._safe_request(
            tag,
            self.client.code_action,
            uri,
            rng,
            diagnostics=diagnostics or [],
            only=only,
        )
        if res is None:
            return []
        self.assertIsInstance(
            res,
            list,
            f"[{tag}] Expected textDocument/codeAction to return list or null, got: {res!r}",
        )
        return [a for a in res if isinstance(a, dict)]

    def _assert_any_action_produces(
        self,
        tag: str,
        uri: str,
        code: str,
        actions: list[dict[str, Any]],
        check_fn: Any,
        expect_desc: str,
    ) -> None:
        self.assertTrue(
            actions,
            f"[{tag}] Expected at least 1 CodeAction ({expect_desc}), but server returned [].\n"
            f"Document:\n{code}",
        )
        titles = [a.get("title") for a in actions]
        applied_texts: list[tuple[str | None, str]] = []
        for act in actions:
            edit = act.get("edit")
            if edit is None and "command" not in act:
                resolved, _ = self.client.try_request("codeAction/resolve", act)
                if isinstance(resolved, dict):
                    edit = resolved.get("edit")
            if isinstance(edit, dict):
                updated_map, _ = self._safe_apply_workspace_edit(tag, edit, {uri: code})
                new_text = updated_map.get(uri, "")
                applied_texts.append((act.get("title"), new_text))
                if check_fn(new_text, act):
                    return
        self.fail(
            f"[{tag}] None of the returned CodeActions satisfied '{expect_desc}'.\n"
            f"Returned titles: {titles}\n"
            f"Applied outputs: {applied_texts}\n"
            f"Raw actions: {actions}"
        )

    def test_quickfix_auto_import_missing_class(self) -> None:
        """[ACT-01] Quick fix on unresolved 'List' must offer auto-import for java.util.List."""
        code = """\
package com.example.act;

public class MissingImport {
  public List<String> items() {
    return null;
  }
}
"""
        uri, diags = self._open_temp_java("src/com/example/act/MissingImport.java", code)
        actions = self._request_actions(
            "ACT-01", uri, range_of(code, "List"), diagnostics=diags
        )
        self._assert_any_action_produces(
            "ACT-01",
            uri,
            code,
            actions,
            lambda txt, _a: "import java.util.List;" in txt,
            "insert 'import java.util.List;'",
        )

    def test_quickfix_create_missing_local_variable(self) -> None:
        """[ACT-02] Quick fix on unresolved assigned variable must offer creating a local variable."""
        code = """\
package com.example.act;

public class MissingLocal {
  public void demo() {
    missingLocal = 42;
  }
}
"""
        uri, diags = self._open_temp_java("src/com/example/act/MissingLocal.java", code)
        actions = self._request_actions(
            "ACT-02", uri, range_of(code, "missingLocal"), diagnostics=diags
        )
        self._assert_any_action_produces(
            "ACT-02",
            uri,
            code,
            actions,
            lambda txt, _a: ("int missingLocal" in txt or "var missingLocal" in txt),
            "create local variable 'int missingLocal'",
        )

    def test_quickfix_create_missing_field(self) -> None:
        """[ACT-03] Quick fix on unresolved 'this.missingField' must offer creating the field."""
        code = """\
package com.example.act;

public class MissingField {
  public void set() {
    this.missingField = "abc";
  }
}
"""
        uri, diags = self._open_temp_java("src/com/example/act/MissingField.java", code)
        actions = self._request_actions(
            "ACT-03", uri, range_of(code, "missingField"), diagnostics=diags
        )
        self._assert_any_action_produces(
            "ACT-03",
            uri,
            code,
            actions,
            lambda txt, _a: "String missingField;" in txt or "String missingField =" in txt,
            "create field 'String missingField'",
        )

    def test_quickfix_create_missing_method(self) -> None:
        """[ACT-04] Quick fix on unresolved 'computeSum(1, 2)' must offer creating the method stub."""
        code = """\
package com.example.act;

public class MissingMethod {
  public int run() {
    return computeSum(1, 2);
  }
}
"""
        uri, diags = self._open_temp_java("src/com/example/act/MissingMethod.java", code)
        actions = self._request_actions(
            "ACT-04", uri, range_of(code, "computeSum"), diagnostics=diags
        )
        self._assert_any_action_produces(
            "ACT-04",
            uri,
            code,
            actions,
            lambda txt, _a: "computeSum(" in txt and txt.count("computeSum(") >= 2,
            "create method stub 'computeSum(...)'",
        )

    def test_quickfix_implement_unimplemented_methods(self) -> None:
        """[ACT-05] Quick fix on class implementing Runnable must generate unimplemented run() method."""
        code = """\
package com.example.act;

public class MissingRun implements Runnable {
}
"""
        uri, diags = self._open_temp_java("src/com/example/act/MissingRun.java", code)
        actions = self._request_actions(
            "ACT-05", uri, range_of(code, "MissingRun"), diagnostics=diags
        )
        self._assert_any_action_produces(
            "ACT-05",
            uri,
            code,
            actions,
            lambda txt, _a: "void run()" in txt,
            "implement 'public void run()'",
        )

    def test_quickfix_remove_unused_import(self) -> None:
        """[ACT-06] Quick fix on unused import must offer removing the unused import."""
        code = """\
package com.example.act;

import java.util.Date;

public class UnusedImportFix {
  public String ok() {
    return "ok";
  }
}
"""
        uri, diags = self._open_temp_java("src/com/example/act/UnusedImportFix.java", code)
        actions = self._request_actions(
            "ACT-06", uri, range_of(code, "import java.util.Date;"), diagnostics=diags
        )
        self._assert_any_action_produces(
            "ACT-06",
            uri,
            code,
            actions,
            lambda txt, _a: "import java.util.Date;" not in txt,
            "remove unused 'import java.util.Date;'",
        )

    def test_quickfix_override_annotation_fixes(self) -> None:
        """[ACT-07] Quick fixes for adding missing @Override and removing invalid @Override."""
        missing_ov = """\
package com.example.act;

public class MissingOverride {
  public String toString() {
    return "x";
  }
}
"""
        uri1, diags1 = self._open_temp_java("src/com/example/act/MissingOverride.java", missing_ov)
        actions1 = self._request_actions(
            "ACT-07a", uri1, range_of(missing_ov, "toString"), diagnostics=diags1
        )
        failures: list[str] = []
        try:
            self._assert_any_action_produces(
                "ACT-07a",
                uri1,
                missing_ov,
                actions1,
                lambda txt, _a: "@Override" in txt,
                "add '@Override' to toString()",
            )
        except AssertionError as exc:
            failures.append(str(exc))

        invalid_ov = """\
package com.example.act;

public class InvalidOverrideFix {
  @Override
  public void bogus() {}
}
"""
        uri2, diags2 = self._open_temp_java("src/com/example/act/InvalidOverrideFix.java", invalid_ov)
        actions2 = self._request_actions(
            "ACT-07b", uri2, range_of(invalid_ov, "@Override"), diagnostics=diags2
        )
        try:
            self._assert_any_action_produces(
                "ACT-07b",
                uri2,
                invalid_ov,
                actions2,
                lambda txt, _a: "@Override" not in txt,
                "remove invalid '@Override'",
            )
        except AssertionError as exc:
            failures.append(str(exc))

        if failures:
            self.fail("[ACT-07] Override quick-fix failure(s):\n" + "\n---\n".join(failures))

    def test_quickfix_unhandled_checked_exception(self) -> None:
        """[ACT-08] Quick fix on unhandled IOException must surround with try/catch or add throws."""
        code = """\
package com.example.act;

import java.io.IOException;

public class CheckedFix {
  public void fail() {
    throw new IOException("boom");
  }
}
"""
        uri, diags = self._open_temp_java("src/com/example/act/CheckedFix.java", code)
        actions = self._request_actions(
            "ACT-08", uri, range_of(code, 'throw new IOException("boom");'), diagnostics=diags
        )
        self._assert_any_action_produces(
            "ACT-08",
            uri,
            code,
            actions,
            lambda txt, _a: ("throws IOException" in txt) or ("try {" in txt and "catch (" in txt),
            "add 'throws IOException' or surround with try/catch",
        )

    def test_source_organize_imports(self) -> None:
        """[ACT-09] source.organizeImports must sort imports, deduplicate, and remove unused ones."""
        code = """\
package com.example.act;

import java.util.Map;
import java.util.Date;
import java.util.List;
import java.util.List;

public class OrganizeMe {
  private List<String> list;
  private Map<String, Integer> map;
}
"""
        uri, diags = self._open_temp_java("src/com/example/act/OrganizeMe.java", code)
        actions = self._request_actions(
            "ACT-09",
            uri,
            range_of(code, "OrganizeMe"),
            diagnostics=diags,
            only=["source.organizeImports"],
        )
        self._assert_any_action_produces(
            "ACT-09",
            uri,
            code,
            actions,
            lambda txt, _a: (
                "import java.util.Date;" not in txt
                and txt.count("import java.util.List;") == 1
                and "import java.util.Map;" in txt
                and txt.index("import java.util.List;") < txt.index("import java.util.Map;")
            ),
            "sort imports, remove duplicate List and unused Date",
        )

    def test_source_generate_getters_setters_constructors(self) -> None:
        """[ACT-10] Source action must offer generating getters/setters or constructors for fields."""
        code = """\
package com.example.act;

public class PojoBean {
  private String name;
  private int age;
}
"""
        uri, diags = self._open_temp_java("src/com/example/act/PojoBean.java", code)
        actions = self._request_actions(
            "ACT-10",
            uri,
            range_of(code, "private String name;"),
            diagnostics=diags,
            only=["source"],
        )
        self._assert_any_action_produces(
            "ACT-10",
            uri,
            code,
            actions,
            lambda txt, _a: (
                "getName(" in txt or "setName(" in txt or "public PojoBean(" in txt
            ),
            "generate getter, setter, or constructor",
        )

    def test_refactor_extract_variable(self) -> None:
        """[ACT-11] refactor.extract.variable must extract expression into a local variable."""
        code = """\
package com.example.act;

public class ExtractVar {
  public int calc(int a, int b) {
    return a + b * 2;
  }
}
"""
        uri, _ = self._open_temp_java("src/com/example/act/ExtractVar.java", code)
        actions = self._request_actions(
            "ACT-11",
            uri,
            range_of(code, "a + b * 2"),
            only=["refactor.extract.variable", "refactor.extract"],
        )
        self._assert_any_action_produces(
            "ACT-11",
            uri,
            code,
            actions,
            lambda txt, _a: ("= a + b * 2;" in txt and "return " in txt and "return a + b * 2;" not in txt),
            "extract 'a + b * 2' into local variable",
        )

    def test_refactor_extract_method(self) -> None:
        """[ACT-12] refactor.extract.function must extract selected statements into a new method."""
        code = """\
package com.example.act;

public class ExtractMethod {
  public int process(int x) {
    int doubled = x * 2;
    int shifted = doubled + 5;
    return shifted;
  }
}
"""
        uri, _ = self._open_temp_java("src/com/example/act/ExtractMethod.java", code)
        sel_range = {
            "start": pos_of(code, "int doubled = x * 2;"),
            "end": pos_of(code, "return shifted;"),
        }
        actions = self._request_actions(
            "ACT-12",
            uri,
            sel_range,
            only=["refactor.extract.function", "refactor.extract"],
        )
        self._assert_any_action_produces(
            "ACT-12",
            uri,
            code,
            actions,
            lambda txt, _a: txt.count("private ") >= 1 or txt.count("(") > code.count("("),
            "extract statements into helper method",
        )

    def test_refactor_inline_variable(self) -> None:
        """[ACT-13] refactor.inline must inline a local variable into its use sites."""
        code = """\
package com.example.act;

public class InlineVar {
  public int compute() {
    int temp = 10 + 20;
    return temp * 2;
  }
}
"""
        uri, _ = self._open_temp_java("src/com/example/act/InlineVar.java", code)
        actions = self._request_actions(
            "ACT-13",
            uri,
            range_of(code, "temp"),
            only=["refactor.inline", "refactor"],
        )
        self._assert_any_action_produces(
            "ACT-13",
            uri,
            code,
            actions,
            lambda txt, _a: "int temp" not in txt and "10 + 20" in txt,
            "inline local variable 'temp'",
        )

    def test_code_action_resolve(self) -> None:
        """[ACT-14] codeAction/resolve must work cleanly and return a CodeAction with edit when applicable."""
        code = """\
package com.example.act;

public class ResolveDemo {
  public List<String> items() {
    return null;
  }
}
"""
        uri, diags = self._open_temp_java("src/com/example/act/ResolveDemo.java", code)
        actions = self._request_actions(
            "ACT-14", uri, range_of(code, "List"), diagnostics=diags
        )
        self.assertTrue(
            actions,
            "[ACT-14] Expected CodeActions on unresolved 'List' to test codeAction/resolve, got [].",
        )
        target = actions[0]
        if "edit" in target and target["edit"] is not None:
            # Verify calling codeAction/resolve still does not crash or return unexpected error if called.
            resolved, err = self.client.try_request("codeAction/resolve", target)
            if err is not None:
                self.fail(
                    f"[ACT-14] codeAction/resolve returned JSON-RPC error {err.code}: {err.message!r} "
                    f"(capabilities codeActionProvider={self.client.server_capabilities.get('codeActionProvider')!r})"
                )
            self.assertIsInstance(resolved, dict, f"[ACT-14] Expected dict from codeAction/resolve, got {resolved!r}")
        else:
            resolved_edit = self._resolve_action_edit("ACT-14", target)
            self.assertIsInstance(
                resolved_edit,
                dict,
                f"[ACT-14] Expected resolved CodeAction to populate 'edit', got {resolved_edit!r}",
            )


class TestRenameOperations(_BaseRefactorTestCase):
    """Tests for prepareRename, rename (local, param, field, method, interface, class), and file renames."""

    def setUp(self) -> None:
        self.project = TempJavaProject(prefix="ref_rename_")
        self.addCleanup(self.project.close)
        self.client = LspClient(root_path=self.project.root, default_timeout=20.0)
        self.addCleanup(self.client.close)
        try:
            self.client.start()
            self.client.initialize_and_ready(root_path=self.project.root, timeout=60.0)
        except Exception as exc:
            self.fail(f"[REF] Server initialization failed: {exc}")

    def _open(self, rel: str, code: str) -> str:
        self.project.write_file(rel, code)
        uri = self.project.uri(rel)
        self._open_and_await_diags(uri, code, timeout=3.0)
        return uri

    def test_rename_local_variable_scoped(self) -> None:
        """[REF-01] Renaming a local variable must stay strictly inside its lexical scope."""
        code = """\
package com.example.ref;

public class ScopedLocal {
  public int alpha() {
    int value = 10;
    return value + 1;
  }

  public int beta() {
    int value = 20;
    return value + 2;
  }
}
"""
        uri = self._open("src/com/example/ref/ScopedLocal.java", code)
        pos = pos_of(code, "value", occurrence=1)
        prep = self._safe_request("REF-01-prep", self.client.prepare_rename, uri, pos)
        self.assertIsNotNone(prep, "[REF-01] prepareRename returned null on local variable 'value'.")

        edit = self._safe_request("REF-01-rename", self.client.rename, uri, pos, "count")
        self.assertIsInstance(edit, dict, f"[REF-01] rename returned {edit!r} instead of WorkspaceEdit.")
        updated_map, _ = self._safe_apply_workspace_edit("REF-01", edit, {uri: code})
        new_code = updated_map[uri]

        alpha_block = new_code.split("public int beta()")[0]
        beta_block = new_code.split("public int beta()")[1]
        self.assertIn(
            "int count = 10;",
            alpha_block,
            f"[REF-01] Expected alpha() declaration renamed to 'count'.\nResult:\n{new_code}",
        )
        self.assertIn(
            "return count + 1;",
            alpha_block,
            f"[REF-01] Expected alpha() usage renamed to 'count'.\nResult:\n{new_code}",
        )
        self.assertIn(
            "int value = 20;",
            beta_block,
            f"[REF-01] Bug: rename leaked across methods into beta()!\nResult:\n{new_code}",
        )
        self.assertIn(
            "return value + 2;",
            beta_block,
            f"[REF-01] Bug: rename leaked across methods into beta() return!\nResult:\n{new_code}",
        )

    def test_rename_parameter(self) -> None:
        """[REF-02] Renaming a method parameter must update declaration and usages inside the method only."""
        code = """\
package com.example.ref;

public class ParamRename {
  public int scale(int factor) {
    return factor * 2 + factor;
  }

  public int other(int factor) {
    return factor;
  }
}
"""
        uri = self._open("src/com/example/ref/ParamRename.java", code)
        pos = pos_of(code, "factor", occurrence=1)
        prep = self._safe_request("REF-02-prep", self.client.prepare_rename, uri, pos)
        self.assertIsNotNone(prep, "[REF-02] prepareRename returned null on parameter 'factor'.")
        edit = self._safe_request("REF-02-rename", self.client.rename, uri, pos, "multiplier")
        self.assertIsInstance(edit, dict, f"[REF-02] rename returned {edit!r} instead of WorkspaceEdit.")
        updated_map, _ = self._safe_apply_workspace_edit("REF-02", edit, {uri: code})
        new_code = updated_map[uri]
        scale_part, other_part = new_code.split("public int other(")
        self.assertIn("scale(int multiplier)", scale_part, f"[REF-02] Param decl not renamed:\n{new_code}")
        self.assertIn("return multiplier * 2 + multiplier;", scale_part, f"[REF-02] Param uses not renamed:\n{new_code}")
        self.assertIn("int factor)", other_part, f"[REF-02] Param rename leaked to other():\n{new_code}")

    def test_rename_field_cross_file(self) -> None:
        """[REF-03] Renaming a field must update declaration, this.field, and cross-file references."""
        holder = """\
package com.example.ref;

public class CounterBox {
  public int counter = 0;

  public void inc() {
    this.counter++;
  }
}
"""
        user = """\
package com.example.ref;

public class CounterUser {
  public int read(CounterBox box) {
    return box.counter;
  }
}
"""
        u1 = self._open("src/com/example/ref/CounterBox.java", holder)
        u2 = self._open("src/com/example/ref/CounterUser.java", user)
        pos = pos_of(holder, "counter", occurrence=1)
        edit = self._safe_request("REF-03", self.client.rename, u1, pos, "totalCount")
        self.assertIsInstance(edit, dict, f"[REF-03] rename returned {edit!r} instead of WorkspaceEdit.")
        updated_map, _ = self._safe_apply_workspace_edit("REF-03", edit, {u1: holder, u2: user})
        self.assertIn("public int totalCount = 0;", updated_map[u1], f"[REF-03] Field decl not renamed:\n{updated_map[u1]}")
        self.assertIn("this.totalCount++;", updated_map[u1], f"[REF-03] this.counter not renamed:\n{updated_map[u1]}")
        self.assertIn("return box.totalCount;", updated_map[u2], f"[REF-03] Cross-file box.counter not renamed:\n{updated_map[u2]}")

    def test_rename_method_single_class_and_caller(self) -> None:
        """[REF-04] Renaming a method must update declaration and cross-file call sites."""
        svc = """\
package com.example.ref;

public class FormatterSvc {
  public String formatTag(String raw) {
    return "[" + raw + "]";
  }
}
"""
        caller = """\
package com.example.ref;

public class FormatterCaller {
  public String run(FormatterSvc svc) {
    return svc.formatTag("ok");
  }
}
"""
        u1 = self._open("src/com/example/ref/FormatterSvc.java", svc)
        u2 = self._open("src/com/example/ref/FormatterCaller.java", caller)
        pos = pos_of(svc, "formatTag")
        edit = self._safe_request("REF-04", self.client.rename, u1, pos, "wrapBracket")
        self.assertIsInstance(edit, dict, f"[REF-04] rename returned {edit!r} instead of WorkspaceEdit.")
        updated_map, _ = self._safe_apply_workspace_edit("REF-04", edit, {u1: svc, u2: caller})
        self.assertIn("wrapBracket(String raw)", updated_map[u1], f"[REF-04] Method decl not renamed:\n{updated_map[u1]}")
        self.assertIn('svc.wrapBracket("ok")', updated_map[u2], f"[REF-04] Caller not renamed:\n{updated_map[u2]}")

    def test_rename_interface_method_updates_impl_and_callers(self) -> None:
        """[REF-05] Renaming an interface method must update interface, @Override impl, and callers."""
        iface = """\
package com.example.ref;

public interface Greeter {
  String sayHello(String who);
}
"""
        impl = """\
package com.example.ref;

public class EnglishGreeter implements Greeter {
  @Override
  public String sayHello(String who) {
    return "Hello, " + who;
  }
}
"""
        caller = """\
package com.example.ref;

public class GreeterApp {
  public String invoke(Greeter g, EnglishGreeter eg) {
    return g.sayHello("a") + eg.sayHello("b");
  }
}
"""
        u_if = self._open("src/com/example/ref/Greeter.java", iface)
        u_im = self._open("src/com/example/ref/EnglishGreeter.java", impl)
        u_ca = self._open("src/com/example/ref/GreeterApp.java", caller)

        pos = pos_of(iface, "sayHello")
        edit = self._safe_request("REF-05", self.client.rename, u_if, pos, "greetUser")
        self.assertIsInstance(edit, dict, f"[REF-05] rename returned {edit!r} instead of WorkspaceEdit.")
        updated_map, _ = self._safe_apply_workspace_edit("REF-05", edit, {u_if: iface, u_im: impl, u_ca: caller})
        self.assertIn("String greetUser(String who);", updated_map[u_if], f"[REF-05] Interface method not renamed:\n{updated_map[u_if]}")
        self.assertIn("public String greetUser(String who)", updated_map[u_im], f"[REF-05] Impl @Override method not renamed:\n{updated_map[u_im]}")
        self.assertIn('g.greetUser("a")', updated_map[u_ca], f"[REF-05] Interface call site not renamed:\n{updated_map[u_ca]}")
        self.assertIn('eg.greetUser("b")', updated_map[u_ca], f"[REF-05] Impl call site not renamed:\n{updated_map[u_ca]}")

    def test_rename_class_updates_usages_imports_and_file_op(self) -> None:
        """[REF-06] Renaming a class must update declaration, constructor, cross-file imports/usages, and RenameFile."""
        widget = """\
package com.example.ref.sub;

public class OldWidget {
  public OldWidget() {}
  public String id() { return "w"; }
}
"""
        consumer = """\
package com.example.ref.app;

import com.example.ref.sub.OldWidget;

public class WidgetClient {
  private final OldWidget widget = new OldWidget();
  public String read() { return widget.id(); }
}
"""
        u_w = self._open("src/com/example/ref/sub/OldWidget.java", widget)
        u_c = self._open("src/com/example/ref/app/WidgetClient.java", consumer)

        pos = pos_of(widget, "OldWidget", occurrence=1)
        prep = self._safe_request("REF-06-prep", self.client.prepare_rename, u_w, pos)
        self.assertIsNotNone(prep, "[REF-06] prepareRename returned null on class name 'OldWidget'.")
        edit = self._safe_request("REF-06-rename", self.client.rename, u_w, pos, "NewWidget")
        self.assertIsInstance(edit, dict, f"[REF-06] rename returned {edit!r} instead of WorkspaceEdit.")

        updated_map, ops = self._safe_apply_workspace_edit("REF-06", edit, {u_w: widget, u_c: consumer})
        self.assertIn("public class NewWidget", updated_map[u_w], f"[REF-06] Class decl not renamed:\n{updated_map[u_w]}")
        self.assertIn("public NewWidget()", updated_map[u_w], f"[REF-06] Constructor not renamed:\n{updated_map[u_w]}")
        self.assertIn("import com.example.ref.sub.NewWidget;", updated_map[u_c], f"[REF-06] Import not updated:\n{updated_map[u_c]}")
        self.assertIn("private final NewWidget widget = new NewWidget();", updated_map[u_c], f"[REF-06] Usages not updated:\n{updated_map[u_c]}")

        rename_ops = [op for op in ops if op.get("kind") == "rename"]
        self.assertTrue(
            any(
                op.get("oldUri") == u_w and str(op.get("newUri", "")).endswith("/NewWidget.java")
                for op in rename_ops
            ),
            f"[REF-06] Expected RenameFile operation for OldWidget.java -> NewWidget.java in documentChanges, got ops={ops}, edit={edit}",
        )

    def test_prepare_rename_rejects_keywords_literals_and_external_symbols(self) -> None:
        """[REF-07] prepareRename must reject (return null or error) on keywords, literals, and JDK symbols."""
        code = """\
package com.example.ref;

public class RejectTargets {
  public void demo() {
    String msg = "hello";
    int n = 42;
    System.out.println(msg + n);
  }
}
"""
        uri = self._open("src/com/example/ref/RejectTargets.java", code)
        targets = [
            ("keyword_public", pos_of(code, "public")),
            ("keyword_void", pos_of(code, "void")),
            ("string_literal", pos_of(code, '"hello"', offset=2)),
            ("number_literal", pos_of(code, "42")),
            ("jdk_String", pos_of(code, "String")),
            ("jdk_println", pos_of(code, "println")),
        ]
        bad_accepts: list[str] = []
        for name, pos in targets:
            try:
                res, err = self.client.try_request(
                    "textDocument/prepareRename",
                    {"textDocument": {"uri": uri}, "position": pos},
                )
            except LspClientError as exc:
                self.fail(f"[REF-07] Client/server crashed on prepareRename({name}): {exc}")
            # Valid rejection in LSP 3.17: result is None OR JSON-RPC error returned.
            if err is None and res is not None:
                bad_accepts.append(f"{name} at {pos} -> {res!r}")
        self.assertEqual(
            [],
            bad_accepts,
            f"[REF-07] prepareRename should reject keywords, literals, and external JDK symbols, "
            f"but accepted: {bad_accepts}",
        )

    def test_rename_conflict_detection(self) -> None:
        """[REF-08] Renaming a local variable to an already-existing name in scope must be rejected."""
        code = """\
package com.example.ref;

public class ConflictDemo {
  public int sum() {
    int first = 1;
    int second = 2;
    return first + second;
  }
}
"""
        uri = self._open("src/com/example/ref/ConflictDemo.java", code)
        pos = pos_of(code, "first", occurrence=1)
        try:
            res, err = self.client.try_request(
                "textDocument/rename",
                {"textDocument": {"uri": uri}, "position": pos, "newName": "second"},
            )
        except LspClientError as exc:
            self.fail(f"[REF-08] Server crashed during rename conflict test: {exc}")

        if err is None and res is not None:
            updated_map, _ = self._safe_apply_workspace_edit("REF-08", res, {uri: code})
            self.fail(
                "[REF-08] Rename conflict not detected: renaming 'first' -> 'second' succeeded silently "
                f"and produced duplicate declaration!\nResulting code:\n{updated_map.get(uri)}"
            )

    def test_workspace_will_rename_files(self) -> None:
        """[REF-09] workspace/willRenameFiles must return edits updating package declaration and cross-file imports."""
        mover = """\
package com.example.ref.pkga;

public class Mover {
  public String ping() { return "pong"; }
}
"""
        client_code = """\
package com.example.ref.app;

import com.example.ref.pkga.Mover;

public class MoverClient {
  public String call(Mover m) {
    return m.ping();
  }
}
"""
        old_uri = self._open("src/com/example/ref/pkga/Mover.java", mover)
        cli_uri = self._open("src/com/example/ref/app/MoverClient.java", client_code)
        new_uri = self.project.uri("src/com/example/ref/pkgb/Mover.java")

        edit = self._safe_request(
            "REF-09",
            self.client.workspace_will_rename_files,
            [(old_uri, new_uri)],
        )
        self.assertIsInstance(
            edit,
            dict,
            f"[REF-09] workspace/willRenameFiles returned {edit!r}, expected WorkspaceEdit updating package/imports.",
        )
        updated_map, _ = self._safe_apply_workspace_edit("REF-09", edit, {old_uri: mover, cli_uri: client_code})
        moved_text = updated_map.get(new_uri) or updated_map.get(old_uri, "")
        self.assertIn(
            "package com.example.ref.pkgb;",
            moved_text,
            f"[REF-09] Expected package declaration updated to com.example.ref.pkgb; got:\n{moved_text}",
        )
        self.assertIn(
            "import com.example.ref.pkgb.Mover;",
            updated_map[cli_uri],
            f"[REF-09] Expected MoverClient import updated to com.example.ref.pkgb.Mover; got:\n{updated_map[cli_uri]}",
        )

    def test_workspace_did_rename_files_updates_index(self) -> None:
        """[REF-10] workspace/didRenameFiles must update server symbol index to the new file URI."""
        old_code = """\
package com.example.ref.mv;

public class AlphaType {
  public int code() { return 7; }
}
"""
        new_code = """\
package com.example.ref.mv;

public class BetaType {
  public int code() { return 7; }
}
"""
        consumer = """\
package com.example.ref.mv;

public class AlphaConsumer {
  public int read(BetaType t) { return t.code(); }
}
"""
        old_rel = "src/com/example/ref/mv/AlphaType.java"
        new_rel = "src/com/example/ref/mv/BetaType.java"
        cons_rel = "src/com/example/ref/mv/AlphaConsumer.java"

        old_uri = self._open(old_rel, old_code)
        cons_uri = self._open(cons_rel, consumer)

        self.client.did_close(old_uri)
        self.project.rename_file(old_rel, new_rel)
        self.project.write_file(new_rel, new_code)
        new_uri = self.project.uri(new_rel)
        try:
            self.client.workspace_did_rename_files([(old_uri, new_uri)])
            self.client.did_open(new_uri, new_code)
        except LspClientError as exc:
            self.fail(f"[REF-10] Server error on workspace/didRenameFiles: {exc}")

        locs = self._safe_request(
            "REF-10-def",
            self.client.definition,
            cons_uri,
            pos_of(consumer, "BetaType"),
        )
        self.assertTrue(
            isinstance(locs, list) and any((l.get("uri") or l.get("targetUri")) == new_uri for l in locs),
            f"[REF-10] Expected definition of BetaType after didRenameFiles to point to {new_uri}, got: {locs!r}",
        )


class TestFormattingAndJdtlsCommands(_BaseRefactorTestCase):
    """Tests for formatting, rangeFormatting, onTypeFormatting, and java/overridableMethods + java/overrideMethods."""

    _init_error: str | None = None

    @classmethod
    def setUpClass(cls) -> None:
        cls.project = TempJavaProject(prefix="fmt_jdt_")
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
            self.fail(f"[FMT/JDT] {self._init_error}")
        if not self.client.is_alive():
            try:
                self.client.restart(clean_storage=False)
                self.client.initialize_and_ready(root_path=self.project.root, timeout=60.0)
            except Exception as exc:
                self.fail(f"[FMT/JDT] Server restart failed: {exc}")

    def _open_temp(self, rel: str, code: str) -> str:
        self.project.write_file(rel, code)
        uri = self.project.uri(rel)

        def _cleanup() -> None:
            try:
                self.client.did_close(uri)
            except LspClientError:
                pass
            self.project.remove_file(rel)

        self.addCleanup(_cleanup)
        self._open_and_await_diags(uri, code, timeout=3.0)
        return uri

    def test_document_formatting(self) -> None:
        """[FMT-01] textDocument/formatting must return non-empty edits fixing indentation and spacing."""
        messy = "package com.example.fmt;\npublic class   MessyFmt{int x=1;\nvoid bar( ){System.out.println(x);}}\n"
        uri = self._open_temp("src/com/example/fmt/MessyFmt.java", messy)
        edits = self._safe_request("FMT-01", self.client.formatting, uri, tab_size=2, insert_spaces=True)
        self.assertIsInstance(edits, list, f"[FMT-01] Expected TextEdit[], got: {edits!r}")
        self.assertTrue(edits, f"[FMT-01] Formatting returned no edits on unformatted code:\n{messy}")
        formatted = self._safe_apply_text_edits("FMT-01", messy, edits)
        self.assertNotEqual(messy, formatted, "[FMT-01] Formatting edits produced unchanged text.")
        self.assertIn("  ", formatted, f"[FMT-01] Expected 2-space indentation in formatted output:\n{formatted}")
        self.assertNotIn("   MessyFmt", formatted, f"[FMT-01] Expected extra spaces collapsed:\n{formatted}")

    def test_range_formatting(self) -> None:
        """[FMT-02] textDocument/rangeFormatting must format the selected range without touching outside lines."""
        code = (
            "package com.example.fmt;\n\n"
            "public class RangeFmt {\n"
            "  void   first( ) { int a=1; }\n"
            "void second(){int b=2;return;}\n"
            "}\n"
        )
        uri = self._open_temp("src/com/example/fmt/RangeFmt.java", code)
        edits = self._safe_request(
            "FMT-02",
            self.client.range_formatting,
            uri,
            range_of(code, "void second(){int b=2;return;}"),
            tab_size=2,
            insert_spaces=True,
        )
        self.assertIsInstance(edits, list, f"[FMT-02] Expected TextEdit[], got: {edits!r}")
        self.assertTrue(edits, "[FMT-02] rangeFormatting returned no edits for unformatted method.")
        formatted = self._safe_apply_text_edits("FMT-02", code, edits)
        self.assertIn(
            "  void   first( ) { int a=1; }",
            formatted,
            f"[FMT-02] rangeFormatting modified lines outside selected range:\n{formatted}",
        )

    def test_on_type_formatting(self) -> None:
        """[FMT-03] textDocument/onTypeFormatting must format on '}' without returning error."""
        code = "package com.example.fmt;\n\npublic class OnTypeFmt {\n  void m() {\n  int x = 1;\n}\n}\n"
        uri = self._open_temp("src/com/example/fmt/OnTypeFmt.java", code)
        pos = pos_of(code, "}", occurrence=1, offset=1)
        edits = self._safe_request("FMT-03", self.client.on_type_formatting, uri, pos, "}")
        self.assertIsInstance(edits, list, f"[FMT-03] Expected TextEdit[], got: {edits!r}")
        self.assertTrue(edits, f"[FMT-03] Expected onTypeFormatting edits for unindented body:\n{code}")

    def test_java_overridable_methods(self) -> None:
        """[JDT-01] java/overridableMethods must list overridable methods from superclass and interfaces."""
        base = """\
package com.example.jdt;

public class BaseWorker {
  public void execute(String job) {}
  protected int priority() { return 1; }
}
"""
        sub = """\
package com.example.jdt;

public class SubWorker extends BaseWorker implements Runnable {
}
"""
        self._open_temp("src/com/example/jdt/BaseWorker.java", base)
        sub_uri = self._open_temp("src/com/example/jdt/SubWorker.java", sub)
        pos = pos_of(sub, "SubWorker")
        res = self._safe_request("JDT-01", self.client.java_overridable_methods, sub_uri, pos)
        methods = res.get("methods", res) if isinstance(res, dict) else res
        self.assertIsInstance(
            methods,
            list,
            f"[JDT-01] Expected list (or dict with 'methods') from java/overridableMethods, got: {res!r}",
        )
        names = {m.get("name") or m.get("methodName") or m.get("label") for m in methods if isinstance(m, dict)}
        self.assertTrue(
            {"run", "execute", "priority"}.issubset(names),
            f"[JDT-01] Expected 'run', 'execute', 'priority' in java/overridableMethods, got names={names}, raw={res!r}",
        )

    def test_java_override_methods(self) -> None:
        """[JDT-02] java/overrideMethods must return edits inserting @Override stub(s)."""
        sub = """\
package com.example.jdt;

public class OverrideTarget implements Runnable {
}
"""
        uri = self._open_temp("src/com/example/jdt/OverrideTarget.java", sub)
        pos = pos_of(sub, "OverrideTarget")
        avail = self._safe_request("JDT-02-list", self.client.java_overridable_methods, uri, pos)
        items = avail.get("methods", avail) if isinstance(avail, dict) else avail
        self.assertIsInstance(items, list, f"[JDT-02] Expected overridable methods list, got: {avail!r}")
        run_items = [m for m in items if isinstance(m, dict) and (m.get("name") or m.get("methodName")) == "run"]
        self.assertTrue(run_items, f"[JDT-02] Expected 'run' in overridableMethods, got: {avail!r}")

        res = self._safe_request("JDT-02-apply", self.client.java_override_methods, uri, methods=run_items, pos_or_line=pos)
        if isinstance(res, list):
            new_text = self._safe_apply_text_edits("JDT-02", sub, res)
        elif isinstance(res, dict):
            updated_map, _ = self._safe_apply_workspace_edit("JDT-02", res, {uri: sub})
            new_text = updated_map.get(uri, "")
        else:
            self.fail(f"[JDT-02] Unexpected response from java/overrideMethods: {res!r}")
            return

        self.assertIn("@Override", new_text, f"[JDT-02] Expected @Override in generated stub:\n{new_text}")
        self.assertIn("void run()", new_text, f"[JDT-02] Expected 'void run()' in generated stub:\n{new_text}")


if __name__ == "__main__":
    unittest.main()
