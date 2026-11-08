"""Hypothesis strategies: small random catalogs and constraints. See test-core-logic/property-tests.md."""
from hypothesis import reject
from hypothesis import strategies as st

from scheduler.models import (
    AvoidGroup,
    AvoidInstructor,
    Compact,
    DayOff,
    ExcludeCourse,
    FreeBlock,
    MaxCredits,
    MaxDays,
    MaxGap,
    MaxHoursPerDay,
    MinCredits,
    MustTake,
    NoClassAfter,
    NoClassBefore,
    PreferGroup,
    PreferInstructor,
    TakeNOf,
)
from tests.helpers.catalog_builder import catalog, course, grp

DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri"]
ALL_DAYS = DAYS + ["Sat"]
# the default week first (Hypothesis shrinks towards it), then other real-world weeks
WEEKS = [DAYS, ["Mon", "Tue", "Wed", "Thu", "Fri"], ["Sun", "Mon", "Tue", "Wed", "Thu"], ALL_DAYS[1:]]
# constraint times needn't be on the hour, unlike session times
TIMES = ["08:00", "09:00", "09:15", "10:00", "10:30", "11:45", "12:00", "13:30", "14:00", "16:00", "17:20", "18:00"]
INSTRUCTORS = ["Cohen", "Levi", None]


def _slots(week):
    return st.builds(
        lambda day, start, length: (day, start, min(start + length, 20)),
        st.sampled_from(week), st.integers(8, 19), st.integers(1, 3),
    )


def _own_sessions_clash(slots) -> bool:
    return any(
        a[0] == b[0] and a[1] < b[2] and b[1] < a[2]
        for i, a in enumerate(slots)
        for b in slots[i + 1:]
    )


def _session_sets(week):
    return st.lists(_slots(week), min_size=1, max_size=2).filter(lambda s: not _own_sessions_clash(s))


@st.composite
def _groups(draw, week) -> list[dict]:
    built = []
    last = None
    for i in range(draw(st.integers(1, 3))):
        # reuse the previous group's times some of the time, so merging gets exercised
        slots = last if last is not None and draw(st.booleans()) else draw(_session_sets(week))
        last = slots
        texts = [f"{day} {start}-{end}" for day, start, end in slots]
        built.append(grp(str(i + 1), *texts, instructor=draw(st.sampled_from(INSTRUCTORS))))
    return built


@st.composite
def catalogs(draw):
    week = draw(st.sampled_from(WEEKS))
    courses = []
    for course_id in ["A", "B", "C", "D"][: draw(st.integers(1, 4))]:
        components = {"lecture": draw(_groups(week))}
        if draw(st.booleans()):
            components["lab"] = draw(_groups(week))
        courses.append(course(course_id, credits=draw(st.sampled_from([2, 3, 3.5, 5])), **components))
    return catalog(*courses, week=None if week == DAYS else week)


def _strength() -> st.SearchStrategy[dict]:
    return st.fixed_dictionaries({
        "strength": st.sampled_from(["hard", "soft"]),
        "weight": st.integers(1, 10),
    })


