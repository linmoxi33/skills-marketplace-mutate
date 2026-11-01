"""The rule pack.

Every rule declares the defect it is meant to catch *and* a mutation that
injects exactly that defect into a clean in-memory specimen.  ``smm.engine``
runs the rule against its own mutant; a rule that does not fire on its mutant
is reported ``unproven`` and the run exits 2.  Rule packs that are only tested
against a real checkout silently rot (the real checkout stops exercising the
rule as soon as upstream fixes the defect), which is what this design avoids.
"""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, List

from .pack import Pack, Skill

ALLOWED_FRONT_KEYS = {"name", "description", "license", "allowed-tools"}
MAX_DESCRIPTION = 1024
MAX_BODY_LINES = 500
NAME_RE = re.compile(r"\A[a-z0-9]+(-[a-z0-9]+)*\Z")
SEMVER_RE = re.compile(r"\A\d+\.\d+\.\d+([-+][0-9A-Za-z.-]+)?\Z")
TRIGGER_RE = re.compile(r"\b(use (this|it|the skill)? ?(when|whenever|if|for|any ?time)|trigger(s|ed)?\b|when the user)", re.I)
IMPERATIVE_HEAD = {
    "use", "create", "build", "make", "write", "generate", "run", "add", "edit",
    "read", "open", "convert", "extract", "fix", "update", "modify", "help",
    "analyze", "produce", "turn", "do", "get", "apply", "manage", "design",
}
SECOND_PERSON_RE = re.compile(r"\A(you|your|we)\b", re.I)


@dataclass
class Finding:
    rule: str
    severity: str
    where: str
    message: str

    def to_dict(self) -> dict:
        return {"rule": self.rule, "severity": self.severity,
                "where": self.where, "message": self.message}


@dataclass
class Rule:
    id: str
    title: str
    severity: str
    scope: str  # "registry" (manifest-only mode OK) or "full"
    check: Callable[[Pack], List[Finding]]
    mutate: Callable[[dict], str]

    def run(self, pack: Pack) -> List[Finding]:
        return self.check(pack)

    def to_dict(self) -> dict:
        return {"id": self.id, "title": self.title,
                "severity": self.severity, "scope": self.scope}


# --------------------------------------------------------------------------
# clean specimen
# --------------------------------------------------------------------------

CLEAN_DESCRIPTION = (
    "Widget toolkit for rendering deterministic mock reports. "
    "Use this skill whenever the user asks for a widget report or a gadget "
    "summary. Triggers on \"widget\", \"gadget\"."
)


def clean_spec() -> dict:
    """A pack that must pass every rule.

    This is the mutation substrate: it is intentionally independent of any
    real checkout, so the self-test proves the rule pack even when upstream is
    momentarily clean.
    """
    def md(name: str, description: str = CLEAN_DESCRIPTION, body: str = "## Body\n\nText.\n") -> str:
        return (f"---\nname: {name}\ndescription: {description}\n"
                f"license: Complete terms in LICENSE.txt\n---\n\n{body}")

    return {
        "marketplace": {
            "name": "demo-pack",
            "owner": {"name": "Demo"},
            "metadata": {"version": "1.0.0"},
            "plugins": [
                {
                    "name": "core",
                    "description": "Core widgets",
                    "source": "./",
                    "strict": False,
                    "skills": ["./skills/widget", "./skills/gadget"],
                }
            ],
        },
        "skills": {
            "widget": {"md": md("widget"), "license_file": True},
            "gadget": {"md": md("gadget"), "license_file": True},
        },
        "manifest": None,
    }


# --------------------------------------------------------------------------
# helpers shared by checks and mutations
# --------------------------------------------------------------------------

def _plugin(pack: Pack, idx: int = 0) -> dict:
    pl = pack.plugins()
    return pl[idx] if idx < len(pl) else {}


def _skill_md(pack: Pack, name: str) -> Skill:
    return pack.skills.get(name, Skill(name, ""))


