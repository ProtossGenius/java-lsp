"""Black-box LSP tests for Java completion, auto-imports, chains, and syntax recovery."""

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
    apply_text_edits,
    pos_of,
)

SPRING_DEMO_DIR = REPO_ROOT / "java17-spring-demo"
_IDENT_SPLIT_RE = re.compile(r"[(\s:<]")


def _item_insert_text(item: dict[str, Any]) -> str:
    text_edit = item.get("textEdit")
    if isinstance(text_edit, dict) and isinstance(text_edit.get("newText"), str):
        return text_edit["newText"]
    ins = item.get("insertText")
    if isinstance(ins, str) and ins:
        return ins
    return str(item.get("label", ""))


def _item_ident(item: dict[str, Any]) -> str:
    label = str(item.get("label", "")).strip()
    if not label:
        return ""
    return _IDENT_SPLIT_RE.split(label, 1)[0]


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


def _format_items_preview(items: list[dict[str, Any]], limit: int = 25) -> str:
    preview = [
        {
            "label": it.get("label"),
            "kind": it.get("kind"),
            "detail": it.get("detail"),
            "insertText": it.get("insertText"),
            "sortText": it.get("sortText"),
        }
        for it in items[:limit]
    ]
    suffix = f" ... (+{len(items) - limit} more)" if len(items) > limit else ""
    return f"{preview}{suffix}"


def _clean_m2_markers(root: Path) -> None:
    for marker in root.rglob(".classpath_m2.txt"):
        marker.unlink(missing_ok=True)


class _CompletionTestBase(unittest.TestCase):
    """Shared safe LSP request and completion assertion helpers."""

    project: TempJavaProject
    client: LspClient
    init_result: dict[str, Any]
    base_files: dict[str, str] = {}
    init_timeout: float = 45.0

    @classmethod
    def _start_workspace(cls) -> None:
        cls.client = LspClient(root_path=cls.project.root, default_timeout=20.0)
        cls.client.start()
        cls.init_result = cls.client.initialize_and_ready(timeout=cls.init_timeout)

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.project = TempJavaProject(files=cls.base_files, prefix="comp_bb_")
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
            self.__class__.init_result = self.client.initialize_and_ready(
                timeout=self.init_timeout
            )

    def _open_temp_variant(
        self,
        rel_path: str,
        modified_text: str,
        original_text: str | None = None,
    ) -> str:
        uri = self.project.uri(rel_path)
        base_text = (
            original_text if original_text is not None else self.project.read_file(rel_path)
        )
        self.client.did_open(uri, base_text)
        if modified_text != base_text:
            self.client.did_change(uri, modified_text)
        self.addCleanup(self._restore_and_close, uri, base_text)
        return uri

    def _restore_and_close(self, uri: str, original_text: str) -> None:
        if not self.client.is_alive():
            return
        try:
            self.client.did_change(uri, original_text)
            self.client.did_close(uri)
        except Exception:
            pass

    def _req_completion(
        self,
        uri: str,
        text: str,
        pos: dict[str, int],
        *,
        tag: str,
        trigger_kind: int = 1,
        trigger_char: str | None = None,
    ) -> list[dict[str, Any]]:
        line_txt = text.splitlines()[pos["line"]] if pos["line"] < len(text.splitlines()) else ""
        try:
            res = self.client.completion(
                uri,
                pos,
                trigger_kind=trigger_kind,
                trigger_character=trigger_char,
            )
        except (LspResponseError, ServerCrashedError, TimeoutError, LspClientError) as exc:
            self.fail(
                f"[{tag}] textDocument/completion failed at {uri} pos={pos} "
                f"line={line_txt!r} (triggerKind={trigger_kind}, triggerChar={trigger_char!r}): "
                f"{type(exc).__name__}: {exc}"
            )
        return _extract_items(res)

    def _req_resolve(
        self,
        item: dict[str, Any],
        *,
        tag: str,
        context_desc: str,
    ) -> dict[str, Any]:
        try:
            res = self.client.completion_resolve(item)
        except (LspResponseError, ServerCrashedError, TimeoutError, LspClientError) as exc:
            self.fail(
                f"[{tag}] completionItem/resolve failed for {context_desc} "
                f"(item={item!r}): {type(exc).__name__}: {exc}"
            )
        if not isinstance(res, dict):
            self.fail(
                f"[{tag}] completionItem/resolve returned non-dict {res!r} for {context_desc}; "
                f"expected resolved CompletionItem dict."
            )
        return res

    def _assert_has_member(
        self,
        items: list[dict[str, Any]],
        expected: str,
        *,
        tag: str,
        context_desc: str,
    ) -> dict[str, Any]:
        for item in items:
            if _item_ident(item) == expected:
                return item
        self.fail(
            f"[{tag}] Expected completion item {expected!r} at {context_desc}, "
            f"but got {len(items)} items: {_format_items_preview(items)}"
        )

    def _assert_lacks_member(
        self,
        items: list[dict[str, Any]],
        forbidden: str,
        *,
        tag: str,
        context_desc: str,
        reason: str,
    ) -> None:
        matches = [it for it in items if _item_ident(it) == forbidden]
        if matches:
            self.fail(
                f"[{tag}] Completion at {context_desc} must NOT include {forbidden!r} "
                f"({reason}), but found: {matches!r}"
            )


