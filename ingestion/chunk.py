"""Structure-aware chunker: one scraped document → a list of retrieval chunks.

Splits by **section semantics** (overview / enrolment conditions / offering /
learning outcomes / additional), not fixed token windows, so each chunk is a
coherent unit. Every chunk's text is prefixed with the document's code + title
so a retrieved chunk stands alone and is correctly attributed — critical when a
citation is shown out of context.

Empty sections are skipped (no empty chunks). Enrolment-condition chunks carry
parsed `rule_type` + `referenced_codes` metadata from `ingestion.rules`.

Input is the on-disk document written by `ingestion.scrape`: the provenance
envelope plus the handbook's `page_content` academic-item object.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ingestion.rules import ParsedRule, parse_rule, strip_html

# A UNSW course code, for pulling references out of the curriculum tree.
_CODE_RE = re.compile(r"\b[A-Z]{4}\d{4}\b")


@dataclass
class Chunk:
    """A self-contained retrieval unit — mirrors the non-embedding chunks columns."""

    doc_code: str
    career: str
    content_type: str
    title: str
    section_type: str
    text: str
    source_url: str
    scraped_at: str
    credit_points: int | None = None
    offering_terms: list[str] = field(default_factory=list)
    rule_type: str | None = None
    referenced_codes: list[str] = field(default_factory=list)


# Map the handbook's "Term 1, Term 2, Term 3" into compact {T1,T2,T3} codes.
_TERM_ALIASES = {
    "term 1": "T1",
    "term 2": "T2",
    "term 3": "T3",
    "summer term": "Summer",
    "summer canberra": "Summer",
}


def _parse_terms(offering_terms: str | None) -> list[str]:
    """Normalise 'Term 1, Term 2, Term 3' → ['T1','T2','T3']; unknowns kept verbatim."""
    if not offering_terms:
        return []
    terms: list[str] = []
    for raw in offering_terms.split(","):
        label = raw.strip()
        if not label:
            continue
        terms.append(_TERM_ALIASES.get(label.lower(), label))
    return terms


def _int_or_none(value: object) -> int | None:
    """Handbook numbers arrive as strings ('6'); coerce, tolerating junk/empty."""
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _source_url(doc: dict) -> str:
    """Absolute handbook URL, from the stored provenance (already absolute)."""
    return doc.get("url", "")


def _prefix(code: str, title: str, section_label: str) -> str:
    """The attribution header every chunk text carries."""
    return f"{code} {title} — {section_label}:"


def chunk_document(doc: dict) -> list[Chunk]:
    """Split one scraped document into section chunks. Empty sections are skipped."""
    pc = doc.get("page_content") or {}
    code = doc.get("code") or pc.get("code") or ""
    title = pc.get("title") or code
    career = doc.get("career", "")
    content_type = doc.get("content_type", "")
    scraped_at = doc.get("scraped_at", "")
    url = _source_url(doc)
    credit_points = _int_or_none(pc.get("credit_points"))

    offering_detail = pc.get("offering_detail")
    offering_terms_raw = (
        offering_detail.get("offering_terms") if isinstance(offering_detail, dict) else None
    )
    terms = _parse_terms(offering_terms_raw)

    def make(section_type: str, section_label: str, body: str, **extra) -> Chunk:
        return Chunk(
            doc_code=code,
            career=career,
            content_type=content_type,
            title=title,
            section_type=section_type,
            text=f"{_prefix(code, title, section_label)} {body}".strip(),
            source_url=url,
            scraped_at=scraped_at,
            credit_points=credit_points,
            offering_terms=terms,
            **extra,
        )

    chunks: list[Chunk] = []

    # 1) Overview — the human description. `overview` is usually empty; the real
    #    prose lives in `description`. Fall back the other way just in case.
    overview = strip_html(pc.get("description") or "") or strip_html(pc.get("overview") or "")
    if overview:
        chunks.append(make("overview", "Overview", overview))

    # 2) Enrolment conditions — one chunk per parsed rule line, enriched with the
    #    structured exclusion/equivalent associations the page also carries.
    chunks.extend(_enrolment_chunks(pc, make))

    # 2b) Structure — specialisations carry a curriculum tree (core/electives +
    #     course lists) instead of enrolment_rules; flatten it into one chunk.
    structure_body, structure_codes = _specialisation_structure(pc)
    if structure_body:
        chunks.append(
            make("structure", "Structure", structure_body, referenced_codes=structure_codes)
        )

    # 3) Offering — terms + UOC + level + campus as one prose sentence, so the
    #    "when can I take it / how many UOC" question has a chunk to hit.
    offering = _offering_sentence(pc, credit_points, terms)
    if offering:
        chunks.append(make("offering", "Offering", offering))

    # 4) Learning outcomes — the CLO list, if present.
    outcomes = _learning_outcomes(pc)
    if outcomes:
        chunks.append(make("learning_outcomes", "Learning outcomes", outcomes))

    # 5) Additional information / notes — only when non-empty.
    additional = "\n".join(
        part
        for part in (
            strip_html(pc.get("additional_information") or ""),
            strip_html(pc.get("notes") or ""),
        )
        if part
    )
    if additional:
        chunks.append(make("additional", "Additional information", additional))

    return chunks


def _enrolment_chunks(pc: dict, make) -> list[Chunk]:
    """Enrolment-condition chunks from `enrolment_rules` + structured associations.

    All use a generic "Enrolment conditions" prefix; the rule's own label
    ("Prerequisite:", "Exclusion:", …) is carried in the body so a chunk reads
    e.g. "COMP3311 … — Enrolment conditions: Prerequisite: COMP1531 AND …".
    """
    chunks: list[Chunk] = []

    # Free-text rules already start with their own "Label:" — keep verbatim.
    rules: list[ParsedRule] = []
    for entry in pc.get("enrolment_rules") or []:
        rules.extend(parse_rule(entry.get("description", "")))

    for rule in rules:
        chunks.append(
            make(
                "enrolment_conditions",
                "Enrolment conditions",
                rule.text,
                rule_type=rule.rule_type,
                referenced_codes=rule.referenced_codes,
            )
        )

    # Structured associations the free-text rules may not spell out. Only emit
    # when the free-text rules didn't already cover that rule_type; prepend the
    # label the structured body lacks.
    covered = {r.rule_type for r in rules}
    for field_name, rule_type, label in (
        ("exclusion", "exclusion", "Exclusion"),
        ("eqivalents", "equivalent", "Equivalent"),  # handbook's spelling
    ):
        assocs = _associations(pc.get(field_name))
        if assocs and rule_type not in covered:
            body = f"{label}: " + "; ".join(f"{c} {t}".strip() for c, t in assocs)
            codes = [c for c, _ in assocs if c]
            chunks.append(
                make(
                    "enrolment_conditions",
                    "Enrolment conditions",
                    body,
                    rule_type=rule_type,
                    referenced_codes=codes,
                )
            )

    return chunks


def _associations(raw) -> list[tuple[str, str]]:
    """Extract (code, title) pairs from an exclusion/equivalent association list."""
    pairs: list[tuple[str, str]] = []
    if not isinstance(raw, list):
        return pairs
    for assoc in raw:
        if not isinstance(assoc, dict):
            continue
        code = assoc.get("assoc_code") or ""
        title = assoc.get("assoc_title") or ""
        if code or title:
            pairs.append((code, title))
    return pairs


def _offering_sentence(pc: dict, credit_points: int | None, terms: list[str]) -> str:
    """A prose summary of when/where/how the course is offered.

    Phrased the way students actually ask ("how many credit points", "when can I
    take it") rather than as terse field values — a Phase 3 finding: the old
    "6 units of credit (UOC). offered in T1, T2, T3." embedded too far from
    natural-language offering questions to be retrieved. The synonyms here
    ("credit points", "take it in") are genuine, not keyword stuffing.
    """
    bits: list[str] = []
    if credit_points is not None:
        bits.append(f"worth {credit_points} units of credit (UOC, or credit points)")
    if terms:
        bits.append(f"offered in {', '.join(terms)} — the terms you can enrol in and take it")
    study_level = pc.get("study_level")
    if isinstance(study_level, list) and study_level:
        label = study_level[0].get("label")
        if label:
            bits.append(f"taught at {label} level")
    campus = pc.get("campus")
    if isinstance(campus, str) and campus.strip():
        bits.append(f"at {campus.strip()}")
    if not bits:
        return ""
    sentence = "; ".join(bits)
    return sentence[0].upper() + sentence[1:] + "."


def _leaf_codes(relationships) -> list[str]:
    """Course codes referenced by a curriculum node's relationship entries."""
    codes: list[str] = []
    for rel in relationships or []:
        if not isinstance(rel, dict):
            continue
        for source in ("child_record", "related_academic_item"):
            value = (rel.get(source) or {}).get("value") or ""
            for code in _CODE_RE.findall(value):
                codes.append(code)
    return codes


def _structure_lines(node: dict, depth: int, all_codes: list[str]) -> list[str]:
    """Flatten one curriculum container node into indented outline lines."""
    title = (node.get("title") or "").strip().rstrip(":")
    cp = str(node.get("credit_points") or "").strip()
    header = f"{title} ({cp} UOC)" if title and cp else title

    codes = _leaf_codes(node.get("relationship")) + _leaf_codes(node.get("dynamic_relationship"))
    all_codes.extend(codes)
    descs = [
        (rel.get("description") or "").strip()
        for rel in node.get("dynamic_relationship") or []
        if isinstance(rel, dict) and (rel.get("description") or "").strip()
    ]

    parts: list[str] = []
    if codes:
        parts.append(", ".join(dict.fromkeys(codes)))  # de-dupe, keep order
    parts.extend(descs)

    lines: list[str] = []
    line = f"{header}: {'; '.join(parts)}" if header and parts else (header or "; ".join(parts))
    if line.strip():
        lines.append("  " * depth + "- " + line.strip())
    for child in node.get("container") or []:
        lines.extend(_structure_lines(child, depth + 1, all_codes))
    return lines


def _specialisation_structure(pc: dict) -> tuple[str, list[str]]:
    """Flatten a specialisation's curriculum tree into (outline text, all codes)."""
    summary = strip_html(pc.get("structure_summary") or "")
    notes = strip_html(pc.get("html_description") or "")

    tree = pc.get("curriculumStructure")
    containers = tree.get("container") if isinstance(tree, dict) else None
    all_codes: list[str] = []
    outline: list[str] = []
    for group in containers or []:
        outline.extend(_structure_lines(group, 0, all_codes))

    blocks = [b for b in (summary, "\n".join(outline), notes) if b]
    body = "\n".join(blocks)
    return body, list(dict.fromkeys(all_codes))


def _learning_outcomes(pc: dict) -> str:
    """Join the CLO descriptions into one block, prefixed by their codes."""
    outcomes = pc.get("unit_learning_outcomes")
    if not isinstance(outcomes, list):
        return ""
    lines: list[str] = []
    for lo in outcomes:
        if not isinstance(lo, dict):
            continue
        desc = strip_html(lo.get("description") or "")
        if not desc:
            continue
        code = lo.get("code")
        lines.append(f"{code}: {desc}" if code else desc)
    return "\n".join(lines)