# --------------------------------------------------------------------------
# rules
# --------------------------------------------------------------------------

def _r01(p: Pack) -> List[Finding]:
    if p.marketplace is None:
        return [Finding("SM001", "error", p.marketplace_error or "marketplace.json",
                        "marketplace.json is missing or is not valid JSON")]
    return []


def _r02(p: Pack) -> List[Finding]:
    if p.marketplace is None:
        return []
    out = []
    if not p.marketplace.get("name"):
        out.append(Finding("SM002", "error", "marketplace.name", "registry has no 'name'"))
    if not isinstance(p.marketplace.get("owner"), dict) or not p.marketplace["owner"].get("name"):
        out.append(Finding("SM002", "error", "marketplace.owner.name", "registry has no 'owner.name'"))
    if not isinstance(p.marketplace.get("plugins"), list) or not p.marketplace["plugins"]:
        out.append(Finding("SM002", "error", "marketplace.plugins", "'plugins' must be a non-empty list"))
    return out


def _r03(p: Pack) -> List[Finding]:
    out = []
    for i, pl in enumerate(p.plugins()):
        for key in ("name", "description", "source"):
            if not pl.get(key):
                out.append(Finding("SM003", "error", f"marketplace.plugins[{i}].{key}",
                                   f"plugin #{i} is missing '{key}'"))
        if not isinstance(pl.get("skills"), list) or not pl["skills"]:
            out.append(Finding("SM003", "error", f"marketplace.plugins[{i}].skills",
                               f"plugin #{i} has no non-empty 'skills' list"))
    return out


def _r04(p: Pack) -> List[Finding]:
    seen: Dict[str, int] = {}
    out = []
    for i, pl in enumerate(p.plugins()):
        n = pl.get("name")
        if not isinstance(n, str):
            continue
        if n in seen:
            out.append(Finding("SM004", "error", f"marketplace.plugins[{i}].name",
                               f"plugin name '{n}' also used by plugin #{seen[n]}"))
        else:
            seen[n] = i
    return out


def _r05(p: Pack) -> List[Finding]:
    out = []
    for pname, ref in p.refs():
        if not isinstance(ref, str):
            out.append(Finding("SM005", "error", f"plugin '{pname}'",
                               f"skills entry is {type(ref).__name__}, expected string path"))
            continue
        d = ref.rstrip("/").split("/")[-1]
        if not p.exists(f"skills/{d}/SKILL.md"):
            out.append(Finding("SM005", "error", f"plugin '{pname}' -> {ref}",
                               f"registry references {ref} but skills/{d}/SKILL.md does not exist"))
    return out


def _r06(p: Pack) -> List[Finding]:
    referenced = set()
    for _, ref in p.refs():
        if isinstance(ref, str):
            referenced.add(ref.rstrip("/").split("/")[-1])
    return [Finding("SM006", "error", f"skills/{d}", f"skills/{d} exists but no plugin references it")
            for d in sorted(p.skills) if d not in referenced]


def _r07(p: Pack) -> List[Finding]:
    seen: Dict[str, str] = {}
    out = []
    for pname, ref in p.refs():
        if not isinstance(ref, str):
            continue
        if ref in seen:
            out.append(Finding("SM007", "error", f"plugin '{pname}' -> {ref}",
                               f"skill path referenced by both '{seen[ref]}' and '{pname}'"))
        else:
            seen[ref] = pname
    return out


def _r08(p: Pack) -> List[Finding]:
    out = []
    for pname, ref in p.refs():
        if not isinstance(ref, str):
            continue
        if not ref.startswith("./"):
            out.append(Finding("SM008", "error", f"plugin '{pname}' -> {ref}",
                               f"skill path '{ref}' must be relative and start with './'"))
        if ".." in ref.split("/"):
            out.append(Finding("SM008", "error", f"plugin '{pname}' -> {ref}",
                               f"skill path '{ref}' escapes the pack root"))
    return out


