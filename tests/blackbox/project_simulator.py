#!/usr/bin/env python3
"""Black-box Automated Project Browser & Simulated Human Coding tool for java-lsp."""

from __future__ import annotations

import argparse
import collections
import dataclasses
import datetime
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests.blackbox.lsp_client import (
    DEFAULT_BINARY_PATH,
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
    path_to_uri,
    position_to_offset,
    uri_exists_on_disk,
    uri_to_path,
)

IGNORED_DIRS = {
    "target",
    "build",
    ".git",
    ".gradle",
    ".idea",
    ".settings",
    ".vscode",
    "node_modules",
    "_mybatis_virtual",
}

JAVA_KEYWORDS = {
    "abstract", "assert", "boolean", "break", "byte", "case", "catch", "char",
    "class", "const", "continue", "default", "do", "double", "else", "enum",
    "extends", "final", "finally", "float", "for", "goto", "if", "implements",
    "import", "instanceof", "int", "interface", "long", "native", "new",
    "package", "private", "protected", "public", "return", "short", "static",
    "strictfp", "super", "switch", "synchronized", "this", "throw", "throws",
    "transient", "try", "void", "volatile", "while", "true", "false", "null",
    "var", "record", "sealed", "permits", "yield",
}

PRIMITIVE_OR_COMMON = {"String", "Object", "Integer", "Long", "Boolean", "Double", "List", "Map", "Set", "Optional"}


# ---------------------------------------------------------------------------
# Lightweight Java source scanner (black-box test site discovery)
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class JavaFileInfo:
    path: Path
    rel_path: str
    uri: str
    module: str
    package: str
    text: str
    lines: list[str]
    declared_types: list[str]
    imports: list[str]
    annotations: list[str]


def discover_java_files(project_dir: Path, max_files: int | None = None) -> list[JavaFileInfo]:
    """Discover .java files in project_dir excluding build/IDE directories."""
    results: list[JavaFileInfo] = []
    for path in sorted(project_dir.rglob("*.java")):
        rel_parts = path.relative_to(project_dir).parts
        if any(part in IGNORED_DIRS for part in rel_parts):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        module = _infer_module(rel_parts)
        pkg_m = re.search(r"^\s*package\s+([\w.]+)\s*;", text, re.MULTILINE)
        package = pkg_m.group(1) if pkg_m else ""
        types = re.findall(
            r"\b(?:class|interface|enum|record|@interface)\s+([A-Za-z_]\w*)",
            _strip_comments_and_strings(text),
        )
        imports = re.findall(r"^\s*import\s+(?:static\s+)?([\w.*]+)\s*;", text, re.MULTILINE)
        annotations = re.findall(r"@([A-Za-z_]\w*)", _strip_comments_and_strings(text))
        results.append(
            JavaFileInfo(
                path=path.resolve(),
                rel_path=str(path.relative_to(project_dir)),
                uri=path_to_uri(path),
                module=module,
                package=package,
                text=text,
                lines=text.splitlines(),
                declared_types=types,
                imports=imports,
                annotations=annotations,
            )
        )
        if max_files is not None and len(results) >= max_files:
            break
    return results


def _infer_module(rel_parts: tuple[str, ...]) -> str:
    if "src" in rel_parts:
        idx = rel_parts.index("src")
        return "/".join(rel_parts[:idx]) if idx > 0 else "(root)"
    return rel_parts[0] if len(rel_parts) > 1 else "(root)"


def _strip_comments_and_strings(text: str) -> str:
    """Replace comments and string/char literals with spaces while preserving line/char offsets."""
    out = list(text)
    n = len(text)
    i = 0
    while i < n:
        ch = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if ch == "/" and nxt == "/":
            out[i] = " "
            out[i + 1] = " "
            i += 2
            while i < n and text[i] != "\n":
                out[i] = " "
                i += 1
        elif ch == "/" and nxt == "*":
            out[i] = " "
            out[i + 1] = " "
            i += 2
            while i < n:
                if text[i] == "*" and i + 1 < n and text[i + 1] == "/":
                    out[i] = " "
                    out[i + 1] = " "
                    i += 2
                    break
                if text[i] != "\n":
                    out[i] = " "
                i += 1
        elif ch == '"' or ch == "'":
            quote = ch
            # Handle Java text blocks """
            if quote == '"' and text[i : i + 3] == '"""':
                for k in range(3):
                    out[i + k] = " "
                i += 3
                while i < n:
                    if text[i : i + 3] == '"""':
                        for k in range(3):
                            out[i + k] = " "
                        i += 3
                        break
                    if text[i] != "\n":
                        out[i] = " "
                    i += 1
            else:
                out[i] = " "
                i += 1
                while i < n:
                    if text[i] == "\\":
                        out[i] = " "
                        if i + 1 < n and text[i + 1] != "\n":
                            out[i + 1] = " "
                        i += 2
                        continue
                    c = text[i]
                    if c != "\n":
                        out[i] = " "
                    i += 1
                    if c == quote or c == "\n":
                        break
        else:
            i += 1
    return "".join(out)


def _find_method_body_insertion_point(text: str) -> tuple[int, int] | None:
    """Find (char_offset, line_num) right after the opening '{' of the first non-empty method body."""
    cleaned = _strip_comments_and_strings(text)
    pattern = re.compile(
        r"(?:public|protected|private|static|final|synchronized|default)?\s*"
        r"[\w<>?\[\], ]+\s+([a-z_]\w*)\s*\([^;{}]*\)\s*(?:throws\s+[\w., ]+)?\{"
    )
    for m in pattern.finditer(cleaned):
        name = m.group(1)
        if name in ("if", "for", "while", "switch", "catch", "synchronized"):
            continue
        insert_off = m.end()
        pos = offset_to_position(text, insert_off)
        return insert_off, pos["line"]
    return None


def _find_typed_var_in_method(text: str, project_types: set[str]) -> tuple[str, str, int] | None:
    """Find a local/field variable or parameter name and insertion offset in a method body."""
    cleaned = _strip_comments_and_strings(text)
    # Match parameters or local variables of known non-primitive types
    method_pat = re.compile(
        r"\b(?:public|protected|private)\s+(?:static\s+)?([\w<>?\[\], ]+?)\s+([a-z_]\w*)\s*\(([^)]*)\)\s*\{"
    )
    for m in method_pat.finditer(cleaned):
        params_str = m.group(3)
        insert_off = m.end()
        # Look for parameter with non-primitive type
        for param in params_str.split(","):
            tokens = [t for t in re.split(r"\s+", param.strip()) if t and not t.startswith("@") and t != "final"]
            if len(tokens) >= 2:
                ptype = re.sub(r"<.*>", "", tokens[-2])
                pname = tokens[-1]
                if ptype not in ("int", "long", "double", "float", "boolean", "char", "byte", "short"):
                    return pname, ptype, insert_off
        # Look for field in class
        field_m = re.search(
            r"\bprivate\s+(?:final\s+)?([A-Z]\w*)(?:<[^>]+>)?\s+([a-z_]\w*)\s*;",
            cleaned[: m.start()],
        )
        if field_m:
            return field_m.group(2), field_m.group(1), insert_off
        return "this", "Object", insert_off
    return None


# ---------------------------------------------------------------------------
# Classification helpers
# ---------------------------------------------------------------------------


def _classify_target_origin(
    source_file: JavaFileInfo,
    target_uri: str,
    project_dir: Path,
    file_by_uri: dict[str, JavaFileInfo],
    storage_dir: Path,
) -> str:
    if not is_file_uri(target_uri):
        return f"virtual({target_uri.split(':', 1)[0]})"
    if target_uri == source_file.uri:
        return "same_file"
    if target_uri in file_by_uri:
        target_info = file_by_uri[target_uri]
        if target_info.module != source_file.module:
            return "cross_module"
        return "cross_file"
    try:
        target_path = uri_to_path(target_uri)
    except ValueError:
        return "invalid_uri"
    if project_dir in target_path.parents:
        if target_path.suffix.lower() == ".xml":
            return "mybatis_xml"
        return "project_other"
    lower_str = str(target_path).lower()
    if (
        "java.base" in lower_str
        or "/jdk" in lower_str
        or "/jvm/" in lower_str
        or "/java-" in lower_str
        or "/java/" in lower_str and ("/src/" in lower_str or "src.zip" in lower_str or str(storage_dir) in str(target_path))
    ):
        if "/com/example/" not in lower_str and ".m2" not in lower_str and "cfr" not in lower_str:
            return "jdk"
    if ".m2" in lower_str or "gradle" in lower_str or str(storage_dir) in str(target_path):
        if "/java/" in lower_str or "/javax/" in lower_str or "/jakarta/" in lower_str:
            # Distinguish JDK cache vs maven dep cache if inside storage_dir
            if "/java/lang/" in lower_str or "/java/util/" in lower_str or "/java/io/" in lower_str or "java.base" in lower_str:
                return "jdk"
        return "maven_dep"
    return "external"


