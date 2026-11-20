"""Black-box LSP 3.17 protocol, serverCapabilities, lifecycle, sync, and encoding tests."""

from __future__ import annotations

import shutil
import unittest
from pathlib import Path
from typing import Any

from tests.blackbox.lsp_client import (
    LspClient,
    LspClientError,
    LspResponseError,
    ServerCrashedError,
    TempJavaProject,
    pos_of,
    range_of,
)


def _minimal_pom(artifact: str = "proto-demo") -> str:
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


def _safe_request(
    case: unittest.TestCase,
    client: LspClient,
    method: str,
    params: Any = None,
    timeout: float = 15.0,
) -> Any:
    """Execute an LSP request, converting transport/protocol exceptions to GAP_FAIL."""
    try:
        return client.request(method, params, timeout=timeout)
    except LspResponseError as exc:
        case.fail(
            f"LSP request {method!r} with params={params!r} returned JSON-RPC error "
            f"code={exc.code} message={exc.message!r} (data={exc.data!r}); "
            "expected valid non-error response"
        )
    except (ServerCrashedError, TimeoutError, LspClientError) as exc:
        case.fail(f"LSP request {method!r} with params={params!r} failed: {exc}")


class TestServerCapabilitiesAudit(unittest.TestCase):
    """Audit initialize response serverCapabilities against LSP 3.17."""

    project: TempJavaProject
    client: LspClient
    init_result: dict[str, Any]
    caps: dict[str, Any]

    @classmethod
    def setUpClass(cls) -> None:
        cls.project = TempJavaProject(
            files={
                "pom.xml": _minimal_pom("cap-audit"),
                "src/main/java/com/acme/Hello.java": (
                    "package com.acme;\n\n"
                    "public class Hello {\n"
                    "  public String greet() { return \"hi\"; }\n"
                    "}\n"
                ),
            },
            create_root_marker=True,
        )
        cls.client = LspClient(root_path=cls.project.root, default_timeout=20.0)
        try:
            cls.init_result = cls.client.initialize_and_ready(timeout=60.0)
            cls.caps = dict(cls.init_result.get("capabilities") or {})
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

    def test_proto_01_server_info_and_initialize_structure(self) -> None:
        """[PROTO-01] initialize returns valid capabilities dict and serverInfo."""
        self.assertIsInstance(
            self.init_result,
            dict,
            f"initialize: expected dict result, got {self.init_result!r}",
        )
        self.assertIsInstance(
            self.caps,
            dict,
            f"initialize.capabilities: expected dict, got {self.caps!r}",
        )
        info = self.init_result.get("serverInfo")
        self.assertIsInstance(
            info,
            dict,
            f"initialize.serverInfo: expected dict, got {info!r}",
        )
        self.assertEqual(
            info.get("name"),
            "java-lsp",
            f"initialize.serverInfo.name: expected 'java-lsp', got {info!r}",
        )
        self.assertTrue(
            info.get("version"),
            f"initialize.serverInfo.version: expected non-empty version, got {info!r}",
        )

    def test_proto_02_incremental_text_document_sync_capability(self) -> None:
        """[PROTO-02] serverCapabilities.textDocumentSync advertises incremental sync (2)."""
        sync = self.caps.get("textDocumentSync")
        change_kind = sync.get("change") if isinstance(sync, dict) else sync
        self.assertEqual(
            change_kind,
            2,
            f"serverCapabilities.textDocumentSync: expected incremental sync (2 or {{change: 2}}), "
            f"got {sync!r}",
        )

    def test_proto_03_completion_resolve_provider_capability(self) -> None:
        """[PROTO-03] completionProvider advertises resolveProvider=true for lazy item resolution."""
        comp = self.caps.get("completionProvider")
        self.assertIsInstance(
            comp,
            dict,
            f"serverCapabilities.completionProvider: expected dict, got {comp!r}",
        )
        self.assertIs(
            comp.get("resolveProvider"),
            True,
            f"serverCapabilities.completionProvider.resolveProvider: expected True, got {comp!r}",
        )

    def test_proto_04_core_advertised_capabilities(self) -> None:
        """[PROTO-04] Core LSP navigation, hover, completion, signature, formatting, rename caps."""
        expected_truthy = [
            "hoverProvider",
            "definitionProvider",
            "declarationProvider",
            "typeDefinitionProvider",
            "implementationProvider",
            "referencesProvider",
            "documentSymbolProvider",
            "codeActionProvider",
            "documentFormattingProvider",
            "completionProvider",
            "signatureHelpProvider",
            "renameProvider",
        ]
        missing = [k for k in expected_truthy if not self.caps.get(k)]
        self.assertFalse(
            missing,
            f"serverCapabilities missing core expected providers {missing}; got keys={sorted(self.caps)}",
        )
        sig_triggers = (self.caps.get("signatureHelpProvider") or {}).get("triggerCharacters", [])
        self.assertIn("(", sig_triggers, f"signatureHelpProvider triggers missing '(': {sig_triggers!r}")
        self.assertIn(",", sig_triggers, f"signatureHelpProvider triggers missing ',': {sig_triggers!r}")
        comp_triggers = (self.caps.get("completionProvider") or {}).get("triggerCharacters", [])
        self.assertIn(".", comp_triggers, f"completionProvider triggers missing '.': {comp_triggers!r}")
        rename_cap = self.caps.get("renameProvider")
        self.assertTrue(
            isinstance(rename_cap, dict) and rename_cap.get("prepareProvider") is True,
            f"renameProvider: expected {{prepareProvider: True}}, got {rename_cap!r}",
        )

    def test_proto_05_workspace_symbol_provider_capability(self) -> None:
        """[PROTO-05] serverCapabilities advertises workspaceSymbolProvider."""
        self.assertTrue(
            self.caps.get("workspaceSymbolProvider"),
            f"serverCapabilities.workspaceSymbolProvider: expected truthy capability, "
            f"got {self.caps.get('workspaceSymbolProvider')!r} (advertised keys={sorted(self.caps)})",
        )

    def test_proto_06_semantic_tokens_provider_capability(self) -> None:
        """[PROTO-06] serverCapabilities advertises semanticTokensProvider with legend."""
        sem = self.caps.get("semanticTokensProvider")
        self.assertIsInstance(
            sem,
            dict,
            f"serverCapabilities.semanticTokensProvider: expected dict with legend, "
            f"got {sem!r} (advertised keys={sorted(self.caps)})",
        )
        self.assertIn("legend", sem, f"semanticTokensProvider missing 'legend': {sem!r}")

    def test_proto_07_inlay_hint_provider_capability(self) -> None:
        """[PROTO-07] serverCapabilities advertises inlayHintProvider."""
        self.assertTrue(
            self.caps.get("inlayHintProvider"),
            f"serverCapabilities.inlayHintProvider: expected truthy capability, "
            f"got {self.caps.get('inlayHintProvider')!r}",
        )

    def test_proto_08_folding_and_selection_range_capabilities(self) -> None:
        """[PROTO-08] serverCapabilities advertises foldingRangeProvider and selectionRangeProvider."""
        missing = [
            k
            for k in ("foldingRangeProvider", "selectionRangeProvider")
            if not self.caps.get(k)
        ]
        self.assertFalse(
            missing,
            f"serverCapabilities missing {missing}; actual values="
            f"{{k: self.caps.get(k) for k in ('foldingRangeProvider', 'selectionRangeProvider')}}",
        )

    def test_proto_09_document_highlight_provider_capability(self) -> None:
        """[PROTO-09] serverCapabilities advertises documentHighlightProvider."""
        self.assertTrue(
            self.caps.get("documentHighlightProvider"),
            f"serverCapabilities.documentHighlightProvider: expected truthy capability, "
            f"got {self.caps.get('documentHighlightProvider')!r}",
        )

    def test_proto_10_call_and_type_hierarchy_capabilities(self) -> None:
        """[PROTO-10] serverCapabilities advertises callHierarchyProvider and typeHierarchyProvider."""
        missing = [
            k
            for k in ("callHierarchyProvider", "typeHierarchyProvider")
            if not self.caps.get(k)
        ]
        self.assertFalse(
            missing,
            f"serverCapabilities missing hierarchy providers {missing}; "
            f"advertised keys={sorted(self.caps)}",
        )

    def test_proto_11_code_lens_and_document_link_capabilities(self) -> None:
        """[PROTO-11] serverCapabilities advertises codeLensProvider and documentLinkProvider."""
        missing = [
            k
            for k in ("codeLensProvider", "documentLinkProvider")
            if not self.caps.get(k)
        ]
        self.assertFalse(
            missing,
            f"serverCapabilities missing {missing}; advertised keys={sorted(self.caps)}",
        )

    def test_proto_12_linked_editing_range_capability(self) -> None:
        """[PROTO-12] serverCapabilities advertises linkedEditingRangeProvider."""
        self.assertTrue(
            self.caps.get("linkedEditingRangeProvider"),
            f"serverCapabilities.linkedEditingRangeProvider: expected truthy capability, "
            f"got {self.caps.get('linkedEditingRangeProvider')!r}",
        )

    def test_proto_13_range_and_on_type_formatting_capabilities(self) -> None:
        """[PROTO-13] serverCapabilities advertises documentRangeFormattingProvider and documentOnTypeFormattingProvider."""
        missing = [
            k
            for k in ("documentRangeFormattingProvider", "documentOnTypeFormattingProvider")
            if not self.caps.get(k)
        ]
        self.assertFalse(
            missing,
            f"serverCapabilities missing formatting providers {missing}; advertised keys={sorted(self.caps)}",
        )

    def test_proto_14_pull_diagnostic_provider_capability(self) -> None:
        """[PROTO-14] serverCapabilities advertises LSP 3.17 diagnosticProvider."""
        self.assertTrue(
            self.caps.get("diagnosticProvider"),
            f"serverCapabilities.diagnosticProvider: expected truthy LSP 3.17 pull diagnosticProvider, "
            f"got {self.caps.get('diagnosticProvider')!r}",
        )

    def test_proto_15_workspace_file_operations_and_folders_capabilities(self) -> None:
        """[PROTO-15] serverCapabilities.workspace advertises fileOperations and workspaceFolders."""
        ws = self.caps.get("workspace")
        self.assertIsInstance(
            ws,
            dict,
            f"serverCapabilities.workspace: expected dict with fileOperations and workspaceFolders, "
            f"got {ws!r} (advertised keys={sorted(self.caps)})",
        )
        file_ops = ws.get("fileOperations")
        self.assertIsInstance(
            file_ops,
            dict,
            f"serverCapabilities.workspace.fileOperations: expected dict, got {file_ops!r}",
        )
        for op in ("didCreate", "didDelete", "didRename", "willRename"):
            self.assertTrue(
                file_ops.get(op),
                f"workspace.fileOperations.{op}: expected truthy registration, got {file_ops!r}",
            )
        folders = ws.get("workspaceFolders")
        self.assertIsInstance(
            folders,
            dict,
            f"serverCapabilities.workspace.workspaceFolders: expected dict, got {folders!r}",
        )
        self.assertIs(
            folders.get("supported"),
            True,
            f"workspace.workspaceFolders.supported: expected True, got {folders!r}",
        )


