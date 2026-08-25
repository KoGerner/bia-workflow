"""Small in-memory retrieval layer for the AI Addendum MCP server."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path


DEFAULT_DATA_DIR = Path(__file__).resolve().parent / "data"  # file-relative (C15)
TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9-]{1,}", re.I)


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
    # Extended metadata (defaults allow loading chunks built before these fields existed)
    bcm_process: str = ""
    ai_capability: str = ""
    risk_level: str = ""
    confidentiality: str = ""
    intended_user: str = ""
    output_type: str = ""
    mode: str = ""
    related_controls: list = field(default_factory=list)
    related_examples: list = field(default_factory=list)
    source_file: str = ""


def tokenize(text: str) -> list[str]:
    return [token.lower() for token in TOKEN_RE.findall(text)]


class AddendumIndex:
    def __init__(self, data_dir: Path = DEFAULT_DATA_DIR):
        raw_chunks = json.loads((data_dir / "chunks.json").read_text(encoding="utf-8"))
        self.chunks = [Chunk(**item) for item in raw_chunks]
        self.index = json.loads((data_dir / "index.json").read_text(encoding="utf-8"))
        self._tokens = {
            chunk.id: tokenize(" ".join([chunk.title, chunk.breadcrumb, chunk.text]))
            for chunk in self.chunks
        }
        self._df: dict[str, int] = {}
        for terms in self._tokens.values():
            for term in set(terms):
                self._df[term] = self._df.get(term, 0) + 1

    def get(self, chunk_id: str) -> Chunk | None:
        return next((chunk for chunk in self.chunks if chunk.id == chunk_id), None)

    def search(
        self,
        query: str,
        pp: str | None = None,
        output_type: str | None = None,
        risk_level: str | None = None,
        confidentiality: str | None = None,
        bcm_process: str | None = None,
        mode: str | None = None,
        limit: int = 4,
    ) -> list[Chunk]:
        terms = tokenize(query)
        if not terms:
            return []
        pp_norm = pp.lower() if pp else None
        ot_norm = output_type.lower() if output_type else None
        rl_norm = risk_level.lower() if risk_level else None
        cf_norm = confidentiality.lower() if confidentiality else None
        bp_norm = bcm_process.lower() if bcm_process else None
        mode_norm = mode.lower() if mode else None

        scored: list[tuple[float, Chunk]] = []
        total_docs = max(len(self.chunks), 1)
        for chunk in self.chunks:
            # Hard filters: skip chunks that have a conflicting value for a filtered field.
            # Chunks with an empty field value always pass (un-annotated chunks remain visible).
            if pp_norm and chunk.pp != pp_norm:
                continue
            if ot_norm and chunk.output_type and chunk.output_type != ot_norm:
                continue
            if rl_norm and chunk.risk_level and chunk.risk_level != rl_norm:
                continue
            if cf_norm and chunk.confidentiality and chunk.confidentiality != cf_norm:
                continue
            if bp_norm and chunk.bcm_process and bp_norm not in chunk.bcm_process.lower():
                continue
            if mode_norm and chunk.mode:
                modes = [item.strip().lower() for item in chunk.mode.split(",") if item.strip()]
                if mode_norm not in modes:
                    continue

            chunk_terms = self._tokens[chunk.id]
            length_norm = 1.0 + math.log(max(len(chunk_terms), 1))
            score = 0.0
            for term in terms:
                tf = chunk_terms.count(term)
                if tf:
                    idf = math.log((1 + total_docs) / (1 + self._df.get(term, 0))) + 1
                    score += (1 + math.log(tf)) * idf
            if pp_norm and chunk.pp == pp_norm:
                score *= 1.15
            if score > 0:
                scored.append((score / length_norm, chunk))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [chunk for _, chunk in scored[:limit]]
