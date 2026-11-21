"""The --review commands: a numbered list of the parsed constraints, edited by number before solving.

The numbers are read off the printed list (the user never counts their own sentences), and they stay fixed until
the text is parsed again, so `d 3 4` is exactly `d 3` then `d 4`. A command that is partly invalid changes nothing.
See plan.md, CLI.
"""
import pytest
from hypothesis import given
from hypothesis import strategies as st

from scheduler.cli import Review
from scheduler.config import Config
from scheduler.models import Compact, DayOff, NoClassBefore, PreferInstructor
from scheduler.parsing.base import ParseResult, Span, UnparsedFragment
from scheduler.parsing.router import RoutedParse

C1 = NoClassBefore(time="10:00")
C2 = DayOff(day="Fri")
C3 = PreferInstructor(name="Cohen", strength="soft")
C4 = Compact()  # soft only
C5 = DayOff(day="Sun", strength="soft", weight=3)


def routed(*constraints, unparsed=(), spans=(), path="rules") -> RoutedParse:
    return RoutedParse(result=ParseResult(constraints=list(constraints), spans=list(spans),
                                          unparsed=[UnparsedFragment(text=t, reason="not_understood") for t in unparsed]),
                       path=path)


class Parses:
    """The parse function a Review uses for `t` and `+`: queued results, and the texts it was given."""

    def __init__(self, *results):
        self.results = list(results)
        self.texts = []

    def __call__(self, text):
        self.texts.append(text)
        return self.results.pop(0)


def review(*constraints, parses=None, **kw) -> Review:
    return Review(routed(*constraints, **kw), parses or Parses())


def listed(r: Review) -> dict:
    return dict(r.items)


# ---- the commands, one number at a time ----

def test_the_list_is_numbered_from_one_in_parse_order():
    assert listed(review(C1, C2, C3)) == {1: C1, 2: C2, 3: C3}


def test_d_deletes_a_constraint():
    r = review(C1, C2, C3)
    r.command("d 2")
    assert listed(r) == {1: C1, 3: C3}


def test_h_switches_hard_and_soft():
    r = review(C1, C3)
    r.command("h 1")
    r.command("h 2")
    assert r.items[1].strength == "soft" and r.items[2].strength == "hard"
    assert r.items[1].model_dump(exclude={"strength"}) == C1.model_dump(exclude={"strength"})


def test_w_sets_the_weight_with_the_last_number():
    r = review(C1, C3)
    r.command("w 2 8")
    assert r.items[2].weight == 8 and r.items[1].weight == 5


def test_numbers_stay_fixed_after_a_delete():
    r = review(C1, C2, C3, C4)
    r.command("d 2")
    r.command("d 3")  # the original #3, not what is now third in the list
    assert listed(r) == {1: C1, 4: C4}


# ---- several numbers at once: exactly like one command per number ----

@pytest.mark.parametrize("together, apart", [
    ("d 3 4", ["d 3", "d 4"]),
    ("d 2,4", ["d 2", "d 4"]),
    ("d 1-3", ["d 1", "d 2", "d 3"]),
    ("h 1 3", ["h 1", "h 3"]),
    ("h 2-5", ["h 2", "h 3", "h 4", "h 5"]),
    ("w 3 4 7", ["w 3 7", "w 4 7"]),
    ("w 1-2 5 9", ["w 1 9", "w 2 9", "w 5 9"]),
])
def test_several_numbers_in_one_command_act_like_one_command_each(together, apart):
    a = review(C1, C2, C3, C5, C1.model_copy(update={"time": 11 * 60}))
    b = review(*a.items.values())
    a.command(together)
    for line in apart:
        b.command(line)
    assert listed(a) == listed(b)


# ---- invalid commands change nothing and say why ----

@pytest.mark.parametrize("line, says", [
    ("d 9", "#9"),
    ("d 2 9", "#9"),             # partly valid: still nothing changes
    ("d 0", "#0"),
    ("d x", "'x'"),
    ("d 3-1", "3-1"),
    ("d 2 2", "twice"),
    ("d", "e.g."),
    ("h 2 4", "can only be soft"),  # #4 is compact
    ("w 1 11", "1 to 10"),
    ("w 1 0", "1 to 10"),
    ("w 7", "e.g."),              # a weight but no constraint number
    ("w 1 x", "'x'"),
])
def test_an_invalid_command_changes_nothing_and_says_why(line, says):
    r = review(C1, C2, C3, C4)
    before = listed(r)
    step = r.command(line)
    assert listed(r) == before
    assert says in step.message


def test_a_number_already_deleted_is_refused():
    r = review(C1, C2, C3)
    r.command("d 2")
    step = r.command("d 2 3")
    assert listed(r) == {1: C1, 3: C3}
    assert "#2" in step.message and "deleted" in step.message


def test_a_weight_on_a_hard_rule_is_set_and_the_user_is_told_it_only_counts_when_soft():
    r = review(C1)
    step = r.command("w 1 9")
    assert r.items[1].weight == 9
    assert "soft" in step.message


# ---- adding, re-parsing, help, solving, quitting ----

def test_plus_adds_the_phrases_constraints_with_the_next_numbers():
    parses = Parses(routed(C3, C4))
    r = review(C1, C2, parses=parses)
    r.command("d 2")
    step = r.command("+ prefer Cohen, compact")
    assert parses.texts == ["prefer Cohen, compact"]
    assert listed(r) == {1: C1, 3: C3, 4: C4}  # 2 stays retired
    assert step.action == "again"


