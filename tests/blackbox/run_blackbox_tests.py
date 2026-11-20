#!/usr/bin/env python3
"""Unified black-box test runner for tests/blackbox/test_*.py suites."""

from __future__ import annotations

import argparse
import collections
import dataclasses
import json
import re
import sys
import time
import traceback
import unittest
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
BLACKBOX_DIR = Path(__file__).resolve().parent
GAP_TAG_RE = re.compile(r"\[([A-Z][A-Z0-9_-]*-\d+)\]")


@dataclasses.dataclass
class TestOutcome:
    """Classified outcome for a single black-box unittest test case."""

    test_id: str
    module: str
    test_class: str
    method: str
    status: str  # PASS | GAP_FAIL | HARNESS_ERROR | SKIP | XFAIL | UXPASS
    gap_ids: list[str]
    duration_ms: float
    message: str = ""
    traceback_text: str = ""


def _extract_gap_ids(test: unittest.TestCase, detail_text: str = "") -> list[str]:
    """Extract Gap ID tags such as [NAV-01] from docstring, class doc, or failure text."""
    found: list[str] = []
    seen: set[str] = set()

    def _scan(text: str | None) -> None:
        if not text:
            return
        for tag in GAP_TAG_RE.findall(text):
            if tag not in seen:
                seen.add(tag)
                found.append(tag)

    _scan(detail_text)
    method_name = getattr(test, "_testMethodName", "")
    if method_name:
        method_obj = getattr(test, method_name, None)
        _scan(getattr(method_obj, "__doc__", None))
    _scan(test.__class__.__doc__)
    _scan(method_name)
    return found


def _test_parts(test: unittest.TestCase) -> tuple[str, str, str, str]:
    test_id = test.id()
    module = test.__class__.__module__
    if module.startswith("tests.blackbox."):
        short_mod = module[len("tests.blackbox.") :]
    else:
        short_mod = module
    return test_id, short_mod, test.__class__.__name__, getattr(test, "_testMethodName", test_id)


def _format_exc(err: tuple[Any, Any, Any]) -> tuple[str, str]:
    exc_type, exc_val, exc_tb = err
    tb_text = "".join(traceback.format_exception(exc_type, exc_val, exc_tb))
    msg = str(exc_val) if exc_val is not None else exc_type.__name__
    first_line = msg.splitlines()[0] if msg else exc_type.__name__
    return first_line, tb_text


class ClassifiedTestResult(unittest.TestResult):
    """TestResult classifying AssertionError as GAP_FAIL and non-assertion errors as HARNESS_ERROR."""

    def __init__(self, verbose: bool = False) -> None:
        super().__init__()
        self.verbose = verbose
        self.outcomes: list[TestOutcome] = []
        self._start_times: dict[str, float] = {}

    def startTest(self, test: unittest.TestCase) -> None:
        super().startTest(test)
        self._start_times[test.id()] = time.monotonic()

    def _elapsed_ms(self, test: unittest.TestCase) -> float:
        started = self._start_times.pop(test.id(), time.monotonic())
        return round((time.monotonic() - started) * 1000.0, 2)

    def _record(
        self,
        test: unittest.TestCase,
        status: str,
        message: str = "",
        tb_text: str = "",
    ) -> None:
        test_id, module, cls_name, method_name = _test_parts(test)
        elapsed = self._elapsed_ms(test)
        gap_ids = _extract_gap_ids(test, f"{message}\n{tb_text}")
        outcome = TestOutcome(
            test_id=test_id,
            module=module,
            test_class=cls_name,
            method=method_name,
            status=status,
            gap_ids=gap_ids,
            duration_ms=elapsed,
            message=message,
            traceback_text=tb_text,
        )
        self.outcomes.append(outcome)
        if self.verbose:
            tag_str = f" [{','.join(gap_ids)}]" if gap_ids else ""
            print(f"  [{status:13s}] {module}.{cls_name}.{method_name}{tag_str} ({elapsed:.1f}ms)")
            sys.stdout.flush()

    def addSuccess(self, test: unittest.TestCase) -> None:
        super().addSuccess(test)
        self._record(test, "PASS")

    def addFailure(self, test: unittest.TestCase, err: Any) -> None:
        super().addFailure(test, err)
        msg, tb_text = _format_exc(err)
        self._record(test, "GAP_FAIL", message=msg, tb_text=tb_text)

    def addError(self, test: unittest.TestCase, err: Any) -> None:
        super().addError(test, err)
        msg, tb_text = _format_exc(err)
        self._record(test, "HARNESS_ERROR", message=msg, tb_text=tb_text)

    def addSkip(self, test: unittest.TestCase, reason: str) -> None:
        super().addSkip(test, reason)
        self._record(test, "SKIP", message=reason)

    def addExpectedFailure(self, test: unittest.TestCase, err: Any) -> None:
        super().addExpectedFailure(test, err)
        msg, tb_text = _format_exc(err)
        self._record(test, "XFAIL", message=msg, tb_text=tb_text)

    def addUnexpectedSuccess(self, test: unittest.TestCase) -> None:
        super().addUnexpectedSuccess(test)
        self._record(test, "UXPASS", message="Expected failure unexpectedly passed")