class TestCompletionBasicsAndVisibility(_CompletionTestBase):
    """Member completion, visibility, filtering, deduplication, and sorting."""

    base_files = {
        "src/main/java/pkg1/BaseEntity.java": """package pkg1;

public class BaseEntity {
  public Long baseId;
  protected String protectedTag;
  String packageNote;
  private String privateSecret;

  /** Returns entity identifier. */
  public Long getBaseId() {
    return baseId;
  }

  protected void onProtectedHook() {}

  void packageHelper() {}

  private void internalOnly() {}

  public String describe() {
    return "base";
  }
}
""",
        "src/main/java/pkg1/UserAccount.java": """package pkg1;

import java.util.List;

public class UserAccount extends BaseEntity implements Greeter {
  public static final String DEFAULT_ROLE = "USER";
  public String publicLabel;
  protected String protectedCode;
  String packageAlias;
  private String privateToken;

  public UserAccount() {}

  public UserAccount(String publicLabel) {
    this.publicLabel = publicLabel;
  }

  public static UserAccount createDefault() {
    return new UserAccount(DEFAULT_ROLE);
  }

  /** Gets the primary user name. */
  public String getUser() {
    return publicLabel;
  }

  public String getUserById(long id) {
    return publicLabel + id;
  }

  public void setPublicLabel(String publicLabel) {
    this.publicLabel = publicLabel;
  }

  public void printValue(int x) {}

  public void printValue(String x) {}

  @Override
  public String greet() {
    return "hi";
  }

  @Override
  public String toString() {
    return "UserAccount";
  }

  public void inspectSelf() {
    this.publicLabel = "x";
  }
}
""",
        "src/main/java/pkg1/Greeter.java": """package pkg1;

public interface Greeter {
  String greet();
}
""",
        "src/main/java/pkg1/SamePackageCaller.java": """package pkg1;

public class SamePackageCaller {
  public void run() {
    UserAccount acc = new UserAccount();
    acc.getUser();
  }
}
""",
        "src/main/java/pkg2/OtherPackageCaller.java": """package pkg2;

import pkg1.UserAccount;

public class OtherPackageCaller {
  public void run() {
    UserAccount acc = new UserAccount();
    acc.getUser();
  }
}
""",
        "src/main/java/pkg2/SubAccount.java": """package pkg2;

import pkg1.UserAccount;

public class SubAccount extends UserAccount {
  public void run() {
    this.getUser();
  }
}
""",
    }

    def test_instance_members_completion(self) -> None:
        """[COMP-01] Instance dot completion includes accessible public methods and fields."""
        rel = "src/main/java/pkg1/SamePackageCaller.java"
        orig = self.project.read_file(rel)
        mod = orig.replace("acc.getUser();", "acc.")
        uri = self._open_temp_variant(rel, mod, orig)
        items = self._req_completion(
            uri, mod, after_of(mod, "acc."), tag="COMP-01", trigger_kind=2, trigger_char="."
        )
        for name in ("getUser", "getUserById", "setPublicLabel", "publicLabel", "greet"):
            self._assert_has_member(items, name, tag="COMP-01", context_desc="acc.")

    def test_inherited_superclass_and_object_methods(self) -> None:
        """[COMP-02] Dot completion includes inherited superclass and java.lang.Object methods."""
        rel = "src/main/java/pkg1/SamePackageCaller.java"
        orig = self.project.read_file(rel)
        mod = orig.replace("acc.getUser();", "acc.")
        uri = self._open_temp_variant(rel, mod, orig)
        items = self._req_completion(
            uri, mod, after_of(mod, "acc."), tag="COMP-02", trigger_kind=2, trigger_char="."
        )
        for name in ("getBaseId", "describe", "toString", "equals", "hashCode", "getClass"):
            self._assert_has_member(
                items, name, tag="COMP-02", context_desc="acc. (inherited + Object)"
            )

    def test_static_members_on_class_name(self) -> None:
        """[COMP-03] Static completion on ClassName. offers static members and excludes instance members."""
        rel = "src/main/java/pkg1/SamePackageCaller.java"
        orig = self.project.read_file(rel)
        mod = orig.replace("acc.getUser();", "UserAccount.")
        uri = self._open_temp_variant(rel, mod, orig)
        items = self._req_completion(
            uri,
            mod,
            after_of(mod, "UserAccount."),
            tag="COMP-03",
            trigger_kind=2,
            trigger_char=".",
        )
        self._assert_has_member(items, "DEFAULT_ROLE", tag="COMP-03", context_desc="UserAccount.")
        self._assert_has_member(items, "createDefault", tag="COMP-03", context_desc="UserAccount.")
        self._assert_lacks_member(
            items,
            "getUser",
            tag="COMP-03",
            context_desc="UserAccount.",
            reason="instance method must not appear on static ClassName. completion",
        )

    def test_visibility_filtering_same_and_cross_package(self) -> None:
        """[COMP-04] Visibility filtering hides private members and cross-package package-private/protected members."""
        # Same package: sees package-private and protected, NOT private
        same_rel = "src/main/java/pkg1/SamePackageCaller.java"
        same_orig = self.project.read_file(same_rel)
        same_mod = same_orig.replace("acc.getUser();", "acc.")
        same_uri = self._open_temp_variant(same_rel, same_mod, same_orig)
        same_items = self._req_completion(
            same_uri,
            same_mod,
            after_of(same_mod, "acc."),
            tag="COMP-04",
            trigger_kind=2,
            trigger_char=".",
        )
        self._assert_has_member(
            same_items, "packageAlias", tag="COMP-04", context_desc="same-package acc."
        )
        self._assert_has_member(
            same_items, "protectedCode", tag="COMP-04", context_desc="same-package acc."
        )
        self._assert_lacks_member(
            same_items,
            "privateToken",
            tag="COMP-04",
            context_desc="same-package acc.",
            reason="private field in UserAccount must not be visible to SamePackageCaller",
        )
        self._assert_lacks_member(
            same_items,
            "privateSecret",
            tag="COMP-04",
            context_desc="same-package acc.",
            reason="private field in BaseEntity must not be visible to SamePackageCaller",
        )

        # Different package non-subclass: sees public only
        other_rel = "src/main/java/pkg2/OtherPackageCaller.java"
        other_orig = self.project.read_file(other_rel)
        other_mod = other_orig.replace("acc.getUser();", "acc.")
        other_uri = self._open_temp_variant(other_rel, other_mod, other_orig)
        other_items = self._req_completion(
            other_uri,
            other_mod,
            after_of(other_mod, "acc."),
            tag="COMP-04",
            trigger_kind=2,
            trigger_char=".",
        )
        self._assert_has_member(
            other_items, "publicLabel", tag="COMP-04", context_desc="cross-package acc."
        )
        for hidden, why in (
            ("privateToken", "private member"),
            ("packageAlias", "package-private member across packages"),
            ("protectedCode", "protected member on non-subclass across packages"),
        ):
            self._assert_lacks_member(
                other_items, hidden, tag="COMP-04", context_desc="cross-package acc.", reason=why
            )

        # Self access inside UserAccount: sees own private members
        self_rel = "src/main/java/pkg1/UserAccount.java"
        self_orig = self.project.read_file(self_rel)
        self_mod = self_orig.replace('this.publicLabel = "x";', "this.")
        self_uri = self._open_temp_variant(self_rel, self_mod, self_orig)
        self_items = self._req_completion(
            self_uri,
            self_mod,
            after_of(self_mod, "this."),
            tag="COMP-04",
            trigger_kind=2,
            trigger_char=".",
        )
        self._assert_has_member(
            self_items, "privateToken", tag="COMP-04", context_desc="this. inside UserAccount"
        )

    def test_prefix_filtering(self) -> None:
        """[COMP-05] Prefix filtering narrows candidates to matching prefix."""
        rel = "src/main/java/pkg1/SamePackageCaller.java"
        orig = self.project.read_file(rel)
        mod = orig.replace("acc.getUser();", "acc.getUs")
        uri = self._open_temp_variant(rel, mod, orig)
        items = self._req_completion(
            uri, mod, after_of(mod, "acc.getUs"), tag="COMP-05", trigger_kind=1
        )
        self._assert_has_member(items, "getUser", tag="COMP-05", context_desc="acc.getUs")
        self._assert_has_member(items, "getUserById", tag="COMP-05", context_desc="acc.getUs")
        self._assert_lacks_member(
            items,
            "setPublicLabel",
            tag="COMP-05",
            context_desc="acc.getUs",
            reason="does not match prefix getUs",
        )

    def test_camelcase_fuzzy_matching(self) -> None:
        """[COMP-06] CamelCase prefix matching resolves gU -> getUser and gUBI -> getUserById."""
        rel = "src/main/java/pkg1/SamePackageCaller.java"
        orig = self.project.read_file(rel)
        mod = orig.replace("acc.getUser();", "acc.gUBI")
        uri = self._open_temp_variant(rel, mod, orig)
        items = self._req_completion(
            uri, mod, after_of(mod, "acc.gUBI"), tag="COMP-06", trigger_kind=1
        )
        self._assert_has_member(items, "getUserById", tag="COMP-06", context_desc="acc.gUBI")

    def test_deduplication_and_overloads(self) -> None:
        """[COMP-07] Overridden interface/Object methods are deduplicated while distinct overloads remain."""
        rel = "src/main/java/pkg1/SamePackageCaller.java"
        orig = self.project.read_file(rel)
        mod = orig.replace("acc.getUser();", "acc.")
        uri = self._open_temp_variant(rel, mod, orig)
        items = self._req_completion(
            uri, mod, after_of(mod, "acc."), tag="COMP-07", trigger_kind=2, trigger_char="."
        )
        greet_items = [it for it in items if _item_ident(it) == "greet"]
        self.assertEqual(
            len(greet_items),
            1,
            f"[COMP-07] Expected exactly 1 deduplicated 'greet' completion item on UserAccount, "
            f"got {len(greet_items)}: {greet_items!r}",
        )
        to_string_items = [it for it in items if _item_ident(it) == "toString"]
        self.assertEqual(
            len(to_string_items),
            1,
            f"[COMP-07] Expected exactly 1 deduplicated 'toString' item on UserAccount, "
            f"got {len(to_string_items)}: {to_string_items!r}",
        )
        print_items = [it for it in items if _item_ident(it) == "printValue"]
        self.assertEqual(
            len(print_items),
            2,
            f"[COMP-07] Expected both overloads of printValue(int) and printValue(String), "
            f"got {len(print_items)}: {print_items!r}",
        )

    def test_sorting_and_sort_text(self) -> None:
        """[COMP-08] Completion items provide sortText and rank direct members ahead of Object methods."""
        rel = "src/main/java/pkg1/SamePackageCaller.java"
        orig = self.project.read_file(rel)
        mod = orig.replace("acc.getUser();", "acc.")
        uri = self._open_temp_variant(rel, mod, orig)
        items = self._req_completion(
            uri, mod, after_of(mod, "acc."), tag="COMP-08", trigger_kind=2, trigger_char="."
        )
        direct = self._assert_has_member(items, "getUser", tag="COMP-08", context_desc="acc.")
        obj_method = self._assert_has_member(items, "hashCode", tag="COMP-08", context_desc="acc.")
        self.assertTrue(
            direct.get("sortText"),
            f"[COMP-08] Expected non-empty sortText on completion item {direct!r}",
        )
        direct_idx = items.index(direct)
        obj_idx = items.index(obj_method)
        self.assertLess(
            direct_idx,
            obj_idx,
            f"[COMP-08] Expected direct member 'getUser' (idx={direct_idx}, sortText={direct.get('sortText')!r}) "
            f"to rank before inherited Object method 'hashCode' (idx={obj_idx}, sortText={obj_method.get('sortText')!r})",
        )


