"""Load an Agent Skills pack (marketplace registry + SKILL.md files).

A "pack" is the on-disk layout used by anthropics/skills and by any harness that
implements the same convention::

    <root>/.claude-plugin/marketplace.json
    <root>/skills/<skill-dir>/SKILL.md
    <root>/skills/<skill-dir>/LICENSE.txt          (optional)

For version snapshots that were captured from a git tree without checking out
every file, the layout may instead carry ``manifest.txt`` (one repo-relative
path per line). When present, the manifest is the authority for "does this
path exist", which lets registry-only rules run without the full checkout.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

MARKETPLACE_REL = ".claude-plugin/marketplace.json"
SKILLS_DIR = "skills"

_FRONT_RE = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*\r?\n?", re.DOTALL)


@dataclass
class Skill:
    """One ``skills/<dir>/SKILL.md``."""

    dir_name: str
    md: str
    license_file: bool = False

    @property
    def front_matter_found(self) -> bool:
        return _FRONT_RE.match(self.md) is not None

    @property
    def front_raw(self) -> str:
        m = _FRONT_RE.match(self.md)
        return m.group(1) if m else ""

    @property
    def front(self) -> Dict[str, Any]:
        """Minimal YAML frontmatter reader.

        Deliberately supports only the subset the skill spec uses: ``key: value``
        pairs, single/double quoted scalars, and block scalars (``>`` / ``|``).
        Multi-line block scalars are folded into a single string.
        """
        out: Dict[str, Any] = {}
        raw = self.front_raw
        if not raw:
            return out
        lines = raw.splitlines()
        i = 0
        while i < len(lines):
            line = lines[i]
            i += 1
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            if ":" not in line:
                continue
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip()
            if value in (">", "|", ">-", "|-", ">+", "|+"):
                block: List[str] = []
                while i < len(lines) and (lines[i].startswith(" ") or not lines[i].strip()):
                    block.append(lines[i].strip())
                    i += 1
                out[key] = " ".join(x for x in block if x).strip()
                continue
            if value == "":
                # Nested block mapping, e.g. the spec's `metadata:` map. Consume
                # the indented children so they are not mistaken for top-level
                # keys (which would make SM018 report false positives).
                nested: List[str] = []
                while i < len(lines) and (lines[i].startswith(" ") or not lines[i].strip()):
                    nested.append(lines[i].strip())
                    i += 1
                out[key] = " ".join(x for x in nested if x).strip()
                continue
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            out[key] = value
        return out

    @property
    def body(self) -> str:
        return _FRONT_RE.sub("", self.md, count=1)


@dataclass
class Pack:
    root: Optional[Path] = None
    marketplace: Optional[dict] = None
    marketplace_error: Optional[str] = None
    marketplace_raw: Optional[str] = None
    skills: Dict[str, Skill] = field(default_factory=dict)
    manifest: Optional[List[str]] = None

    # ---- existence -----------------------------------------------------
    def exists(self, repo_rel: str) -> bool:
        rel = repo_rel.lstrip("./")
        if self.manifest is not None:
            return rel in set(self.manifest)
        if self.root is not None:
            return (self.root / rel).exists()
        # spec-backed pack (mutation engine): existence comes from the skill map
        m = re.fullmatch(r"skills/([^/]+)/SKILL\.md", rel)
        return bool(m) and m.group(1) in self.skills

    def plugins(self) -> List[dict]:
        if not isinstance(self.marketplace, dict):
            return []
        pl = self.marketplace.get("plugins")
        return [p for p in pl if isinstance(p, dict)] if isinstance(pl, list) else []

    def refs(self) -> List[Any]:
        """Every ``skills[]`` entry, in registry order, with its plugin name."""
        out = []
        for p in self.plugins():
            s = p.get("skills")
            if isinstance(s, list):
                for entry in s:
                    out.append((p.get("name"), entry))
        return out

    # ---- constructors --------------------------------------------------
    @classmethod
    def from_dir(cls, root: Path) -> "Pack":
        root = Path(root)
        pack = cls(root=root)
        mp = root / MARKETPLACE_REL
        if mp.exists():
            pack.marketplace_raw = mp.read_text(encoding="utf-8", errors="replace")
            try:
                pack.marketplace = json.loads(pack.marketplace_raw)
            except json.JSONDecodeError as exc:
                pack.marketplace_error = f"{exc.msg} (line {exc.lineno} col {exc.colno})"
        else:
            pack.marketplace_error = f"missing {MARKETPLACE_REL}"
        man = root / "manifest.txt"
        if man.exists():
            pack.manifest = [ln.strip() for ln in man.read_text().splitlines() if ln.strip()]
        sk = root / SKILLS_DIR
        if sk.is_dir():
            for child in sorted(sk.iterdir()):
                f = child / "SKILL.md"
                if f.is_file():
                    pack.skills[child.name] = Skill(
                        dir_name=child.name,
                        md=f.read_text(encoding="utf-8", errors="replace"),
                        license_file=(child / "LICENSE.txt").is_file(),
                    )
        elif pack.manifest is not None:
            for rel in pack.manifest:
                m = re.fullmatch(r"skills/([^/]+)/SKILL\.md", rel)
                if m:
                    pack.skills[m.group(1)] = Skill(m.group(1), "", False)
        return pack

    @classmethod
    def from_spec(cls, spec: dict) -> "Pack":
        """Build a pack from an in-memory spec (used by the mutation engine).

        Spec shape::

            {"marketplace": dict | None,
             "break_json": bool,
             "skills": {"<dir>": {"md": str, "license_file": bool}},
             "manifest": list[str] | None}
        """
        pack = cls()
        if spec.get("break_json"):
            pack.marketplace_raw = spec.get("marketplace_raw", "{ not json")
            pack.marketplace_error = "Expecting property name enclosed in double quotes (line 1 col 3)"
        else:
            pack.marketplace = spec.get("marketplace")
            pack.marketplace_raw = json.dumps(spec.get("marketplace"))
        pack.manifest = spec.get("manifest")
        for name, s in (spec.get("skills") or {}).items():
            pack.skills[name] = Skill(name, s.get("md", ""), bool(s.get("license_file")))
        return pack