class TestLifecycleAndProtocolHandling(unittest.TestCase):
    """Test LSP lifecycle, cancellation, unknown methods, incremental sync, and workspace events."""

    def _new_project_and_client(
        self,
        files: dict[str, str],
    ) -> tuple[TempJavaProject, LspClient]:
        proj = TempJavaProject(files=files, create_root_marker=True)
        self.addCleanup(proj.close)
        client = LspClient(root_path=proj.root, default_timeout=20.0)
        self.addCleanup(client.close)
        try:
            client.initialize_and_ready(timeout=60.0)
        except Exception as exc:
            self.fail(f"Failed to initialize server on temp project: {exc}")
        return proj, client

    def test_proto_16_shutdown_and_exit_clean_zero(self) -> None:
        """[PROTO-16] shutdown returns null and exit terminates process with exit code 0."""
        proj = TempJavaProject(
            files={
                "pom.xml": _minimal_pom("lifecycle"),
                "src/main/java/com/acme/A.java": "package com.acme;\npublic class A {}\n",
            },
            create_root_marker=True,
        )
        self.addCleanup(proj.close)
        client = LspClient(root_path=proj.root, default_timeout=20.0)
        self.addCleanup(client.close)
        try:
            client.initialize_and_ready(timeout=60.0)
            res = client.shutdown(timeout=10.0)
        except (LspClientError, TimeoutError) as exc:
            self.fail(f"shutdown failed: {exc}")
        self.assertIsNone(res, f"shutdown: expected null (None), got {res!r}")
        try:
            client.exit()
        except LspClientError as exc:
            self.fail(f"exit notification failed: {exc}")
        proc = client._proc
        self.assertIsNotNone(proc, "Expected subprocess to exist after exit")
        try:
            rc = proc.wait(timeout=5.0)
        except Exception as exc:
            self.fail(f"Server did not exit within 5s after exit notification: {exc}")
        self.assertEqual(rc, 0, f"exit after shutdown: expected returncode 0, got {rc}")

    def test_proto_17_request_after_shutdown_returns_invalid_request(self) -> None:
        """[PROTO-17] Requests sent after shutdown must return JSON-RPC -32600 (InvalidRequest)."""
        proj = TempJavaProject(
            files={
                "pom.xml": _minimal_pom("after-shutdown"),
                "src/main/java/com/acme/A.java": "package com.acme;\npublic class A {}\n",
            },
            create_root_marker=True,
        )
        self.addCleanup(proj.close)
        client = LspClient(root_path=proj.root, default_timeout=20.0)
        self.addCleanup(client.close)
        uri = proj.uri("src/main/java/com/acme/A.java")
        text = proj.read_file("src/main/java/com/acme/A.java")
        try:
            client.initialize_and_ready(timeout=60.0)
            client.did_open(uri, text)
            res_shutdown = client.shutdown(timeout=10.0)
        except (LspClientError, TimeoutError) as exc:
            self.fail(f"Setup/shutdown failed: {exc}")
        self.assertIsNone(res_shutdown, f"shutdown expected None, got {res_shutdown!r}")

        try:
            res, err = client.try_request(
                "textDocument/documentSymbol",
                {"textDocument": {"uri": uri}},
                timeout=5.0,
            )
        except (ServerCrashedError, TimeoutError, LspClientError) as exc:
            self.fail(
                f"textDocument/documentSymbol after shutdown failed unexpectedly ({exc}); "
                "expected JSON-RPC -32600 InvalidRequest"
            )
        self.assertIsNotNone(
            err,
            f"textDocument/documentSymbol after shutdown returned result={res!r} instead of "
            "JSON-RPC error -32600 (InvalidRequest)",
        )
        self.assertEqual(
            err.code,
            -32600,
            f"textDocument/documentSymbol after shutdown: expected error code -32600, "
            f"got {err.code} ({err.message!r})",
        )
        client.exit()

    def test_proto_18_cancel_request_does_not_crash_or_hang(self) -> None:
        """[PROTO-18] $/cancelRequest cancels or completes cleanly without crashing or wedging server."""
        src = (
            "package com.acme;\n\n"
            "public class CancelTarget {\n"
            "  public int compute(int x) {\n"
            "    int local = x + 1;\n"
            "    return local * 2;\n"
            "  }\n"
            "}\n"
        )
        proj, client = self._new_project_and_client(
            {
                "pom.xml": _minimal_pom("cancel"),
                "src/main/java/com/acme/CancelTarget.java": src,
            }
        )
        uri = proj.uri("src/main/java/com/acme/CancelTarget.java")
        client.did_open(uri, src)
        pos = pos_of(src, "local", occurrence=2)

        pending_handles = []
        for _ in range(4):
            pending_handles.append(
                client.send_request_async(
                    "textDocument/references",
                    {
                        "textDocument": {"uri": uri},
                        "position": pos,
                        "context": {"includeDeclaration": True},
                    },
                )
            )
        for p in pending_handles:
            p.cancel()

        for p in pending_handles:
            try:
                p.wait(timeout=10.0)
            except LspResponseError as exc:
                self.assertEqual(
                    exc.code,
                    -32800,
                    f"$/cancelRequest: expected -32800 RequestCancelled when error returned, "
                    f"got code={exc.code} message={exc.message!r}",
                )
            except (ServerCrashedError, TimeoutError, LspClientError) as exc:
                self.fail(f"$/cancelRequest caused failure on pending request {p.id}: {exc}")

        # Verify subsequent normal request still succeeds cleanly (use method hover + documentSymbol).
        compute_pos = pos_of(src, "public int compute", offset=11)
        hover_res = _safe_request(
            self,
            client,
            "textDocument/hover",
            {"textDocument": {"uri": uri}, "position": compute_pos},
        )
        self.assertIsNotNone(
            hover_res,
            "Post-cancel textDocument/hover on method 'compute' returned null; expected valid hover",
        )
        syms = _safe_request(
            self,
            client,
            "textDocument/documentSymbol",
            {"textDocument": {"uri": uri}},
        )
        self.assertTrue(
            isinstance(syms, list) and len(syms) > 0,
            f"Post-cancel documentSymbol expected non-empty symbols, got {syms!r}",
        )

    def test_proto_19_unknown_method_returns_32601(self) -> None:
        """[PROTO-19] Unknown LSP method returns JSON-RPC -32601 Method not found cleanly."""
        src = "package com.acme;\npublic class UnknownMethod {\n  int val = 1;\n}\n"
        proj, client = self._new_project_and_client(
            {
                "pom.xml": _minimal_pom("unknown-method"),
                "src/main/java/com/acme/UnknownMethod.java": src,
            }
        )
        uri = proj.uri("src/main/java/com/acme/UnknownMethod.java")
        client.did_open(uri, src)

        try:
            res, err = client.try_request("custom/nonExistentLspMethod", {"foo": "bar"}, timeout=5.0)
        except (ServerCrashedError, TimeoutError, LspClientError) as exc:
            self.fail(f"Unknown method 'custom/nonExistentLspMethod' crashed or timed out: {exc}")

        self.assertIsNotNone(
            err,
            f"Unknown method 'custom/nonExistentLspMethod' returned result={res!r}; "
            "expected JSON-RPC error -32601",
        )
        self.assertEqual(
            err.code,
            -32601,
            f"Unknown method 'custom/nonExistentLspMethod': expected -32601, got {err.code} ({err.message!r})",
        )

        # Server must remain alive and responsive afterward.
        sym = _safe_request(self, client, "textDocument/documentSymbol", {"textDocument": {"uri": uri}})
        self.assertIsInstance(sym, list, f"Post-error documentSymbol expected list, got {sym!r}")

    def test_proto_20_unknown_notification_ignored_safely(self) -> None:
        """[PROTO-20] Unknown notification $/nonExistentNotification is ignored without crashing."""
        src = "package com.acme;\npublic class NotifyOk {\n  public int f() { return 42; }\n}\n"
        proj, client = self._new_project_and_client(
            {
                "pom.xml": _minimal_pom("unknown-notif"),
                "src/main/java/com/acme/NotifyOk.java": src,
            }
        )
        uri = proj.uri("src/main/java/com/acme/NotifyOk.java")
        client.did_open(uri, src)
        try:
            client.notify("$/nonExistentNotification", {"detail": [1, 2, 3]})
        except LspClientError as exc:
            self.fail(f"Sending unknown notification failed: {exc}")
        sym = _safe_request(self, client, "textDocument/documentSymbol", {"textDocument": {"uri": uri}})
        self.assertTrue(
            isinstance(sym, list) and len(sym) > 0,
            f"documentSymbol after unknown notification expected symbols, got {sym!r}",
        )

    def test_proto_21_did_change_incremental_range_edits(self) -> None:
        """[PROTO-21] textDocument/didChange with incremental range edits updates document AST accurately."""
        src = (
            "package com.acme;\n\n"
            "public class IncEdit {\n"
            "  public int run() {\n"
            "    int alpha = 10;\n"
            "    int res = alpha + 1;\n"
            "    return res;\n"
            "  }\n"
            "}\n"
        )
        proj, client = self._new_project_and_client(
            {
                "pom.xml": _minimal_pom("inc-edit"),
                "src/main/java/com/acme/IncEdit.java": src,
            }
        )
        uri = proj.uri("src/main/java/com/acme/IncEdit.java")
        seq0 = client.did_open(uri, src)
        try:
            client.wait_for_diagnostics_quiescent(uri, timeout=10.0, after_seq=seq0)
        except (TimeoutError, LspClientError) as exc:
            self.fail(f"Initial diagnostics failed for IncEdit.java: {exc}")

        # Replace both occurrences of "alpha" with "beta" using incremental range edits.
        # Apply second occurrence first so earlier range positions stay valid in sequential application.
        r2 = range_of(src, "alpha", occurrence=2)
        r1 = range_of(src, "alpha", occurrence=1)
        seq1 = client.did_change(
            uri,
            changes=[
                {"range": r2, "text": "beta"},
                {"range": r1, "text": "beta"},
            ],
        )
        updated = src.replace("alpha", "beta")
        try:
            diags = client.wait_for_diagnostics_quiescent(uri, timeout=10.0, after_seq=seq1)
        except (TimeoutError, LspClientError) as exc:
            self.fail(f"Diagnostics after incremental didChange failed: {exc}")

        errors = [d for d in diags if d.get("severity", 1) == 1]
        self.assertEqual(
            errors,
            [],
            f"Incremental didChange replacing 'alpha'->'beta' produced unexpected errors: {errors!r}",
        )
        use_pos = pos_of(updated, "beta", occurrence=2)
        decl_rng = range_of(updated, "beta", occurrence=1)
        defn = _safe_request(
            self,
            client,
            "textDocument/definition",
            {"textDocument": {"uri": uri}, "position": use_pos},
        )
        locs = defn if isinstance(defn, list) else ([defn] if isinstance(defn, dict) else [])
        self.assertTrue(
            locs,
            f"textDocument/definition on incrementally edited 'beta' at {use_pos} returned {defn!r}; "
            f"expected location at {decl_rng}",
        )
        target_rng = locs[0].get("range") or locs[0].get("targetSelectionRange") or locs[0].get("targetRange")
        self.assertEqual(
            target_rng["start"]["line"],
            decl_rng["start"]["line"],
            f"definition after incremental didChange resolved to {locs[0]!r}; expected line {decl_rng['start']['line']}",
        )

    def test_proto_22_multi_file_open_close_reopen_lifecycle(self) -> None:
        """[PROTO-22] Multi-file didOpen/didChange/didClose falls back to disk and reopen overrides disk."""
        target_disk = (
            "package com.acme;\n\n"
            "public class Target {\n"
            "  public int diskMethod() { return 1; }\n"
            "}\n"
        )
        caller_v1 = (
            "package com.acme;\n\n"
            "public class Caller {\n"
            "  public int call(Target t) { return t.diskMethod(); }\n"
            "}\n"
        )
        proj, client = self._new_project_and_client(
            {
                "pom.xml": _minimal_pom("open-close"),
                "src/main/java/com/acme/Target.java": target_disk,
                "src/main/java/com/acme/Caller.java": caller_v1,
            }
        )
        target_uri = proj.uri("src/main/java/com/acme/Target.java")
        caller_uri = proj.uri("src/main/java/com/acme/Caller.java")

        client.did_open(target_uri, target_disk)
        client.did_open(caller_uri, caller_v1)

        # Edit Target in-memory (not saved to disk) and reference memMethod in Caller.
        target_mem = target_disk.replace("diskMethod", "memMethod")
        caller_mem = caller_v1.replace("diskMethod", "memMethod")
        client.did_change(target_uri, text=target_mem)
        client.did_change(caller_uri, text=caller_mem)

        hov_mem = _safe_request(
            self,
            client,
            "textDocument/hover",
            {"textDocument": {"uri": caller_uri}, "position": pos_of(caller_mem, "memMethod")},
        )
        self.assertTrue(
            hov_mem and "memMethod" in str(hov_mem),
            f"hover for in-memory Target.memMethod() returned {hov_mem!r}; expected Target.memMethod signature",
        )

        # Close Target -> Caller reverting to diskMethod must resolve to disk file.
        client.did_close(target_uri)
        client.did_change(caller_uri, text=caller_v1)
        hov_disk = _safe_request(
            self,
            client,
            "textDocument/hover",
            {"textDocument": {"uri": caller_uri}, "position": pos_of(caller_v1, "diskMethod")},
        )
        self.assertTrue(
            hov_disk and "diskMethod" in str(hov_disk),
            f"hover after didClose(Target) for diskMethod() returned {hov_disk!r}; "
            "expected fallback to disk Target.java",
        )

        # Reopen Target with new content -> Caller calling reopenedMethod resolves cleanly.
        target_reopened = target_disk.replace("diskMethod", "reopenedMethod")
        caller_reopened = caller_v1.replace("diskMethod", "reopenedMethod")
        client.did_open(target_uri, target_reopened, version=1)
        client.did_change(caller_uri, text=caller_reopened)
        hov_reopen = _safe_request(
            self,
            client,
            "textDocument/hover",
            {"textDocument": {"uri": caller_uri}, "position": pos_of(caller_reopened, "reopenedMethod")},
        )
        self.assertTrue(
            hov_reopen and "reopenedMethod" in str(hov_reopen),
            f"hover after reopen(Target) for reopenedMethod() returned {hov_reopen!r}; "
            "expected reopened Target.java method signature",
        )

    def test_proto_23_workspace_did_change_workspace_folders(self) -> None:
        """[PROTO-23] workspace/didChangeWorkspaceFolders adds a workspace folder whose symbols resolve."""
        main_src = (
            "package com.acme;\n\n"
            "import com.extra.ExtraHelper;\n\n"
            "public class MainWs {\n"
            "  public String go() { return ExtraHelper.ping(); }\n"
            "}\n"
        )
        proj, client = self._new_project_and_client(
            {
                "pom.xml": _minimal_pom("ws-folders-1"),
                "src/main/java/com/acme/MainWs.java": main_src,
            }
        )
        extra_proj = TempJavaProject(
            files={
                "pom.xml": _minimal_pom("ws-folders-2"),
                "src/main/java/com/extra/ExtraHelper.java": (
                    "package com.extra;\n\n"
                    "public class ExtraHelper {\n"
                    "  public static String ping() { return \"pong\"; }\n"
                    "}\n"
                ),
            },
            create_root_marker=True,
        )
        self.addCleanup(extra_proj.close)

        try:
            client.notify(
                "workspace/didChangeWorkspaceFolders",
                {
                    "event": {
                        "added": [{"uri": extra_proj.root_uri, "name": "extra"}],
                        "removed": [],
                    }
                },
            )
        except LspClientError as exc:
            self.fail(f"workspace/didChangeWorkspaceFolders notification failed: {exc}")

        main_uri = proj.uri("src/main/java/com/acme/MainWs.java")
        client.did_open(main_uri, main_src)
        hov = _safe_request(
            self,
            client,
            "textDocument/hover",
            {"textDocument": {"uri": main_uri}, "position": pos_of(main_src, "ping")},
        )
        self.assertTrue(
            hov and "ping" in str(hov),
            f"hover on ExtraHelper.ping() after didChangeWorkspaceFolders returned {hov!r}; "
            "expected 'String ping()' from added workspace folder",
        )

    def test_proto_24_workspace_did_change_watched_files(self) -> None:
        """[PROTO-24] workspace/didChangeWatchedFiles (Created/Changed/Deleted) updates workspace index."""
        caller_src = (
            "package com.acme;\n\n"
            "public class WatchCaller {\n"
            "  public int use(WatchedBean b) { return b.v1(); }\n"
            "}\n"
        )
        proj, client = self._new_project_and_client(
            {
                "pom.xml": _minimal_pom("watched"),
                "src/main/java/com/acme/WatchCaller.java": caller_src,
            }
        )
        caller_uri = proj.uri("src/main/java/com/acme/WatchCaller.java")
        client.did_open(caller_uri, caller_src)

        bean_rel = "src/main/java/com/acme/WatchedBean.java"
        bean_v1 = (
            "package com.acme;\n\n"
            "public class WatchedBean {\n"
            "  public int v1() { return 1; }\n"
            "}\n"
        )
        proj.write_file(bean_rel, bean_v1)
        bean_uri = proj.uri(bean_rel)

        try:
            client.workspace_did_change_watched_files([{"uri": bean_uri, "type": 1}])
        except LspClientError as exc:
            self.fail(f"didChangeWatchedFiles(Created) failed: {exc}")

        hov1 = _safe_request(
            self,
            client,
            "textDocument/hover",
            {"textDocument": {"uri": caller_uri}, "position": pos_of(caller_src, "v1")},
        )
        self.assertTrue(
            hov1 and "v1" in str(hov1),
            f"hover on WatchedBean.v1() after didChangeWatchedFiles(Created) returned {hov1!r}; "
            "expected 'int v1()'",
        )

        # Modify on disk and send Changed (type=2).
        bean_v2 = (
            "package com.acme;\n\n"
            "public class WatchedBean {\n"
            "  public int v2() { return 2; }\n"
            "}\n"
        )
        proj.write_file(bean_rel, bean_v2)
        caller_v2 = caller_src.replace("v1", "v2")
        client.workspace_did_change_watched_files([{"uri": bean_uri, "type": 2}])
        client.did_change(caller_uri, text=caller_v2)

        hov2 = _safe_request(
            self,
            client,
            "textDocument/hover",
            {"textDocument": {"uri": caller_uri}, "position": pos_of(caller_v2, "v2")},
        )
        self.assertTrue(
            hov2 and "v2" in str(hov2),
            f"hover on WatchedBean.v2() after didChangeWatchedFiles(Changed) returned {hov2!r}; "
            "expected 'int v2()'",
        )

        # Delete on disk and send Deleted (type=3).
        proj.remove_file(bean_rel)
        seq_del = client.diagnostics_seq()
        client.workspace_did_change_watched_files([{"uri": bean_uri, "type": 3}])
        client.did_change(caller_uri, text=caller_v2 + "\n")
        try:
            diags = client.wait_for_diagnostics_quiescent(caller_uri, timeout=8.0, after_seq=seq_del)
        except (TimeoutError, LspClientError) as exc:
            self.fail(f"Diagnostics after didChangeWatchedFiles(Deleted) failed: {exc}")
        self.assertTrue(
            any(d.get("severity", 1) == 1 for d in diags),
            f"Expected error diagnostic in WatchCaller after WatchedBean deleted, got {diags!r}",
        )

    def test_proto_25_workspace_file_operation_notifications(self) -> None:
        """[PROTO-25] workspace/didCreateFiles, didRenameFiles, didDeleteFiles keep workspace index in sync."""
        caller_src = (
            "package com.acme;\n\n"
            "public class FileOpCaller {\n"
            "  public int run(CreatedBean b) { return b.code(); }\n"
            "}\n"
        )
        proj, client = self._new_project_and_client(
            {
                "pom.xml": _minimal_pom("file-ops"),
                "src/main/java/com/acme/FileOpCaller.java": caller_src,
            }
        )
        caller_uri = proj.uri("src/main/java/com/acme/FileOpCaller.java")
        client.did_open(caller_uri, caller_src)

        created_rel = "src/main/java/com/acme/CreatedBean.java"
        created_src = (
            "package com.acme;\n\n"
            "public class CreatedBean {\n"
            "  public int code() { return 7; }\n"
            "}\n"
        )
        proj.write_file(created_rel, created_src)
        created_uri = proj.uri(created_rel)
        try:
            client.workspace_did_create_files([created_uri])
        except LspClientError as exc:
            self.fail(f"workspace/didCreateFiles failed: {exc}")

        hov_created = _safe_request(
            self,
            client,
            "textDocument/hover",
            {"textDocument": {"uri": caller_uri}, "position": pos_of(caller_src, "code")},
        )
        self.assertTrue(
            hov_created and "code" in str(hov_created),
            f"hover after workspace/didCreateFiles returned {hov_created!r}; expected 'int code()'",
        )

        # Rename CreatedBean.java -> RenamedBean.java on disk and notify didRenameFiles.
        renamed_rel = "src/main/java/com/acme/RenamedBean.java"
        proj.rename_file(created_rel, renamed_rel)
        proj.write_file(renamed_rel, created_src.replace("CreatedBean", "RenamedBean"))
        renamed_uri = proj.uri(renamed_rel)
        try:
            client.workspace_did_rename_files([(created_uri, renamed_uri)])
        except LspClientError as exc:
            self.fail(f"workspace/didRenameFiles failed: {exc}")

        caller_renamed = caller_src.replace("CreatedBean", "RenamedBean")
        client.did_change(caller_uri, text=caller_renamed)
        hov_renamed = _safe_request(
            self,
            client,
            "textDocument/hover",
            {"textDocument": {"uri": caller_uri}, "position": pos_of(caller_renamed, "code")},
        )
        self.assertTrue(
            hov_renamed and "code" in str(hov_renamed),
            f"hover after workspace/didRenameFiles returned {hov_renamed!r}; expected 'int code()'",
        )

        # Delete RenamedBean.java and notify didDeleteFiles.
        proj.remove_file(renamed_rel)
        seq_del = client.diagnostics_seq()
        try:
            client.workspace_did_delete_files([renamed_uri])
        except LspClientError as exc:
            self.fail(f"workspace/didDeleteFiles failed: {exc}")
        client.did_change(caller_uri, text=caller_renamed + "\n")
        try:
            diags = client.wait_for_diagnostics_quiescent(caller_uri, timeout=8.0, after_seq=seq_del)
        except (TimeoutError, LspClientError) as exc:
            self.fail(f"Diagnostics after didDeleteFiles failed: {exc}")
        self.assertTrue(
            any(d.get("severity", 1) == 1 for d in diags),
            f"Expected error diagnostic after RenamedBean deleted via didDeleteFiles, got {diags!r}",
        )


