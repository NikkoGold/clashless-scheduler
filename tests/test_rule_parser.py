"""RuleParser: the phrase table in tests/data/phrases.yaml, plus behaviours a table can't express."""
import difflib
import json
from pathlib import Path

import pytest
import yaml
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from scheduler.catalog import load_catalog
from scheduler.constraints import check_references
from scheduler.models import AvoidGroup, DayOff, NoClassBefore, PreferGroup, parse_constraint
from scheduler.parsing.base import ParseResult, PreferenceParser, Span, UnparsedFragment
from scheduler.parsing.rules import RuleParser
from tests.helpers.catalog_builder import catalog, course, grp

DATA = Path(__file__).parent / "data"
SAMPLE = Path(__file__).parent.parent / "data" / "sample_courses.json"

CASE_KEYS = {"text", "catalog", "rules", "llm", "tags"}
RULES_KEYS = {"constraints", "unparsed"}
UNPARSED_KEYS = {"text", "reason", "candidates"}
# a case's optional `catalog:` names the catalog it's parsed against (default: phrase)
CATALOG_FILES = {"phrase": DATA / "phrase_catalog.json", "sample": SAMPLE, "fullname": DATA / "fullname_catalog.json"}


def load_phrase_cases(path: Path) -> list[dict]:
    """Load the phrase table, failing loudly on a malformed entry."""
    cases = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(cases, list), f"{path.name}: expected a list of cases"
    for case in cases:
        label = case.get("text", case) if isinstance(case, dict) else case
        assert isinstance(case, dict), f"case {label!r} is not a mapping"
        assert not set(case) - CASE_KEYS, f"case {label!r}: unknown keys {sorted(set(case) - CASE_KEYS)}"
        assert "text" in case and "rules" in case, f"case {label!r}: needs both text and rules"
        assert case.get("catalog", "phrase") in CATALOG_FILES, f"case {label!r}: unknown catalog {case['catalog']!r}"
        rules = case["rules"]
        assert set(rules) == RULES_KEYS, f"case {label!r}: rules needs exactly {sorted(RULES_KEYS)}"
        for frag in rules["unparsed"]:
            assert isinstance(frag, dict) and {"text", "reason"} <= set(frag) <= UNPARSED_KEYS, (
                f"case {label!r}: unparsed entries need text and reason (and optional candidates)"
            )
    return cases


CASES = load_phrase_cases(DATA / "phrases.yaml")
CATALOGS = {name: load_catalog(path) for name, path in CATALOG_FILES.items()}


def catalog_for(case: dict):
    return CATALOGS[case.get("catalog", "phrase")]


@pytest.fixture(scope="module")
def phrase_catalog():
    return CATALOGS["phrase"]


def _key(c) -> str:
    return json.dumps(c.model_dump(mode="json"), sort_keys=True)


def _keys(constraints) -> list[str]:
    return sorted(_key(c) for c in constraints)


# ---- the phrase table ----

@pytest.mark.parametrize("case", CASES, ids=[c["text"] for c in CASES])
def test_phrase(case):
    result = RuleParser().parse(case["text"], catalog_for(case))
    expected = case["rules"]
    assert _keys(result.constraints) == _keys(parse_constraint(c) for c in expected["constraints"])
    assert [f.model_dump() for f in result.unparsed] == [
        UnparsedFragment(**f).model_dump() for f in expected["unparsed"]
    ]


def test_every_phrase_result_only_names_things_in_the_catalog():
    # the solver rejects unknown names, so the rule parser must never emit one
    for case in CASES:
        cat = catalog_for(case)
        check_references(cat, RuleParser().parse(case["text"], cat).constraints)


def test_every_phrase_runs_against_both_catalogs_without_error():
    # a phrase written for one catalog must still parse safely against the other
    for case in CASES:
        for cat in CATALOGS.values():
            check_references(cat, RuleParser().parse(case["text"], cat).constraints)


# ---- every verb with every list form, on both catalogs (generated, not hand-picked) ----

