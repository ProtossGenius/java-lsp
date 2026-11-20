"""Reusable LSP 3.17 JSON-RPC stdio client and utilities for black-box testing of java-lsp."""

from __future__ import annotations

import copy
import dataclasses
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BINARY_PATH = REPO_ROOT / "bin" / "java-lsp"


class LspClientError(Exception):
    """Base exception for LSP black-box client errors."""


class LspResponseError(LspClientError):
    """Raised when the LSP server returns a JSON-RPC error response."""

    def __init__(
        self,
        code: int,
        message: str,
        data: Any = None,
        method: str = "",
    ) -> None:
        self.code = code
        self.message = message
        self.data = data
        self.method = method
        detail = f" (data={data!r})" if data is not None else ""
        prefix = f"[{method}] " if method else ""
        super().__init__(f"{prefix}JSON-RPC error {code}: {message}{detail}")


class ServerCrashedError(LspClientError):
    """Raised when the LSP server exits prematurely or closes its stdio pipe."""

    def __init__(
        self,
        message: str,
        returncode: int | None = None,
        stderr_tail: list[str] | None = None,
    ) -> None:
        self.returncode = returncode
        self.stderr_tail = list(stderr_tail or [])
        tail_txt = "\n".join(self.stderr_tail[-15:])
        suffix = f"\nRecent stderr:\n{tail_txt}" if tail_txt else ""
        super().__init__(f"{message} (returncode={returncode}){suffix}")


@dataclasses.dataclass
class DiagnosticsRecord:
    """Latest publishDiagnostics snapshot for a document URI."""

    uri: str
    diagnostics: list[dict[str, Any]]
    version: int | None
    seq: int
    timestamp: float


class PendingRequest:
    """Handle for an in-flight JSON-RPC request supporting waiting and cancellation."""

    def __init__(
        self,
        req_id: int,
        method: str,
        cancel_fn: Callable[[int], None],
    ) -> None:
        self.id = req_id
        self.method = method
        self._cancel_fn = cancel_fn
        self._event = threading.Event()
        self._result: Any = None
        self._error: Exception | None = None

    def _resolve(self, result: Any) -> None:
        self._result = result
        self._event.set()

    def _reject(self, error: Exception) -> None:
        self._error = error
        self._event.set()

    def done(self) -> bool:
        return self._event.is_set()

    def cancel(self) -> None:
        """Send $/cancelRequest for this in-flight request."""
        self._cancel_fn(self.id)

    def wait(self, timeout: float | None = 15.0) -> Any:
        """Wait for response, raising LspResponseError, ServerCrashedError, or TimeoutError."""
        if not self._event.wait(timeout=timeout):
            raise TimeoutError(
                f"Timed out after {timeout}s waiting for response to {self.method} (id={self.id})"
            )
        if self._error is not None:
            raise self._error
        return self._result


# ---------------------------------------------------------------------------
# URI, UTF-16 position, and text editing helpers
# ---------------------------------------------------------------------------


def path_to_uri(path: str | Path) -> str:
    """Convert a filesystem path to a canonical file:// URI."""
    return Path(path).expanduser().resolve().as_uri()


def is_file_uri(uri: str) -> bool:
    """Return True if uri uses the file scheme."""
    return urllib.parse.urlparse(uri).scheme == "file"


def uri_to_path(uri: str) -> Path:
    """Convert a file:// URI to a local Path."""
    parsed = urllib.parse.urlparse(uri)
    if parsed.scheme != "file":
        raise ValueError(f"Expected file:// URI, got: {uri!r}")
    # url2pathname handles percent decoding and leading slashes on Linux.
    raw_path = urllib.request.url2pathname(parsed.path)
    if parsed.netloc and parsed.netloc not in ("", "localhost"):
        raw_path = f"//{parsed.netloc}{raw_path}"
    return Path(raw_path)


def uri_exists_on_disk(uri: str) -> bool:
    """Return True if uri is a file:// URI whose target exists on disk."""
    if not is_file_uri(uri):
        return False
    try:
        return uri_to_path(uri).exists()
    except (ValueError, OSError):
        return False


def utf16_len(text: str) -> int:
    """Return the number of UTF-16 code units in text."""
    return len(text.encode("utf-16-le")) // 2


def _line_starts(text: str) -> list[int]:
    """Return character indices of the start of each line in text."""
    starts = [0]
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "\r":
            if i + 1 < n and text[i + 1] == "\n":
                i += 2
            else:
                i += 1
            starts.append(i)
        elif ch == "\n":
            i += 1
            starts.append(i)
        else:
            i += 1
    return starts


def offset_to_position(text: str, offset: int) -> dict[str, int]:
    """Convert a 0-based Python str index into an LSP UTF-16 Position."""
    if offset < 0 or offset > len(text):
        raise ValueError(f"Offset {offset} out of bounds for text of length {len(text)}")
    starts = _line_starts(text)
    # Binary search for line containing offset.
    lo, hi = 0, len(starts) - 1
    while lo <= hi:
        mid = (lo + hi) // 2
        if starts[mid] <= offset:
            lo = mid + 1
        else:
            hi = mid - 1
    line = hi
    col_slice = text[starts[line] : offset]
    return {"line": line, "character": utf16_len(col_slice)}


def position_to_offset(text: str, line: int, character: int) -> int:
    """Convert an LSP 0-based (line, UTF-16 character) position to a Python str index."""
    if line < 0 or character < 0:
        raise ValueError(f"Negative LSP position ({line}, {character})")
    starts = _line_starts(text)
    if line >= len(starts):
        raise ValueError(f"Line {line} out of range (total lines={len(starts)})")
    line_start = starts[line]
    line_end = starts[line + 1] if line + 1 < len(starts) else len(text)
    # Strip trailing line break from line_end for clamping within line content.
    while line_end > line_start and text[line_end - 1] in ("\n", "\r"):
        line_end -= 1
    line_str = text[line_start:line_end]
    units = 0
    for idx, ch in enumerate(line_str):
        if units >= character:
            return line_start + idx
        units += 2 if ord(ch) > 0xFFFF else 1
    return line_start + len(line_str)


def pos_of(
    text: str,
    anchor: str,
    offset: int = 0,
    occurrence: int = 1,
) -> dict[str, int]:
    """Find the occurrence-th (1-based) anchor in text and return LSP position at anchor_start + offset."""
    if not anchor:
        raise ValueError("anchor must be non-empty")
    if occurrence < 1:
        raise ValueError(f"occurrence must be >= 1, got {occurrence}")
    start = 0
    found = -1
    for _ in range(occurrence):
        found = text.find(anchor, start)
        if found < 0:
            raise ValueError(f"Anchor {anchor!r} (occurrence={occurrence}) not found in text")
        start = found + 1
    target_idx = found + offset
    if target_idx < 0 or target_idx > len(text):
        raise ValueError(
            f"Computed index {target_idx} out of bounds (anchor={anchor!r}, offset={offset})"
        )
    return offset_to_position(text, target_idx)


def after_of(text: str, anchor: str, occurrence: int = 1) -> dict[str, int]:
    """Return the LSP position immediately after the occurrence-th anchor in text."""
    return pos_of(text, anchor, offset=len(anchor), occurrence=occurrence)


