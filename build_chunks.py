#!/usr/bin/env python3
"""Build machine-readable chunks from the clean AI Addendum master + knowledge/ dir."""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path


_HERE = Path(__file__).resolve().parent  # file-relative defaults (C15, 2026-08-18)
DEFAULT_SOURCE = _HERE / "addendum-clean.md"
DEFAULT_KNOWLEDGE_DIR = _HERE / "knowledge"
DEFAULT_DATA_DIR = _HERE / "data"
PUBLIC_BASE_URL = "https://agent.ai4bcm.org/demo/kb"


HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
PP_RE = re.compile(r"\bPP([1-6])\b", re.I)
META_RE = re.compile(r"<!--\s*meta:\s*(.+?)\s*-->")


SECTION_TYPES = {
    "introduction": "introduction",
    "typical ai uses": "typical_uses",
    "suitable tools": "suitable_tools",
    "minimum controls": "minimum_controls",
    "process": "process",
    "methods and techniques": "methods",
    "outcomes and review": "outcomes",
}

# knowledge/*.md filename → default section_type for all chunks in that file
KNOWLEDGE_FILE_TYPES: dict[str, str] = {
    "prompts.md": "prompt",
    "workflows.md": "workflow",
    "faq.md": "faq",
    "do-not-use.md": "do_not_use",
    "tool-compare.md": "comparison",
}

REQUIRED_KNOWLEDGE_TYPES = frozenset(KNOWLEDGE_FILE_TYPES.values())


@dataclass
class Heading:
    level: int
    title: str
    line: int


@dataclass
class Chunk:
    id: str
    pp: str | None
    section_type: str
    title: str
    breadcrumb: str
    text: str
    url: str
    char_count: int
    # Extended metadata (all optional; defaults allow un-annotated chunks to build cleanly)
    bcm_process: str = ""
    ai_capability: str = ""
    risk_level: str = ""
    confidentiality: str = ""
    intended_user: str = ""
    output_type: str = ""
    mode: str = ""
    related_controls: list[str] = field(default_factory=list)
    related_examples: list[str] = field(default_factory=list)
    source_file: str = ""


def slugify(value: str) -> str:
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-") or "section"


def title_key(title: str) -> str:
    return re.sub(r"\s+", " ", title.lower().strip())


def section_type_for(title: str, pp: str | None) -> str:
    key = title_key(title)
    if pp and key in SECTION_TYPES:
        return SECTION_TYPES[key]
    if "principle" in key:
        return "principle"
    if "glossary" in key:
        return "glossary"
    if "annex" in key:
        return "matrix" if "matrix" in key else "annex"
    if "concept" in key or "tool" in key:
        return "concept"
    return "section"


def parse_sections(text: str) -> list[tuple[Heading, str]]:
    lines = text.splitlines()
    headings: list[Heading] = []
    for idx, line in enumerate(lines):
        match = HEADING_RE.match(line)
        if match:
            headings.append(Heading(len(match.group(1)), match.group(2).strip(), idx))

    sections: list[tuple[Heading, str]] = []
    for pos, heading in enumerate(headings):
        end = headings[pos + 1].line if pos + 1 < len(headings) else len(lines)
        body = "\n".join(lines[heading.line + 1 : end]).strip()
        if body:
            sections.append((heading, body))
    return sections


def extract_meta(body: str) -> tuple[dict[str, str], str]:
    """Extract the first <!-- meta: key=val --> comment from body.

    Returns (meta_dict, body_with_comment_removed). All meta fields are optional.
    """
    lines = body.split("\n")
    meta: dict[str, str] = {}
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        m = META_RE.search(stripped)
        if m:
            for pair in m.group(1).split():
                if "=" in pair:
                    k, _, v = pair.partition("=")
                    meta[k.strip()] = v.strip()
            lines.pop(i)
            return meta, "\n".join(lines).strip()
        break  # only check the first non-empty line
    return meta, body


def build_chunks(text: str) -> list[Chunk]:
    """Build chunks from addendum-clean.md (main document)."""
    chunks: list[Chunk] = []
    current_pp: tuple[str, str] | None = None
    seen: dict[str, int] = {}

    for heading, body in parse_sections(text):
        pp_match = PP_RE.search(heading.title)
        if heading.level == 1 and pp_match:
            current_pp = (f"pp{pp_match.group(1)}", heading.title)
            continue
        if heading.level == 1:
            current_pp = None

        meta, clean_body = extract_meta(body)

        pp = meta.get("pp") or (current_pp[0] if current_pp else None)
        section_type = meta.get("section_type") or section_type_for(heading.title, pp)
        if pp and current_pp:
            base_id = f"{pp}-{section_type}"
            breadcrumb = f"{current_pp[1]} > {heading.title}"
        else:
            base_id = slugify(heading.title)
            breadcrumb = heading.title

        count = seen.get(base_id, 0)
        seen[base_id] = count + 1
        chunk_id = base_id if count == 0 else f"{base_id}-{count + 1}"
        chunk_text = f"{breadcrumb}\n\n{clean_body}".strip()

        related_controls = [c.strip() for c in meta.get("controls", "").split(",") if c.strip()]
        related_examples = [e.strip() for e in meta.get("examples", "").split(",") if e.strip()]

        chunks.append(
            Chunk(
                id=chunk_id,
                pp=pp,
                section_type=section_type,
                title=heading.title,
                breadcrumb=breadcrumb,
                text=chunk_text,
                url=f"{PUBLIC_BASE_URL}/{chunk_id}/",
                char_count=len(chunk_text),
                bcm_process=meta.get("bcm_process", ""),
                ai_capability=meta.get("capability", ""),
                risk_level=meta.get("risk", ""),
                confidentiality=meta.get("confidentiality", ""),
                intended_user=meta.get("user", ""),
                output_type=meta.get("output", ""),
                mode=meta.get("mode", ""),
                related_controls=related_controls,
                related_examples=related_examples,
                source_file="addendum-clean.md",
            )
        )
    return chunks