# verb -> (kind for a course, kind for an instructor or None if it doesn't apply, strength)
VERBS = {
    "take": ("must_take", None, "hard"),
    "ideally take": ("must_take", None, "soft"),
    "drop": ("exclude_course", None, "hard"),
    "skip": ("exclude_course", None, "hard"),
    "don't take": ("exclude_course", None, "hard"),
    "prefer": ("must_take", "prefer_instructor", "soft"),
    "avoid": ("exclude_course", "avoid_instructor", "soft"),
    "never": ("exclude_course", "avoid_instructor", "hard"),
    "no": ("exclude_course", "avoid_instructor", "hard"),
}
LIST_FORMS = ["{} and {}", "{}, {} and {}", "{}, {}"]
# what the user types -> what the rule should name
NAMES = {
    "phrase": {"course": [("Algorithms", "ALG"), ("Databases", "DB"), ("CS101", "CS101")],
               "instructor": [("Cohen", "Cohen"), ("Adler", "Adler"), ("Levi", "Levi")]},
    "sample": {"course": [("XBI", "XBI"), ("Ground Vehicle Maintenance", "GVM"), ("NAV2", "NAV2")],
               "instructor": [("Brenner", "Brenner"), ("Dagan", "Dagan"), ("Erez", "Erez")]},
    "fullname": {"course": [("Statistics", "STAT"), ("PHYS", "PHYS"), ("Chemistry", "CHEM")],
                 "instructor": [("Levi", "Dana Levi"), ("Avi", "Avi Cohen"), ("Ben-David", "Ruth Ben-David")]},
}


def _list_cases():
    for cat_name, by_type in NAMES.items():
        for verb, (course_kind, instructor_kind, strength) in VERBS.items():
            for form in LIST_FORMS:
                for name_type, kind in (("course", course_kind), ("instructor", instructor_kind)):
                    if kind is None:
                        continue
                    picked = by_type[name_type][: form.count("{}")]
                    text = f"{verb} " + form.format(*(typed for typed, _ in picked))
                    yield pytest.param(cat_name, text, kind, strength, [value for _, value in picked], id=f"{cat_name}: {text}")


@pytest.mark.parametrize("cat_name, text, kind, strength, values", list(_list_cases()))
def test_each_verb_applies_to_every_item_of_a_list(cat_name, text, kind, strength, values):
    result = RuleParser().parse(text, CATALOGS[cat_name])
    assert result.unparsed == []
    field = "name" if kind.endswith("instructor") else "course"
    assert sorted((c.kind, getattr(c, field), c.strength) for c in result.constraints) == sorted(
        (kind, v, strength) for v in values
    )


# ---- the phrase file's own shape ----

def _write(tmp_path, text) -> Path:
    path = tmp_path / "phrases.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_a_phrase_case_with_an_unknown_key_fails_to_load(tmp_path):
    path = _write(tmp_path, '- text: "x"\n  rules: {constraints: [], unparsed: []}\n  rule: {}\n')
    with pytest.raises(AssertionError, match="unknown keys"):
        load_phrase_cases(path)


def test_a_phrase_case_without_rules_fails_to_load(tmp_path):
    with pytest.raises(AssertionError, match="needs both"):
        load_phrase_cases(_write(tmp_path, '- text: "x"\n  llm: {constraints: []}\n'))


def test_an_unparsed_entry_without_a_reason_fails_to_load(tmp_path):
    path = _write(tmp_path, '- text: "x"\n  rules: {constraints: [], unparsed: [{text: "x"}]}\n')
    with pytest.raises(AssertionError, match="text and reason"):
        load_phrase_cases(path)


# ---- ParseResult and the protocol ----

def test_rule_parser_is_a_preference_parser():
    assert isinstance(RuleParser(), PreferenceParser)


def test_parse_result_defaults_to_empty():
    result = ParseResult()
    assert result.constraints == [] and result.unparsed == [] and result.spans == []


def test_an_unparsed_fragment_rejects_an_unknown_reason():
    with pytest.raises(ValidationError):
        UnparsedFragment(text="x", reason="confused")