def _r09(p: Pack) -> List[Finding]:
    if p.marketplace is None:
        return []
    v = (p.marketplace.get("metadata") or {}).get("version")
    if not isinstance(v, str) or not SEMVER_RE.match(v):
        return [Finding("SM009", "error", "marketplace.metadata.version",
                        f"version {v!r} is not semver (x.y.z)")]
    return []


def _r10(p: Pack) -> List[Finding]:
    return [Finding("SM010", "error", f"skills/{d}/SKILL.md",
                    "file does not open with a '---' YAML frontmatter block")
            for d in sorted(p.skills) if not _skill_md(p, d).front_matter_found]


def _r11(p: Pack) -> List[Finding]:
    return [Finding("SM011", "error", f"skills/{d}/SKILL.md",
                    "frontmatter has no 'name' field")
            for d in sorted(p.skills)
            if _skill_md(p, d).front_matter_found and not _skill_md(p, d).front.get("name")]


def _r12(p: Pack) -> List[Finding]:
    return [Finding("SM012", "error", f"skills/{d}/SKILL.md",
                    "frontmatter has no 'description' field")
            for d in sorted(p.skills)
            if _skill_md(p, d).front_matter_found and not _skill_md(p, d).front.get("description")]


def _r13(p: Pack) -> List[Finding]:
    out = []
    for d in sorted(p.skills):
        n = _skill_md(p, d).front.get("name")
        if isinstance(n, str) and n and n != d:
            out.append(Finding("SM013", "error", f"skills/{d}/SKILL.md",
                               f"frontmatter name '{n}' != directory name '{d}'"))
    return out


def _r14(p: Pack) -> List[Finding]:
    out = []
    for d in sorted(p.skills):
        n = _skill_md(p, d).front.get("name")
        if not isinstance(n, str) or not n:
            continue
        if len(n) > 64 or not NAME_RE.match(n):
            out.append(Finding("SM014", "error", f"skills/{d}/SKILL.md",
                               f"name '{n}' must be lowercase alphanumeric/hyphen and <=64 chars"))
    return out


def _r15(p: Pack) -> List[Finding]:
    out = []
    for d in sorted(p.skills):
        desc = _skill_md(p, d).front.get("description")
        if isinstance(desc, str) and len(desc) > MAX_DESCRIPTION:
            out.append(Finding("SM015", "error", f"skills/{d}/SKILL.md",
                               f"description is {len(desc)} chars, limit is {MAX_DESCRIPTION}"))
    return out


def _r16(p: Pack) -> List[Finding]:
    """Descriptions are read by the model in third person; imperative voice breaks retrieval."""
    out = []
    for d in sorted(p.skills):
        desc = _skill_md(p, d).front.get("description")
        if not isinstance(desc, str) or not desc.strip():
            continue
        head = re.split(r"[\s,.:;]+", desc.strip(), maxsplit=1)[0]
        if SECOND_PERSON_RE.match(desc.strip()):
            out.append(Finding("SM016", "error", f"skills/{d}/SKILL.md",
                               "description addresses the reader ('you'); write it in third person"))
        elif head.lower() in IMPERATIVE_HEAD:
            out.append(Finding("SM016", "error", f"skills/{d}/SKILL.md",
                               f"description starts with imperative verb '{head}'; write it in third person"))
    return out


def _r17(p: Pack) -> List[Finding]:
    out = []
    for d in sorted(p.skills):
        desc = _skill_md(p, d).front.get("description")
        if not isinstance(desc, str) or not desc.strip():
            continue
        if not TRIGGER_RE.search(desc):
            out.append(Finding("SM017", "warning", f"skills/{d}/SKILL.md",
                               "description states what the skill does but not when to use it"))
    return out


def _r18(p: Pack) -> List[Finding]:
    out = []
    for d in sorted(p.skills):
        keys = set(_skill_md(p, d).front)
        extra = sorted(keys - ALLOWED_FRONT_KEYS)
        if extra:
            out.append(Finding("SM018", "warning", f"skills/{d}/SKILL.md",
                               f"unknown frontmatter key(s): {', '.join(extra)}"))
    return out


