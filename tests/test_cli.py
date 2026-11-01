"""Test suite: syntax-free, behaviour-first.

Behaviour cases shell out to the CLI and assert on stdout JSON + exit code, so
the exit-code contract is exercised the same way CI would exercise it.
"""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "bin" / "skills-marketplace-mutate"
HEAD = ROOT / "examples" / "in" / "upstream-head"
VERSIONS = ROOT / "examples" / "in" / "versions"


def run(*args):
    return subprocess.run([sys.executable, str(CLI), *args],
                          capture_output=True, text=True, cwd=ROOT)


class MutationSuite(unittest.TestCase):
    def test_01_self_test_all_rules_proven(self):
        r = run("--self-test", "--json")
        d = json.loads(r.stdout)
        self.assertEqual(d["self_test"]["unproven"], [])
        self.assertEqual(d["self_test"]["proven"], d["self_test"]["rules"])

    def test_02_rule_count_at_least_18(self):
        d = json.loads(run("--self-test", "--json").stdout)
        self.assertGreaterEqual(d["self_test"]["rules"], 18)

    def test_03_clean_specimen_is_clean(self):
        d = json.loads(run("--self-test", "--json").stdout)
        self.assertEqual(d["self_test"]["clean_spec_findings"], [])

    def test_04_every_rule_has_a_mutation(self):
        d = json.loads(run("--self-test", "--json").stdout)
        for res in d["self_test"]["results"]:
            self.assertTrue(res["mutation"], res["rule"])


class BehaviourSuite(unittest.TestCase):
    def test_05_real_pack_reports_findings_exit_1(self):
        r = run("--root", str(HEAD), "--json")
        self.assertEqual(r.returncode, 1)
        d = json.loads(r.stdout)
        self.assertGreater(d["root"]["report"]["errors"], 0)

    def test_06_findings_are_sorted_and_well_formed(self):
        d = json.loads(run("--root", str(HEAD), "--json").stdout)
        wheres = [f["where"] for f in d["root"]["report"]["findings"]]
        self.assertEqual(wheres, sorted(wheres))
        for f in d["root"]["report"]["findings"]:
            self.assertTrue(f["rule"].startswith("SM"))
            self.assertIn(f["severity"], ("error", "warning"))

    def test_07_errors_only_narrows_counts(self):
        full = json.loads(run("--root", str(HEAD), "--json").stdout)
        errs = json.loads(run("--root", str(HEAD), "--json", "--errors-only").stdout)
        self.assertLessEqual(errs["root"]["report"]["warnings"],
                             full["root"]["report"]["warnings"])

    def test_08_usage_error_exit_2(self):
        self.assertEqual(run().returncode, 2)

    def test_09_missing_root_exit_2(self):
        self.assertEqual(run("--root", "/nonexistent-pack-xyz").returncode, 2)

    def test_10_broken_marketplace_exit_2(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / ".claude-plugin"
            p.mkdir(parents=True)
            (p / "marketplace.json").write_text('{ "name": "x", }')
            r = run("--root", td, "--json")
            self.assertEqual(r.returncode, 2)
            self.assertIn("error", json.loads(r.stdout)["root"])

    def test_11_json_is_parseable_and_echoes_exit_code(self):
        r = run("--root", str(HEAD), "--json")
        d = json.loads(r.stdout)
        self.assertEqual(d["exit_code"], r.returncode)


class MatrixSuite(unittest.TestCase):
    ORDER = ["4d1e3f3f78", "89dcaa3a28", "f379e5ad66", "0a64e398ec"]

    def _matrix(self):
        dirs = ",".join(str(VERSIONS / s) for s in self.ORDER)
        r = run("--versions", dirs, "--json")
        return r, json.loads(r.stdout)["versions"]

    def test_12_matrix_covers_four_versions(self):
        _, m = self._matrix()
        self.assertEqual([v["name"] for v in m["versions"]], self.ORDER)

    def test_13_matrix_rows_align_with_versions(self):
        _, m = self._matrix()
        for rid, row in m["matrix"].items():
            self.assertEqual(len(row), len(self.ORDER), rid)

    def test_14_skill_growth_is_detected(self):
        _, m = self._matrix()
        growth = [len(d["added_skills"]) for d in m["drift"]]
        self.assertGreater(sum(growth), 0)

    def test_15_academy_rename_is_detected(self):
        _, m = self._matrix()
        pairs = [(x["from"], x["to"]) for d in m["drift"] for x in d["renamed_skills"]]
        self.assertIn(("claude-academy-guide", "academy-guide"), pairs)

    def test_16_registry_churn_exits_1(self):
        r, _ = self._matrix()
        self.assertEqual(r.returncode, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