class TestAutoImportsKeywordsAnnotationsAndResolve(_CompletionTestBase):
    """Unimported class auto-imports, static imports, constructors, annotations, overrides, and resolve."""

    base_files = {
        "src/main/java/com/acme/other/RemoteOrder.java": """package com.acme.other;

public class RemoteOrder {
  private String orderNo;
  public String getOrderNo() { return orderNo; }
}
""",
        "src/main/java/com/acme/app/MyTag.java": """package com.acme.app;

import java.lang.annotation.Retention;
import java.lang.annotation.RetentionPolicy;

@Retention(RetentionPolicy.RUNTIME)
public @interface MyTag {
  String value() default "";
  int priority() default 1;
  boolean enabled() default true;
}
""",
        "src/main/java/com/acme/app/BaseWorker.java": """package com.acme.app;

public abstract class BaseWorker {
  public abstract String executeTask(int code);
  public String label() { return "base"; }
}
""",
        "src/main/java/com/acme/app/Person.java": """package com.acme.app;

public class Person {
  private final String name;
  public Person() { this.name = "anon"; }
  public Person(String name) { this.name = name; }
  public String getName() { return name; }
}
""",
        "src/main/java/com/acme/app/ImportPlayground.java": """package com.acme.app;

import static java.util.Collections.emptyList;

import java.util.List;

public class ImportPlayground extends BaseWorker {
  @Override
  public String executeTask(int code) {
    List<String> base = emptyList();
    return String.valueOf(base.size() + code);
  }
}
""",
    }

    def _assert_auto_import_edit(
        self,
        items: list[dict[str, Any]],
        class_name: str,
        expected_fqcn: str,
        *,
        tag: str,
        context_desc: str,
        source_text: str,
    ) -> None:
        matches = [it for it in items if _item_ident(it) == class_name]
        self.assertTrue(
            matches,
            f"[{tag}] Expected unimported class {class_name!r} at {context_desc}, "
            f"got: {_format_items_preview(items)}",
        )
        with_import = []
        for item in matches:
            edits = item.get("additionalTextEdits") or []
            if any(expected_fqcn in str(e.get("newText", "")) for e in edits):
                with_import.append(item)
        self.assertTrue(
            with_import,
            f"[{tag}] Completion for unimported {class_name!r} at {context_desc} lacked "
            f"additionalTextEdits importing {expected_fqcn!r}; matching items: {matches!r}",
        )
        applied = apply_text_edits(source_text, with_import[0]["additionalTextEdits"])
        self.assertIn(
            f"import {expected_fqcn};",
            applied,
            f"[{tag}] Applying additionalTextEdits did not insert 'import {expected_fqcn};'. "
            f"Result:\n{applied}",
        )

    def test_unimported_jdk_class_completion_with_auto_import(self) -> None:
        """[COMP-09] Unimported JDK classes (ArrayList, Collectors) offer auto-import additionalTextEdits."""
        rel = "src/main/java/com/acme/app/ImportPlayground.java"
        orig = self.project.read_file(rel)
        mod = orig.replace(
            "List<String> base = emptyList();",
            "ArrayList\n    List<String> base = emptyList();",
        )
        uri = self._open_temp_variant(rel, mod, orig)
        items = self._req_completion(
            uri, mod, after_of(mod, "ArrayList"), tag="COMP-09", trigger_kind=1
        )
        self._assert_auto_import_edit(
            items,
            "ArrayList",
            "java.util.ArrayList",
            tag="COMP-09",
            context_desc="unimported JDK ArrayList",
            source_text=mod,
        )

        mod2 = orig.replace(
            "List<String> base = emptyList();",
            "Collectors\n    List<String> base = emptyList();",
        )
        self.client.did_change(uri, mod2)
        items2 = self._req_completion(
            uri, mod2, after_of(mod2, "Collectors"), tag="COMP-09", trigger_kind=1
        )
        self._assert_auto_import_edit(
            items2,
            "Collectors",
            "java.util.stream.Collectors",
            tag="COMP-09",
            context_desc="unimported JDK Collectors",
            source_text=mod2,
        )

    def test_unimported_project_class_completion_with_auto_import(self) -> None:
        """[COMP-10] Unimported project class from another package offers auto-import additionalTextEdits."""
        rel = "src/main/java/com/acme/app/ImportPlayground.java"
        orig = self.project.read_file(rel)
        mod = orig.replace(
            "List<String> base = emptyList();",
            "RemoteOr\n    List<String> base = emptyList();",
        )
        uri = self._open_temp_variant(rel, mod, orig)
        items = self._req_completion(
            uri, mod, after_of(mod, "RemoteOr"), tag="COMP-10", trigger_kind=1
        )
        self._assert_auto_import_edit(
            items,
            "RemoteOrder",
            "com.acme.other.RemoteOrder",
            tag="COMP-10",
            context_desc="unimported project RemoteOrder",
            source_text=mod,
        )

    def test_static_import_completion(self) -> None:
        """[COMP-12] Static imported symbols (emptyList) and static members (Collectors.toList) complete cleanly."""
        rel = "src/main/java/com/acme/app/ImportPlayground.java"
        orig = self.project.read_file(rel)
        mod = orig.replace(
            "List<String> base = emptyList();",
            "List<String> base = emptyL",
        )
        uri = self._open_temp_variant(rel, mod, orig)
        items = self._req_completion(
            uri, mod, after_of(mod, "emptyL"), tag="COMP-12", trigger_kind=1
        )
        self._assert_has_member(
            items, "emptyList", tag="COMP-12", context_desc="static imported emptyList"
        )

        mod2 = orig.replace(
            "import static java.util.Collections.emptyList;",
            "import static java.util.Collections.emptyList;\nimport static java.util.stream.Collectors.toList;",
        ).replace(
            "List<String> base = emptyList();",
            "List<String> base = List.of(\"a\").stream().collect(toL);",
        )
        self.client.did_change(uri, mod2)
        items2 = self._req_completion(
            uri, mod2, after_of(mod2, "collect(toL"), tag="COMP-12", trigger_kind=1
        )
        self._assert_has_member(
            items2, "toList", tag="COMP-12", context_desc="static imported Collectors.toList"
        )

    def test_new_constructor_completion_diamond_and_overloads(self) -> None:
        """[COMP-13] Constructor completion after 'new ' suggests diamond generics and overloaded constructors."""
        rel = "src/main/java/com/acme/app/ImportPlayground.java"
        orig = self.project.read_file(rel)
        mod = orig.replace(
            "List<String> base = emptyList();",
            "List<String> base = new ArrayL",
        )
        uri = self._open_temp_variant(rel, mod, orig)
        items = self._req_completion(
            uri, mod, after_of(mod, "new ArrayL"), tag="COMP-13", trigger_kind=1
        )
        self._assert_has_member(
            items, "ArrayList", tag="COMP-13", context_desc="new ArrayL diamond constructor"
        )

        mod2 = orig.replace(
            "List<String> base = emptyList();",
            "Person p = new Person\n    List<String> base = emptyList();",
        )
        self.client.did_change(uri, mod2)
        items2 = self._req_completion(
            uri, mod2, after_of(mod2, "new Person"), tag="COMP-13", trigger_kind=1
        )
        person_ctors = [it for it in items2 if _item_ident(it) == "Person"]
        self.assertGreaterEqual(
            len(person_ctors),
            1,
            f"[COMP-13] Expected Person constructor completion after 'new Person', "
            f"got: {_format_items_preview(items2)}",
        )
        # Expect overloaded constructors Person() and Person(String name), not only bare Class kind 7
        self.assertGreaterEqual(
            len(person_ctors),
            2,
            f"[COMP-13] Expected both overloaded Person() and Person(String) constructors after 'new Person', "
            f"got {len(person_ctors)}: {person_ctors!r}",
        )

    def test_annotation_and_attribute_completion(self) -> None:
        """[COMP-14][COMP-15] '@' triggers annotation completion and inside '@MyTag(' suggests annotation attributes."""
        rel = "src/main/java/com/acme/app/ImportPlayground.java"
        orig = self.project.read_file(rel)
        mod = orig.replace(
            "@Override",
            "@MyT\n  @Override",
        )
        uri = self._open_temp_variant(rel, mod, orig)
        items = self._req_completion(
            uri, mod, after_of(mod, "@MyT"), tag="COMP-14", trigger_kind=1
        )
        self._assert_has_member(items, "MyTag", tag="COMP-14", context_desc="@MyT annotation")

        mod_attr = orig.replace(
            "@Override",
            "@MyTag()\n  @Override",
        )
        self.client.did_change(uri, mod_attr)
        attr_items = self._req_completion(
            uri, mod_attr, after_of(mod_attr, "@MyTag("), tag="COMP-15", trigger_kind=1
        )
        for attr in ("value", "priority", "enabled"):
            self._assert_has_member(
                attr_items, attr, tag="COMP-15", context_desc="@MyTag( attributes"
            )

    def test_import_statement_completion(self) -> None:
        """[COMP-16] Completion inside 'import java.util.' and 'import com.acme.' lists packages and types."""
        rel = "src/main/java/com/acme/app/ImportPlayground.java"
        orig = self.project.read_file(rel)
        mod = orig.replace(
            "import java.util.List;",
            "import java.util.List;\nimport java.util.",
        )
        uri = self._open_temp_variant(rel, mod, orig)
        items = self._req_completion(
            uri,
            mod,
            after_of(mod, "import java.util."),
            tag="COMP-16",
            trigger_kind=2,
            trigger_char=".",
        )
        self._assert_has_member(items, "Map", tag="COMP-16", context_desc="import java.util.")
        self._assert_has_member(items, "ArrayList", tag="COMP-16", context_desc="import java.util.")

    def test_override_method_completion_in_class_body(self) -> None:
        """[COMP-17] Typing superclass/interface method prefix in class body offers override completion."""
        rel = "src/main/java/com/acme/app/ImportPlayground.java"
        orig = self.project.read_file(rel)
        mod = orig.replace(
            "@Override",
            "lab\n  @Override",
        )
        uri = self._open_temp_variant(rel, mod, orig)
        items = self._req_completion(
            uri, mod, after_of(mod, "lab"), tag="COMP-17", trigger_kind=1
        )
        match = self._assert_has_member(
            items, "label", tag="COMP-17", context_desc="override 'label' in class body"
        )
        ins = _item_insert_text(match)
        self.assertTrue(
            "label" in ins and ("{" in ins or "Override" in ins or "public String label" in ins),
            f"[COMP-17] Expected override method stub for BaseWorker.label(), got item: {match!r}",
        )

    def test_completion_item_resolve_and_javadoc(self) -> None:
        """[COMP-18] completionProvider.resolveProvider and completionItem/resolve provide documentation/detail."""
        cap = self.init_result.get("capabilities", {}).get("completionProvider", {})
        resolve_supported = bool(cap.get("resolveProvider"))
        rel = "src/main/java/com/acme/app/ImportPlayground.java"
        orig = self.project.read_file(rel)
        mod = orig.replace(
            "List<String> base = emptyList();",
            "Person p = new Person();\n    p.getN;\n    List<String> base = emptyList();",
        )
        uri = self._open_temp_variant(rel, mod, orig)
        items = self._req_completion(
            uri, mod, after_of(mod, "p.getN"), tag="COMP-18", trigger_kind=1
        )
        get_name_item = self._assert_has_member(
            items, "getName", tag="COMP-18", context_desc="p.getN"
        )
        self.assertTrue(
            resolve_supported,
            f"[COMP-18] Expected completionProvider.resolveProvider=True in serverCapabilities, "
            f"got {cap!r}",
        )
        resolved = self._req_resolve(
            get_name_item, tag="COMP-18", context_desc="Person.getName"
        )
        doc = resolved.get("documentation")
        detail = resolved.get("detail")
        self.assertTrue(
            bool(doc) or bool(detail),
            f"[COMP-18] Expected documentation or detail after completionItem/resolve, got {resolved!r}",
        )


