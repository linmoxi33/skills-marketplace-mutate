"""Audit, mutation self-test, and multi-version drift matrix."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Dict, List, Optional

from .pack import Pack
from .rules import RULES, Rule, clean_spec


def _rule_scopes(pack: Pack) -> Dict[str, str]:
    """Return {rule_id: "ran"|"skipped"} for this pack shape."""
    # A manifest-only snapshot has no SKILL.md bodies, so only registry rules run.
    bodyless = bool(pack.skills) and all(not s.md for s in pack.skills.values())
    out = {}
    for r in RULES:
        out[r.id] = "skipped" if (bodyless and r.scope == "full") else "ran"
    return out


def audit(pack: Pack) -> dict:
    status = _rule_scopes(pack)
    findings = []
    for r in RULES:
        if status[r.id] == "skipped":
            continue
        findings.extend(r.run(pack))
    findings.sort(key=lambda f: (f.where, f.rule))
    return {
        "rules_total": len(RULES),
        "rules_run": sum(1 for v in status.values() if v == "ran"),
        "rules_skipped": sorted(k for k, v in status.items() if v == "skipped"),
        "findings": [f.to_dict() for f in findings],
        "errors": sum(1 for f in findings if f.severity == "error"),
        "warnings": sum(1 for f in findings if f.severity == "warning"),
        "skills": sorted(pack.skills),
        "plugins": [p.get("name") for p in pack.plugins()],
    }


def self_test() -> dict:
    """Prove every rule by injecting the defect it exists to catch.

    A rule that stays silent on its own mutant is ``unproven``: the run must
    fail loudly rather than report a clean bill of health it cannot justify.
    """
    baseline = Pack.from_spec(clean_spec())
    baseline_findings = audit(baseline)["findings"]
    results = []
    for rule in RULES:
        spec = copy.deepcopy(clean_spec())
        mutation = rule.mutate(spec)
        mutant = Pack.from_spec(spec)
        fired = [f["rule"] for f in audit(mutant)["findings"]]
        results.append({
            "rule": rule.id,
            "title": rule.title,
            "mutation": mutation,
            "proven": rule.id in fired,
            "collateral": sorted(set(fired) - {rule.id}),
        })
    unproven = [r["rule"] for r in results if not r["proven"]]
    return {
        "clean_spec_findings": baseline_findings,
        "rules": len(RULES),
        "proven": sum(1 for r in results if r["proven"]),
        "unproven": unproven,
        "results": results,
        "ok": not unproven and not baseline_findings,
    }


def _registry_facts(pack: Pack) -> dict:
    version = None
    if isinstance(pack.marketplace, dict):
        version = (pack.marketplace.get("metadata") or {}).get("version")
    refs = []
    for pname, ref in pack.refs():
        if isinstance(ref, str):
            refs.append((pname, ref.rstrip("/").split("/")[-1]))
    return {
        "version": version,
        "plugins": [p.get("name") for p in pack.plugins()],
        "refs": refs,
        "skills": sorted(pack.skills),
    }


def _drift(a: dict, b: dict) -> dict:
    removed_skills = set(a["skills"]) - set(b["skills"])
    added_skills = set(b["skills"]) - set(a["skills"])
    ra = [d for _, d in a["refs"]]
    rb = [d for _, d in b["refs"]]
    renamed, reordered = [], []
    for i, (x, y) in enumerate(zip(ra, rb)):
        if x == y:
            continue
        rec = {"index": i, "from": x, "to": y}
        # Only a 1:1 replacement (old name gone, new name fresh) is a rename;
        # anything else is a reorder, which is not a breaking change.
        if x in removed_skills and y in added_skills:
            renamed.append(rec)
        else:
            reordered.append(rec)
    return {
        "added_skills": sorted(added_skills),
        "removed_skills": sorted(removed_skills),
        "renamed_skills": renamed,
        "reordered_entries": reordered,
        "added_plugins": sorted(set(b["plugins"]) - set(a["plugins"])),
        "removed_plugins": sorted(set(a["plugins"]) - set(b["plugins"])),
        "version": [a["version"], b["version"]],
    }


def version_matrix(dirs: List[Path]) -> dict:
    versions = []
    for d in dirs:
        pack = Pack.from_dir(d)
        rep = audit(pack)
        versions.append({"name": d.name, "path": str(d), "facts": _registry_facts(pack), "report": rep})

    rule_ids = [r.id for r in RULES]
    matrix = {rid: [] for rid in rule_ids}
    for v in versions:
        counts: Dict[str, int] = {rid: 0 for rid in rule_ids}
        for f in v["report"]["findings"]:
            counts[f["rule"]] += 1
        for rid in rule_ids:
            matrix[rid].append(counts[rid])

    drifts = []
    for i in range(1, len(versions)):
        d = _drift(versions[i - 1]["facts"], versions[i]["facts"])
        d["from"] = versions[i - 1]["name"]
        d["to"] = versions[i]["name"]
        drifts.append(d)

    breaking = sum(len(d["removed_skills"]) + len(d["renamed_skills"]) + len(d["removed_plugins"])
                   for d in drifts)
    findings_total = sum(v["report"]["errors"] + v["report"]["warnings"] for v in versions)
    return {
        "mode": "version-matrix",
        "rule_ids": rule_ids,
        "versions": [{"name": v["name"], "path": v["path"], "version": v["facts"]["version"],
                      "skills": len(v["facts"]["skills"]), "plugins": len(v["facts"]["plugins"]),
                      "errors": v["report"]["errors"], "warnings": v["report"]["warnings"],
                      "rules_skipped": v["report"]["rules_skipped"]} for v in versions],
        "matrix": matrix,
        "drift": drifts,
        "breaking_changes": breaking,
        "findings_total": findings_total,
    }


def dump(obj) -> str:
    return json.dumps(obj, indent=2, sort_keys=False)