def range_of(text: str, anchor: str, occurrence: int = 1) -> dict[str, dict[str, int]]:
    """Return the LSP Range spanning the occurrence-th anchor in text."""
    return {
        "start": pos_of(text, anchor, offset=0, occurrence=occurrence),
        "end": pos_of(text, anchor, offset=len(anchor), occurrence=occurrence),
    }


def apply_text_edits(text: str, edits: list[dict[str, Any]]) -> str:
    """Apply a list of LSP TextEdit objects to text and return the updated string."""
    if not edits:
        return text
    indexed: list[tuple[int, int, int, str]] = []
    for order, edit in enumerate(edits):
        rng = edit["range"]
        start_off = position_to_offset(
            text, rng["start"]["line"], rng["start"]["character"]
        )
        end_off = position_to_offset(
            text, rng["end"]["line"], rng["end"]["character"]
        )
        indexed.append((start_off, end_off, order, edit.get("newText", "")))
    # Apply from back to front so earlier offsets remain valid.
    indexed.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
    result = text
    for start_off, end_off, _order, new_text in indexed:
        result = result[:start_off] + new_text + result[end_off:]
    return result


# ---------------------------------------------------------------------------
# Response normalization helpers
# ---------------------------------------------------------------------------


def extract_completion_items(result: Any) -> list[dict[str, Any]]:
    """Normalize CompletionItem[] or CompletionList into a list of CompletionItem dicts."""
    if result is None:
        return []
    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        items = result.get("items")
        if isinstance(items, list):
            return items
    return []


def extract_locations(result: Any) -> list[dict[str, Any]]:
    """Normalize Location | Location[] | LocationLink[] | None into a list of dicts."""
    if result is None:
        return []
    if isinstance(result, dict):
        return [result]
    if isinstance(result, list):
        return [loc for loc in result if isinstance(loc, dict)]
    return []


def location_uri(loc: dict[str, Any]) -> str | None:
    """Extract target URI from either Location or LocationLink."""
    return loc.get("uri") or loc.get("targetUri")


def location_range(loc: dict[str, Any]) -> dict[str, Any] | None:
    """Extract target range from either Location or LocationLink."""
    return loc.get("range") or loc.get("targetSelectionRange") or loc.get("targetRange")


def extract_hover_text(result: Any) -> str:
    """Extract readable text from an LSP Hover response."""
    if not isinstance(result, dict):
        return ""
    contents = result.get("contents")
    if contents is None:
        return ""
    if isinstance(contents, str):
        return contents
    if isinstance(contents, dict):
        return str(contents.get("value", ""))
    if isinstance(contents, list):
        parts: list[str] = []
        for entry in contents:
            if isinstance(entry, str):
                parts.append(entry)
            elif isinstance(entry, dict):
                parts.append(str(entry.get("value", "")))
        return "\n".join(p for p in parts if p)
    return str(contents)


def flatten_document_symbols(result: Any) -> list[dict[str, Any]]:
    """Flatten hierarchical DocumentSymbol[] or SymbolInformation[] into a single list."""
    if not isinstance(result, list):
        return []
    flat: list[dict[str, Any]] = []

    def _walk(items: list[dict[str, Any]]) -> None:
        for item in items:
            if not isinstance(item, dict):
                continue
            flat.append(item)
            children = item.get("children")
            if isinstance(children, list) and children:
                _walk(children)

    _walk(result)
    return flat


# ---------------------------------------------------------------------------
# Temp Java project helper
# ---------------------------------------------------------------------------


