# skills-marketplace-mutate

Offline auditor for an **Agent Skills marketplace registry** (`marketplace.json` + `SKILL.md` frontmatter) that proves its own rules by mutation and tracks registry drift across versions — so a registry that silently stops matching the skills on disk fails CI instead of shipping.

Upstream: https://github.com/anthropics/skills

Why now: the Agent Skills convention is spreading fast (Claude Code plugins, Cursor plugins, OpenClaw, Codex). A marketplace registry is a *manifest that points at other files*: every entry is a path that can dangle, a name that can drift from its directory, or a license claim with no file behind it. Nothing in the convention checks that, and the registry is exactly the artifact CI never looks at.

## What the upstream is, and what it leaves unverified

`anthropics/skills` is Anthropic's public Agent Skills pack. `.claude-plugin/marketplace.json` groups 19 skills into 5 plugins, and each `skills/<dir>/SKILL.md` declares `name` / `description` frontmatter.

That registry is hand-maintained and has no in-repo validator. Real, open reports of the class of problem this tool covers:

- [issue #2090](https://github.com/anthropics/skills/issues/2090) — "docx/pdf/pptx/xlsx descriptions aren't third person; skill-creator is over 500 lines"
- [issue #2064](https://github.com/anthropics/skills/issues/2064) — "theme-factory: description says what the skill does but not when to use it"
- [issue #2048](https://github.com/anthropics/skills/issues/2048) — "Is there a recommended max length for skill description fields?"

Those are issue reports, not checks: nothing re-runs them. Running this tool against a verbatim snapshot of upstream `HEAD` (19 skills, 5 plugins) reproduces the same classes mechanically: **13 findings — 7 errors, 6 warnings** (`examples/out/upstream-head-report.json`).

## What this tool does — and what it does not

Does:

- 21 rules over the two machine-readable layers: the registry (`SM001`–`SM009`) and `SKILL.md` frontmatter/body (`SM010`–`SM021`). Thresholds are taken from the official [Agent Skills specification](https://agentskills.io/specification) (`name` ≤ 64 chars lowercase-hyphen, `description` ≤ 1024 chars, description must say what the skill does *and when to use it*), not invented.
- **Mutation self-proof.** Each rule ships a mutation that injects the exact defect it exists to catch. `--self-test` runs every rule against its own mutant; a rule that stays silent is reported `unproven` and the process exits `2`. A rule pack that is only ever exercised against a real checkout rots as soon as upstream fixes the defect — this design makes that rot loud.
- **Multi-version drift matrix.** `--versions a,b,c` audits several snapshots and emits a rule × version finding matrix plus registry drift: added/removed skills, added/removed plugins, and unambiguous 1:1 renames. A rename is a breaking change for every installed plugin pinning the old path.
- Exit codes `0` / `1` / `2`, a parseable `--json` payload, and a fully offline core (no network call in any rule or test).

Does not: run the skills, validate that a description *retrieves well* (that needs an eval harness, not a linter), check licenses' legal meaning, or modify anything it audits.

## Install

Python 3.9+, zero third-party dependencies, no network required.

```bash
git clone git@github.com:linmoxi33/skills-marketplace-mutate.git
cd skills-marketplace-mutate
python3 bin/skills-marketplace-mutate --self-test --json
```

## Usage

```bash
# prove every rule (21/21) against its own mutant
python3 bin/skills-marketplace-mutate --self-test

# audit one pack root (registry + every SKILL.md)
python3 bin/skills-marketplace-mutate --root examples/in/upstream-head

# registry drift across snapshots
python3 bin/skills-marketplace-mutate --versions \
  examples/in/versions/4d1e3f3f78,examples/in/versions/89dcaa3a28,\
examples/in/versions/f379e5ad66,examples/in/versions/0a64e398ec
```

`--json` example (abridged, real output in `examples/out/`):

```json
{
  "root": {"path": "examples/in/upstream-head", "report": {
    "rules_total": 21, "rules_run": 21, "rules_skipped": [],
    "findings": [
      {"rule": "SM015", "severity": "error", "where": "skills/claude-api/SKILL.md",
       "message": "description is 1068 chars, limit is 1024"},
      {"rule": "SM016", "severity": "error", "where": "skills/docx/SKILL.md",
       "message": "description starts with imperative verb 'Use'; write it in third person"}
    ],
    "errors": 7, "warnings": 6, "skills": ["academy-guide", "algorithmic-art", "..."], "plugins": ["document-skills", "..."]
  }},
  "exit_code": 1
}
```

Exit codes:

| code | meaning |
| --- | --- |
| `0` | every rule proven, nothing found |
| `1` | findings and/or breaking registry drift detected |
| `2` | usage error, unreadable pack, invalid JSON, or an unproven rule |

## Example data

`examples/in/upstream-head/` is a verbatim snapshot of `anthropics/skills` at `HEAD`
(`.claude-plugin/marketplace.json`, all 19 `SKILL.md`, all 18 `LICENSE.txt`, plus
`manifest.txt` — the git tree path list). `examples/in/versions/<sha>/` are four
registry snapshots captured from the same repository's history
(`4d1e3f3f78` → `89dcaa3a28` → `f379e5ad66` → `0a64e398ec`), each with
`marketplace.json` + `manifest.txt`. Nothing in `examples/` is synthetic.
`examples/in/clean-pack/` is a small synthetic pack that passes all 21 rules
(used to show the `0` exit path).

## Limits

- Registry-level rules need either a real checkout or a `manifest.txt`; frontmatter rules (`SM010`–`SM021`) are skipped on manifest-only snapshots, and the JSON reports which rule ids were skipped. This is deliberate: no silent "looked at it" claims.
- The frontmatter reader implements the subset of YAML the skill spec uses (`key: value`, quoted scalars, `>`/`|` block scalars,
  and nested block maps such as the spec's `metadata:` map, whose children are consumed as a block rather than promoted to top-level keys).
  It is not a YAML 1.2 parser; exotic constructs may be read as strings.
- A manifest-only snapshot (`marketplace.json` + `manifest.txt`) skips `SM010`-`SM021` and says so in the JSON. An empty
  `SKILL.md` found on disk is *not* treated that way: it runs every rule and fails `SM010`/`SM020` (regression test `test_18`).
- `SM016` (third person) and `SM017` (states when to use) are heuristics over the description's first token and trigger vocabulary. They are reported with the matched word so a human can overrule.
- Rename detection is deliberately conservative: only a 1:1 replacement (old name gone, new name fresh) counts; reordering a `skills[]` list is reported separately as `reordered_entries` and is not treated as breaking.

## 本轮创新

First rule pack for an Agent Skills registry that **proves each rule by injecting the defect it exists to catch** (`--self-test`: 21 rules ↔ 21 mutations, and the run exits `2` if any rule stays silent), and pairs that with a **rule × version drift matrix** over four real registry snapshots — the drift matrix is what surfaces the `claude-academy-guide → academy-guide` rename (a breaking change for installed plugin pins) that no static linter can see, because a single-commit audit has no "before" to compare against.

## 与同类项目的差异

Searched with `gh search repos "skills marketplace lint"`, `gh search code "marketplace.json"` and reviewed this team's own published linters (`expo-skills-registry-lint`, `cursor-plugins-parity-lint`, `thinkrail-spec-lint`). All of them audit **one** upstream snapshot with rules that are only ever run against that snapshot, so any rule that silently stops matching produces exit `0` and no warning. This tool differs in two measurable ways: (1) rules must prove themselves against a built-in clean specimen and its mutant, independent of the audited pack; (2) it scores the same rule pack over multiple registry versions and reports the drift. Report of what differs is in `examples/out/version-matrix.json`, not prose.

## License

MIT. Example snapshots under `examples/` are redistributed from
[`anthropics/skills`](https://github.com/anthropics/skills) (MIT for the repository
metadata; per-skill `LICENSE.txt` files are kept verbatim beside each skill).
