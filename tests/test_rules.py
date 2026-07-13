"""Unit tests for the enrolment-rule parser (pure, no DB, no torch)."""

from ingestion.rules import parse_rule, strip_html


def test_strip_html_normalises_breaks_and_unescapes():
    text = strip_html("Prerequisite: A &amp; B<br/><br/>Exclusion: C")
    assert text == "Prerequisite: A & B\n\nExclusion: C"


def test_parses_boolean_prerequisite_verbatim_with_all_codes():
    rules = parse_rule("Prerequisite: COMP1531 AND (COMP2521 OR MTRN2500)")
    assert len(rules) == 1
    rule = rules[0]
    assert rule.rule_type == "prerequisite"
    # Boolean structure is kept verbatim (no AST) for grounded answers.
    assert rule.text == "Prerequisite: COMP1531 AND (COMP2521 OR MTRN2500)"
    assert rule.referenced_codes == ["COMP1531", "COMP2521", "MTRN2500"]


def test_splits_multiple_rules_on_br_and_classifies_each():
    rules = parse_rule("Prerequisite: COMP2521<br/>Exclusion: COMP1927<br/>")
    assert [(r.rule_type, r.referenced_codes) for r in rules] == [
        ("prerequisite", ["COMP2521"]),
        ("exclusion", ["COMP1927"]),
    ]


def test_slash_separated_exclusion_extracts_both_codes():
    (rule,) = parse_rule("Exclusion: COMP1511/DPST1091")
    assert rule.rule_type == "exclusion"
    assert rule.referenced_codes == ["COMP1511", "DPST1091"]


def test_program_enrolment_rule_has_no_course_codes():
    (rule,) = parse_rule("Prerequisite: Enrolment in 3777 Bachelor of Cyber Security")
    assert rule.rule_type == "prerequisite"
    assert rule.referenced_codes == []


def test_unlabelled_line_falls_back_to_enrolment_requirement():
    (rule,) = parse_rule("Must be in good academic standing")
    assert rule.rule_type == "enrolment_requirement"


def test_deduplicates_repeated_codes_keeping_order():
    (rule,) = parse_rule("Prerequisite: COMP2521 or COMP2521 or COMP1927")
    assert rule.referenced_codes == ["COMP2521", "COMP1927"]


def test_empty_description_yields_no_rules():
    assert parse_rule("") == []