def test_an_unparsed_fragment_has_no_candidates_by_default():
    assert UnparsedFragment(text="x", reason="not_understood").candidates == []


def test_a_span_holds_its_text_and_constraints():
    span = Span(text="fridays off", constraints=[DayOff(day="Fri")])
    assert span.constraints == [DayOff(day="Fri")]


@pytest.mark.parametrize("text", ["", "   ", "\n\t"])
def test_empty_input_gives_nothing(text, phrase_catalog):
    assert RuleParser().parse(text, phrase_catalog) == ParseResult()


# ---- the week ----

def mon_to_fri():
    return catalog(course("A", lecture=[grp("1", "Mon 10-12")]), week=["Mon", "Tue", "Wed", "Thu", "Fri"])


def test_except_takes_days_from_the_catalogs_week():
    result = RuleParser().parse("no classes before 10 except Tuesdays", mon_to_fri())
    assert result.constraints == [NoClassBefore(time="10:00", days=["Mon", "Wed", "Thu", "Fri"])]


def test_weekdays_are_the_catalogs_week_without_fri_and_sat():
    result = RuleParser().parse("no classes before 10 on weekdays", mon_to_fri())
    assert result.constraints == [NoClassBefore(time="10:00", days=["Mon", "Tue", "Wed", "Thu"])]


def test_every_day_means_no_day_restriction_whatever_the_week():
    result = RuleParser().parse("no classes before 10 every day", mon_to_fri())
    assert result.constraints == [NoClassBefore(time="10:00")]


# ---- groups named without their component, in a catalog where that's ambiguous ----

def lab_and_tutorial_11():
    return catalog(course(
        "OLD", name="Officer Leadership",
        lecture=[grp("1", "Mon 10-12")],
        tutorial=[grp("11", "Tue 10-11"), grp("12", "Tue 11-12")],
        lab=[grp("11", "Wed 10-11")],
    ))


def test_a_group_number_in_several_components_is_ambiguous():
    result = RuleParser().parse("prefer OLD group 11", lab_and_tutorial_11())
    assert result.constraints == []
    assert [f.model_dump() for f in result.unparsed] == [
        {"text": "prefer OLD group 11", "reason": "ambiguous_name", "candidates": ["lab 11", "tutorial 11"]}
    ]


def test_naming_the_component_resolves_the_ambiguity():
    result = RuleParser().parse("prefer OLD tutorial group 11", lab_and_tutorial_11())
    assert result.constraints == [PreferGroup(course="OLD", component="tutorial", group="11", strength="soft")]


def test_a_group_number_in_only_one_component_needs_no_component():
    result = RuleParser().parse("prefer OLD group 12", lab_and_tutorial_11())
    assert result.constraints == [PreferGroup(course="OLD", component="tutorial", group="12", strength="soft")]


@pytest.mark.parametrize("word", ["tut", "recitation"])
def test_tutorial_abbreviations_count_when_the_catalog_has_a_tutorial(word):
    result = RuleParser().parse(f"prefer OLD {word} 12", lab_and_tutorial_11())
    assert result.constraints == [PreferGroup(course="OLD", component="tutorial", group="12", strength="soft")]


def test_lec_means_lecture_when_the_catalog_has_lectures():
    result = RuleParser().parse("avoid OLD lec 1", lab_and_tutorial_11())
    assert result.constraints == [AvoidGroup(course="OLD", component="lecture", group="1", strength="soft")]


# ---- names that are equally close ----

def test_two_names_equally_close_to_a_typo_are_ambiguous():
    cat = catalog(
        course("A", lecture=[grp("1", "Mon 10-12", instructor="Rosenberg")]),
        course("B", lecture=[grp("1", "Tue 10-12", instructor="Rosenburg")]),
    )
    result = RuleParser().parse("prefer Rosenbirg", cat)
    assert result.constraints == []
    assert [f.model_dump() for f in result.unparsed] == [
        {"text": "prefer Rosenbirg", "reason": "ambiguous_name", "candidates": ["Rosenberg", "Rosenburg"]}
    ]