def _r19(p: Pack) -> List[Finding]:
    out = []
    for d in sorted(p.skills):
        s = _skill_md(p, d)
        lic = s.front.get("license")
        if isinstance(lic, str) and "LICENSE.txt" in lic and not s.license_file:
            out.append(Finding("SM019", "error", f"skills/{d}/SKILL.md",
                               f"license says '{lic}' but skills/{d}/LICENSE.txt is absent"))
    return out


def _r20(p: Pack) -> List[Finding]:
    out = []
    for d in sorted(p.skills):
        if not _skill_md(p, d).body.strip():
            out.append(Finding("SM020", "error", f"skills/{d}/SKILL.md", "body is empty"))
    return out


def _r21(p: Pack) -> List[Finding]:
    out = []
    for d in sorted(p.skills):
        n = len(_skill_md(p, d).body.splitlines())
        if n > MAX_BODY_LINES:
            out.append(Finding("SM021", "warning", f"skills/{d}/SKILL.md",
                               f"body is {n} lines, over the {MAX_BODY_LINES}-line budget"))
    return out


# --------------------------------------------------------------------------
# mutations
# --------------------------------------------------------------------------

def _m01(s: dict) -> str:
    s["break_json"] = True
    s["marketplace_raw"] = '{ "name": "demo-pack", }'
    return "truncate marketplace.json to invalid JSON"


def _m02(s: dict) -> str:
    del s["marketplace"]["owner"]
    return "delete marketplace.owner.name"


def _m03(s: dict) -> str:
    del s["marketplace"]["plugins"][0]["description"]
    return "delete plugins[0].description"


def _m04(s: dict) -> str:
    s["marketplace"]["plugins"].append(copy.deepcopy(s["marketplace"]["plugins"][0]))
    return "duplicate plugin name 'core'"


def _m05(s: dict) -> str:
    s["marketplace"]["plugins"][0]["skills"].append("./skills/ghost")
    return "reference ./skills/ghost which does not exist"


def _m06(s: dict) -> str:
    s["skills"]["orphan"] = {"md": s["skills"]["widget"]["md"], "license_file": True}
    return "add skills/orphan that no plugin references"


def _m07(s: dict) -> str:
    s["marketplace"]["plugins"].append({
        "name": "extra", "description": "Extra", "source": "./",
        "strict": False, "skills": ["./skills/widget"]})
    return "reference ./skills/widget from a second plugin"


def _m08(s: dict) -> str:
    s["marketplace"]["plugins"][0]["skills"][0] = "skills/widget"
    return "drop the leading './' from a skill path"


def _m09(s: dict) -> str:
    s["marketplace"]["metadata"]["version"] = "1.0"
    return "set metadata.version to '1.0'"


def _m10(s: dict) -> str:
    s["skills"]["widget"]["md"] = "name: widget\ndescription: x\n"
    return "remove the '---' frontmatter delimiters"


def _m11(s: dict) -> str:
    s["skills"]["widget"]["md"] = s["skills"]["widget"]["md"].replace("name: widget\n", "")
    return "remove frontmatter 'name'"


def _m12(s: dict) -> str:
    s["skills"]["widget"]["md"] = s["skills"]["widget"]["md"].replace(
        "description: Widget toolkit", "description_missing: Widget toolkit")
    return "remove frontmatter 'description'"


def _m13(s: dict) -> str:
    s["skills"]["widget"]["md"] = s["skills"]["widget"]["md"].replace("name: widget", "name: widget-x")
    return "rename frontmatter name away from the directory name"


def _m14(s: dict) -> str:
    s["skills"]["widget"]["md"] = s["skills"]["widget"]["md"].replace("name: widget", "name: Widget_Toolkit")
    return "use an invalid skill name 'Widget_Toolkit'"