class TestMavenUnimportedCompletion(_CompletionTestBase):
    """Auto-import completion for Maven dependencies in an isolated temp copy of java17-spring-demo."""

    init_timeout = 60.0

    @classmethod
    def setUpClass(cls) -> None:
        unittest.TestCase.setUpClass()
        cls.project = TempJavaProject(prefix="comp_spring_bb_")
        cls.project.copy_from(SPRING_DEMO_DIR)
        _clean_m2_markers(cls.project.root)
        cls._start_workspace()

    def test_unimported_maven_dependency_completion(self) -> None:
        """[COMP-11] Unimported Maven dependency class (ResponseEntity) completes with auto-import."""
        rel = "core/src/main/java/com/example/demo/controller/UserController.java"
        orig = self.project.read_file(rel)
        mod = orig.replace(
            "return userService.listUsers();",
            "ResponseEnt\n    return userService.listUsers();",
        )
        uri = self._open_temp_variant(rel, mod, orig)
        items = self._req_completion(
            uri, mod, after_of(mod, "ResponseEnt"), tag="COMP-11", trigger_kind=1
        )
        matches = [it for it in items if _item_ident(it) == "ResponseEntity"]
        self.assertTrue(
            matches,
            f"[COMP-11] Expected Maven dependency class 'ResponseEntity' in completion, "
            f"got: {_format_items_preview(items)}",
        )
        with_import = [
            it
            for it in matches
            if any(
                "org.springframework.http.ResponseEntity" in str(e.get("newText", ""))
                for e in (it.get("additionalTextEdits") or [])
            )
        ]
        self.assertTrue(
            with_import,
            f"[COMP-11] Expected additionalTextEdits importing org.springframework.http.ResponseEntity, "
            f"got: {matches!r}",
        )


