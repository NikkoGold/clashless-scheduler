"""The test helpers fail loudly when misused, rather than quietly computing the wrong answer."""
import pytest

from scheduler.models import AvoidGroup, PreferInstructor
from tests.helpers.catalog_builder import catalog, course, grp
from tests.helpers.invariants import hard_member_allows, member_penalty


def sample():
    cat = catalog(course("A", lecture=[grp("1", "Sun 10-12", instructor="Cohen")]))
    return cat, cat.courses[0], cat.courses[0].components["lecture"][0]


@pytest.mark.parametrize("helper", [member_penalty, hard_member_allows])
@pytest.mark.parametrize(
    "rule",
    [PreferInstructor(name="Cohen"), AvoidGroup(course="A", component="lecture", group="1")],
    ids=lambda r: r.kind,
)
def test_member_helpers_reject_a_course_object_instead_of_its_id(helper, rule):
    # the solver's member_penalty takes the Course object; the checker's takes the id
    cat, course_obj, group = sample()
    with pytest.raises(TypeError, match="course id"):
        helper(rule, course_obj, "lecture", group, cat)


# ---- FakeProvider ----

from tests.helpers.fake_provider import FakeProvider  # noqa: E402


def test_fake_provider_returns_replies_in_order_and_raises_queued_exceptions():
    fake = FakeProvider([{"a": 1}, "raw text", RuntimeError("503")])
    assert fake.complete("s", "u", {}) == '{"a": 1}'
    assert fake.complete("s", "u", {}) == "raw text"
    with pytest.raises(RuntimeError, match="503"):
        fake.complete("s", "u", {})


def test_fake_provider_records_every_call():
    fake = FakeProvider(["x", "y"])
    fake.complete("sys1", "user1", {"type": "object"})
    fake.complete("sys2", "user2", {})
    assert fake.calls == [
        {"system": "sys1", "user": "user1", "schema": {"type": "object"}},
        {"system": "sys2", "user": "user2", "schema": {}},
    ]


def test_fake_provider_fails_loudly_when_called_more_times_than_replies_queued():
    fake = FakeProvider(["x"])
    fake.complete("s", "u", {})
    with pytest.raises(AssertionError, match="only 1 replies were queued"):
        fake.complete("s", "u", {})