def test_weekdays_in_a_week_of_only_fri_and_sat_is_not_understood():
    cat = catalog(course("A", lecture=[grp("1", "Fri 10-12")]), week=["Fri", "Sat"])
    result = RuleParser().parse("no classes before 10 on weekdays", cat)
    assert result.constraints == [] and [f.reason for f in result.unparsed] == ["not_understood"]


# ---- spans ----

def test_each_parsed_clause_is_a_span_with_its_original_text_in_order(phrase_catalog):
    result = RuleParser().parse("Fridays off, No classes before 10", phrase_catalog)
    assert [s.text for s in result.spans] == ["Fridays off", "No classes before 10"]
    assert result.spans[0].constraints == [DayOff(day="Fri")]
    assert result.spans[1].constraints == [NoClassBefore(time="10:00")]


def test_a_span_does_not_start_with_the_and_that_joined_it(phrase_catalog):
    result = RuleParser().parse("no classes before 10 on mondays, and fridays off", phrase_catalog)
    assert [s.text for s in result.spans] == ["no classes before 10 on mondays", "fridays off"]


def test_a_clause_with_several_constraints_is_one_span(phrase_catalog):
    result = RuleParser().parse("Mon and Wed off", phrase_catalog)
    assert len(result.spans) == 1
    assert result.spans[0].constraints == [DayOff(day="Mon"), DayOff(day="Wed")]


def test_unparsed_clauses_are_not_spans(phrase_catalog):
    result = RuleParser().parse("fridays off, I'm not a morning person", phrase_catalog)
    assert [s.text for s in result.spans] == ["fridays off"]


@pytest.mark.parametrize("case", CASES, ids=[c["text"] for c in CASES])
def test_spans_together_give_exactly_the_constraints(case):
    result = RuleParser().parse(case["text"], catalog_for(case))
    assert {_key(c) for s in result.spans for c in s.constraints} == {_key(c) for c in result.constraints}


# ---- duplicates ----

def test_the_same_constraint_twice_is_kept_once(phrase_catalog):
    result = RuleParser().parse("fridays off, Fridays off", phrase_catalog)
    assert result.constraints == [DayOff(day="Fri")]
    assert len(result.spans) == 2


# ---- robustness ----

@given(text=st.text(max_size=80))
def test_any_text_parses_without_error_and_fragments_come_from_the_input(text):
    cat = load_catalog(DATA / "phrase_catalog.json")
    result = RuleParser().parse(text, cat)
    for piece in [f.text for f in result.unparsed] + [s.text for s in result.spans]:
        assert piece and piece in text


VOCAB = [
    "no", "not", "never", "can't", "prefer", "ideally", "must", "avoid", "take", "drop", "keep", "free", "off",
    "before", "after", "by", "between", "and", "to", "from", "except", "on", "every", "day", "weekdays",
    "weekends", "fridays", "Mon", "sun", "lunch", "break", "gaps", "gap", "compact", "at", "most", "least",
    "max", "days", "hours", "minutes", "credits", "a", "week", "per", "10", "4pm", "12-13", "8-10pm", "13-12",
    "25", "4.30pm", "noon", "Cohen", "Cohn", "Prof.", "Levi", "CS101", "lab", "lecture", "11", "2", "ground",
    "group", "section", "sec.", "lec", "tut", "13",
    "vehicle", "Databases", ",", ".", ";", "-", "–", "'", "please", "thanks",
]


@given(words=st.lists(st.sampled_from(VOCAB), max_size=12))
def test_word_salad_from_the_parsers_own_vocabulary_never_breaks_it(words):
    text = " ".join(words)
    cat = load_catalog(DATA / "phrase_catalog.json")
    result = RuleParser().parse(text, cat)
    for piece in [f.text for f in result.unparsed] + [s.text for s in result.spans]:
        assert piece and piece in text
    check_references(cat, result.constraints)


# ---- the manual-check phrase, on the sample catalog (parsing only, no solve results) ----

