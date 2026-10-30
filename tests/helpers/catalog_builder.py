"""Short builders for test catalogs.

    cat = catalog(
        course("CS101", lecture=[grp("1", "Mon 10-12", "Wed 10-12", instructor="Cohen")],
                        lab=[grp("11", "Tue 14-16"), grp("12", "Tue 14-16")]),
    )
"""
from scheduler.catalog import parse_catalog
from scheduler.models import Catalog, MustTake


def _time(text: str) -> str:
    # "10" -> "10:00"; "10:30" is passed through so tests can check it gets rejected
    return text if ":" in text else f"{int(text):02d}:00"


def slot(text: str, room: str | None = None) -> dict:
    """`"Sun 10-12"` -> a session dict."""
    day, times = text.split()
    start, end = times.split("-")
    session = {"day": day, "start": _time(start), "end": _time(end)}
    if room is not None:
        session["room"] = room
    return session


def grp(group, *slots, instructor=None, attendance_required=True, parent=None, room=None) -> dict:
    g = {"group": str(group), "sessions": [slot(s, room) for s in slots]}
    if instructor is not None:
        g["instructor"] = instructor
    if parent is not None:
        g["parent"] = str(parent)
    if not attendance_required:
        g["attendance_required"] = False
    return g


def course(id, name=None, credits=3, required=False, **components) -> dict:
    c = {"id": id, "name": name or id, "credits": credits, "components": components}
    if required:
        c["required"] = True
    return c


def catalog(*courses, week=None, term="T", raw=False) -> Catalog | dict:
    data = {"term": term, "courses": list(courses)}
    if week is not None:
        data["week"] = week
    return data if raw else parse_catalog(data)


def must(*course_ids) -> list[MustTake]:
    return [MustTake(course=c) for c in course_ids]
