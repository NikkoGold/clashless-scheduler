"""Load and validate courses JSON; time helpers; merging groups that meet at identical times."""
import json
from pathlib import Path

from pydantic import ValidationError

from scheduler.models import Catalog, Course, Option, Session, format_time, parse_time  # noqa: F401 (time helpers)


class CatalogError(ValueError):
    pass


def load_catalog(path) -> Catalog:
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise CatalogError(f"{path.name}: not valid JSON ({e})") from None
    try:
        return parse_catalog(data)
    except CatalogError as e:
        raise CatalogError(f"{path.name}: {e}") from None


def parse_catalog(data: dict) -> Catalog:
    try:
        return Catalog.model_validate(data)
    except ValidationError as e:
        lines = [_describe(err, data) for err in e.errors()]
        raise CatalogError("invalid catalog:\n" + "\n".join(f"  - {line}" for line in lines)) from None


def _describe(err: dict, data) -> str:
    """Turn a Pydantic error into 'course CS101 > lecture > group 1 > session 1 > day: ... (got 'Mnd')'."""
    labels, node, prev = [], data, None
    loc = err["loc"]
    for i, key in enumerate(loc):
        try:
            node = node[key]
        except (KeyError, IndexError, TypeError):
            node = None
        if isinstance(key, int):
            if prev == "courses":
                labels.append(f"course {node['id']}" if isinstance(node, dict) and "id" in node else f"course #{key + 1}")
            elif prev == "sessions":
                labels.append(f"session {key + 1}")
            elif isinstance(node, dict) and "group" in node:
                labels.append(f"group {node['group']}")
            else:
                labels.append(f"#{key + 1}")
        elif key not in ("courses", "components", "sessions") or i == len(loc) - 1:
            # these are implied by the label that follows them ("course CS101", "session 1")
            labels.append(str(key))
        prev = key

    msg = err["msg"].removeprefix("Value error, ")
    if err["type"] not in ("value_error", "missing", "extra_forbidden") and isinstance(err.get("input"), (str, int, float)):
        msg += f" (got {err['input']!r})"
    return f"{' > '.join(labels)}: {msg}" if labels else msg


def overlaps(a: Session, b: Session) -> bool:
    """Back-to-back (10–12 then 12–14) is not an overlap."""
    return a.day == b.day and a.start < b.end and b.start < a.end


def merge_options(course: Course) -> dict[str, list[Option]]:
    """Per component, groups with identical session sets (in any order) become one option."""
    merged = {}
    for name, groups in course.components.items():
        by_times: dict[frozenset, list] = {}
        for g in groups:
            by_times.setdefault(frozenset((s.day, s.start, s.end) for s in g.sessions), []).append(g)
        merged[name] = [Option(groups=members) for members in by_times.values()]
    return merged