def test_plus_reports_what_it_didnt_understand():
    r = review(C1, parses=Parses(routed(C2, unparsed=["and keep it light"])))
    step = r.command("+ fridays off and keep it light")
    assert listed(r) == {1: C1, 2: C2}
    assert "and keep it light" in step.message


def test_plus_doesnt_add_a_constraint_already_in_the_list():
    r = review(C1, parses=Parses(routed(C1, C2)))
    step = r.command("+ no classes before 10, fridays off")
    assert listed(r) == {1: C1, 2: C2}
    assert "Already in the list: no class before 10:00." in step.message


def test_plus_with_nothing_understood_adds_nothing():
    r = review(C1, parses=Parses(routed(unparsed=["blah"])))
    r.command("+ blah")
    assert listed(r) == {1: C1}


def test_t_parses_new_text_renumbers_from_one_and_says_edits_were_replaced():
    parses = Parses(routed(C3, C4, unparsed=["meh"]))
    r = review(C1, C2, parses=parses)
    r.command("w 1 9")
    step = r.command("t prefer Cohen, compact, meh")
    assert parses.texts == ["prefer Cohen, compact, meh"]
    assert listed(r) == {1: C3, 2: C4}
    assert [f.text for f in r.routed.result.unparsed] == ["meh"]
    assert "replaced" in step.message


def test_a_bare_t_asks_for_the_new_text():
    assert review(C1).command("t").action == "ask_text"


@pytest.mark.parametrize("line", ["?", "help", "x 1", "delete 2"])
def test_help_and_unknown_commands_print_the_command_list(line):
    r = review(C1)
    step = r.command(line)
    assert listed(r) == {1: C1}
    for cmd in ("d ", "h ", "w ", "t", "+ ", "q"):
        assert cmd in step.message


@pytest.mark.parametrize("line, action", [("", "solve"), ("   ", "solve"), ("q", "quit")])
def test_enter_solves_and_q_quits(line, action):
    assert review(C1).command(line).action == action


def test_the_constraints_to_solve_are_whats_left_in_number_order():
    r = review(C1, C2, C3, parses=Parses(routed(C4)))
    r.command("d 1")
    r.command("+ compact")
    assert r.constraints() == [C2, C3, C4]


# ---- what the list looks like ----

def test_the_list_shows_numbers_descriptions_and_strength():
    text = review(C1, C3).render()
    assert "1. no class before 10:00 (hard)" in text
    assert "2. prefer Cohen (soft, weight 5)" in text


def test_the_list_shows_what_was_left_unparsed():
    assert "I'm not a morning person" in review(C1, unparsed=["I'm not a morning person"]).render()


def test_verbose_groups_constraints_under_the_phrase_that_made_them():
    spans = [Span(text="no classes before 10", constraints=[C1]), Span(text="fridays off", constraints=[C2])]
    text = review(C1, C2, spans=spans).render(verbose=True)
    assert text.index('"no classes before 10"') < text.index("1. no class before") < text.index('"fridays off"') \
        < text.index("2. Fri off")


def test_verbose_on_the_llm_path_is_a_flat_list():
    # the LLM returns no spans, so there's nothing to group by
    text = review(C1, C2, path="llm").render(verbose=True)
    assert '"' not in text.split("\n", 1)[0] and "1. no class before" in text


def test_without_verbose_there_is_no_grouping():
    spans = [Span(text="no classes before 10", constraints=[C1])]
    assert '"no classes before 10"' not in review(C1, spans=spans).render()


# ---- random command sequences against a simple model ----

KINDS = [C1, C2, C3, C4, C5, NoClassBefore(time="09:00", strength="soft", weight=2), DayOff(day="Mon")]


def model_apply(items: dict, op: str, numbers: list[int], weight: int | None) -> dict:
    """The rule in plain code: all numbers must be live, distinct and allowed, or nothing changes."""
    if not numbers or len(set(numbers)) != len(numbers) or any(n not in items for n in numbers):
        return items
    if op == "w" and not (weight is not None and 1 <= weight <= 10):
        return items
    if op == "h" and any(items[n].kind == "compact" and items[n].strength == "soft" for n in numbers):
        return items
    out = dict(items)
    for n in numbers:
        if op == "d":
            del out[n]
        elif op == "h":
            out[n] = out[n].model_copy(update={"strength": "soft" if out[n].strength == "hard" else "hard"})
        else:
            out[n] = out[n].model_copy(update={"weight": weight})
    return out


commands = st.tuples(st.sampled_from("dhw"), st.lists(st.integers(0, 8), max_size=4), st.integers(0, 11))


@given(st.lists(st.sampled_from(KINDS), min_size=1, max_size=7), st.lists(commands, max_size=12))
def test_random_command_sequences_match_the_model_and_numbers_never_shift(start, cmds):
    r = review(*start)
    expected = dict(r.items)
    for op, numbers, weight in cmds:
        line = " ".join([op, *map(str, numbers), *([str(weight)] if op == "w" else [])])
        r.command(line)
        expected = model_apply(expected, op, numbers, weight if op == "w" else None)
        assert listed(r) == expected
    assert set(r.items) <= set(range(1, len(start) + 1))
