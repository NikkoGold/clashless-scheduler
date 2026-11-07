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