def test_the_manual_check_phrase_parses_fully_on_the_sample_catalog():
    from scheduler.models import FreeBlock, PreferInstructor

    result = RuleParser().parse("no classes before 10, prefer Cohen, keep 12-13 free", load_catalog(SAMPLE))
    assert result.unparsed == []
    assert _keys(result.constraints) == _keys([
        NoClassBefore(time="10:00"),
        PreferInstructor(name="Cohen", strength="soft"),
        FreeBlock(start="12:00", end="13:00"),
    ])


def test_adding_fridays_off_to_the_manual_check_phrase_adds_a_day_off():
    result = RuleParser().parse(
        "no classes before 10, fridays off, prefer Cohen, keep 12-13 free", load_catalog(SAMPLE)
    )
    assert result.unparsed == []
    assert DayOff(day="Fri") in result.constraints
    assert len(result.constraints) == 4


# ---- suggestions for unknown names: a course ID only for a word about as long as it (generated, both catalogs) ----

def _id_cases():
    for cat_name, cat in CATALOGS.items():
        for c in cat.courses:
            yield cat_name, c.id


def _suggested_or_read(text: str, cat, course_id: str) -> bool:
    result = RuleParser().parse(text, cat)
    return (any(getattr(c, "course", None) == course_id for c in result.constraints)
            or any(course_id in f.candidates for f in result.unparsed))


@pytest.mark.parametrize("cat_name, course_id", list(_id_cases()))
@pytest.mark.parametrize("pad", [("k", "lan"), ("", "zqw"), ("wq", "")])
def test_a_longer_word_containing_an_id_is_not_suggested_that_id(cat_name, course_id, pad):
    word = pad[0] + course_id.lower() + pad[1]
    name = next(c.name for c in CATALOGS[cat_name].courses if c.id == course_id).lower()
    if difflib.SequenceMatcher(None, word, name).ratio() >= 0.6:
        pytest.skip(f"{word!r} looks like the course name {name!r} itself, which may be suggested")  # "wqphys", "physics"
    assert not _suggested_or_read(f"take {word}", CATALOGS[cat_name], course_id)


# a two-letter ID with one letter wrong ("Dq" for DB) is half a different word, not a typo
@pytest.mark.parametrize("cat_name, course_id", [(n, i) for n, i in _id_cases() if len(i) >= 3])
def test_a_one_letter_typo_of_an_id_is_still_suggested(cat_name, course_id):
    typo = course_id[:-1] + ("q" if course_id[-1].lower() != "q" else "x")
    assert _suggested_or_read(f"take {typo}", CATALOGS[cat_name], course_id)


@pytest.mark.parametrize("cat_name, course_id", [(n, i) for n, i in _id_cases() if len(i) >= 3])
def test_an_id_typed_as_a_word_among_others_is_still_suggested(cat_name, course_id):
    # "prefer CS101 lab" (no group number): the user did name CS101
    assert _suggested_or_read(f"take {course_id} zzz", CATALOGS[cat_name], course_id)


# ---- instructors with several words in their name (generated over the full-name catalog) ----

def _name_word_cases():
    cat = CATALOGS["fullname"]
    instructors = sorted({g.instructor for c in cat.courses for gs in c.components.values() for g in gs if g.instructor})
    words = sorted({w for name in instructors for w in name.replace("-", " ").split()})
    for word in words:
        having = [name for name in instructors if word.lower() in name.replace("-", " ").lower().split()]
        for verb in ("prefer", "avoid", "never", "ideally avoid"):
            yield pytest.param(f"{verb} {word}", having, id=f"{verb} {word}")


@pytest.mark.parametrize("text, having", list(_name_word_cases()))
def test_any_word_of_an_instructors_name_picks_them_or_is_ambiguous(text, having):
    result = RuleParser().parse(text, CATALOGS["fullname"])
    if len(having) == 1:
        assert result.unparsed == [] and [c.name for c in result.constraints] == having
    else:
        assert result.constraints == []
        assert [(f.reason, f.candidates) for f in result.unparsed] == [("ambiguous_name", having)]
