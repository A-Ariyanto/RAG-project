"""Parse handbook enrolment-rule strings into queryable metadata.

The rule text lives in each course's `enrolment_rules[].description` as a small
HTML fragment, one rule per `<br/>`-separated line, e.g.::

    Prerequisite: COMP1531 AND (COMP2521 OR MTRN2500)<br/>Exclusion: COMP9021<br/>

We turn each line into a `ParsedRule`: its *type* (prerequisite / corequisite /
exclusion / equivalent / enrolment_requirement), the cleaned *text* (kept
verbatim — grounded answers need the exact wording), and the *course codes* it
references (so retrieval can filter on "which courses list COMP2521").

Deliberately **not** parsed: the boolean AND/OR/() structure into an AST. Raw
text plus the referenced-code set is enough for hybrid retrieval and a grounded
LLM answer; a rules engine is out of scope for v1.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass

# A UNSW course code: four letters + four digits (COMP1511, MTRN2500, DPST1091).
_COURSE_CODE_RE = re.compile(r"\b[A-Z]{4}\d{4}\b")

# <br>, <br/>, <br /> — the line separator inside a rule description.
_BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)

# Any remaining HTML tag, stripped after <br/> handling.
_TAG_RE = re.compile(r"<[^>]+>")

# Leading "Label:" on a rule line, mapped to our canonical rule_type. Order
# matters only for readability; labels are matched exactly (case-insensitively).
_RULE_LABELS: tuple[tuple[str, str], ...] = (
    ("prerequisite", "prerequisite"),
    ("pre-requisite", "prerequisite"),
    ("corequisite", "corequisite"),
    ("co-requisite", "corequisite"),
    ("exclusion", "exclusion"),
    ("equivalent", "equivalent"),
    ("enrolment requirements", "enrolment_requirement"),
    ("enrolment requirement", "enrolment_requirement"),
)


@dataclass(frozen=True)
class ParsedRule:
    """One enrolment-rule line: its type, cleaned text, and cited course codes."""

    rule_type: str
    text: str
    referenced_codes: list[str]


def strip_html(fragment: str) -> str:
    """Flatten a small HTML fragment to text: <br/> → newline, drop tags, unescape."""
    if not fragment:
        return ""
    text = _BR_RE.sub("\n", fragment)
    text = _TAG_RE.sub("", text)
    text = html.unescape(text)
    # Collapse runs of spaces/tabs but keep the newlines that separate rules.
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def _classify(line: str) -> str:
    """Map a rule line to a canonical rule_type by its leading label."""
    lowered = line.lower()
    for label, rule_type in _RULE_LABELS:
        if lowered.startswith(label):
            return rule_type
    # No recognised label — still a genuine enrolment condition, just untyped.
    return "enrolment_requirement"


def _course_codes(line: str) -> list[str]:
    """Distinct course codes cited in a line, in first-seen order."""
    seen: dict[str, None] = {}
    for code in _COURSE_CODE_RE.findall(line):
        seen.setdefault(code, None)
    return list(seen)


def parse_rule(description_html: str) -> list[ParsedRule]:
    """Parse one `enrolment_rules[].description` into its constituent rule lines."""
    text = strip_html(description_html)
    rules: list[ParsedRule] = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        rules.append(
            ParsedRule(
                rule_type=_classify(line),
                text=line,
                referenced_codes=_course_codes(line),
            )
        )
    return rules