def _constraints_by_kind(cat) -> dict:
    """A strategy for each constraint kind this catalog can support."""
    ids = [c.id for c in cat.courses]
    time = st.sampled_from(TIMES)
    # mostly days in the catalog's week, sometimes any day (a rule on a day outside the week has no effect)
    day = st.sampled_from(cat.week) | st.sampled_from(ALL_DAYS)
    groups = [
        (c.id, component, g.group)
        for c in cat.courses
        for component, gs in c.components.items()
        for g in gs
    ]
    teachers = sorted({g.instructor for c in cat.courses for gs in c.components.values() for g in gs if g.instructor})

    def with_strength(build, *args):
        return st.builds(lambda s, *a: build(*a, **s), _strength(), *args)

    def pools():
        return st.lists(st.sampled_from(ids), min_size=1, unique=True).flatmap(
            lambda pool: with_strength(lambda n, **s: TakeNOf(courses=pool, n=n, **s), st.integers(1, len(pool)))
        )

    def group_rule(rule):
        return with_strength(
            lambda g, **s: rule(course=g[0], component=g[1], group=g[2], **s), st.sampled_from(groups)
        )

    options = {
        "must_take": with_strength(lambda c, **s: MustTake(course=c, **s), st.sampled_from(ids)),
        "exclude_course": with_strength(lambda c, **s: ExcludeCourse(course=c, **s), st.sampled_from(ids)),
        "take_n_of": pools(),
        "no_class_before": with_strength(
            lambda t, d, **s: NoClassBefore(time=t, days=d, **s),
            time, st.none() | st.lists(day, min_size=1, max_size=2, unique=True),
        ),
        "no_class_after": with_strength(
            lambda t, d, **s: NoClassAfter(time=t, days=d, **s),
            time, st.none() | st.lists(day, min_size=1, max_size=2, unique=True),
        ),
        "day_off": with_strength(lambda d, **s: DayOff(day=d, **s), day),
        "max_days": with_strength(lambda n, **s: MaxDays(n=n, **s), st.integers(1, 4)),
        "max_hours_per_day": with_strength(lambda h, **s: MaxHoursPerDay(h=h, **s), st.integers(1, 6)),
        "free_block": with_strength(
            lambda d, start, minute, length, **s: FreeBlock(
                day=d, start=f"{start:02d}:{minute:02d}", end=f"{start + length:02d}:{minute:02d}", **s
            ),
            st.none() | day, st.integers(8, 18), st.sampled_from([0, 0, 15, 30, 45]), st.integers(1, 3),
        ),
        "max_gap": with_strength(
            lambda m, **s: MaxGap(minutes=m, **s), st.sampled_from([0, 30, 45, 60, 75, 90, 120, 180])
        ),
        "compact": st.integers(1, 10).map(lambda w: Compact(weight=w)),
        "prefer_group": group_rule(PreferGroup),
        "avoid_group": group_rule(AvoidGroup),
        "min_credits": with_strength(lambda n, **s: MinCredits(n=n, **s), st.sampled_from([3, 4.5, 6, 10])),
        "max_credits": with_strength(lambda n, **s: MaxCredits(n=n, **s), st.sampled_from([3, 4.5, 6, 10])),
    }
    if teachers:
        name = st.sampled_from(teachers)
        options["prefer_instructor"] = with_strength(lambda n, **s: PreferInstructor(name=n, **s), name)
        options["avoid_instructor"] = with_strength(lambda n, **s: AvoidInstructor(name=n, **s), name)
    return options


ALL_KINDS = [
    "must_take", "exclude_course", "take_n_of",
    "no_class_before", "no_class_after", "day_off", "max_days", "max_hours_per_day", "free_block", "max_gap", "compact",
    "prefer_group", "avoid_group", "prefer_instructor", "avoid_instructor",
    "min_credits", "max_credits",
]


def _one_constraint(cat):
    return st.one_of(list(_constraints_by_kind(cat).values()))


@st.composite
def problems(draw, kind=None):
    """(catalog, constraints): always selects at least one course, plus 0-5 other constraints.

    With `kind`, the constraints always include one of that kind, so rare kinds still get tested.
    The selection can mix hard and soft course requests: a blind spot here once hid a conflict bug.
    """
    cat = draw(catalogs())
    if kind in ("prefer_instructor", "avoid_instructor") and kind not in _constraints_by_kind(cat):
        reject()  # nobody teaches in this catalog, so there's no name to use
    ids = [c.id for c in cat.courses]
    pool = draw(st.lists(st.sampled_from(ids), min_size=1, unique=True))
    n = draw(st.integers(1, len(pool)))
    mixed = [MustTake(course=c, **draw(_strength())) for c in pool]
    selection = draw(st.sampled_from([
        [MustTake(course=c) for c in pool],
        [MustTake(course=c, strength="soft", weight=draw(st.integers(1, 10))) for c in pool],
        mixed,
        [TakeNOf(courses=pool, n=n)],
        [TakeNOf(courses=pool, n=n, strength="soft", weight=draw(st.integers(1, 10)))],
    ]))
    others = draw(st.lists(_one_constraint(cat), max_size=5))
    if kind is not None:
        others.insert(draw(st.integers(0, len(others))), draw(_constraints_by_kind(cat)[kind]))
    return cat, selection + others