def build_knowledge_chunks(text: str, filename: str) -> list[Chunk]:
    """Build chunks from a knowledge/*.md file."""
    file_key = filename.lower()
    default_section_type = KNOWLEDGE_FILE_TYPES.get(file_key, "section")
    file_prefix = re.sub(r"[^a-z0-9]+", "-", file_key.replace(".md", "")).strip("-")

    chunks: list[Chunk] = []
    seen: dict[str, int] = {}

    for heading, body in parse_sections(text):
        meta, clean_body = extract_meta(body)

        section_type = meta.get("section_type") or default_section_type
        pp_val = meta.get("pp") or None

        breadcrumb = heading.title
        base_id = f"{file_prefix}-{slugify(heading.title)}"
        count = seen.get(base_id, 0)
        seen[base_id] = count + 1
        chunk_id = base_id if count == 0 else f"{base_id}-{count + 1}"

        chunk_text = f"{breadcrumb}\n\n{clean_body}".strip()

        related_controls = [c.strip() for c in meta.get("controls", "").split(",") if c.strip()]
        related_examples = [e.strip() for e in meta.get("examples", "").split(",") if e.strip()]

        chunks.append(
            Chunk(
                id=chunk_id,
                pp=pp_val,
                section_type=section_type,
                title=heading.title,
                breadcrumb=breadcrumb,
                text=chunk_text,
                url=f"{PUBLIC_BASE_URL}/{chunk_id}/",
                char_count=len(chunk_text),
                bcm_process=meta.get("bcm_process", ""),
                ai_capability=meta.get("capability", ""),
                risk_level=meta.get("risk", ""),
                confidentiality=meta.get("confidentiality", ""),
                intended_user=meta.get("user", ""),
                output_type=meta.get("output", ""),
                mode=meta.get("mode", ""),
                related_controls=related_controls,
                related_examples=related_examples,
                source_file=filename,
            )
        )
    return chunks


def build_index(chunks: list[Chunk]) -> dict[str, object]:
    return {
        "title": "BCI AI Addendum knowledge index",
        "chunk_count": len(chunks),
        "topics": [
            {
                "id": chunk.id,
                "title": chunk.title,
                "breadcrumb": chunk.breadcrumb,
                "pp": chunk.pp,
                "section_type": chunk.section_type,
                "url": chunk.url,
            }
            for chunk in chunks
        ],
    }


def validate(chunks: list[Chunk]) -> None:
    ids = {chunk.id for chunk in chunks}
    required = {
        f"pp{pp}-{section}"
        for pp in range(1, 7)
        for section in SECTION_TYPES.values()
    }
    missing = sorted(required - ids)
    if missing:
        raise SystemExit(f"missing required chunks: {', '.join(missing)}")
    if not 45 <= len(chunks) <= 300:
        raise SystemExit(f"unexpected chunk count: {len(chunks)}")
    bad = [chunk.id for chunk in chunks if "{#" in chunk.text or "Table of content" in chunk.text]
    if bad:
        raise SystemExit(f"cleaning artifacts remain in chunks: {', '.join(bad[:10])}")

    # Soft check: note missing knowledge content types (warn only, does not fail the build)
    present_types = {chunk.section_type for chunk in chunks}
    for kt in sorted(REQUIRED_KNOWLEDGE_TYPES):
        if kt not in present_types:
            print(f"note: no chunks of type '{kt}' — knowledge/{kt.replace('_', '-')}.md may be missing")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--knowledge-dir", type=Path, default=DEFAULT_KNOWLEDGE_DIR)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    args = parser.parse_args()

    chunks = build_chunks(args.source.read_text(encoding="utf-8"))

    if args.knowledge_dir.is_dir():
        for md_file in sorted(args.knowledge_dir.glob("*.md")):
            if md_file.name.lower() not in KNOWLEDGE_FILE_TYPES:
                continue  # skip vault nav files (index.md, etc.)
            knowledge_chunks = build_knowledge_chunks(
                md_file.read_text(encoding="utf-8"),
                md_file.name,
            )
            chunks.extend(knowledge_chunks)

    validate(chunks)
    args.data_dir.mkdir(parents=True, exist_ok=True)
    (args.data_dir / "chunks.json").write_text(
        json.dumps([asdict(chunk) for chunk in chunks], indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (args.data_dir / "index.json").write_text(
        json.dumps(build_index(chunks), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {len(chunks)} chunks to {args.data_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