class TestProtocolEdgeCasesAndUtf16(unittest.TestCase):
    """Test edge cases: out-of-bounds positions, empty/broken files, UTF-16 surrogate offsets, CRLF."""

    project: TempJavaProject
    client: LspClient

    @classmethod
    def setUpClass(cls) -> None:
        cls.project = TempJavaProject(
            files={
                "pom.xml": _minimal_pom("edge-cases"),
                "src/main/java/com/acme/Baseline.java": (
                    "package com.acme;\npublic class Baseline {\n  public int id = 1;\n}\n"
                ),
            },
            create_root_marker=True,
        )
        cls.client = LspClient(root_path=cls.project.root, default_timeout=20.0)
        try:
            cls.client.initialize_and_ready(timeout=60.0)
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

    def test_proto_26_out_of_bounds_positions_do_not_crash(self) -> None:
        """[PROTO-26] Out-of-bounds and negative positions return safely without crashing server."""
        src = "package com.acme;\npublic class Oob {\n  int x = 10;\n}\n"
        uri = self.project.uri("src/main/java/com/acme/Oob.java")
        self.project.write_file("src/main/java/com/acme/Oob.java", src)
        self.client.did_open(uri, src)
        self.addCleanup(lambda: self.client.did_close(uri) if self.client.is_alive() else None)

        bad_positions = [
            {"line": 9999, "character": 9999},
            {"line": 2, "character": 9999},
            {"line": -1, "character": 0},
            {"line": 0, "character": -5},
        ]
        for method in (
            "textDocument/hover",
            "textDocument/definition",
            "textDocument/completion",
            "textDocument/signatureHelp",
        ):
            for pos in bad_positions:
                try:
                    res, err = self.client.try_request(
                        method,
                        {"textDocument": {"uri": uri}, "position": pos},
                        timeout=8.0,
                    )
                except (ServerCrashedError, TimeoutError, LspClientError) as exc:
                    self.fail(f"{method} at out-of-bounds position {pos} crashed/timed out: {exc}")
                # Either null/empty result or clean InvalidParams (-32602) is acceptable, never crash.
                if err is not None:
                    self.assertIn(
                        err.code,
                        (-32602, -32600),
                        f"{method} at {pos} returned unexpected JSON-RPC error code={err.code} ({err.message!r})",
                    )
                _ = res

        # Verify server still handles valid request right after OOB probes.
        valid_hover = _safe_request(
            self,
            self.client,
            "textDocument/hover",
            {"textDocument": {"uri": uri}, "position": pos_of(src, "x")},
        )
        self.assertIsNotNone(valid_hover, "Valid hover after OOB requests returned null")

    def test_proto_27_empty_file_handling(self) -> None:
        """[PROTO-27] Empty .java file does not crash on open, documentSymbol, hover, definition, completion."""
        uri = self.project.uri("src/main/java/com/acme/EmptyFile.java")
        self.project.write_file("src/main/java/com/acme/EmptyFile.java", "")
        self.client.did_open(uri, "")
        self.addCleanup(lambda: self.client.did_close(uri) if self.client.is_alive() else None)

        syms = _safe_request(self, self.client, "textDocument/documentSymbol", {"textDocument": {"uri": uri}})
        self.assertIn(syms, (None, []), f"documentSymbol on empty file expected []/None, got {syms!r}")

        hov = _safe_request(
            self,
            self.client,
            "textDocument/hover",
            {"textDocument": {"uri": uri}, "position": {"line": 0, "character": 0}},
        )
        self.assertIsNone(hov, f"hover on empty file expected None, got {hov!r}")

        defn = _safe_request(
            self,
            self.client,
            "textDocument/definition",
            {"textDocument": {"uri": uri}, "position": {"line": 0, "character": 0}},
        )
        self.assertIn(defn, (None, []), f"definition on empty file expected None/[], got {defn!r}")

        comp = _safe_request(
            self,
            self.client,
            "textDocument/completion",
            {"textDocument": {"uri": uri}, "position": {"line": 0, "character": 0}},
        )
        self.assertTrue(
            comp is None or isinstance(comp, (list, dict)),
            f"completion on empty file returned unexpected type: {comp!r}",
        )

    def test_proto_28_unclosed_brace_syntax_broken_file(self) -> None:
        """[PROTO-28] Severely syntax-broken file (unclosed brace, partial dot) produces diagnostics without crash."""
        broken = (
            "package com.acme;\n\n"
            "public class Broken {\n"
            "  public void run() {\n"
            "    String text = \"ok\";\n"
            "    text.\n"
        )
        uri = self.project.uri("src/main/java/com/acme/Broken.java")
        self.project.write_file("src/main/java/com/acme/Broken.java", broken)
        seq = self.client.did_open(uri, broken)
        self.addCleanup(lambda: self.client.did_close(uri) if self.client.is_alive() else None)

        try:
            diags = self.client.wait_for_diagnostics_quiescent(uri, timeout=10.0, after_seq=seq)
        except (TimeoutError, LspClientError) as exc:
            self.fail(f"Broken syntax file diagnostics failed: {exc}")
        self.assertTrue(
            len(diags) > 0,
            f"Expected syntax diagnostics for unclosed-brace Broken.java, got {diags!r}",
        )

        # Completion, hover, and documentSymbol must all respond cleanly without crashing.
        _safe_request(
            self,
            self.client,
            "textDocument/completion",
            {"textDocument": {"uri": uri}, "position": pos_of(broken, "text.", offset=5)},
        )
        _safe_request(
            self,
            self.client,
            "textDocument/hover",
            {"textDocument": {"uri": uri}, "position": pos_of(broken, "text", occurrence=1)},
        )
        _safe_request(
            self,
            self.client,
            "textDocument/documentSymbol",
            {"textDocument": {"uri": uri}},
        )

    def test_proto_29_utf16_surrogate_pairs_and_cjk_column_offsets(self) -> None:
        """[PROTO-29] Positions after CJK + UTF-16 surrogate-pair emoji on same line use UTF-16 code units."""
        src = (
            "package com.acme;\n\n"
            "public class Utf16Offset {\n"
            "  public int compute() {\n"
            '    String msg = "你好😀🚀"; int targetVal = msg.length();\n'
            "    /* 中文😀🚀 */ int afterComment = afterEmojiMethod() + targetVal;\n"
            "    return afterComment;\n"
            "  }\n"
            "  /* 注释😀 */ public int afterEmojiMethod() { return 1; }\n"
            "}\n"
        )
        uri = self.project.uri("src/main/java/com/acme/Utf16Offset.java")
        self.project.write_file("src/main/java/com/acme/Utf16Offset.java", src)
        self.client.did_open(uri, src)
        self.addCleanup(lambda: self.client.did_close(uri) if self.client.is_alive() else None)

        failures: list[str] = []

        # 1. Hover on method call after CJK + emoji on same line (avoids local-var hover gap).
        call_pos = pos_of(src, "afterEmojiMethod()", occurrence=1)
        hov = _safe_request(
            self,
            self.client,
            "textDocument/hover",
            {"textDocument": {"uri": uri}, "position": call_pos},
        )
        if not (hov and "afterEmojiMethod" in str(hov)):
            failures.append(
                f"hover(afterEmojiMethod after '/* 中文😀🚀 */') at UTF-16 pos {call_pos} returned {hov!r}"
            )

        # 2. Definition on targetVal (usage on line 5 -> declaration after "你好😀🚀" on line 4).
        use_pos = pos_of(src, "targetVal", occurrence=2)
        expected_rng = range_of(src, "targetVal", occurrence=1)
        defn = _safe_request(
            self,
            self.client,
            "textDocument/definition",
            {"textDocument": {"uri": uri}, "position": use_pos},
        )
        locs = defn if isinstance(defn, list) else ([defn] if isinstance(defn, dict) else [])
        if not locs:
            failures.append(
                f"definition(targetVal) at UTF-16 pos {use_pos} returned {defn!r}; expected {expected_rng}"
            )
        else:
            actual_rng = locs[0].get("range") or locs[0].get("targetSelectionRange") or locs[0].get("targetRange")
            if actual_rng != expected_rng:
                failures.append(
                    f"definition(targetVal) range: expected UTF-16 {expected_rng!r}, got {actual_rng!r}"
                )

        # 3. documentSymbol selectionRange for afterEmojiMethod after '/* 注释😀 */'.
        syms = _safe_request(self, self.client, "textDocument/documentSymbol", {"textDocument": {"uri": uri}})
        expected_m_rng = range_of(src, "afterEmojiMethod", occurrence=2)
        flat: list[dict[str, Any]] = []

        def _walk(items: Any) -> None:
            if not isinstance(items, list):
                return
            for it in items:
                if isinstance(it, dict):
                    flat.append(it)
                    _walk(it.get("children"))

        _walk(syms)
        matching = [s for s in flat if s.get("name", "").startswith("afterEmojiMethod")]
        if not matching:
            failures.append(f"documentSymbol missing 'afterEmojiMethod'; got {[s.get('name') for s in flat]!r}")
        else:
            sel = matching[0].get("selectionRange") or (matching[0].get("location") or {}).get("range")
            if sel != expected_m_rng:
                failures.append(
                    f"documentSymbol selectionRange for 'afterEmojiMethod' after '/* 注释😀 */': "
                    f"expected UTF-16 {expected_m_rng!r}, got {sel!r}"
                )

        self.assertFalse(failures, "UTF-16 offset mismatches:\n  - " + "\n  - ".join(failures))

    def test_proto_30_crlf_line_endings_positions(self) -> None:
        """[PROTO-30] Files with CRLF (\\r\\n) line endings report accurate 0-based line numbers."""
        lf_src = (
            "package com.acme;\n"
            "\n"
            "public class CrlfDoc {\n"
            "  private int counter = 5;\n"
            "\n"
            "  public int readCounter() {\n"
            "    return counter;\n"
            "  }\n"
            "}\n"
        )
        crlf_src = lf_src.replace("\n", "\r\n")
        uri = self.project.uri("src/main/java/com/acme/CrlfDoc.java")
        self.project.write_file("src/main/java/com/acme/CrlfDoc.java", crlf_src)
        self.client.did_open(uri, crlf_src)
        self.addCleanup(lambda: self.client.did_close(uri) if self.client.is_alive() else None)

        use_pos = pos_of(crlf_src, "counter", occurrence=2)
        decl_rng = range_of(crlf_src, "counter", occurrence=1)
        defn = _safe_request(
            self,
            self.client,
            "textDocument/definition",
            {"textDocument": {"uri": uri}, "position": use_pos},
        )
        locs = defn if isinstance(defn, list) else ([defn] if isinstance(defn, dict) else [])
        self.assertTrue(
            locs,
            f"definition in CRLF file at {use_pos} returned {defn!r}; expected {decl_rng}",
        )
        actual_rng = locs[0].get("range") or locs[0].get("targetSelectionRange") or locs[0].get("targetRange")
        self.assertEqual(
            actual_rng,
            decl_rng,
            f"definition range in CRLF file: expected {decl_rng!r}, got {actual_rng!r}",
        )


if __name__ == "__main__":
    unittest.main()