def _iter_tests(suite: unittest.TestSuite) -> list[unittest.TestCase]:
    tests: list[unittest.TestCase] = []
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            tests.extend(_iter_tests(item))
        else:
            tests.append(item)
    return tests


def discover_tests(filter_pattern: str | None = None) -> unittest.TestSuite:
    """Discover all tests/blackbox/test_*.py suites, optionally filtered by regex."""
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))

    loader = unittest.TestLoader()
    discovered = loader.discover(
        start_dir=str(BLACKBOX_DIR),
        pattern="test_*.py",
        top_level_dir=str(REPO_ROOT),
    )

    if not filter_pattern:
        return discovered

    regex = re.compile(filter_pattern)
    filtered = unittest.TestSuite()
    for test in _iter_tests(discovered):
        gap_ids = _extract_gap_ids(test)
        haystack = " ".join([test.id(), *gap_ids])
        if regex.search(haystack):
            filtered.addTest(test)
    return filtered


def _print_summary(outcomes: list[TestOutcome], summary_only: bool = False) -> dict[str, Any]:
    counts = collections.Counter(o.status for o in outcomes)
    total = len(outcomes)

    if not summary_only:
        failures = [o for o in outcomes if o.status in ("GAP_FAIL", "HARNESS_ERROR")]
        if failures:
            print("\n=== FAILURE & ERROR DETAILS ===")
            for o in failures:
                tags = f" {' '.join(f'[{g}]' for g in o.gap_ids)}" if o.gap_ids else ""
                print(f"\n[{o.status}]{tags} {o.module}.{o.test_class}.{o.method}")
                if o.message:
                    print(f"  Reason: {o.message}")
                if o.traceback_text:
                    for line in o.traceback_text.rstrip().splitlines():
                        print(f"  | {line}")

    # Module summary table
    by_module: dict[str, collections.Counter[str]] = collections.defaultdict(collections.Counter)
    for o in outcomes:
        by_module[o.module][o.status] += 1

    print("\n=== MODULE SUMMARY ===")
    header = f"{'Module':<34} {'Total':>6} {'PASS':>6} {'GAP_FAIL':>9} {'HARNESS_ERR':>11} {'SKIP':>6}"
    print(header)
    print("-" * len(header))
    for mod in sorted(by_module):
        c = by_module[mod]
        mod_total = sum(c.values())
        print(
            f"{mod:<34} {mod_total:>6} {c['PASS']:>6} {c['GAP_FAIL']:>9} "
            f"{c['HARNESS_ERROR']:>11} {c['SKIP']:>6}"
        )
    print("-" * len(header))
    print(
        f"{'TOTAL':<34} {total:>6} {counts['PASS']:>6} {counts['GAP_FAIL']:>9} "
        f"{counts['HARNESS_ERROR']:>11} {counts['SKIP']:>6}"
    )

    # Gap tag summary table
    by_gap: dict[str, list[TestOutcome]] = collections.defaultdict(list)
    for o in outcomes:
        for gid in o.gap_ids:
            by_gap[gid].append(o)

    if by_gap:
        print("\n=== GAP TAG SUMMARY ===")
        gheader = f"{'Gap ID':<16} {'Status':<13} {'Count':>5}  {'Tests'}"
        print(gheader)
        print("-" * 78)
        for gid in sorted(by_gap):
            items = by_gap[gid]
            statuses = {i.status for i in items}
            if "HARNESS_ERROR" in statuses:
                agg = "HARNESS_ERROR"
            elif "GAP_FAIL" in statuses:
                agg = "GAP_FAIL"
            elif statuses == {"SKIP"}:
                agg = "SKIP"
            else:
                agg = "PASS"
            names = ", ".join(f"{i.module}.{i.method}" for i in items[:3])
            if len(items) > 3:
                names += f" (+{len(items) - 3} more)"
            print(f"{gid:<16} {agg:<13} {len(items):>5}  {names}")

    return {
        "total": total,
        "pass": counts["PASS"],
        "gap_fail": counts["GAP_FAIL"],
        "harness_error": counts["HARNESS_ERROR"],
        "skip": counts["SKIP"],
        "xfail": counts["XFAIL"],
        "uxpass": counts["UXPASS"],
        "by_module": {mod: dict(c) for mod, c in sorted(by_module.items())},
        "by_gap_id": {
            gid: [
                {"test_id": o.test_id, "status": o.status, "message": o.message}
                for o in items
            ]
            for gid, items in sorted(by_gap.items())
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run black-box LSP tests under tests/blackbox/test_*.py."
    )
    parser.add_argument(
        "--filter",
        dest="filter_pattern",
        default=None,
        help="Regex filter matched against test ID and Gap ID tags (e.g. 'spring_demo|NAV-01').",
    )
    parser.add_argument(
        "--json-out",
        default=None,
        help="Optional path to write structured JSON test results.",
    )
    parser.add_argument(
        "--summary-only",
        action="store_true",
        help="Suppress individual failure tracebacks and print only summary tables.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero on any GAP_FAIL or HARNESS_ERROR (default behavior).",
    )
    parser.add_argument(
        "--check-harness-only",
        "--allow-gaps",
        dest="check_harness_only",
        action="store_true",
        help="Exit 0 when HARNESS_ERROR == 0 even if GAP_FAIL > 0.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Print each test outcome live as it completes.",
    )
    parser.add_argument(
        "-x",
        "--fail-fast",
        action="store_true",
        help="Stop on the first failure or error.",
    )
    args = parser.parse_args(argv)

    suite = discover_tests(args.filter_pattern)
    test_count = suite.countTestCases()
    print(f"Discovered {test_count} black-box test(s) in {BLACKBOX_DIR}")

    result = ClassifiedTestResult(verbose=args.verbose)
    result.failfast = args.fail_fast
    started = time.monotonic()
    suite.run(result)
    elapsed_sec = round(time.monotonic() - started, 3)

    summary = _print_summary(result.outcomes, summary_only=args.summary_only)
    print(f"\nElapsed: {elapsed_sec:.3f}s")

    if args.json_out:
        out_path = Path(args.json_out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "elapsed_sec": elapsed_sec,
            "summary": summary,
            "tests": [dataclasses.asdict(o) for o in result.outcomes],
        }
        out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"Wrote JSON report: {out_path}")

    if summary["harness_error"] > 0:
        return 2
    if not args.check_harness_only and summary["gap_fail"] > 0:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