class TestChainedTypeGraphAndRecovery(_CompletionTestBase):
    """Smart chained type-graph completion, filtering, ranking, and incomplete syntax recovery."""

    base_files = {
        "src/main/java/chain/Address.java": """package chain;

public class Address {
  private String city;
  public String getCity() { return city; }
}
""",
        "src/main/java/chain/UserDto.java": """package chain;

public class UserDto {
  private Long id;
  private String email;
  private Address address;

  public Long getId() { return id; }
  public String getEmail() { return email; }
  public Address getAddress() { return address; }
}
""",
        "src/main/java/chain/ChainPlayground.java": """package chain;

import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Set;

public class ChainPlayground {
  public List<String> convertReturn(List<Integer> nums) {
    return List.of();
  }

  public void testChains(
      List<Integer> list,
      Optional<UserDto> optUser,
      Map<String, UserDto> userMap,
      Set<UserDto> userSet,
      UserDto user) {
    int keepAlive = list.size() + userMap.size() + userSet.size();
    if (optUser.isPresent() && keepAlive > 0) {
      user.getEmail();
    }
  }

  private void consume(String val) {}
}
""",
    }

    def test_list_integer_to_list_string_chain(self) -> None:
        """[COMP-19] Typed assignment List<String> strList = list. proposes stream().map(...).toList() chains."""
        rel = "src/main/java/chain/ChainPlayground.java"
        orig = self.project.read_file(rel)
        mod = orig.replace(
            "int keepAlive = list.size() + userMap.size() + userSet.size();",
            "List<String> strList = list.\n    int keepAlive = list.size() + userMap.size() + userSet.size();",
        )
        uri = self._open_temp_variant(rel, mod, orig)
        items = self._req_completion(
            uri,
            mod,
            after_of(mod, "List<String> strList = list."),
            tag="COMP-19",
            trigger_kind=2,
            trigger_char=".",
        )
        chain_texts = [
            f"{it.get('label', '')} | {_item_insert_text(it)}"
            for it in items
            if "stream()" in f"{it.get('label', '')} {_item_insert_text(it)}"
            and ("toList()" in f"{it.get('label', '')} {_item_insert_text(it)}" or "Collectors.toList" in f"{it.get('label', '')} {_item_insert_text(it)}")
        ]
        self.assertTrue(
            chain_texts,
            f"[COMP-19] Expected chained transformation stream().map(...).toList() / Collectors.toList() "
            f"for 'List<String> strList = list.', got: {_format_items_preview(items)}",
        )

    def test_smart_filtering_invalid_methods_in_assignment(self) -> None:
        """[COMP-20] Typed assignment List<String> strList = list. filters out clear(), size(), add()."""
        rel = "src/main/java/chain/ChainPlayground.java"
        orig = self.project.read_file(rel)
        mod = orig.replace(
            "int keepAlive = list.size() + userMap.size() + userSet.size();",
            "List<String> strList = list.\n    int keepAlive = list.size() + userMap.size() + userSet.size();",
        )
        uri = self._open_temp_variant(rel, mod, orig)
        items = self._req_completion(
            uri,
            mod,
            after_of(mod, "List<String> strList = list."),
            tag="COMP-20",
            trigger_kind=2,
            trigger_char=".",
        )
        for bad, why in (
            ("clear", "returns void, cannot produce List<String>"),
            ("size", "returns int, cannot produce List<String>"),
            ("add", "returns boolean, cannot produce List<String>"),
        ):
            self._assert_lacks_member(
                items,
                bad,
                tag="COMP-20",
                context_desc="List<String> strList = list.",
                reason=why,
            )

    def test_smart_filtering_in_return_context(self) -> None:
        """[COMP-21] Return statement 'return nums.' suggests List<String> chains and filters out clear()/size()."""
        rel = "src/main/java/chain/ChainPlayground.java"
        orig = self.project.read_file(rel)
        mod = orig.replace("return List.of();", "return nums.")
        uri = self._open_temp_variant(rel, mod, orig)
        items = self._req_completion(
            uri, mod, after_of(mod, "return nums."), tag="COMP-21", trigger_kind=2, trigger_char="."
        )
        has_chain = any(
            "stream()" in f"{it.get('label', '')} {_item_insert_text(it)}"
            for it in items
        )
        self.assertTrue(
            has_chain,
            f"[COMP-21] Expected stream chain in 'return nums.' inside List<String> method, "
            f"got: {_format_items_preview(items)}",
        )
        self._assert_lacks_member(
            items,
            "clear",
            tag="COMP-21",
            context_desc="return nums.",
            reason="void clear() is invalid in List<String> return context",
        )
        self._assert_lacks_member(
            items,
            "size",
            tag="COMP-21",
            context_desc="return nums.",
            reason="int size() is invalid in List<String> return context",
        )

    def test_optional_to_optional_string_chain(self) -> None:
        """[COMP-22] Optional<String> email = optUser. suggests map(...) and filters void/boolean methods."""
        rel = "src/main/java/chain/ChainPlayground.java"
        orig = self.project.read_file(rel)
        mod = orig.replace(
            "int keepAlive = list.size() + userMap.size() + userSet.size();",
            "Optional<String> email = optUser.\n    int keepAlive = list.size() + userMap.size() + userSet.size();",
        )
        uri = self._open_temp_variant(rel, mod, orig)
        items = self._req_completion(
            uri,
            mod,
            after_of(mod, "Optional<String> email = optUser."),
            tag="COMP-22",
            trigger_kind=2,
            trigger_char=".",
        )
        has_map = any("map" in _item_ident(it) for it in items)
        self.assertTrue(
            has_map,
            f"[COMP-22] Expected Optional.map transformation for 'Optional<String> email = optUser.', "
            f"got: {_format_items_preview(items)}",
        )
        self._assert_lacks_member(
            items,
            "isPresent",
            tag="COMP-22",
            context_desc="Optional<String> email = optUser.",
            reason="boolean isPresent() cannot yield Optional<String>",
        )

    def test_optional_unwrap_to_user(self) -> None:
        """[COMP-23] UserDto u = optUser. suggests unwrap methods (orElse, orElseGet, orElseThrow, get)."""
        rel = "src/main/java/chain/ChainPlayground.java"
        orig = self.project.read_file(rel)
        mod = orig.replace(
            "int keepAlive = list.size() + userMap.size() + userSet.size();",
            "UserDto u = optUser.\n    int keepAlive = list.size() + userMap.size() + userSet.size();",
        )
        uri = self._open_temp_variant(rel, mod, orig)
        items = self._req_completion(
            uri,
            mod,
            after_of(mod, "UserDto u = optUser."),
            tag="COMP-23",
            trigger_kind=2,
            trigger_char=".",
        )
        found = {_item_ident(it) for it in items}
        expected_any = {"orElse", "orElseGet", "orElseThrow", "get"}
        self.assertTrue(
            found & expected_any,
            f"[COMP-23] Expected one of {sorted(expected_any)} for 'UserDto u = optUser.', "
            f"got: {_format_items_preview(items)}",
        )

    def test_map_and_set_transformations(self) -> None:
        """[COMP-24] Set<String> keys = userMap. suggests keySet(), and List<String> names = userSet. suggests stream chain."""
        rel = "src/main/java/chain/ChainPlayground.java"
        orig = self.project.read_file(rel)
        mod = orig.replace(
            "int keepAlive = list.size() + userMap.size() + userSet.size();",
            "Set<String> keys = userMap.\n    int keepAlive = list.size() + userMap.size() + userSet.size();",
        )
        uri = self._open_temp_variant(rel, mod, orig)
        map_items = self._req_completion(
            uri,
            mod,
            after_of(mod, "Set<String> keys = userMap."),
            tag="COMP-24",
            trigger_kind=2,
            trigger_char=".",
        )
        self._assert_has_member(
            map_items, "keySet", tag="COMP-24", context_desc="Set<String> keys = userMap."
        )

        mod2 = orig.replace(
            "int keepAlive = list.size() + userMap.size() + userSet.size();",
            "List<String> names = userSet.\n    int keepAlive = list.size() + userMap.size() + userSet.size();",
        )
        self.client.did_change(uri, mod2)
        set_items = self._req_completion(
            uri,
            mod2,
            after_of(mod2, "List<String> names = userSet."),
            tag="COMP-24",
            trigger_kind=2,
            trigger_char=".",
        )
        has_stream = any(
            "stream()" in f"{it.get('label', '')} {_item_insert_text(it)}"
            for it in set_items
        )
        self.assertTrue(
            has_stream,
            f"[COMP-24] Expected stream() chain for 'List<String> names = userSet.', "
            f"got: {_format_items_preview(set_items)}",
        )

    def test_multi_hop_method_chain_resolution(self) -> None:
        """[COMP-25] Multi-hop chain user.getAddress().getCity(). resolves String methods."""
        rel = "src/main/java/chain/ChainPlayground.java"
        orig = self.project.read_file(rel)
        mod = orig.replace(
            "user.getEmail();",
            "user.getAddress().getCity().",
        )
        uri = self._open_temp_variant(rel, mod, orig)
        items = self._req_completion(
            uri,
            mod,
            after_of(mod, "user.getAddress().getCity()."),
            tag="COMP-25",
            trigger_kind=2,
            trigger_char=".",
        )
        self._assert_has_member(
            items, "toUpperCase", tag="COMP-25", context_desc="user.getAddress().getCity()."
        )
        self._assert_has_member(
            items, "length", tag="COMP-25", context_desc="user.getAddress().getCity()."
        )

    @staticmethod
    def _chain_hop_count(expr: str) -> int:
        depth = 0
        hops = 1
        for ch in expr:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth = max(0, depth - 1)
            elif ch == "." and depth == 0:
                hops += 1
        return hops

    def test_chain_ranking_length_then_alphabetical(self) -> None:
        """[COMP-26] Chain ranking orders shorter chains before longer chains, and ties alphabetically."""
        rel = "src/main/java/chain/ChainPlayground.java"
        orig = self.project.read_file(rel)
        mod = orig.replace(
            "int keepAlive = list.size() + userMap.size() + userSet.size();",
            "List<String> strList = list.\n    int keepAlive = list.size() + userMap.size() + userSet.size();",
        )
        uri = self._open_temp_variant(rel, mod, orig)
        items = self._req_completion(
            uri,
            mod,
            after_of(mod, "List<String> strList = list."),
            tag="COMP-26",
            trigger_kind=2,
            trigger_char=".",
        )
        chains = [
            it
            for it in items
            if "Chained completion" in str(it.get("detail", ""))
            or self._chain_hop_count(_item_insert_text(it)) > 1
        ]
        self.assertTrue(
            len(chains) >= 2,
            f"[COMP-26] Expected >= 2 chained completions to verify ranking, got {chains!r}",
        )
        hop_counts = [
            self._chain_hop_count(_item_insert_text(it) or str(it.get("label", "")))
            for it in chains
        ]
        self.assertEqual(
            hop_counts,
            sorted(hop_counts),
            f"[COMP-26] Expected chained completions sorted by chain hop count ascending, "
            f"got hop counts {hop_counts} for {[it.get('label') for it in chains]}",
        )
        for hops in sorted(set(hop_counts)):
            labels = [
                str(it.get("label", ""))
                for it, h in zip(chains, hop_counts)
                if h == hops
            ]
            self.assertEqual(
                labels,
                sorted(labels),
                f"[COMP-26] Expected equal-length ({hops}-hop) chains sorted alphabetically, got {labels!r}",
            )

    def test_incomplete_syntax_mid_statement_no_semicolon(self) -> None:
        """[COMP-27] Incomplete statement 'user.' without semicolon recovers and offers UserDto members."""
        rel = "src/main/java/chain/ChainPlayground.java"
        orig = self.project.read_file(rel)
        mod = orig.replace("user.getEmail();", "user.")
        uri = self._open_temp_variant(rel, mod, orig)
        items = self._req_completion(
            uri, mod, after_of(mod, "user."), tag="COMP-27", trigger_kind=2, trigger_char="."
        )
        self._assert_has_member(items, "getEmail", tag="COMP-27", context_desc="user. (no semicolon)")
        self._assert_has_member(items, "getAddress", tag="COMP-27", context_desc="user. (no semicolon)")

    def test_incomplete_syntax_inside_if_condition(self) -> None:
        """[COMP-28] Incomplete expression inside 'if (user.' recovers and offers UserDto members."""
        rel = "src/main/java/chain/ChainPlayground.java"
        orig = self.project.read_file(rel)
        mod = orig.replace(
            "if (optUser.isPresent() && keepAlive > 0) {",
            "if (user.\n    if (optUser.isPresent() && keepAlive > 0) {",
        )
        uri = self._open_temp_variant(rel, mod, orig)
        items = self._req_completion(
            uri, mod, after_of(mod, "if (user."), tag="COMP-28", trigger_kind=2, trigger_char="."
        )
        self._assert_has_member(items, "getEmail", tag="COMP-28", context_desc="if (user.")

    def test_incomplete_syntax_inside_method_args(self) -> None:
        """[COMP-29] Incomplete call 'consume(user.' recovers and offers UserDto members."""
        rel = "src/main/java/chain/ChainPlayground.java"
        orig = self.project.read_file(rel)
        mod = orig.replace("user.getEmail();", "consume(user.")
        uri = self._open_temp_variant(rel, mod, orig)
        items = self._req_completion(
            uri, mod, after_of(mod, "consume(user."), tag="COMP-29", trigger_kind=2, trigger_char="."
        )
        self._assert_has_member(items, "getEmail", tag="COMP-29", context_desc="consume(user.")

    def test_incomplete_syntax_mid_stream_lambda(self) -> None:
        """[COMP-30] Incomplete chained stream lambda 'userSet.stream().filter(u -> u.' resolves UserDto members."""
        rel = "src/main/java/chain/ChainPlayground.java"
        orig = self.project.read_file(rel)
        mod = orig.replace("user.getEmail();", "userSet.stream().filter(u -> u.")
        uri = self._open_temp_variant(rel, mod, orig)
        items = self._req_completion(
            uri,
            mod,
            after_of(mod, "filter(u -> u."),
            tag="COMP-30",
            trigger_kind=2,
            trigger_char=".",
        )
        self._assert_has_member(
            items, "getEmail", tag="COMP-30", context_desc="userSet.stream().filter(u -> u."
        )


if __name__ == "__main__":
    unittest.main()