def _m15(s: dict) -> str:
    s["skills"]["widget"]["md"] = s["skills"]["widget"]["md"].replace(
        CLEAN_DESCRIPTION, "A" * (MAX_DESCRIPTION + 20))
    return f"blow the description up to {MAX_DESCRIPTION + 20} chars"


def _m16(s: dict) -> str:
    s["skills"]["widget"]["md"] = s["skills"]["widget"]["md"].replace(
        CLEAN_DESCRIPTION, "Use this skill whenever the user asks for a widget report.")
    return "rewrite the description in imperative voice"


def _m17(s: dict) -> str:
    s["skills"]["widget"]["md"] = s["skills"]["widget"]["md"].replace(
        CLEAN_DESCRIPTION, "Widget toolkit for rendering deterministic mock reports.")
    return "drop the 'when to use' clause"


def _m18(s: dict) -> str:
    s["skills"]["widget"]["md"] = s["skills"]["widget"]["md"].replace(
        "license: Complete terms in LICENSE.txt", "license: Complete terms in LICENSE.txt\nmodel: claude-sonnet")
    return "add an unknown frontmatter key"


def _m19(s: dict) -> str:
    s["skills"]["widget"]["license_file"] = False
    return "delete LICENSE.txt while frontmatter still points at it"


def _m20(s: dict) -> str:
    s["skills"]["widget"]["md"] = s["skills"]["widget"]["md"].replace("## Body\n\nText.\n", "\n")
    return "empty the SKILL.md body"


def _m21(s: dict) -> str:
    s["skills"]["widget"]["md"] = s["skills"]["widget"]["md"].replace(
        "## Body\n\nText.\n", "## Body\n" + "line\n" * (MAX_BODY_LINES + 5))
    return f"grow the body past {MAX_BODY_LINES} lines"


RULES: List[Rule] = [
    Rule("SM001", "marketplace.json parses as JSON", "error", "registry", _r01, _m01),
    Rule("SM002", "registry declares name / owner.name / plugins", "error", "registry", _r02, _m02),
    Rule("SM003", "every plugin declares name / description / source / skills", "error", "registry", _r03, _m03),
    Rule("SM004", "plugin names are unique", "error", "registry", _r04, _m04),
    Rule("SM005", "every referenced skill path exists", "error", "registry", _r05, _m05),
    Rule("SM006", "every skill directory is referenced", "error", "registry", _r06, _m06),
    Rule("SM007", "no skill path is referenced twice", "error", "registry", _r07, _m07),
    Rule("SM008", "skill paths are relative and stay in the pack", "error", "registry", _r08, _m08),
    Rule("SM009", "metadata.version is semver", "error", "registry", _r09, _m09),
    Rule("SM010", "SKILL.md opens with YAML frontmatter", "error", "full", _r10, _m10),
    Rule("SM011", "frontmatter declares name", "error", "full", _r11, _m11),
    Rule("SM012", "frontmatter declares description", "error", "full", _r12, _m12),
    Rule("SM013", "frontmatter name matches the directory", "error", "full", _r13, _m13),
    Rule("SM014", "skill name is lowercase-hyphen and <=64 chars", "error", "full", _r14, _m14),
    Rule("SM015", f"description is <= {MAX_DESCRIPTION} chars", "error", "full", _r15, _m15),
    Rule("SM016", "description is written in third person", "error", "full", _r16, _m16),
    Rule("SM017", "description says when to use the skill", "warning", "full", _r17, _m17),
    Rule("SM018", "frontmatter uses only documented keys", "warning", "full", _r18, _m18),
    Rule("SM019", "declared LICENSE.txt exists", "error", "full", _r19, _m19),
    Rule("SM020", "SKILL.md body is not empty", "error", "full", _r20, _m20),
    Rule("SM021", f"SKILL.md body is <= {MAX_BODY_LINES} lines", "warning", "full", _r21, _m21),
]

RULE_BY_ID = {r.id: r for r in RULES}
