"""CLI entry point.

Exit codes
----------
0  every rule proven (when --self-test) and nothing found
1  findings and/or breaking registry drift detected
2  usage error, unreadable pack, or an unproven rule
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .engine import audit, dump, self_test, version_matrix
from .pack import Pack

USAGE = 2


def _fail(message: str, as_json: bool) -> int:
    if as_json:
        print(json.dumps({"ok": False, "error": message}))
    else:
        print(f"error: {message}", file=sys.stderr)
    return USAGE


def _render_report(rep: dict, path: str) -> str:
    lines = [f"pack: {path}",
             f"rules: {rep['rules_run']}/{rep['rules_total']} ran"
             + (f" (skipped: {', '.join(rep['rules_skipped'])})" if rep["rules_skipped"] else ""),
             f"skills: {len(rep['skills'])}  plugins: {len(rep['plugins'])}",
             f"findings: {len(rep['findings'])} ({rep['errors']} error, {rep['warnings']} warning)"]
    for f in rep["findings"]:
        lines.append(f"  [{f['severity']:7}] {f['rule']} {f['where']}: {f['message']}")
    return "\n".join(lines)


def _render_selftest(st: dict) -> str:
    lines = [f"mutation self-test: {st['proven']}/{st['rules']} rules proven",
             f"clean specimen findings: {len(st['clean_spec_findings'])}"]
    for r in st["results"]:
        mark = "ok  " if r["proven"] else "FAIL"
        extra = f"  collateral={','.join(r['collateral'])}" if r["collateral"] else ""
        lines.append(f"  {mark} {r['rule']} <- {r['mutation']}{extra}")
    if st["unproven"]:
        lines.append("unproven rules: " + ", ".join(st["unproven"]))
    return "\n".join(lines)


def _render_matrix(mx: dict) -> str:
    lines = ["version matrix", ""]
    head = "rule    " + "".join(f"{v['name'][:10]:>12}" for v in mx["versions"])
    lines.append(head)
    for rid in mx["rule_ids"]:
        row = mx["matrix"][rid]
        if not any(row):
            continue
        lines.append(f"{rid:<8}" + "".join(f"{c:>12}" for c in row))
    lines.append("")
    for v in mx["versions"]:
        lines.append(f"  {v['name']}: version={v['version']} skills={v['skills']} "
                     f"plugins={v['plugins']} errors={v['errors']} warnings={v['warnings']}")
    for d in mx["drift"]:
        lines.append(f"  drift {d['from']} -> {d['to']}: "
                     f"+skills={d['added_skills']} -skills={d['removed_skills']} "
                     f"renamed={d['renamed_skills']} +plugins={d['added_plugins']} "
                     f"-plugins={d['removed_plugins']} version={d['version']}")
    lines.append(f"breaking changes: {mx['breaking_changes']}  findings: {mx['findings_total']}")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="skills-marketplace-mutate",
        description="Audit an Agent Skills marketplace registry + SKILL.md frontmatter, "
                    "with rule-level mutation self-proof and a multi-version drift matrix.")
    ap.add_argument("--root", default=None, help="pack root containing .claude-plugin/marketplace.json")
    ap.add_argument("--versions", default=None,
                    help="comma-separated snapshot dirs (each with marketplace.json + manifest.txt)")
    ap.add_argument("--self-test", action="store_true", help="inject one defect per rule and require it to fire")
    ap.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    ap.add_argument("--errors-only", action="store_true", help="count only error-severity findings as failures")
    args = ap.parse_args(argv)

    if not args.root and not args.versions and not args.self_test:
        return _fail("nothing to do: pass --root, --versions and/or --self-test", args.json)

    rc = 0
    payload = {}

    if args.self_test:
        st = self_test()
        payload["self_test"] = st
        if not st["ok"]:
            rc = USAGE
        if not args.json:
            print(_render_selftest(st))

    if args.root:
        root = Path(args.root)
        if not root.is_dir():
            return _fail(f"--root {root} is not a directory", args.json)
        pack = Pack.from_dir(root)
        if pack.marketplace is None:
            payload["root"] = {"path": str(root), "error": pack.marketplace_error}
            if args.json:
                payload["exit_code"] = USAGE
                print(dump(payload))
            else:
                print(f"error: {root}/.claude-plugin/marketplace.json: {pack.marketplace_error}",
                      file=sys.stderr)
            return USAGE
        rep = audit(pack)
        payload["root"] = {"path": str(root), "report": rep}
        bad = rep["errors"] if args.errors_only else rep["errors"] + rep["warnings"]
        if bad:
            rc = max(rc, 1)
        if not args.json:
            print(_render_report(rep, str(root)))

    if args.versions:
        dirs = [Path(x.strip()) for x in args.versions.split(",") if x.strip()]
        missing = [d for d in dirs if not d.is_dir()]
        if missing:
            return _fail(f"version dir(s) not found: {', '.join(str(m) for m in missing)}", args.json)
        mx = version_matrix(dirs)
        payload["versions"] = mx
        bad = mx["breaking_changes"]
        if not args.errors_only:
            bad += mx["findings_total"]
        if bad:
            rc = max(rc, 1)
        if not args.json:
            print(_render_matrix(mx))

    if args.json:
        payload["exit_code"] = rc
        print(dump(payload))
    return rc


if __name__ == "__main__":
    sys.exit(main())