class TempJavaProject:
    """Isolated temporary Java/Maven/Gradle project workspace with automatic cleanup."""

    def __init__(
        self,
        files: dict[str, str] | None = None,
        prefix: str = "java_lsp_proj_",
        create_root_marker: bool = False,
    ) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix=prefix)
        self.root = Path(self._tmp.name).resolve()
        if create_root_marker:
            (self.root / ".root").write_text("", encoding="utf-8")
        if files:
            for rel, content in files.items():
                self.write_file(rel, content)

    @property
    def root_uri(self) -> str:
        return path_to_uri(self.root)

    def path(self, rel_path: str | Path) -> Path:
        target = (self.root / rel_path).resolve()
        # Prevent escaping temp root via ..
        if target != self.root and self.root not in target.parents:
            raise ValueError(f"Path {rel_path!r} escapes temp project root {self.root}")
        return target

    def uri(self, rel_path: str | Path) -> str:
        return path_to_uri(self.path(rel_path))

    def write_file(self, rel_path: str | Path, content: str) -> Path:
        dest = self.path(rel_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
        return dest

    def read_file(self, rel_path: str | Path) -> str:
        return self.path(rel_path).read_text(encoding="utf-8")

    def remove_file(self, rel_path: str | Path) -> None:
        target = self.path(rel_path)
        if target.exists():
            target.unlink()

    def rename_file(self, old_rel: str | Path, new_rel: str | Path) -> Path:
        src = self.path(old_rel)
        dst = self.path(new_rel)
        dst.parent.mkdir(parents=True, exist_ok=True)
        src.rename(dst)
        return dst

    def copy_from(self, src_dir: str | Path) -> None:
        """Copy an existing project tree into this isolated temp workspace (excluding build artifacts)."""
        src_path = Path(src_dir).resolve()
        ignore = shutil.ignore_patterns(
            "target",
            "build",
            ".git",
            ".gradle",
            ".idea",
            ".settings",
            "*.class",
            ".classpath_m2.txt",
        )
        shutil.copytree(src_path, self.root, dirs_exist_ok=True, ignore=ignore)

    def close(self) -> None:
        self._tmp.cleanup()

    def __enter__(self) -> TempJavaProject:
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()


# ---------------------------------------------------------------------------
# Default LSP 3.17 client capabilities
# ---------------------------------------------------------------------------


DEFAULT_CLIENT_CAPABILITIES: dict[str, Any] = {
    "workspace": {
        "applyEdit": True,
        "workspaceEdit": {
            "documentChanges": True,
            "resourceOperations": ["create", "rename", "delete"],
        },
        "didChangeWatchedFiles": {"dynamicRegistration": False},
        "symbol": {"dynamicRegistration": False},
        "fileOperations": {
            "didCreate": True,
            "didRename": True,
            "willRename": True,
            "didDelete": True,
        },
    },
    "textDocument": {
        "synchronization": {
            "dynamicRegistration": False,
            "willSave": False,
            "willSaveWaitUntil": False,
            "didSave": True,
        },
        "completion": {
            "dynamicRegistration": False,
            "completionItem": {
                "snippetSupport": True,
                "commitCharactersSupport": True,
                "documentationFormat": ["markdown", "plaintext"],
                "deprecatedSupport": True,
                "preselectSupport": True,
                "insertReplaceSupport": True,
                "resolveSupport": {"properties": ["documentation", "detail", "additionalTextEdits"]},
            },
            "contextSupport": True,
        },
        "hover": {
            "dynamicRegistration": False,
            "contentFormat": ["markdown", "plaintext"],
        },
        "signatureHelp": {
            "dynamicRegistration": False,
            "signatureInformation": {
                "documentationFormat": ["markdown", "plaintext"],
                "parameterInformation": {"labelOffsetSupport": True},
                "activeParameterSupport": True,
            },
            "contextSupport": True,
        },
        "declaration": {"dynamicRegistration": False, "linkSupport": True},
        "definition": {"dynamicRegistration": False, "linkSupport": True},
        "typeDefinition": {"dynamicRegistration": False, "linkSupport": True},
        "implementation": {"dynamicRegistration": False, "linkSupport": True},
        "references": {"dynamicRegistration": False},
        "documentHighlight": {"dynamicRegistration": False},
        "documentSymbol": {
            "dynamicRegistration": False,
            "hierarchicalDocumentSymbolSupport": True,
        },
        "codeAction": {
            "dynamicRegistration": False,
            "codeActionLiteralSupport": {
                "codeActionKind": {
                    "valueSet": [
                        "",
                        "quickfix",
                        "refactor",
                        "refactor.extract",
                        "refactor.inline",
                        "refactor.rewrite",
                        "source",
                        "source.organizeImports",
                    ]
                }
            },
            "resolveSupport": {"properties": ["edit"]},
        },
        "codeLens": {"dynamicRegistration": False},
        "documentLink": {"dynamicRegistration": False},
        "formatting": {"dynamicRegistration": False},
        "rangeFormatting": {"dynamicRegistration": False},
        "onTypeFormatting": {"dynamicRegistration": False},
        "rename": {"dynamicRegistration": False, "prepareSupport": True},
        "publishDiagnostics": {
            "relatedInformation": True,
            "versionSupport": True,
        },
        "foldingRange": {"dynamicRegistration": False},
        "selectionRange": {"dynamicRegistration": False},
        "linkedEditingRange": {"dynamicRegistration": False},
        "callHierarchy": {"dynamicRegistration": False},
        "typeHierarchy": {"dynamicRegistration": False},
        "semanticTokens": {
            "dynamicRegistration": False,
            "requests": {"full": {"delta": True}, "range": True},
            "tokenTypes": [
                "namespace",
                "type",
                "class",
                "enum",
                "interface",
                "struct",
                "typeParameter",
                "parameter",
                "variable",
                "property",
                "enumMember",
                "event",
                "function",
                "method",
                "macro",
                "keyword",
                "modifier",
                "comment",
                "string",
                "number",
                "regexp",
                "operator",
            ],
            "tokenModifiers": [
                "declaration",
                "definition",
                "readonly",
                "static",
                "deprecated",
                "abstract",
                "async",
                "modification",
                "documentation",
                "defaultLibrary",
            ],
            "formats": ["relative"],
        },
        "inlayHint": {"dynamicRegistration": False},
    },
    "window": {
        "workDoneProgress": True,
    },
}


# ---------------------------------------------------------------------------
# Buffered binary Content-Length reader
# ---------------------------------------------------------------------------


class _BufferedFdReader:
    """Minimal buffered reader over a binary stream ensuring exact byte reads."""

    def __init__(self, stream: Any) -> None:
        self._stream = stream
        self._buf = bytearray()

    def readline_crlf(self) -> bytes:
        """Read until '\\n', returning b'' only on clean EOF with empty buffer."""
        while True:
            nl = self._buf.find(b"\n")
            if nl >= 0:
                line = bytes(self._buf[: nl + 1])
                del self._buf[: nl + 1]
                return line
            chunk = self._read_chunk()
            if not chunk:
                if not self._buf:
                    return b""
                line = bytes(self._buf)
                self._buf.clear()
                return line
            self._buf.extend(chunk)

    def read_exact(self, n: int) -> bytes:
        """Read exactly n bytes, or fewer only if EOF is reached."""
        while len(self._buf) < n:
            chunk = self._read_chunk()
            if not chunk:
                data = bytes(self._buf)
                self._buf.clear()
                return data
            self._buf.extend(chunk)
        data = bytes(self._buf[:n])
        del self._buf[:n]
        return data

    def _read_chunk(self) -> bytes:
        try:
            read1 = getattr(self._stream, "read1", None)
            if callable(read1):
                return read1(65536)
            return self._stream.read(65536)
        except OSError:
            return b""


# ---------------------------------------------------------------------------
# LspClient
# ---------------------------------------------------------------------------


class LspClient:
    """Thread-safe LSP 3.17 JSON-RPC stdio client for bin/java-lsp."""

    def __init__(
        self,
        binary_path: str | Path | None = None,
        storage_dir: str | Path | None = None,
        root_path: str | Path | None = None,
        extra_args: list[str] | None = None,
        env: dict[str, str] | None = None,
        default_timeout: float = 20.0,
        storage_flag: str = "-storage",
        auto_start: bool = False,
        cleanup_storage: bool | None = None,
    ) -> None:
        self.binary_path = Path(binary_path) if binary_path is not None else DEFAULT_BINARY_PATH
        self.root_path = Path(root_path).resolve() if root_path is not None else None
        self.extra_args = list(extra_args or [])
        self.env = dict(env) if env is not None else None
        self.default_timeout = float(default_timeout)
        self.storage_flag = storage_flag

        self._temp_storage: tempfile.TemporaryDirectory[str] | None = None
        if storage_dir is None:
            self._temp_storage = tempfile.TemporaryDirectory(prefix="java_lsp_storage_")
            self.storage_dir = Path(self._temp_storage.name).resolve()
            self._cleanup_storage = True if cleanup_storage is None else cleanup_storage
        else:
            self.storage_dir = Path(storage_dir).resolve()
            self.storage_dir.mkdir(parents=True, exist_ok=True)
            self._cleanup_storage = False if cleanup_storage is None else cleanup_storage

        self._proc: subprocess.Popen[bytes] | None = None
        self._stdout_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None

        self._state_lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._pending: dict[int | str, PendingRequest] = {}
        self._next_id = 1
        self._shutdown_requested = False
        self._crashed_error: ServerCrashedError | None = None

        # Captured streams / diagnostics / notifications
        self.stderr_lines: list[str] = []
        self.log_messages: list[dict[str, Any]] = []
        self.show_messages: list[dict[str, Any]] = []
        self.progress_events: list[dict[str, Any]] = []
        self.notifications: list[dict[str, Any]] = []
        self.server_requests: list[dict[str, Any]] = []

        self._diag_cond = threading.Condition()
        self._diag_seq = 0
        self._diagnostics_by_uri: dict[str, DiagnosticsRecord] = {}
        self._diagnostics_history: list[DiagnosticsRecord] = []

        self._doc_versions: dict[str, int] = {}
        self._doc_texts: dict[str, str] = {}

        self._server_request_handlers: dict[str, Callable[[dict[str, Any]], Any]] = {}
        self.server_capabilities: dict[str, Any] = {}
        self.server_info: dict[str, Any] = {}
        self.initialized_done = False

        if auto_start:
            self.start()

    # ------------------------------------------------------------------
    # Process lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Spawn the LSP server process and reader threads."""
        with self._state_lock:
            if self._proc is not None and self._proc.poll() is None:
                return
            if not self.binary_path.exists():
                raise FileNotFoundError(f"LSP binary not found: {self.binary_path}")

            self._shutdown_requested = False
            self._crashed_error = None
            self.initialized_done = False
            self.server_capabilities = {}
            self.server_info = {}

            self.storage_dir.mkdir(parents=True, exist_ok=True)
            cmd = [str(self.binary_path)]
            if self.storage_flag:
                cmd.extend([self.storage_flag, str(self.storage_dir)])
            cmd.extend(self.extra_args)

            proc_env = os.environ.copy()
            if self.env:
                proc_env.update(self.env)

            self._proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=str(self.root_path) if self.root_path else None,
                env=proc_env,
                bufsize=0,
            )

            self._stdout_thread = threading.Thread(
                target=self._stdout_reader_loop,
                args=(self._proc,),
                name="lsp-stdout-reader",
                daemon=True,
            )
            self._stderr_thread = threading.Thread(
                target=self._stderr_reader_loop,
                args=(self._proc,),
                name="lsp-stderr-reader",
                daemon=True,
            )
            self._stdout_thread.start()
            self._stderr_thread.start()

    def is_alive(self) -> bool:
        """Return True if the server process is currently running."""
        proc = self._proc
        return proc is not None and proc.poll() is None

    def returncode(self) -> int | None:
        """Return subprocess return code if exited, else None."""
        proc = self._proc
        return proc.poll() if proc is not None else None

    def restart(self, clean_storage: bool = False, clear_history: bool = True) -> None:
        """Stop the current server process (if any) and spawn a fresh one."""
        self._stop_process(graceful=True)
        if clean_storage and self.storage_dir.exists():
            shutil.rmtree(self.storage_dir, ignore_errors=True)
            self.storage_dir.mkdir(parents=True, exist_ok=True)
        if clear_history:
            with self._diag_cond:
                self._diagnostics_by_uri.clear()
                self._diagnostics_history.clear()
            with self._state_lock:
                self.stderr_lines.clear()
                self.log_messages.clear()
                self.show_messages.clear()
                self.progress_events.clear()
                self.notifications.clear()
                self.server_requests.clear()
                self._doc_versions.clear()
                self._doc_texts.clear()
        self.start()

    def close(self) -> None:
        """Shut down the server cleanly if running and remove temporary storage."""
        try:
            self._stop_process(graceful=True)
        finally:
            if self._temp_storage is not None and self._cleanup_storage:
                self._temp_storage.cleanup()
                self._temp_storage = None
            elif self._cleanup_storage and self.storage_dir.exists():
                shutil.rmtree(self.storage_dir, ignore_errors=True)

    def _stop_process(self, graceful: bool = True) -> None:
        proc = self._proc
        if proc is not None:
            if graceful and proc.poll() is None:
                try:
                    if self.initialized_done:
                        self.shutdown(timeout=3.0)
                    self.exit()
                except Exception:
                    pass
                try:
                    proc.wait(timeout=3.0)
                except subprocess.TimeoutExpired:
                    pass

            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=2.0)

            for stream in (proc.stdin, proc.stdout, proc.stderr):
                if stream is not None:
                    try:
                        stream.close()
                    except OSError:
                        pass

            if self._stdout_thread is not None and self._stdout_thread.is_alive():
                self._stdout_thread.join(timeout=1.0)
            if self._stderr_thread is not None and self._stderr_thread.is_alive():
                self._stderr_thread.join(timeout=1.0)
            self._proc = None

    def __enter__(self) -> LspClient:
        self.start()
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()

    # ------------------------------------------------------------------
    # JSON-RPC transport
    # ------------------------------------------------------------------

    def _ensure_running(self) -> subprocess.Popen[bytes]:
        if self._proc is None:
            self.start()
        proc = self._proc
        assert proc is not None
        if self._crashed_error is not None:
            raise self._crashed_error
        rc = proc.poll()
        if rc is not None:
            raise ServerCrashedError(
                "LSP server is not running",
                returncode=rc,
                stderr_tail=self.stderr_lines,
            )
        return proc

    def _send_message(self, payload_obj: dict[str, Any]) -> None:
        proc = self._ensure_running()
        assert proc.stdin is not None
        body = json.dumps(payload_obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
        with self._write_lock:
            try:
                proc.stdin.write(header + body)
                proc.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                rc = proc.poll()
                err = ServerCrashedError(
                    f"Failed writing to LSP stdin: {exc}",
                    returncode=rc,
                    stderr_tail=self.stderr_lines,
                )
                self._fail_all_pending(err)
                raise err from exc

    def send_request_async(
        self,
        method: str,
        params: Any = None,
    ) -> PendingRequest:
        """Send a JSON-RPC request and return a PendingRequest handle without blocking."""
        with self._state_lock:
            req_id = self._next_id
            self._next_id += 1
            pending = PendingRequest(req_id, method, self.cancel_request)
            self._pending[req_id] = pending

        msg: dict[str, Any] = {"jsonrpc": "2.0", "id": req_id, "method": method}
        if params is not None:
            msg["params"] = params
        try:
            self._send_message(msg)
        except Exception:
            with self._state_lock:
                self._pending.pop(req_id, None)
            raise
        return pending

    def request(
        self,
        method: str,
        params: Any = None,
        timeout: float | None = None,
    ) -> Any:
        """Send a JSON-RPC request and block until response, error, crash, or timeout."""
        effective_timeout = self.default_timeout if timeout is None else timeout
        pending = self.send_request_async(method, params)
        return pending.wait(timeout=effective_timeout)

    def try_request(
        self,
        method: str,
        params: Any = None,
        timeout: float | None = None,
    ) -> tuple[Any, LspResponseError | None]:
        """Like request(), but returns (result, error) for LspResponseError instead of raising."""
        try:
            return self.request(method, params, timeout=timeout), None
        except LspResponseError as err:
            return None, err

    def notify(self, method: str, params: Any = None) -> None:
        """Send a JSON-RPC notification (no id)."""
        msg: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        self._send_message(msg)

    def cancel_request(self, request_id: int | str) -> None:
        """Send $/cancelRequest notification for an in-flight request."""
        self.notify("$/cancelRequest", {"id": request_id})

    def on_server_request(
        self,
        method: str,
        handler: Callable[[dict[str, Any]], Any],
    ) -> None:
        """Register a custom callback returning the JSON-RPC result for a server->client request."""
        self._server_request_handlers[method] = handler

    # ------------------------------------------------------------------
    # Background reader loops
    # ------------------------------------------------------------------

    def _stderr_reader_loop(self, proc: subprocess.Popen[bytes]) -> None:
        assert proc.stderr is not None
        try:
            for raw_line in iter(proc.stderr.readline, b""):
                text = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
                with self._state_lock:
                    self.stderr_lines.append(text)
        except OSError:
            pass

    def _stdout_reader_loop(self, proc: subprocess.Popen[bytes]) -> None:
        assert proc.stdout is not None
        reader = _BufferedFdReader(proc.stdout)
        try:
            while True:
                headers: dict[str, str] = {}
                while True:
                    line = reader.readline_crlf()
                    if not line:
                        raise EOFError("LSP stdout reached EOF")
                    stripped = line.strip()
                    if not stripped:
                        break
                    if b":" in stripped:
                        k, v = stripped.split(b":", 1)
                        headers[k.decode("ascii", errors="replace").strip().lower()] = (
                            v.decode("ascii", errors="replace").strip()
                        )
                if "content-length" not in headers:
                    continue
                content_length = int(headers["content-length"])
                body = reader.read_exact(content_length)
                if len(body) != content_length:
                    raise EOFError(
                        f"Truncated LSP message body: expected {content_length}, got {len(body)}"
                    )
                msg = json.loads(body.decode("utf-8"))
                self._dispatch_incoming(msg)
        except EOFError:
            # Give stderr thread a brief moment to flush final lines on crash.
            time.sleep(0.05)
            rc = proc.poll()
            if rc is None:
                try:
                    rc = proc.wait(timeout=0.5)
                except subprocess.TimeoutExpired:
                    rc = None
            if not self._shutdown_requested or (rc is not None and rc != 0):
                err = ServerCrashedError(
                    "LSP server exited or closed stdout unexpectedly",
                    returncode=rc,
                    stderr_tail=self.stderr_lines,
                )
                self._fail_all_pending(err)
            else:
                # Clean shutdown: still wake any remaining waiters if any.
                err = ServerCrashedError(
                    "LSP server closed stdout after shutdown",
                    returncode=rc,
                    stderr_tail=self.stderr_lines,
                )
                self._fail_all_pending(err)
        except Exception as exc:
            rc = proc.poll()
            err = ServerCrashedError(
                f"Unexpected error in LSP stdout reader: {exc}",
                returncode=rc,
                stderr_tail=self.stderr_lines,
            )
            self._fail_all_pending(err)

    def _fail_all_pending(self, err: ServerCrashedError) -> None:
        with self._state_lock:
            if not self._shutdown_requested:
                self._crashed_error = err
            pending_list = list(self._pending.values())
            self._pending.clear()
        for pending in pending_list:
            pending._reject(err)
        with self._diag_cond:
            self._diag_cond.notify_all()

    def _dispatch_incoming(self, msg: dict[str, Any]) -> None:
        has_id = "id" in msg and msg["id"] is not None
        has_method = "method" in msg

        if has_id and not has_method:
            req_id = msg["id"]
            with self._state_lock:
                pending = self._pending.pop(req_id, None)
            if pending is None:
                return
            if "error" in msg and msg["error"] is not None:
                err_obj = msg["error"]
                pending._reject(
                    LspResponseError(
                        code=int(err_obj.get("code", -32603)),
                        message=str(err_obj.get("message", "Unknown JSON-RPC error")),
                        data=err_obj.get("data"),
                        method=pending.method,
                    )
                )
            else:
                pending._resolve(msg.get("result"))
            return

        if has_id and has_method:
            self._handle_server_request(msg)
            return

        if has_method:
            self._handle_notification(msg)

    def _handle_server_request(self, msg: dict[str, Any]) -> None:
        method = msg["method"]
        req_id = msg["id"]
        params = msg.get("params")
        with self._state_lock:
            self.server_requests.append(msg)

        handler = self._server_request_handlers.get(method)
        response: dict[str, Any] = {"jsonrpc": "2.0", "id": req_id}
        try:
            if handler is not None:
                response["result"] = handler(msg)
            elif method == "workspace/configuration":
                items = (params or {}).get("items", []) if isinstance(params, dict) else []
                response["result"] = [None for _ in items]
            elif method == "workspace/applyEdit":
                response["result"] = {"applied": False}
            else:
                response["result"] = None
        except Exception as exc:
            response["error"] = {"code": -32603, "message": str(exc)}

        try:
            self._send_message(response)
        except Exception:
            pass

    def _handle_notification(self, msg: dict[str, Any]) -> None:
        method = msg["method"]
        params = msg.get("params") or {}
        with self._state_lock:
            self.notifications.append(msg)
            if method == "window/logMessage":
                self.log_messages.append(params)
            elif method == "window/showMessage":
                self.show_messages.append(params)
            elif method == "$/progress":
                self.progress_events.append(params)

        if method == "textDocument/publishDiagnostics" and isinstance(params, dict):
            uri = str(params.get("uri", ""))
            diags = list(params.get("diagnostics") or [])
            version = params.get("version")
            with self._diag_cond:
                self._diag_seq += 1
                rec = DiagnosticsRecord(
                    uri=uri,
                    diagnostics=diags,
                    version=version,
                    seq=self._diag_seq,
                    timestamp=time.monotonic(),
                )
                self._diagnostics_by_uri[uri] = rec
                self._diagnostics_history.append(rec)
                self._diag_cond.notify_all()

    # ------------------------------------------------------------------
    # Diagnostics helpers
    # ------------------------------------------------------------------

    def diagnostics_seq(self) -> int:
        """Return the current monotonic publishDiagnostics sequence counter."""
        with self._diag_cond:
            return self._diag_seq

    def get_diagnostics(self, uri: str) -> list[dict[str, Any]]:
        """Return the latest diagnostics list for uri (empty list if none received)."""
        with self._diag_cond:
            rec = self._diagnostics_by_uri.get(uri)
            return list(rec.diagnostics) if rec is not None else []

    def get_diagnostics_record(self, uri: str) -> DiagnosticsRecord | None:
        """Return the latest DiagnosticsRecord for uri, or None."""
        with self._diag_cond:
            rec = self._diagnostics_by_uri.get(uri)
            if rec is None:
                return None
            return dataclasses.replace(rec, diagnostics=list(rec.diagnostics))

    def diagnostics_history(self, uri: str | None = None) -> list[DiagnosticsRecord]:
        """Return recorded publishDiagnostics notifications, optionally filtered by uri."""
        with self._diag_cond:
            if uri is None:
                return list(self._diagnostics_history)
            return [r for r in self._diagnostics_history if r.uri == uri]

    def wait_for_diagnostics(
        self,
        uri: str,
        timeout: float = 10.0,
        after_seq: int | None = None,
        min_version: int | None = None,
        predicate: Callable[[list[dict[str, Any]]], bool] | None = None,
        settle_time: float = 0.0,
    ) -> list[dict[str, Any]]:
        """Wait for publishDiagnostics for uri matching optional seq, version, predicate, and settle_time."""
        deadline = time.monotonic() + timeout
        with self._diag_cond:
            matched_rec: DiagnosticsRecord | None = None
            while True:
                if self._crashed_error is not None:
                    raise self._crashed_error
                rec = self._diagnostics_by_uri.get(uri)
                if rec is not None:
                    seq_ok = after_seq is None or rec.seq > after_seq
                    ver_ok = (
                        min_version is None
                        or rec.version is None
                        or rec.version >= min_version
                    )
                    pred_ok = predicate is None or predicate(rec.diagnostics)
                    if seq_ok and ver_ok and pred_ok:
                        matched_rec = rec
                        break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(
                        f"Timed out after {timeout}s waiting for diagnostics on {uri}"
                    )
                self._diag_cond.wait(timeout=min(remaining, 0.25))

            if settle_time > 0:
                last_seq = matched_rec.seq
                settle_deadline = min(deadline, time.monotonic() + settle_time)
                while True:
                    remaining = settle_deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    self._diag_cond.wait(timeout=remaining)
                    if self._crashed_error is not None:
                        raise self._crashed_error
                    rec = self._diagnostics_by_uri.get(uri)
                    if rec is not None and rec.seq > last_seq:
                        last_seq = rec.seq
                        matched_rec = rec
                        settle_deadline = min(deadline, time.monotonic() + settle_time)

            return list(matched_rec.diagnostics)

    def wait_for_diagnostics_quiescent(
        self,
        uri: str,
        timeout: float = 10.0,
        quiet_period: float = 0.25,
        after_seq: int | None = None,
    ) -> list[dict[str, Any]]:
        """Wait for diagnostics on uri to arrive and stabilize for quiet_period seconds."""
        return self.wait_for_diagnostics(
            uri=uri,
            timeout=timeout,
            after_seq=after_seq,
            settle_time=quiet_period,
        )

    # ------------------------------------------------------------------
    # Position normalization helper
    # ------------------------------------------------------------------

    @staticmethod
    def _coerce_pos(
        pos_or_line: dict[str, int] | int,
        character: int | None = None,
    ) -> dict[str, int]:
        if isinstance(pos_or_line, dict):
            return {"line": int(pos_or_line["line"]), "character": int(pos_or_line["character"])}
        if character is None:
            raise TypeError("character must be provided when line is passed as an int")
        return {"line": int(pos_or_line), "character": int(character)}

    def _td_pos_params(
        self,
        uri: str,
        pos_or_line: dict[str, int] | int,
        character: int | None = None,
    ) -> dict[str, Any]:
        return {
            "textDocument": {"uri": uri},
            "position": self._coerce_pos(pos_or_line, character),
        }

    # ------------------------------------------------------------------
    # LSP 3.17 Lifecycle
    # ------------------------------------------------------------------

    def initialize(
        self,
        root_uri: str | None = None,
        root_path: str | Path | None = None,
        capabilities: dict[str, Any] | None = None,
        initialization_options: dict[str, Any] | None = None,
        workspace_folders: list[dict[str, str]] | None = None,
        client_info: dict[str, str] | None = None,
        process_id: int | None = None,
        trace: str = "off",
        timeout: float = 60.0,
    ) -> dict[str, Any]:
        """Send initialize request, record server capabilities, and return full result."""
        if self._proc is None:
            self.start()

        resolved_root_path: str | None = None
        if root_path is not None:
            resolved_root_path = str(Path(root_path).resolve())
        elif self.root_path is not None:
            resolved_root_path = str(self.root_path)

        if root_uri is None and resolved_root_path is not None:
            root_uri = path_to_uri(resolved_root_path)
        elif root_uri is not None and resolved_root_path is None and is_file_uri(root_uri):
            resolved_root_path = str(uri_to_path(root_uri))

        if workspace_folders is None and root_uri is not None:
            name = Path(resolved_root_path).name if resolved_root_path else "workspace"
            workspace_folders = [{"uri": root_uri, "name": name}]

        params: dict[str, Any] = {
            "processId": os.getpid() if process_id is None else process_id,
            "clientInfo": client_info or {"name": "java-lsp-blackbox", "version": "1.0"},
            "rootUri": root_uri,
            "capabilities": (
                copy.deepcopy(DEFAULT_CLIENT_CAPABILITIES)
                if capabilities is None
                else capabilities
            ),
            "trace": trace,
        }
        if resolved_root_path is not None:
            params["rootPath"] = resolved_root_path
        if workspace_folders is not None:
            params["workspaceFolders"] = workspace_folders
        if initialization_options is not None:
            params["initializationOptions"] = initialization_options

        result = self.request("initialize", params, timeout=timeout)
        if isinstance(result, dict):
            self.server_capabilities = dict(result.get("capabilities") or {})
            self.server_info = dict(result.get("serverInfo") or {})
        return result

    def initialized(self) -> None:
        """Send initialized notification."""
        self.notify("initialized", {})
        self.initialized_done = True

    def initialize_and_ready(
        self,
        root_uri: str | None = None,
        root_path: str | Path | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Convenience helper: send initialize followed by initialized."""
        res = self.initialize(root_uri=root_uri, root_path=root_path, **kwargs)
        self.initialized()
        return res

    def shutdown(self, timeout: float = 5.0) -> Any:
        """Send shutdown request."""
        self._shutdown_requested = True
        return self.request("shutdown", None, timeout=timeout)

    def exit(self) -> None:
        """Send exit notification."""
        self._shutdown_requested = True
        self.notify("exit", None)

    # ------------------------------------------------------------------
    # Document synchronization
    # ------------------------------------------------------------------

    def did_open(
        self,
        uri: str,
        text: str,
        language_id: str = "java",
        version: int = 1,
    ) -> int:
        """Send textDocument/didOpen and return diagnostics sequence number prior to send."""
        seq = self.diagnostics_seq()
        self._doc_versions[uri] = version
        self._doc_texts[uri] = text
        self.notify(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": uri,
                    "languageId": language_id,
                    "version": version,
                    "text": text,
                }
            },
        )
        return seq

    def did_change(
        self,
        uri: str,
        text: str | None = None,
        changes: list[dict[str, Any]] | None = None,
        version: int | None = None,
    ) -> int:
        """Send textDocument/didChange with full text or incremental range changes."""
        if text is None and changes is None:
            raise ValueError("Either text or changes must be provided to did_change")
        if version is None:
            version = self._doc_versions.get(uri, 0) + 1
        self._doc_versions[uri] = version

        content_changes: list[dict[str, Any]]
        if changes is not None:
            content_changes = changes
            if uri in self._doc_texts:
                cur = self._doc_texts[uri]
                for ch in changes:
                    if "range" in ch:
                        cur = apply_text_edits(
                            cur, [{"range": ch["range"], "newText": ch["text"]}]
                        )
                    else:
                        cur = ch["text"]
                self._doc_texts[uri] = cur
        else:
            assert text is not None
            content_changes = [{"text": text}]
            self._doc_texts[uri] = text

        seq = self.diagnostics_seq()
        self.notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": uri, "version": version},
                "contentChanges": content_changes,
            },
        )
        return seq

    def did_save(self, uri: str, text: str | None = None) -> None:
        """Send textDocument/didSave."""
        params: dict[str, Any] = {"textDocument": {"uri": uri}}
        if text is not None:
            params["text"] = text
        self.notify("textDocument/didSave", params)

    def did_close(self, uri: str) -> None:
        """Send textDocument/didClose."""
        self._doc_versions.pop(uri, None)
        self._doc_texts.pop(uri, None)
        self.notify("textDocument/didClose", {"textDocument": {"uri": uri}})

    # ------------------------------------------------------------------
    # LSP 3.17 Language Features
    # ------------------------------------------------------------------

    def completion(
        self,
        uri: str,
        pos_or_line: dict[str, int] | int,
        character: int | None = None,
        trigger_kind: int = 1,
        trigger_character: str | None = None,
        timeout: float | None = None,
    ) -> Any:
        params = self._td_pos_params(uri, pos_or_line, character)
        ctx: dict[str, Any] = {"triggerKind": trigger_kind}
        if trigger_character is not None:
            ctx["triggerCharacter"] = trigger_character
        params["context"] = ctx
        return self.request("textDocument/completion", params, timeout=timeout)

    def completion_resolve(
        self,
        item: dict[str, Any],
        timeout: float | None = None,
    ) -> Any:
        return self.request("completionItem/resolve", item, timeout=timeout)

    def hover(
        self,
        uri: str,
        pos_or_line: dict[str, int] | int,
        character: int | None = None,
        timeout: float | None = None,
    ) -> Any:
        return self.request(
            "textDocument/hover",
            self._td_pos_params(uri, pos_or_line, character),
            timeout=timeout,
        )

    def definition(
        self,
        uri: str,
        pos_or_line: dict[str, int] | int,
        character: int | None = None,
        timeout: float | None = None,
    ) -> Any:
        return self.request(
            "textDocument/definition",
            self._td_pos_params(uri, pos_or_line, character),
            timeout=timeout,
        )

    def declaration(
        self,
        uri: str,
        pos_or_line: dict[str, int] | int,
        character: int | None = None,
        timeout: float | None = None,
    ) -> Any:
        return self.request(
            "textDocument/declaration",
            self._td_pos_params(uri, pos_or_line, character),
            timeout=timeout,
        )

    def type_definition(
        self,
        uri: str,
        pos_or_line: dict[str, int] | int,
        character: int | None = None,
        timeout: float | None = None,
    ) -> Any:
        return self.request(
            "textDocument/typeDefinition",
            self._td_pos_params(uri, pos_or_line, character),
            timeout=timeout,
        )

    def implementation(
        self,
        uri: str,
        pos_or_line: dict[str, int] | int,
        character: int | None = None,
        timeout: float | None = None,
    ) -> Any:
        return self.request(
            "textDocument/implementation",
            self._td_pos_params(uri, pos_or_line, character),
            timeout=timeout,
        )

    def references(
        self,
        uri: str,
        pos_or_line: dict[str, int] | int,
        character: int | None = None,
        include_declaration: bool = True,
        timeout: float | None = None,
    ) -> Any:
        params = self._td_pos_params(uri, pos_or_line, character)
        params["context"] = {"includeDeclaration": include_declaration}
        return self.request("textDocument/references", params, timeout=timeout)

    def document_symbol(self, uri: str, timeout: float | None = None) -> Any:
        return self.request(
            "textDocument/documentSymbol",
            {"textDocument": {"uri": uri}},
            timeout=timeout,
        )

    def workspace_symbol(self, query: str = "", timeout: float | None = None) -> Any:
        return self.request("workspace/symbol", {"query": query}, timeout=timeout)

    def signature_help(
        self,
        uri: str,
        pos_or_line: dict[str, int] | int,
        character: int | None = None,
        trigger_kind: int = 1,
        trigger_character: str | None = None,
        is_retrigger: bool = False,
        active_signature_help: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> Any:
        params = self._td_pos_params(uri, pos_or_line, character)
        ctx: dict[str, Any] = {
            "triggerKind": trigger_kind,
            "isRetrigger": is_retrigger,
        }
        if trigger_character is not None:
            ctx["triggerCharacter"] = trigger_character
        if active_signature_help is not None:
            ctx["activeSignatureHelp"] = active_signature_help
        params["context"] = ctx
        return self.request("textDocument/signatureHelp", params, timeout=timeout)

    def code_action(
        self,
        uri: str,
        range_or_start: dict[str, Any],
        end: dict[str, int] | None = None,
        diagnostics: list[dict[str, Any]] | None = None,
        only: list[str] | None = None,
        trigger_kind: int | None = None,
        timeout: float | None = None,
    ) -> Any:
        if "start" in range_or_start and "end" in range_or_start:
            rng = range_or_start
        else:
            rng = {
                "start": range_or_start,
                "end": end if end is not None else range_or_start,
            }
        ctx: dict[str, Any] = {"diagnostics": list(diagnostics or [])}
        if only is not None:
            ctx["only"] = only
        if trigger_kind is not None:
            ctx["triggerKind"] = trigger_kind
        return self.request(
            "textDocument/codeAction",
            {
                "textDocument": {"uri": uri},
                "range": rng,
                "context": ctx,
            },
            timeout=timeout,
        )

    def code_action_resolve(
        self,
        action: dict[str, Any],
        timeout: float | None = None,
    ) -> Any:
        return self.request("codeAction/resolve", action, timeout=timeout)

    def rename(
        self,
        uri: str,
        pos_or_line: dict[str, int] | int,
        character_or_new_name: int | str | None = None,
        new_name: str | None = None,
        timeout: float | None = None,
    ) -> Any:
        if isinstance(pos_or_line, dict):
            pos = self._coerce_pos(pos_or_line)
            resolved_name = (
                new_name
                if new_name is not None
                else (
                    character_or_new_name
                    if isinstance(character_or_new_name, str)
                    else ""
                )
            )
        else:
            if not isinstance(character_or_new_name, int):
                raise TypeError("character must be int when line is passed as int")
            pos = self._coerce_pos(pos_or_line, character_or_new_name)
            resolved_name = "" if new_name is None else new_name
        return self.request(
            "textDocument/rename",
            {
                "textDocument": {"uri": uri},
                "position": pos,
                "newName": resolved_name,
            },
            timeout=timeout,
        )

    def prepare_rename(
        self,
        uri: str,
        pos_or_line: dict[str, int] | int,
        character: int | None = None,
        timeout: float | None = None,
    ) -> Any:
        return self.request(
            "textDocument/prepareRename",
            self._td_pos_params(uri, pos_or_line, character),
            timeout=timeout,
        )

    def formatting(
        self,
        uri: str,
        tab_size: int = 2,
        insert_spaces: bool = True,
        options: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> Any:
        opts = {"tabSize": tab_size, "insertSpaces": insert_spaces}
        if options:
            opts.update(options)
        return self.request(
            "textDocument/formatting",
            {"textDocument": {"uri": uri}, "options": opts},
            timeout=timeout,
        )

    def range_formatting(
        self,
        uri: str,
        range_dict: dict[str, Any],
        tab_size: int = 2,
        insert_spaces: bool = True,
        options: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> Any:
        opts = {"tabSize": tab_size, "insertSpaces": insert_spaces}
        if options:
            opts.update(options)
        return self.request(
            "textDocument/rangeFormatting",
            {"textDocument": {"uri": uri}, "range": range_dict, "options": opts},
            timeout=timeout,
        )

    def on_type_formatting(
        self,
        uri: str,
        pos_or_line: dict[str, int] | int,
        character_or_ch: int | str | None = None,
        ch: str = "}",
        tab_size: int = 2,
        insert_spaces: bool = True,
        options: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> Any:
        if isinstance(pos_or_line, dict):
            pos = self._coerce_pos(pos_or_line)
            resolved_ch = character_or_ch if isinstance(character_or_ch, str) else ch
        else:
            if not isinstance(character_or_ch, int):
                raise TypeError("character must be int when line is passed as int")
            pos = self._coerce_pos(pos_or_line, character_or_ch)
            resolved_ch = ch
        opts = {"tabSize": tab_size, "insertSpaces": insert_spaces}
        if options:
            opts.update(options)
        return self.request(
            "textDocument/onTypeFormatting",
            {
                "textDocument": {"uri": uri},
                "position": pos,
                "ch": resolved_ch,
                "options": opts,
            },
            timeout=timeout,
        )

    def semantic_tokens_full(self, uri: str, timeout: float | None = None) -> Any:
        return self.request(
            "textDocument/semanticTokens/full",
            {"textDocument": {"uri": uri}},
            timeout=timeout,
        )

    def semantic_tokens_delta(
        self,
        uri: str,
        previous_result_id: str,
        timeout: float | None = None,
    ) -> Any:
        return self.request(
            "textDocument/semanticTokens/full/delta",
            {
                "textDocument": {"uri": uri},
                "previousResultId": previous_result_id,
            },
            timeout=timeout,
        )

    def inlay_hint(
        self,
        uri: str,
        range_dict: dict[str, Any],
        timeout: float | None = None,
    ) -> Any:
        return self.request(
            "textDocument/inlayHint",
            {"textDocument": {"uri": uri}, "range": range_dict},
            timeout=timeout,
        )

    def folding_range(self, uri: str, timeout: float | None = None) -> Any:
        return self.request(
            "textDocument/foldingRange",
            {"textDocument": {"uri": uri}},
            timeout=timeout,
        )

    def selection_range(
        self,
        uri: str,
        positions: dict[str, int] | list[dict[str, int]],
        timeout: float | None = None,
    ) -> Any:
        pos_list = [positions] if isinstance(positions, dict) else list(positions)
        return self.request(
            "textDocument/selectionRange",
            {"textDocument": {"uri": uri}, "positions": pos_list},
            timeout=timeout,
        )

    def document_highlight(
        self,
        uri: str,
        pos_or_line: dict[str, int] | int,
        character: int | None = None,
        timeout: float | None = None,
    ) -> Any:
        return self.request(
            "textDocument/documentHighlight",
            self._td_pos_params(uri, pos_or_line, character),
            timeout=timeout,
        )

    def call_hierarchy_prepare(
        self,
        uri: str,
        pos_or_line: dict[str, int] | int,
        character: int | None = None,
        timeout: float | None = None,
    ) -> Any:
        return self.request(
            "textDocument/prepareCallHierarchy",
            self._td_pos_params(uri, pos_or_line, character),
            timeout=timeout,
        )

    def call_hierarchy_incoming(
        self,
        item: dict[str, Any],
        timeout: float | None = None,
    ) -> Any:
        return self.request("callHierarchy/incomingCalls", {"item": item}, timeout=timeout)

    def call_hierarchy_outgoing(
        self,
        item: dict[str, Any],
        timeout: float | None = None,
    ) -> Any:
        return self.request("callHierarchy/outgoingCalls", {"item": item}, timeout=timeout)

    def type_hierarchy_prepare(
        self,
        uri: str,
        pos_or_line: dict[str, int] | int,
        character: int | None = None,
        timeout: float | None = None,
    ) -> Any:
        return self.request(
            "textDocument/prepareTypeHierarchy",
            self._td_pos_params(uri, pos_or_line, character),
            timeout=timeout,
        )

    def type_hierarchy_supertypes(
        self,
        item: dict[str, Any],
        timeout: float | None = None,
    ) -> Any:
        return self.request("typeHierarchy/supertypes", {"item": item}, timeout=timeout)

    def type_hierarchy_subtypes(
        self,
        item: dict[str, Any],
        timeout: float | None = None,
    ) -> Any:
        return self.request("typeHierarchy/subtypes", {"item": item}, timeout=timeout)

    def code_lens(self, uri: str, timeout: float | None = None) -> Any:
        return self.request(
            "textDocument/codeLens",
            {"textDocument": {"uri": uri}},
            timeout=timeout,
        )

    def document_link(self, uri: str, timeout: float | None = None) -> Any:
        return self.request(
            "textDocument/documentLink",
            {"textDocument": {"uri": uri}},
            timeout=timeout,
        )

    def linked_editing_range(
        self,
        uri: str,
        pos_or_line: dict[str, int] | int,
        character: int | None = None,
        timeout: float | None = None,
    ) -> Any:
        return self.request(
            "textDocument/linkedEditingRange",
            self._td_pos_params(uri, pos_or_line, character),
            timeout=timeout,
        )

    # ------------------------------------------------------------------
    # Workspace file operations & watched files
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_rename_files(
        files: list[dict[str, str]] | list[tuple[str, str]] | tuple[str, str],
    ) -> list[dict[str, str]]:
        if isinstance(files, tuple) and len(files) == 2:
            return [{"oldUri": files[0], "newUri": files[1]}]
        out: list[dict[str, str]] = []
        for entry in files:
            if isinstance(entry, dict):
                out.append({"oldUri": entry["oldUri"], "newUri": entry["newUri"]})
            else:
                out.append({"oldUri": entry[0], "newUri": entry[1]})
        return out

    @staticmethod
    def _normalize_uri_files(files: list[dict[str, str]] | list[str] | str) -> list[dict[str, str]]:
        if isinstance(files, str):
            return [{"uri": files}]
        out: list[dict[str, str]] = []
        for entry in files:
            if isinstance(entry, dict):
                out.append({"uri": entry["uri"]})
            else:
                out.append({"uri": str(entry)})
        return out

    def workspace_did_rename_files(
        self,
        files: list[dict[str, str]] | list[tuple[str, str]] | tuple[str, str],
    ) -> None:
        self.notify(
            "workspace/didRenameFiles",
            {"files": self._normalize_rename_files(files)},
        )

    def workspace_will_rename_files(
        self,
        files: list[dict[str, str]] | list[tuple[str, str]] | tuple[str, str],
        timeout: float | None = None,
    ) -> Any:
        return self.request(
            "workspace/willRenameFiles",
            {"files": self._normalize_rename_files(files)},
            timeout=timeout,
        )

    def workspace_did_create_files(
        self,
        files: list[dict[str, str]] | list[str] | str,
    ) -> None:
        self.notify(
            "workspace/didCreateFiles",
            {"files": self._normalize_uri_files(files)},
        )

    def workspace_did_delete_files(
        self,
        files: list[dict[str, str]] | list[str] | str,
    ) -> None:
        self.notify(
            "workspace/didDeleteFiles",
            {"files": self._normalize_uri_files(files)},
        )

    def workspace_did_change_watched_files(
        self,
        changes: list[dict[str, Any]],
    ) -> None:
        """Send workspace/didChangeWatchedFiles (change type: 1=Created, 2=Changed, 3=Deleted)."""
        self.notify("workspace/didChangeWatchedFiles", {"changes": changes})

    # ------------------------------------------------------------------
    # JDTLS custom Java requests
    # ------------------------------------------------------------------

    def java_overridable_methods(
        self,
        uri: str,
        pos_or_line: dict[str, int] | int | None = None,
        character: int | None = None,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> Any:
        """Send custom JDTLS java/overridableMethods request."""
        req_params: dict[str, Any] = (
            dict(params) if params is not None else {"textDocument": {"uri": uri}}
        )
        if pos_or_line is not None and "position" not in req_params:
            req_params["position"] = self._coerce_pos(pos_or_line, character)
        return self.request("java/overridableMethods", req_params, timeout=timeout)

    def java_override_methods(
        self,
        uri: str,
        methods: list[dict[str, Any]] | None = None,
        pos_or_line: dict[str, int] | int | None = None,
        character: int | None = None,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> Any:
        """Send custom JDTLS java/overrideMethods request."""
        req_params: dict[str, Any] = (
            dict(params) if params is not None else {"textDocument": {"uri": uri}}
        )
        if methods is not None and "methods" not in req_params and "overridableMethods" not in req_params:
            req_params["methods"] = methods
        if pos_or_line is not None and "position" not in req_params:
            req_params["position"] = self._coerce_pos(pos_or_line, character)
        return self.request("java/overrideMethods", req_params, timeout=timeout)