def _validate_range_dict(rng: Any) -> bool:
    if not isinstance(rng, dict):
        return False
    start = rng.get("start")
    end = rng.get("end")
    if not isinstance(start, dict) or not isinstance(end, dict):
        return False
    sl, sc = start.get("line"), start.get("character")
    el, ec = end.get("line"), end.get("character")
    if not all(isinstance(x, int) and x >= 0 for x in (sl, sc, el, ec)):
        return False
    return (sl, sc) <= (el, ec)


# ---------------------------------------------------------------------------
# Project Simulator
# ---------------------------------------------------------------------------


class ProjectSimulator:
    """Runs Mode 1 (Browser) and Mode 2 (Simulated Human Coding) on a Java project."""

    def __init__(
        self,
        project_dir: Path,
        binary_path: Path = DEFAULT_BINARY_PATH,
        timeout: float = 20.0,
        max_files: int | None = None,
        verbose: bool = False,
        isolated_copy: bool = True,
    ) -> None:
        self.source_project_dir = project_dir.resolve()
        self.binary_path = binary_path
        self.timeout = timeout
        self.max_files = max_files
        self.verbose = verbose
        self.isolated_copy = isolated_copy

        self._temp_proj: TempJavaProject | None = None
        if self.isolated_copy:
            self._temp_proj = TempJavaProject(prefix="java_lsp_sim_")
            self._temp_proj.copy_from(self.source_project_dir)
            self.project_dir = self._temp_proj.root
        else:
            self.project_dir = self.source_project_dir

        self.files = discover_java_files(self.project_dir, max_files=max_files)
        self.file_by_uri = {f.uri: f for f in self.files}
        self.project_types: dict[str, JavaFileInfo] = {}
        for f in self.files:
            for t in f.declared_types:
                self.project_types[t] = f

        self.report: dict[str, Any] = {
            "project_dir": str(self.source_project_dir),
            "work_dir": str(self.project_dir),
            "isolated_copy": self.isolated_copy,
            "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
            "file_count": len(self.files),
            "modules": sorted({f.module for f in self.files}),
            "server_info": {},
            "server_capabilities": {},
            "harness_errors": [],
            "mode1": {},
            "mode2": {},
        }

    def _log(self, msg: str) -> None:
        if self.verbose:
            print(msg)
            sys.stdout.flush()

    def run(self, mode: str = "all") -> dict[str, Any]:
        if not self.files:
            if self._temp_proj is not None:
                self._temp_proj.close()
                self._temp_proj = None
            raise RuntimeError(f"No .java files found under {self.source_project_dir}")

        try:
            with LspClient(
                binary_path=self.binary_path,
                root_path=self.project_dir,
                default_timeout=self.timeout,
            ) as client:
                init_res = client.initialize_and_ready(root_path=self.project_dir, timeout=max(60.0, self.timeout))
                self.report["server_info"] = init_res.get("serverInfo", {})
                self.report["server_capabilities"] = init_res.get("capabilities", {})

                if mode in ("all", "browse"):
                    self.report["mode1"] = self._run_mode1_browser(client)
                if mode in ("all", "simulate"):
                    if mode == "simulate":
                        for jf in self.files:
                            seq = client.did_open(jf.uri, jf.text)
                            try:
                                client.wait_for_diagnostics(jf.uri, timeout=min(5.0, self.timeout), after_seq=seq, settle_time=0.1)
                            except TimeoutError:
                                pass
                    self.report["mode2"] = self._run_mode2_simulation(client)

                self.report["server_stderr_count"] = len(client.stderr_lines)
                self.report["server_stderr_tail"] = client.stderr_lines[-20:]
                self.report["server_alive_at_end"] = client.is_alive()
        finally:
            if self._temp_proj is not None:
                self._temp_proj.close()
                self._temp_proj = None

        return self.report

    # ------------------------------------------------------------------
    # Mode 1: Automated Project Browser
    # ------------------------------------------------------------------

    def _run_mode1_browser(self, client: LspClient) -> dict[str, Any]:
        self._log("[Mode 1] Opening all Java files and collecting diagnostics & document features...")
        file_results: list[dict[str, Any]] = []
        unsupported_methods: set[str] = set()

        for idx, jf in enumerate(self.files):
            seq = client.did_open(jf.uri, jf.text)
            wait_budget = min(10.0, self.timeout) if idx == 0 else min(4.0, self.timeout)
            diag_received = True
            try:
                diags = client.wait_for_diagnostics(
                    jf.uri, timeout=wait_budget, after_seq=seq, settle_time=0.15
                )
            except TimeoutError:
                diag_received = False
                diags = client.get_diagnostics(jf.uri)

            err_diags = [d for d in diags if d.get("severity", 1) == 1]
            warn_diags = [d for d in diags if d.get("severity") == 2]

            doc_sym = self._call_endpoint(client, "documentSymbol", lambda: client.document_symbol(jf.uri), unsupported_methods)
            fold = self._call_endpoint(client, "foldingRange", lambda: client.folding_range(jf.uri), unsupported_methods)
            sem_tok = self._call_endpoint(client, "semanticTokens/full", lambda: client.semantic_tokens_full(jf.uri), unsupported_methods)
            last_line = max(0, len(jf.lines) - 1)
            last_col = len(jf.lines[-1]) if jf.lines else 0
            inlay = self._call_endpoint(
                client,
                "inlayHint",
                lambda: client.inlay_hint(
                    jf.uri,
                    {"start": {"line": 0, "character": 0}, "end": {"line": last_line, "character": last_col}},
                ),
                unsupported_methods,
            )
            lens = self._call_endpoint(client, "codeLens", lambda: client.code_lens(jf.uri), unsupported_methods)

            sym_list = flatten_document_symbols(doc_sym.get("result")) if doc_sym["status"] == "ok" else []
            fold_list = fold.get("result") if fold["status"] == "ok" and isinstance(fold.get("result"), list) else []
            sem_data = (
                sem_tok.get("result", {}).get("data", [])
                if sem_tok["status"] == "ok" and isinstance(sem_tok.get("result"), dict)
                else []
            )
            inlay_list = inlay.get("result") if inlay["status"] == "ok" and isinstance(inlay.get("result"), list) else []
            lens_list = lens.get("result") if lens["status"] == "ok" and isinstance(lens.get("result"), list) else []

            file_results.append(
                {
                    "rel_path": jf.rel_path,
                    "module": jf.module,
                    "diag_received": diag_received,
                    "error_count": len(err_diags),
                    "warning_count": len(warn_diags),
                    "errors": [
                        {
                            "line": d.get("range", {}).get("start", {}).get("line", -1) + 1,
                            "message": d.get("message", ""),
                        }
                        for d in err_diags[:20]
                    ],
                    "documentSymbol": {"status": doc_sym["status"], "count": len(sym_list)},
                    "foldingRange": {"status": fold["status"], "count": len(fold_list)},
                    "semanticTokens": {
                        "status": sem_tok["status"],
                        "token_count": len(sem_data) // 5 if isinstance(sem_data, list) else 0,
                        "valid_mod5": isinstance(sem_data, list) and len(sem_data) % 5 == 0,
                    },
                    "inlayHint": {"status": inlay["status"], "count": len(inlay_list)},
                    "codeLens": {"status": lens["status"], "count": len(lens_list)},
                }
            )

        # Workspace symbols
        ws_queries = sorted(self.project_types.keys())[:5] or ["User", "Service"]
        ws_probes: list[dict[str, Any]] = []
        for q in ws_queries:
            res = self._call_endpoint(client, "workspace/symbol", lambda q=q: client.workspace_symbol(q), unsupported_methods)
            items = res.get("result") if res["status"] == "ok" and isinstance(res.get("result"), list) else []
            ws_probes.append({"query": q, "status": res["status"], "count": len(items)})

        # Extract position probes across all files
        probes = self._extract_navigation_probes()
        self._log(f"[Mode 1] Probing {len(probes)} real symbol/call/type/Lombok positions...")

        endpoint_stats: dict[str, collections.Counter[str]] = collections.defaultdict(collections.Counter)
        category_stats: dict[str, dict[str, collections.Counter[str]]] = collections.defaultdict(
            lambda: collections.defaultdict(collections.Counter)
        )
        origin_stats: dict[str, collections.Counter[str]] = collections.defaultdict(collections.Counter)
        probe_records: list[dict[str, Any]] = []

        nav_endpoints = [
            "hover",
            "definition",
            "declaration",
            "typeDefinition",
            "implementation",
            "references",
            "documentHighlight",
            "prepareRename",
        ]

        for probe in probes:
            jf: JavaFileInfo = probe["file"]
            pos = probe["position"]
            cat = probe["category"]
            rec: dict[str, Any] = {
                "file": jf.rel_path,
                "module": jf.module,
                "line": pos["line"] + 1,
                "character": pos["character"],
                "symbol": probe["symbol"],
                "category": cat,
                "results": {},
            }
            for ep in nav_endpoints:
                if ep in unsupported_methods:
                    status = "unsupported"
                    detail: dict[str, Any] = {"status": "unsupported"}
                else:
                    detail = self._probe_nav_endpoint(client, ep, jf, pos, unsupported_methods)
                    status = detail["status"]
                endpoint_stats[ep][status] += 1
                category_stats[cat][ep][status] += 1
                if "origins" in detail:
                    for orig in detail["origins"]:
                        origin_stats[ep][orig] += 1
                rec["results"][ep] = detail
            probe_records.append(rec)

        return {
            "files": file_results,
            "total_diagnostic_errors": sum(f["error_count"] for f in file_results),
            "total_diagnostic_warnings": sum(f["warning_count"] for f in file_results),
            "files_with_errors": [f["rel_path"] for f in file_results if f["error_count"] > 0],
            "unsupported_methods": sorted(unsupported_methods),
            "workspace_symbol": ws_probes,
            "probe_count": len(probe_records),
            "endpoint_summary": {ep: dict(cnt) for ep, cnt in endpoint_stats.items()},
            "category_summary": {
                cat: {ep: dict(cnt) for ep, cnt in eps.items()}
                for cat, eps in category_stats.items()
            },
            "origin_summary": {ep: dict(cnt) for ep, cnt in origin_stats.items()},
            "probes": probe_records,
        }

    def _call_endpoint(
        self,
        client: LspClient,
        name: str,
        fn: Any,
        unsupported_methods: set[str],
    ) -> dict[str, Any]:
        if name in unsupported_methods:
            return {"status": "unsupported"}
        try:
            res = fn()
            return {"status": "ok", "result": res}
        except LspResponseError as exc:
            if exc.code == -32601:
                unsupported_methods.add(name)
                return {"status": "unsupported", "error": str(exc)}
            return {"status": "error", "error": str(exc)}
        except (ServerCrashedError, TimeoutError) as exc:
            self.report["harness_errors"].append(f"{name}: {exc}")
            return {"status": "crash_or_timeout", "error": str(exc)}
        except Exception as exc:
            self.report["harness_errors"].append(f"{name}: {exc}")
            return {"status": "harness_error", "error": str(exc)}

    def _probe_nav_endpoint(
        self,
        client: LspClient,
        ep: str,
        jf: JavaFileInfo,
        pos: dict[str, int],
        unsupported_methods: set[str],
    ) -> dict[str, Any]:
        fn_map = {
            "hover": lambda: client.hover(jf.uri, pos),
            "definition": lambda: client.definition(jf.uri, pos),
            "declaration": lambda: client.declaration(jf.uri, pos),
            "typeDefinition": lambda: client.type_definition(jf.uri, pos),
            "implementation": lambda: client.implementation(jf.uri, pos),
            "references": lambda: client.references(jf.uri, pos, include_declaration=True),
            "documentHighlight": lambda: client.document_highlight(jf.uri, pos),
            "prepareRename": lambda: client.prepare_rename(jf.uri, pos),
        }
        call_res = self._call_endpoint(client, ep, fn_map[ep], unsupported_methods)
        if call_res["status"] != "ok":
            return call_res
        res = call_res["result"]

        if ep == "hover":
            txt = extract_hover_text(res).strip()
            if not txt:
                return {"status": "empty"}
            return {"status": "hit", "preview": txt[:120]}

        if ep in ("definition", "declaration", "typeDefinition", "implementation", "references"):
            locs = extract_locations(res)
            if not locs:
                return {"status": "empty"}
            origins: list[str] = []
            invalid = 0
            for loc in locs:
                t_uri = location_uri(loc)
                t_rng = location_range(loc)
                if not t_uri or not _validate_range_dict(t_rng):
                    invalid += 1
                    continue
                if is_file_uri(t_uri) and not uri_exists_on_disk(t_uri):
                    invalid += 1
                    origins.append("missing_file")
                    continue
                origins.append(
                    _classify_target_origin(
                        jf, t_uri, self.project_dir, self.file_by_uri, client.storage_dir
                    )
                )
            if invalid == len(locs):
                return {"status": "invalid_uri", "count": len(locs), "origins": origins}
            # Detect degenerate self-echo (zero-width range at cursor) or file-head (0,0) fallback
            first_rng = location_range(locs[0])
            first_uri = location_uri(locs[0])
            self_echo = (
                len(locs) == 1
                and first_uri == jf.uri
                and first_rng is not None
                and first_rng.get("start") == pos
                and first_rng.get("end") == pos
            )
            file_head = (
                len(locs) == 1
                and first_rng is not None
                and first_rng.get("start") == {"line": 0, "character": 0}
                and first_rng.get("end") == {"line": 0, "character": 0}
            )
            return {
                "status": "hit",
                "count": len(locs),
                "origins": origins,
                "self_echo": self_echo,
                "file_head_0_0": file_head,
            }

        if ep == "documentHighlight":
            if not isinstance(res, list) or not res:
                return {"status": "empty"}
            valid = all(isinstance(h, dict) and _validate_range_dict(h.get("range")) for h in res)
            return {"status": "hit" if valid else "error", "count": len(res)}

        if ep == "prepareRename":
            if res is None:
                return {"status": "empty"}
            if isinstance(res, dict) and (
                _validate_range_dict(res)
                or _validate_range_dict(res.get("range"))
                or res.get("defaultBehavior") is True
            ):
                return {"status": "hit"}
            return {"status": "error", "raw": repr(res)[:100]}

        return {"status": "error"}

    def _extract_navigation_probes(self) -> list[dict[str, Any]]:
        """Automatically discover representative positions across all files & symbol categories."""
        probes: list[dict[str, Any]] = []
        seen_keys: set[tuple[str, int, int]] = set()

        def _add(jf: JavaFileInfo, offset: int, symbol: str, category: str) -> None:
            pos = offset_to_position(jf.text, offset)
            key = (jf.uri, pos["line"], pos["character"])
            if key in seen_keys:
                return
            seen_keys.add(key)
            probes.append({"file": jf, "position": pos, "symbol": symbol, "category": category})

        for jf in self.files:
            cleaned = _strip_comments_and_strings(jf.text)
            per_file_added = 0

            # 1. Imports (project cross-file, cross-module, JDK, Maven)
            for m in re.finditer(r"^\s*import\s+(?:static\s+)?([\w.]+)\.([A-Za-z_]\w*)\s*;", jf.text, re.MULTILINE):
                pkg, sym = m.group(1), m.group(2)
                if sym in self.project_types:
                    target_mod = self.project_types[sym].module
                    cat = "cross_module_import" if target_mod != jf.module else "project_import"
                elif pkg.startswith("java.") or pkg.startswith("javax."):
                    cat = "jdk_import"
                else:
                    cat = "maven_import"
                _add(jf, m.start(2) + len(sym) // 2, sym, cat)
                per_file_added += 1
                if per_file_added >= 4:
                    break

            # 2. Lombok @Slf4j log usage
            if "@Slf4j" in cleaned:
                for m in re.finditer(r"\blog\s*\.\s*([A-Za-z_]\w*)", cleaned):
                    _add(jf, m.start() + 1, "log", "lombok_slf4j")
                    _add(jf, m.start(1) + 1, m.group(1), "lombok_slf4j_method")
                    break

            # 3. Annotations
            for m in re.finditer(r"@([A-Za-z_]\w*)", cleaned):
                aname = m.group(1)
                if aname in ("Slf4j", "Data", "Builder", "RequiredArgsConstructor", "Service", "RestController", "FeignClient", "GetMapping", "PostMapping", "Override", "SpringBootTest"):
                    _add(jf, m.start(1) + 1, f"@{aname}", "annotation")
                    break

            # 4. Cross-file / cross-module type references in body (outside import/package)
            body_start = cleaned.find("{")
            if body_start >= 0:
                body = cleaned[body_start:]
                for tname, target_file in self.project_types.items():
                    if target_file.uri == jf.uri:
                        continue
                    tm = re.search(rf"\b({re.escape(tname)})\b", body)
                    if tm:
                        cat = "cross_module_type" if target_file.module != jf.module else "cross_file_type"
                        _add(jf, body_start + tm.start(1) + 1, tname, cat)
                        break

                # 5. Method calls (receiver.method)
                call_count = 0
                for m in re.finditer(r"\b([A-Za-z_]\w*)\s*\.\s*([a-z_]\w*)\s*\(", body):
                    recv, meth = m.group(1), m.group(2)
                    if recv in JAVA_KEYWORDS or recv == "log":
                        continue
                    off = body_start + m.start(2) + max(1, len(meth) // 2)
                    if meth in ("builder", "build") or meth.startswith("get") or meth.startswith("set"):
                        cat = "lombok_or_method_call"
                    elif recv in ("System", "List", "Map", "String", "Collections"):
                        cat = "jdk_method_call"
                    else:
                        cat = "method_call"
                    _add(jf, off, f"{recv}.{meth}", cat)
                    # Also probe the receiver once per file
                    if call_count == 0 and recv[0].islower():
                        _add(jf, body_start + m.start(1) + 1, recv, "variable_or_field")
                    call_count += 1
                    if call_count >= 3:
                        break

                # 6. Lambda parameters
                lm = re.search(r"\b([a-z_]\w*)\s*->\s*\1\s*\.", body)
                if lm:
                    _add(jf, body_start + lm.start(1), lm.group(1), "lambda_param")

                # 7. Type declaration itself
                dm = re.search(r"\b(?:class|interface|enum|record)\s+([A-Za-z_]\w*)", cleaned)
                if dm:
                    _add(jf, dm.start(1) + 1, dm.group(1), "type_declaration")

        return probes

    # ------------------------------------------------------------------
    # Mode 2: Simulated Human Coding
    # ------------------------------------------------------------------

    def _run_mode2_simulation(self, client: LspClient) -> dict[str, Any]:
        self._log("[Mode 2] Running Simulated Human Coding workflows (in-memory only)...")
        completion_res = self._sim_progressive_completion(client)
        sig_res = self._sim_signature_help(client)
        diag_res = self._sim_error_injection(client)
        ca_res = self._sim_code_actions(client)
        rename_res = self._sim_rename(client)
        return {
            "completion": completion_res,
            "signature_help": sig_res,
            "error_injection": diag_res,
            "code_actions": ca_res,
            "rename": rename_res,
        }

    def _with_temp_doc_edit(
        self,
        client: LspClient,
        jf: JavaFileInfo,
        edited_text: str,
        wait_diag: bool = False,
    ) -> list[dict[str, Any]]:
        seq = client.did_change(jf.uri, text=edited_text)
        if not wait_diag:
            return []
        try:
            return client.wait_for_diagnostics(
                jf.uri, timeout=min(5.0, self.timeout), after_seq=seq, settle_time=0.15
            )
        except TimeoutError:
            return client.get_diagnostics(jf.uri)

    def _restore_doc(self, client: LspClient, jf: JavaFileInfo, wait_diag: bool = False) -> list[dict[str, Any]]:
        seq = client.did_change(jf.uri, text=jf.text)
        if not wait_diag:
            return []
        try:
            return client.wait_for_diagnostics(
                jf.uri, timeout=min(5.0, self.timeout), after_seq=seq, settle_time=0.15
            )
        except TimeoutError:
            return client.get_diagnostics(jf.uri)

    # 2.1 Progressive typing & completion
    def _sim_progressive_completion(self, client: LspClient) -> dict[str, Any]:
        self._log("  [2.1] Progressive completion scenarios...")
        scenarios: list[dict[str, Any]] = []

        for jf in self.files:
            cleaned = _strip_comments_and_strings(jf.text)
            body_start = cleaned.find("{")
            if body_start < 0:
                continue

            # Member & chained completion sites
            for m in re.finditer(r"([A-Za-z_]\w*(?:\([^()\n]*\))?)\s*\.\s*([A-Za-z_]\w*)", cleaned[body_start:]):
                abs_start = body_start + m.start(2)
                abs_end = body_start + m.end(2)
                recv = m.group(1)
                expected = m.group(2)
                if recv in JAVA_KEYWORDS or expected in ("class", "this", "super"):
                    continue
                if "(" in recv:
                    cat = "chained_call"
                elif recv == "log" and "@Slf4j" in cleaned:
                    cat = "lombok_log"
                elif recv in ("System", "String", "List", "Map", "HttpStatus", "Collections", "Objects"):
                    cat = "jdk_or_static"
                elif expected in ("builder", "build") or expected.startswith(("get", "set")):
                    cat = "lombok_or_accessor"
                else:
                    cat = "member_dot"

                # Test at dot (empty prefix) and at 2-char prefix if long enough
                scenarios.append(
                    {
                        "file": jf,
                        "category": cat,
                        "expected": expected,
                        "prefix": "",
                        "edited_text": jf.text[:abs_start] + jf.text[abs_end:],
                        "cursor_offset": abs_start,
                        "trigger_kind": 2,
                        "trigger_character": ".",
                    }
                )
                if len(expected) >= 3:
                    prefix = expected[:2]
                    scenarios.append(
                        {
                            "file": jf,
                            "category": f"{cat}_prefix",
                            "expected": expected,
                            "prefix": prefix,
                            "edited_text": jf.text[:abs_start] + prefix + jf.text[abs_end:],
                            "cursor_offset": abs_start + len(prefix),
                            "trigger_kind": 1,
                            "trigger_character": None,
                        }
                    )
                if len([s for s in scenarios if s["file"].uri == jf.uri]) >= 8:
                    break

            # Constructor `new ` completion
            nm = re.search(r"\bnew\s+([A-Z]\w*)", cleaned[body_start:])
            if nm:
                abs_start = body_start + nm.start(1)
                abs_end = body_start + nm.end(1)
                expected = nm.group(1)
                prefix = expected[:3]
                scenarios.append(
                    {
                        "file": jf,
                        "category": "new_constructor",
                        "expected": expected,
                        "prefix": prefix,
                        "edited_text": jf.text[:abs_start] + prefix + jf.text[abs_end:],
                        "cursor_offset": abs_start + len(prefix),
                        "trigger_kind": 1,
                        "trigger_character": None,
                    }
                )

            # Annotation `@` completion
            am = re.search(r"@([A-Z]\w*)", cleaned)
            if am and am.start(1) < body_start:
                abs_start = am.start(1)
                abs_end = am.end(1)
                expected = am.group(1)
                prefix = expected[:3]
                scenarios.append(
                    {
                        "file": jf,
                        "category": "annotation",
                        "expected": expected,
                        "prefix": prefix,
                        "edited_text": jf.text[:abs_start] + prefix + jf.text[abs_end:],
                        "cursor_offset": abs_start + len(prefix),
                        "trigger_kind": 1,
                        "trigger_character": None,
                    }
                )

        # Synthetic high-value scenarios on first suitable service/controller file
        target_jf = next(
            (f for f in self.files if "ServiceImpl" in f.rel_path or "Controller" in f.rel_path),
            self.files[0],
        )
        ins = _find_method_body_insertion_point(target_jf.text)
        if ins is not None:
            ins_off, _ = ins
            # Static import completion check (in test file if present)
            static_jf = next((f for f in self.files if "import static " in f.text), None)
            if static_jf is not None:
                sm = re.search(r"import\s+static\s+[\w.]+\.([A-Za-z_]\w*)\s*;", static_jf.text)
                if sm:
                    static_sym = sm.group(1)
                    body_m = re.search(rf"\b({re.escape(static_sym)})\s*\(", static_jf.text[sm.end():])
                    if body_m:
                        abs_s = sm.end() + body_m.start(1)
                        abs_e = sm.end() + body_m.end(1)
                        pref = static_sym[:4]
                        scenarios.append(
                            {
                                "file": static_jf,
                                "category": "static_import",
                                "expected": static_sym,
                                "prefix": pref,
                                "edited_text": static_jf.text[:abs_s] + pref + static_jf.text[abs_e:],
                                "cursor_offset": abs_s + len(pref),
                                "trigger_kind": 1,
                                "trigger_character": None,
                            }
                        )

            # Lambda param completion
            lambda_stmt = "\n    java.util.List.of(\"a\").stream().filter(simItem -> simItem.);"
            lambda_cursor = ins_off + lambda_stmt.index("simItem.") + len("simItem.")
            scenarios.append(
                {
                    "file": target_jf,
                    "category": "lambda_param",
                    "expected": "length",
                    "prefix": "",
                    "edited_text": target_jf.text[:ins_off] + lambda_stmt + target_jf.text[ins_off:],
                    "cursor_offset": lambda_cursor,
                    "trigger_kind": 2,
                    "trigger_character": ".",
                }
            )

            # Auto-import additionalTextEdits (JDK class not imported)
            auto_stmt = "\n    BigDec"
            scenarios.append(
                {
                    "file": target_jf,
                    "category": "auto_import",
                    "expected": "BigDecimal",
                    "prefix": "BigDec",
                    "edited_text": target_jf.text[:ins_off] + auto_stmt + target_jf.text[ins_off:],
                    "cursor_offset": ins_off + len(auto_stmt),
                    "trigger_kind": 1,
                    "trigger_character": None,
                    "check_auto_import": True,
                }
            )

            # Chained type-graph completion
            chain_stmt = "\n    java.util.List<Integer> __simNums = java.util.List.of(1);\n    java.util.List<String> __simStrs = __simNums."
            scenarios.append(
                {
                    "file": target_jf,
                    "category": "chained_type_graph",
                    "expected_any": ["stream", "toList", "map"],
                    "expected": "stream().map",
                    "prefix": "",
                    "edited_text": target_jf.text[:ins_off] + chain_stmt + target_jf.text[ins_off:],
                    "cursor_offset": ins_off + len(chain_stmt),
                    "trigger_kind": 2,
                    "trigger_character": ".",
                    "check_chained_graph": True,
                }
            )

        # Execute completion scenarios
        results: list[dict[str, Any]] = []
        cat_stats: dict[str, collections.Counter[str]] = collections.defaultdict(collections.Counter)
        snippet_hits = 0
        auto_import_hits = 0
        auto_import_total = 0

        for sc in scenarios:
            jf = sc["file"]
            pos = offset_to_position(sc["edited_text"], sc["cursor_offset"])
            try:
                self._with_temp_doc_edit(client, jf, sc["edited_text"], wait_diag=False)
                raw = client.completion(
                    jf.uri,
                    pos,
                    trigger_kind=sc["trigger_kind"],
                    trigger_character=sc["trigger_character"],
                )
                items = extract_completion_items(raw)
                labels = [str(it.get("label", "")) for it in items]
                insert_texts = [str(it.get("insertText") or it.get("label") or "") for it in items]

                expected = sc["expected"]
                rank = self._find_completion_rank(items, expected, sc.get("expected_any"))
                hit = rank is not None
                top5 = rank is not None and rank <= 5
                has_snippet = any(it.get("insertTextFormat") == 2 for it in items)
                if has_snippet:
                    snippet_hits += 1

                auto_import_ok = None
                if sc.get("check_auto_import"):
                    auto_import_total += 1
                    matched_items = [
                        it for it in items
                        if re.split(r"[\s(<:\[]", str(it.get("label", "")), maxsplit=1)[0] == expected
                        or str(it.get("insertText", "")) == expected
                    ]
                    auto_import_ok = False
                    for mi in matched_items[:3]:
                        edits = mi.get("additionalTextEdits") or []
                        if not edits:
                            try:
                                resolved = client.completion_resolve(mi)
                                if isinstance(resolved, dict):
                                    edits = resolved.get("additionalTextEdits") or []
                            except Exception:
                                pass
                        if any("import " in str(e.get("newText", "")) for e in edits if isinstance(e, dict)):
                            auto_import_ok = True
                            break
                    if auto_import_ok:
                        auto_import_hits += 1

                cat = sc["category"]
                cat_stats[cat]["total"] += 1
                if hit:
                    cat_stats[cat]["hit"] += 1
                if top5:
                    cat_stats[cat]["top5"] += 1

                results.append(
                    {
                        "file": jf.rel_path,
                        "category": cat,
                        "expected": expected,
                        "prefix": sc["prefix"],
                        "total_items": len(items),
                        "hit": hit,
                        "rank": rank,
                        "top5": top5,
                        "has_snippet": has_snippet,
                        "auto_import_ok": auto_import_ok,
                        "top5_labels": labels[:5],
                        "top5_inserts": insert_texts[:5],
                    }
                )
            except Exception as exc:
                self.report["harness_errors"].append(f"completion({jf.rel_path}): {exc}")
                results.append(
                    {
                        "file": jf.rel_path,
                        "category": sc["category"],
                        "expected": sc["expected"],
                        "error": str(exc),
                        "hit": False,
                        "top5": False,
                    }
                )
            finally:
                try:
                    self._restore_doc(client, jf, wait_diag=False)
                except Exception as exc:
                    self.report["harness_errors"].append(f"restore({jf.rel_path}): {exc}")

        total = len(results)
        hits = sum(1 for r in results if r.get("hit"))
        top5_hits = sum(1 for r in results if r.get("top5"))
        return {
            "total_scenarios": total,
            "hits": hits,
            "hit_rate_pct": round((hits / total * 100.0) if total else 0.0, 1),
            "top5_hits": top5_hits,
            "top5_rate_pct": round((top5_hits / total * 100.0) if total else 0.0, 1),
            "snippet_scenarios": snippet_hits,
            "auto_import_hits": auto_import_hits,
            "auto_import_total": auto_import_total,
            "by_category": {k: dict(v) for k, v in sorted(cat_stats.items())},
            "details": results,
        }

    @staticmethod
    def _find_completion_rank(
        items: list[dict[str, Any]],
        expected: str,
        expected_any: list[str] | None = None,
    ) -> int | None:
        targets = expected_any if expected_any else [expected]
        for idx, it in enumerate(items, start=1):
            label = str(it.get("label", ""))
            filter_txt = str(it.get("filterText") or "")
            insert_txt = str(it.get("insertText") or "")
            # Extract exact identifier prefix before params/generics/type annotations
            base_label = re.split(r"[\s(<:\[]", label, maxsplit=1)[0]
            base_insert = re.split(r"[\s(<:\[\$]", insert_txt, maxsplit=1)[0]
            for tgt in targets:
                if (
                    base_label == tgt
                    or filter_txt == tgt
                    or base_insert == tgt
                    or (expected_any and tgt in insert_txt)
                ):
                    return idx
        return None

    # 2.2 Live Signature Help
    def _sim_signature_help(self, client: LspClient) -> dict[str, Any]:
        self._log("  [2.2] Live signatureHelp scenarios...")
        scenarios: list[dict[str, Any]] = []
        seen_callees: set[tuple[str, str]] = set()

        for jf in self.files:
            cleaned = _strip_comments_and_strings(jf.text)
            body_start = cleaned.find("{")
            if body_start < 0:
                continue
            for m in re.finditer(r"\b([A-Za-z_]\w*(?:\s*\.\s*[A-Za-z_]\w*)?)\s*\(([^()\n;{}]+)\)", cleaned[body_start:]):
                abs_m_start = body_start + m.start()
                abs_m_end = body_start + m.end()
                # Skip annotations like @GetMapping(...) and method declarations ending in '{' or ';' on interfaces
                prev_non_ws = cleaned[:abs_m_start].rstrip()[-1:] if abs_m_start > 0 else ""
                next_non_ws = cleaned[abs_m_end:].lstrip()[:1]
                if prev_non_ws == "@" or next_non_ws == "{":
                    continue
                if prev_non_ws not in ("", "=", "(", ",", ".", "return", ":") and not re.search(
                    r"(?:^|[;{}()\s=,.]|new|return|throw)\s*$", cleaned[:abs_m_start]
                ):
                    continue
                callee = re.sub(r"\s+", "", m.group(1))
                if callee.split(".")[0] in JAVA_KEYWORDS or callee in ("if", "for", "while", "switch", "catch"):
                    continue
                if (jf.uri, callee) in seen_callees:
                    continue
                seen_callees.add((jf.uri, callee))
                args_start = body_start + m.start(2)
                args_end = body_start + m.end(2)
                raw_args = jf.text[args_start:args_end]
                # Test activeParameter == 0 right after '('
                edited0 = jf.text[:args_start] + jf.text[args_end:]
                scenarios.append(
                    {
                        "file": jf,
                        "callee": callee,
                        "expected_param": 0,
                        "edited_text": edited0,
                        "cursor_offset": args_start,
                        "trigger_char": "(",
                    }
                )
                # If comma exists at top level of raw_args, test activeParameter == 1
                if "," in raw_args:
                    first_comma = raw_args.find(",")
                    keep = raw_args[: first_comma + 1] + " "
                    edited1 = jf.text[:args_start] + keep + jf.text[args_end:]
                    scenarios.append(
                        {
                            "file": jf,
                            "callee": callee,
                            "expected_param": 1,
                            "edited_text": edited1,
                            "cursor_offset": args_start + len(keep),
                            "trigger_char": ",",
                        }
                    )
                if len(scenarios) >= 24:
                    break
            if len(scenarios) >= 24:
                break

        results: list[dict[str, Any]] = []
        unsupported = False
        for sc in scenarios:
            jf = sc["file"]
            pos = offset_to_position(sc["edited_text"], sc["cursor_offset"])
            try:
                self._with_temp_doc_edit(client, jf, sc["edited_text"], wait_diag=False)
                res = client.signature_help(
                    jf.uri,
                    pos,
                    trigger_kind=2,
                    trigger_character=sc["trigger_char"],
                )
                sigs = res.get("signatures", []) if isinstance(res, dict) else []
                active_param = res.get("activeParameter") if isinstance(res, dict) else None
                hit = bool(sigs)
                param_match = hit and (active_param == sc["expected_param"])
                results.append(
                    {
                        "file": jf.rel_path,
                        "callee": sc["callee"],
                        "expected_param": sc["expected_param"],
                        "hit": hit,
                        "signature_count": len(sigs),
                        "active_parameter": active_param,
                        "param_match": param_match,
                        "top_label": sigs[0].get("label", "") if sigs else "",
                    }
                )
            except LspResponseError as exc:
                if exc.code == -32601:
                    unsupported = True
                    results.append(
                        {
                            "file": jf.rel_path,
                            "callee": sc["callee"],
                            "expected_param": sc["expected_param"],
                            "hit": False,
                            "unsupported": True,
                        }
                    )
                    break
                self.report["harness_errors"].append(f"signatureHelp({jf.rel_path}): {exc}")
            except Exception as exc:
                self.report["harness_errors"].append(f"signatureHelp({jf.rel_path}): {exc}")
            finally:
                try:
                    self._restore_doc(client, jf, wait_diag=False)
                except Exception:
                    pass

        total = len(results)
        hits = sum(1 for r in results if r.get("hit"))
        param_matches = sum(1 for r in results if r.get("param_match"))
        return {
            "total": total,
            "hits": hits,
            "param_matches": param_matches,
            "unsupported": unsupported,
            "details": results,
        }

    # 2.3 Error Injection & Live Diagnostics
    def _sim_error_injection(self, client: LspClient) -> dict[str, Any]:
        self._log("  [2.3] Error injection & live diagnostics...")
        # Prefer a service impl or controller with 0 baseline errors
        candidates = sorted(
            self.files,
            key=lambda f: (
                len(client.get_diagnostics(f.uri)),
                0 if "ServiceImpl" in f.rel_path or "Controller" in f.rel_path else 1,
            ),
        )
        target_jf = candidates[0]
        ins = _find_method_body_insertion_point(target_jf.text)
        if ins is None:
            return {"total": 0, "detected": 0, "reverted_clean": 0, "details": []}
        ins_off, ins_line = ins
        var_info = _find_typed_var_in_method(target_jf.text, set(self.project_types.keys()))
        var_name = var_info[0] if var_info else "this"

        baseline_errs = len([d for d in client.get_diagnostics(target_jf.uri) if d.get("severity", 1) == 1])

        # Build realistic error injections
        injections = [
            ("syntax_error", "\n    int __simMissingSemi = 1\n"),
            ("undefined_identifier", "\n    __simUnknownIdentifierXYZ.toString();\n"),
            ("unknown_method", f"\n    {var_name}.__simNonExistentMethodXYZ();\n"),
            ("wrong_arg_count", "\n    String.valueOf();\n"),
            ("wrong_arg_type", "\n    Long.parseLong(12345);\n"),
            ("incompatible_return_or_assign", '\n    int __simBadAssign = "not_an_int";\n'),
            ("missing_import", "\n    BigDecimal __simMissingImport = null;\n"),
            ("unimplemented_interface_method", "\n    Runnable __simUnimpl = new Runnable() {};\n"),
        ]

        results: list[dict[str, Any]] = []
        for kind, snippet in injections:
            edited = target_jf.text[:ins_off] + snippet + target_jf.text[ins_off:]
            injected_line = ins_line + 1
            try:
                diags = self._with_temp_doc_edit(client, target_jf, edited, wait_diag=True)
                err_diags = [d for d in diags if d.get("severity", 1) == 1]
                detected = len(diags) > baseline_errs
                error_severity_detected = len(err_diags) > baseline_errs
                near_line = any(
                    abs(d.get("range", {}).get("start", {}).get("line", -99) - injected_line) <= 2
                    for d in diags
                )
            except Exception as exc:
                self.report["harness_errors"].append(f"error_injection({kind}): {exc}")
                diags = []
                detected = False
                error_severity_detected = False
                near_line = False
            finally:
                reverted_diags = self._restore_doc(client, target_jf, wait_diag=True)

            reverted_errs = len([d for d in reverted_diags if d.get("severity", 1) == 1])
            reverted_clean = reverted_errs <= baseline_errs
            results.append(
                {
                    "kind": kind,
                    "file": target_jf.rel_path,
                    "injected_line": injected_line + 1,
                    "detected": detected,
                    "error_severity_detected": error_severity_detected,
                    "line_accurate": near_line,
                    "diag_count": len(diags),
                    "sample_message": diags[0].get("message", "") if diags else "",
                    "reverted_clean": reverted_clean,
                }
            )

        return {
            "file": target_jf.rel_path,
            "baseline_errors": baseline_errs,
            "total": len(results),
            "detected": sum(1 for r in results if r["detected"]),
            "reverted_clean": sum(1 for r in results if r["reverted_clean"]),
            "details": results,
        }

    # 2.4 CodeActions
    def _sim_code_actions(self, client: LspClient) -> dict[str, Any]:
        self._log("  [2.4] CodeAction scenarios...")
        target_jf = next(
            (f for f in self.files if "ServiceImpl" in f.rel_path or "Controller" in f.rel_path),
            self.files[0],
        )
        ins = _find_method_body_insertion_point(target_jf.text)
        if ins is None:
            return {"total": 0, "hits": 0, "unsupported": False, "details": []}
        ins_off, _ = ins

        probes = [
            ("missing_import_fix", "\n    BigDecimal __simCaImport = null;\n", "BigDecimal"),
            ("unimplemented_method_fix", "\n    Runnable __simCaRun = new Runnable() {};\n", "Runnable"),
        ]
        results: list[dict[str, Any]] = []
        unsupported = False

        for kind, snippet, anchor in probes:
            edited = target_jf.text[:ins_off] + snippet + target_jf.text[ins_off:]
            anchor_idx = ins_off + snippet.index(anchor)
            start_pos = offset_to_position(edited, anchor_idx)
            end_pos = offset_to_position(edited, anchor_idx + len(anchor))
            try:
                diags = self._with_temp_doc_edit(client, target_jf, edited, wait_diag=True)
                actions = client.code_action(
                    target_jf.uri,
                    start_pos,
                    end_pos,
                    diagnostics=diags,
                )
                act_list = actions if isinstance(actions, list) else []
                titles = [str(a.get("title", "")) for a in act_list if isinstance(a, dict)]
                quickfix_hit = any(
                    (
                        str(a.get("kind", "")).startswith("quickfix")
                        or anchor.lower() in str(a.get("title", "")).lower()
                        or "import" in str(a.get("title", "")).lower()
                        or "implement" in str(a.get("title", "")).lower()
                    )
                    and "organize imports" not in str(a.get("title", "")).lower()
                    for a in act_list
                    if isinstance(a, dict)
                )
                results.append(
                    {
                        "kind": kind,
                        "file": target_jf.rel_path,
                        "action_count": len(act_list),
                        "titles": titles[:10],
                        "hit": len(act_list) > 0,
                        "quickfix_hit": quickfix_hit,
                    }
                )
            except LspResponseError as exc:
                if exc.code == -32601:
                    unsupported = True
                    results.append(
                        {"kind": kind, "file": target_jf.rel_path, "unsupported": True, "hit": False, "quickfix_hit": False}
                    )
                    break
                self.report["harness_errors"].append(f"codeAction({kind}): {exc}")
            except Exception as exc:
                self.report["harness_errors"].append(f"codeAction({kind}): {exc}")
            finally:
                try:
                    self._restore_doc(client, target_jf, wait_diag=True)
                except Exception:
                    pass

        return {
            "total": len(results),
            "hits": sum(1 for r in results if r.get("hit")),
            "quickfix_hits": sum(1 for r in results if r.get("quickfix_hit")),
            "unsupported": unsupported,
            "details": results,
        }

    # 2.5 Rename workflows (in-memory edit inspection, never written to disk)
    def _sim_rename(self, client: LspClient) -> dict[str, Any]:
        self._log("  [2.5] Rename workflows (read-only edit verification)...")
        targets: list[tuple[str, JavaFileInfo, int, str]] = []

        # Pick class and interface method with highest cross-file reference count
        class_candidates: list[tuple[int, JavaFileInfo, int, str]] = []
        method_candidates: list[tuple[int, JavaFileInfo, int, str]] = []
        field_candidate: tuple[str, JavaFileInfo, int, str] | None = None
        local_candidate: tuple[str, JavaFileInfo, int, str] | None = None

        for jf in self.files:
            cleaned = _strip_comments_and_strings(jf.text)
            cm = re.search(r"\bpublic\s+class\s+([A-Z]\w*)", cleaned)
            if cm:
                cname = cm.group(1)
                usage_files = sum(
                    1 for other in self.files
                    if other.uri != jf.uri and re.search(rf"\b{re.escape(cname)}\b", other.text)
                )
                class_candidates.append((usage_files, jf, cm.start(1) + 1, cname))

            if "interface " in cleaned:
                for mm in re.finditer(r"\b(?:List<[^>]+>|[\w<>]+)\s+([a-z_]\w*)\s*\([^;{}]*\)\s*;", cleaned):
                    mname = mm.group(1)
                    usage_files = sum(
                        1 for other in self.files
                        if other.uri != jf.uri and re.search(rf"\b{re.escape(mname)}\s*\(", other.text)
                    )
                    method_candidates.append((usage_files, jf, mm.start(1) + 1, mname))

            if field_candidate is None:
                fm = re.search(r"\bprivate\s+(?:final\s+)?[\w<>]+\s+([a-z_]\w*)\s*;", cleaned)
                if fm:
                    field_candidate = ("field", jf, fm.start(1) + 1, fm.group(1))

            if local_candidate is None:
                lm = re.search(r"\b([A-Z]\w*)\s+([a-z_]\w*)\s*=\s*", cleaned)
                if lm and lm.group(1) not in JAVA_KEYWORDS:
                    local_candidate = ("local", jf, lm.start(2) + 1, lm.group(2))

        if class_candidates:
            _, jf, off, name = max(class_candidates, key=lambda x: x[0])
            targets.append(("class", jf, off, name))
        if method_candidates:
            _, jf, off, name = max(method_candidates, key=lambda x: x[0])
            targets.append(("method", jf, off, name))
        if field_candidate is not None:
            targets.append(field_candidate)
        if local_candidate is not None:
            targets.append(local_candidate)

        results: list[dict[str, Any]] = []
        for kind, jf, offset, orig_name in targets:
            pos = offset_to_position(jf.text, offset)
            new_name = f"{orig_name}SimRenamed"
            prep_ok = False
            try:
                prep = client.prepare_rename(jf.uri, pos)
                prep_ok = prep is not None
            except LspResponseError:
                prep_ok = False
            except Exception as exc:
                self.report["harness_errors"].append(f"prepareRename({kind}): {exc}")

            try:
                edit = client.rename(jf.uri, pos, new_name)
                file_edits = self._extract_workspace_edits(edit)
                total_edits = sum(len(ed) for ed in file_edits.values())
                # Verify edits apply cleanly in memory on open files
                apply_ok = True
                for e_uri, edits in file_edits.items():
                    if e_uri in self.file_by_uri:
                        _ = apply_text_edits(self.file_by_uri[e_uri].text, edits)
                results.append(
                    {
                        "kind": kind,
                        "symbol": orig_name,
                        "file": jf.rel_path,
                        "prepare_ok": prep_ok,
                        "edited_files_count": len(file_edits),
                        "total_edits": total_edits,
                        "apply_ok": apply_ok,
                        "hit": total_edits > 0,
                        "edited_files": [
                            self.file_by_uri[u].rel_path if u in self.file_by_uri else u
                            for u in sorted(file_edits)
                        ],
                    }
                )
            except LspResponseError as exc:
                results.append(
                    {
                        "kind": kind,
                        "symbol": orig_name,
                        "file": jf.rel_path,
                        "prepare_ok": prep_ok,
                        "hit": False,
                        "error": str(exc),
                    }
                )
            except Exception as exc:
                self.report["harness_errors"].append(f"rename({kind}): {exc}")
                results.append(
                    {
                        "kind": kind,
                        "symbol": orig_name,
                        "file": jf.rel_path,
                        "prepare_ok": prep_ok,
                        "hit": False,
                        "error": str(exc),
                    }
                )

        return {
            "total": len(results),
            "hits": sum(1 for r in results if r.get("hit")),
            "details": results,
        }

    @staticmethod
    def _extract_workspace_edits(ws_edit: Any) -> dict[str, list[dict[str, Any]]]:
        out: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
        if not isinstance(ws_edit, dict):
            return out
        changes = ws_edit.get("changes")
        if isinstance(changes, dict):
            for uri, edits in changes.items():
                if isinstance(edits, list):
                    out[uri].extend(edits)
        doc_changes = ws_edit.get("documentChanges")
        if isinstance(doc_changes, list):
            for dc in doc_changes:
                if isinstance(dc, dict) and "textDocument" in dc and "edits" in dc:
                    uri = dc["textDocument"].get("uri", "")
                    if uri:
                        out[uri].extend(dc["edits"])
        return out


# ---------------------------------------------------------------------------
# Markdown Report Generator
# ---------------------------------------------------------------------------


def render_markdown_report(report: dict[str, Any]) -> str:
    lines: list[str] = []
    srv = report.get("server_info") or {}
    srv_label = f"{srv.get('name', 'java-lsp')} {srv.get('version', '')}".strip()

    lines.append("# Black-Box Project Simulator Report")
    lines.append("")
    lines.append(f"- **Target Project**: `{report['project_dir']}`")
    lines.append(f"- **Generated At**: {report['timestamp']}")
    lines.append(f"- **Server**: `{srv_label}`")
    lines.append(f"- **Modules**: {', '.join(f'`{m}`' for m in report.get('modules', []))}")
    lines.append(f"- **Java Files Discovered**: {report['file_count']}")
    lines.append(f"- **Harness Errors / Crashes**: {len(report.get('harness_errors', []))}")
    lines.append(f"- **Server Alive At End**: {report.get('server_alive_at_end')}")
    lines.append("")

    if report.get("harness_errors"):
        lines.append("## Harness Errors")
        for err in report["harness_errors"]:
            lines.append(f"- `{err}`")
        lines.append("")

    caps = report.get("server_capabilities") or {}
    if caps:
        expected_caps = [
            "textDocumentSync", "completionProvider", "hoverProvider", "signatureHelpProvider",
            "declarationProvider", "definitionProvider", "typeDefinitionProvider", "implementationProvider",
            "referencesProvider", "documentHighlightProvider", "documentSymbolProvider", "workspaceSymbolProvider",
            "codeActionProvider", "codeLensProvider", "documentLinkProvider", "documentFormattingProvider",
            "documentRangeFormattingProvider", "documentOnTypeFormattingProvider", "renameProvider",
            "foldingRangeProvider", "selectionRangeProvider", "linkedEditingRangeProvider",
            "callHierarchyProvider", "typeHierarchyProvider", "semanticTokensProvider",
            "inlayHintProvider", "diagnosticProvider",
        ]
        advertised = [c for c in expected_caps if caps.get(c)]
        missing_caps = [c for c in expected_caps if not caps.get(c)]
        lines.append("## Server Capabilities Audit (LSP 3.17)")
        lines.append("")
        lines.append(f"- **Advertised**: {', '.join(f'`{c}`' for c in advertised)}")
        lines.append(f"- **Unadvertised / Missing**: {', '.join(f'`{c}`' for c in missing_caps)}")
        lines.append(f"- **Incremental Sync (`textDocumentSync == 2`)**: {caps.get('textDocumentSync') == 2} (actual={caps.get('textDocumentSync')})")
        comp_cap = caps.get("completionProvider") or {}
        lines.append(f"- **`completionItem/resolve` (`resolveProvider`)**: {bool(isinstance(comp_cap, dict) and comp_cap.get('resolveProvider'))}")
        lines.append("")

    # Mode 1
    m1 = report.get("mode1") or {}
    if m1:
        lines.append("## Mode 1: Automated Project Browser")
        lines.append("")
        lines.append(f"- **Total LSP Diagnostic Errors**: {m1.get('total_diagnostic_errors', 0)}")
        lines.append(f"- **Total LSP Diagnostic Warnings**: {m1.get('total_diagnostic_warnings', 0)}")
        lines.append(f"- **Unsupported Methods (`-32601`)**: {', '.join(f'`{u}`' for u in m1.get('unsupported_methods', [])) or 'None'}")
        lines.append(f"- **Total Navigation Probes**: {m1.get('probe_count', 0)}")
        lines.append("")

        lines.append("### Per-File Diagnostics & Structural Endpoints")
        lines.append("")
        lines.append("| File | Module | Errors | Warns | `documentSymbol` | `foldingRange` | `semanticTokens` | `inlayHint` | `codeLens` |")
        lines.append("| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |")
        for f in m1.get("files", []):
            ds = f["documentSymbol"]
            fr = f["foldingRange"]
            st = f["semanticTokens"]
            ih = f["inlayHint"]
            cl = f["codeLens"]
            lines.append(
                f"| `{f['rel_path']}` | `{f['module']}` | {f['error_count']} | {f['warning_count']} "
                f"| {ds['status']}:{ds['count']} | {fr['status']}:{fr['count']} "
                f"| {st['status']}:{st['token_count']} | {ih['status']}:{ih['count']} "
                f"| {cl['status']}:{cl['count']} |"
            )
        lines.append("")

        files_with_errs = [f for f in m1.get("files", []) if f["error_count"] > 0]
        if files_with_errs:
            lines.append("### Diagnostic Error Details (Potential False Positives on Compiling Code)")
            lines.append("")
            for f in files_with_errs:
                lines.append(f"- **`{f['rel_path']}`** ({f['error_count']} error(s)):")
                for e in f["errors"][:10]:
                    lines.append(f"  - line {e['line']}: `{e['message']}`")
            lines.append("")

        lines.append("### Position Probe Summary by Endpoint")
        lines.append("")
        lines.append("| Endpoint | Hit | Self-Echo / `(0,0)` | Empty | Unsupported | Invalid URI | Error | Crash/Timeout |")
        lines.append("| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |")
        probes_list = m1.get("probes") or []
        for ep, cnt in (m1.get("endpoint_summary") or {}).items():
            degen = sum(
                1 for p in probes_list
                if p.get("results", {}).get(ep, {}).get("self_echo")
                or p.get("results", {}).get(ep, {}).get("file_head_0_0")
            )
            lines.append(
                f"| `{ep}` | {cnt.get('hit', 0)} | {degen} | {cnt.get('empty', 0)} | {cnt.get('unsupported', 0)} "
                f"| {cnt.get('invalid_uri', 0)} | {cnt.get('error', 0)} | {cnt.get('crash_or_timeout', 0)} |"
            )
        lines.append("")

        if m1.get("origin_summary"):
            lines.append("### Resolution Target Origins")
            lines.append("")
            lines.append("| Endpoint | Same File | Cross-File | Cross-Module | JDK | Maven Dep | MyBatis XML | Missing File |")
            lines.append("| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |")
            for ep, cnt in m1["origin_summary"].items():
                lines.append(
                    f"| `{ep}` | {cnt.get('same_file', 0)} | {cnt.get('cross_file', 0)} "
                    f"| {cnt.get('cross_module', 0)} | {cnt.get('jdk', 0)} "
                    f"| {cnt.get('maven_dep', 0)} | {cnt.get('mybatis_xml', 0)} | {cnt.get('missing_file', 0)} |"
                )
            lines.append("")

        if m1.get("category_summary"):
            lines.append("### Probe Hits by Symbol Category (`definition` / `hover` / `references`)")
            lines.append("")
            lines.append("| Category | `hover` Hit/Total | `definition` Hit/Total | `typeDefinition` Hit/Total | `implementation` Hit/Total | `references` Hit/Total |")
            lines.append("| :--- | :---: | :---: | :---: | :---: | :---: |")
            for cat, eps in sorted(m1["category_summary"].items()):
                def _fmt(ep_name: str) -> str:
                    c = eps.get(ep_name, {})
                    tot = sum(c.values())
                    return f"{c.get('hit', 0)}/{tot}"
                lines.append(
                    f"| `{cat}` | {_fmt('hover')} | {_fmt('definition')} | {_fmt('typeDefinition')} "
                    f"| {_fmt('implementation')} | {_fmt('references')} |"
                )
            lines.append("")

        if m1.get("workspace_symbol"):
            lines.append("### `workspace/symbol` Probes")
            lines.append("")
            for wp in m1["workspace_symbol"]:
                lines.append(f"- Query `{wp['query']}`: status=`{wp['status']}`, matches={wp['count']}")
            lines.append("")

    # Mode 2
    m2 = report.get("mode2") or {}
    if m2:
        lines.append("## Mode 2: Simulated Human Coding")
        lines.append("")

        comp = m2.get("completion") or {}
        if comp:
            lines.append("### 2.1 Progressive Typing & Completion")
            lines.append("")
            lines.append(f"- **Total Scenarios**: {comp.get('total_scenarios', 0)}")
            lines.append(f"- **Hit Rate**: {comp.get('hits', 0)}/{comp.get('total_scenarios', 0)} ({comp.get('hit_rate_pct', 0.0)}%)")
            lines.append(f"- **Top-5 Rate**: {comp.get('top5_hits', 0)}/{comp.get('total_scenarios', 0)} ({comp.get('top5_rate_pct', 0.0)}%)")
            lines.append(f"- **Scenarios Returning Snippets**: {comp.get('snippet_scenarios', 0)}")
            lines.append(f"- **Auto-Import `additionalTextEdits`**: {comp.get('auto_import_hits', 0)}/{comp.get('auto_import_total', 0)}")
            lines.append("")
            lines.append("| Category | Total | Hits | Top-5 |")
            lines.append("| :--- | :---: | :---: | :---: |")
            for cat, c in (comp.get("by_category") or {}).items():
                lines.append(f"| `{cat}` | {c.get('total', 0)} | {c.get('hit', 0)} | {c.get('top5', 0)} |")
            lines.append("")

            missed = [d for d in comp.get("details", []) if not d.get("hit")]
            if missed:
                lines.append("#### Missed Completion Scenarios")
                lines.append("")
                for d in missed[:25]:
                    lines.append(
                        f"- `{d.get('file')}` [`{d.get('category')}`] expected `{d.get('expected')}` "
                        f"(prefix=`{d.get('prefix')}`, items={d.get('total_items', 0)}, top5={d.get('top5_labels', [])})"
                    )
                lines.append("")

        sig = m2.get("signature_help") or {}
        if sig:
            lines.append("### 2.2 Live Signature Help")
            lines.append("")
            lines.append(f"- **Scenarios**: {sig.get('hits', 0)}/{sig.get('total', 0)} returned signatures, {sig.get('param_matches', 0)}/{sig.get('total', 0)} matched `activeParameter`")
            lines.append("")
            lines.append("| File | Callee | Expected `activeParameter` | Hit | Actual `activeParameter` | Top Signature |")
            lines.append("| :--- | :--- | :---: | :---: | :---: | :--- |")
            for d in sig.get("details", []):
                lines.append(
                    f"| `{d.get('file')}` | `{d.get('callee')}` | {d.get('expected_param')} "
                    f"| {'YES' if d.get('hit') else 'NO'} | {d.get('active_parameter')} | `{d.get('top_label', '')[:60]}` |"
                )
            lines.append("")

        diag = m2.get("error_injection") or {}
        if diag:
            lines.append("### 2.3 Error Injection & Live Diagnostics")
            lines.append("")
            lines.append(
                f"- **Target File**: `{diag.get('file')}` (baseline errors: {diag.get('baseline_errors', 0)})"
            )
            lines.append(
                f"- **Detected**: {diag.get('detected', 0)}/{diag.get('total', 0)}, **Reverted Cleanly**: {diag.get('reverted_clean', 0)}/{diag.get('total', 0)}"
            )
            lines.append("")
            lines.append("| Error Kind | Injected Line | Detected | Severity=Error | Line Accurate | Reverted Clean | Sample Diagnostic |")
            lines.append("| :--- | :---: | :---: | :---: | :---: | :---: | :--- |")
            for d in diag.get("details", []):
                lines.append(
                    f"| `{d['kind']}` | {d['injected_line']} | {'YES' if d['detected'] else 'NO'} "
                    f"| {'YES' if d['error_severity_detected'] else 'NO'} | {'YES' if d['line_accurate'] else 'NO'} "
                    f"| {'YES' if d['reverted_clean'] else 'NO'} | `{d['sample_message'][:70]}` |"
                )
            lines.append("")

        ca = m2.get("code_actions") or {}
        if ca:
            lines.append("### 2.4 Code Actions")
            lines.append("")
            lines.append(f"- **Unsupported (`-32601`)**: {ca.get('unsupported')}")
            lines.append(f"- **Any-Action Hits**: {ca.get('hits', 0)}/{ca.get('total', 0)}")
            lines.append(f"- **Error-Specific Quick-Fix Hits**: {ca.get('quickfix_hits', 0)}/{ca.get('total', 0)}")
            for d in ca.get("details", []):
                lines.append(
                    f"- `{d.get('kind')}` (`{d.get('file')}`): count={d.get('action_count', 0)}, quickfix={d.get('quickfix_hit', False)}, titles={d.get('titles', [])}"
                )
            lines.append("")

        ren = m2.get("rename") or {}
        if ren:
            lines.append("### 2.5 Rename Workflows")
            lines.append("")
            lines.append("| Scope | Symbol | File | `prepareRename` | Edited Files | Total Edits | Files Touched |")
            lines.append("| :--- | :--- | :--- | :---: | :---: | :---: | :--- |")
            for d in ren.get("details", []):
                files_str = ", ".join(f"`{x}`" for x in d.get("edited_files", [])) or (d.get("error", "")[:50])
                lines.append(
                    f"| `{d.get('kind')}` | `{d.get('symbol')}` | `{d.get('file')}` "
                    f"| {'YES' if d.get('prepare_ok') else 'NO'} | {d.get('edited_files_count', 0)} "
                    f"| {d.get('total_edits', 0)} | {files_str} |"
                )
            lines.append("")

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Automated black-box Java project browser and human coding simulator for java-lsp."
    )
    parser.add_argument("project_dir", help="Path to target Java/Maven/Gradle project directory.")
    parser.add_argument("--md-out", default=None, help="Path to write Markdown report.")
    parser.add_argument("--json-out", default=None, help="Path to write JSON report.")
    parser.add_argument(
        "--binary",
        default=str(DEFAULT_BINARY_PATH),
        help="Path to java-lsp binary (default: bin/java-lsp).",
    )
    parser.add_argument("--timeout", type=float, default=20.0, help="Per-request LSP timeout in seconds.")
    parser.add_argument("--mode", choices=["all", "browse", "simulate"], default="all")
    parser.add_argument("--max-files", type=int, default=None, help="Optional max .java files to inspect.")
    parser.add_argument(
        "--in-place",
        action="store_true",
        help="Run directly against project_dir instead of an isolated temp copy (may generate .classpath_m2.txt).",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose progress logging.")
    args = parser.parse_args(argv)

    project_dir = Path(args.project_dir).resolve()
    if not project_dir.is_dir():
        print(f"Error: project_dir is not a directory: {project_dir}", file=sys.stderr)
        return 2

    started = time.monotonic()
    sim = ProjectSimulator(
        project_dir=project_dir,
        binary_path=Path(args.binary),
        timeout=args.timeout,
        max_files=args.max_files,
        verbose=args.verbose,
        isolated_copy=not args.in_place,
    )
    report = sim.run(mode=args.mode)
    report["elapsed_sec"] = round(time.monotonic() - started, 3)

    md = render_markdown_report(report)
    if args.md_out:
        md_path = Path(args.md_out)
        md_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.write_text(md, encoding="utf-8")
        print(f"Wrote Markdown report: {md_path}")
    else:
        print(md)

    if args.json_out:
        json_path = Path(args.json_out)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"Wrote JSON report: {json_path}")

    return 1 if report.get("harness_errors") else 0


if __name__ == "__main__":
    raise SystemExit(main())
