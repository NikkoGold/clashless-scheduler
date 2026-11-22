"""Exports of one schedule, at any rank: an Excel workbook and a JSON file. Shared by the CLI (--export) and the web API.

The workbook has three sheets: the weekly grid (a column per day of the catalog's week, a row per hour, each class one
cell in its course's colour, merged over its hours), every session as a row, and every rule met or broken with the
notes, so the file keeps the user as fully informed as the screen does. Printing (PDF) is the browser's job.
"""
import colorsys
import io
import json
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from scheduler.models import Catalog, Constraint, Schedule, format_time
from scheduler.present import either, schedule_view

# The page's course hues, in catalog order. web/static/dom.js keeps a copy; tests/test_export.py checks they match.
HUES = [262, 200, 160, 32, 330, 95, 12, 285, 186, 48, 222, 138, 350, 305, 70]
FORMAT, VERSION = "clashless-schedule", 1
FORMATS = (".xlsx", ".json")
GRID_SHEET, SESSIONS_SHEET, RULES_SHEET = "Schedule", "Sessions", "Rules"
GRID_HEADER_ROW = 3  # row 1 the title, row 2 the courses, row 3 the days; the hours start below
XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class ExportError(ValueError):
    """A schedule that doesn't belong to its catalog, or a file name the exports can't write."""


# ---- colours ----

def hsl_to_hex(hue: float, saturation: float, lightness: float) -> str:
    """CSS hsl() (degrees, percent, percent) as "RRGGBB"."""
    r, g, b = colorsys.hls_to_rgb(hue / 360, lightness / 100, saturation / 100)
    return "".join(f"{round(x * 255):02X}" for x in (r, g, b))


def course_colours(index: int) -> dict[str, str]:
    """The page's light-mode colours for the course at this catalog position (see --cb-*, --cbd-*, --ct-* in style.css)."""
    hue = HUES[index % len(HUES)]
    return {"fill": hsl_to_hex(hue, 80, 91), "border": hsl_to_hex(hue, 50, 62), "text": hsl_to_hex(hue, 65, 22)}


# ---- checking a schedule sent back by the page ----

def check_schedule(catalog: Catalog, schedule: Schedule) -> None:
    """Every course, component, group and session must be the catalog's own, or the export would misstate it."""
    courses = {c.id: c for c in catalog.courses}
    for ch in schedule.choices:
        c = courses.get(ch.course)
        if c is None:
            raise ExportError(f"the schedule has course {ch.course}, which isn't in the catalog")
        groups = c.components.get(ch.component)
        if groups is None:
            raise ExportError(f"course {ch.course} has no component {ch.component!r} in the catalog")
        by_id = {g.group: g for g in groups}
        for g in ch.option.groups:
            if g.group not in by_id:
                raise ExportError(f"{ch.course} {ch.component} has no group {g.group} in the catalog")
            if g != by_id[g.group]:
                raise ExportError(f"{ch.course} {ch.component} group {g.group} doesn't match the catalog")


# ---- JSON ----

def to_json(catalog: Catalog, schedule: Schedule, rank: int, constraints: list[Constraint],
            best: Schedule | None = None, courses: str = "") -> dict:
    """Everything behind one schedule: the catalog, every rule solved with (course choice included), the schedule and
    its display text. No import reads it yet; it's kept complete so one can."""
    return {
        "format": FORMAT,
        "version": VERSION,
        "term": catalog.term,
        "rank": rank,
        "courses": courses,
        "catalog": catalog.model_dump(mode="json"),
        "constraints": [c.model_dump(mode="json") for c in constraints],
        "schedule": schedule.model_dump(mode="json"),
        "view": schedule_view(schedule, best or schedule, catalog),
    }


# ---- Excel ----

def to_xlsx(catalog: Catalog, schedule: Schedule, rank: int, best: Schedule | None = None, courses: str = "") -> bytes:
    view = schedule_view(schedule, best or schedule, catalog)
    wb = Workbook()
    _grid(wb.active, catalog, schedule, rank, courses)
    _sessions(wb.create_sheet(SESSIONS_SHEET), catalog, schedule)
    _rules(wb.create_sheet(RULES_SHEET), view)
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


BOLD = Font(bold=True)
TOP_LEFT = Alignment(vertical="top", horizontal="left", wrap_text=True)


def _grid(ws, catalog: Catalog, schedule: Schedule, rank: int, courses: str) -> None:
    ws.title = GRID_SHEET
    ws["A1"] = f"Schedule {rank} · {catalog.term}"
    ws["A1"].font = Font(bold=True, size=14)
    if courses:
        ws["A2"] = f"Courses: {courses}"
    ws.column_dimensions["A"].width = 8
    for i, day in enumerate(catalog.week):
        cell = ws.cell(GRID_HEADER_ROW, i + 2, day)
        cell.font = BOLD
        cell.alignment = Alignment(horizontal="center")
        ws.column_dimensions[get_column_letter(i + 2)].width = 24

    blocks = [(ch, s, k) for ch in schedule.choices for k, s in enumerate(ch.option.sessions)]
    if not blocks:
        return
    first = min(s.start for _, s, _ in blocks) // 60
    last = max(s.end for _, s, _ in blocks) // 60
    for hour in range(first, last):
        row = GRID_HEADER_ROW + 1 + hour - first
        ws.cell(row, 1, format_time(hour * 60)).alignment = Alignment(vertical="top")
        ws.row_dimensions[row].height = 36

    index = {c.id: i for i, c in enumerate(catalog.courses)}
    for ch, s, k in blocks:
        colours = course_colours(index[ch.course])
        col = catalog.week.index(s.day) + 2
        top = GRID_HEADER_ROW + 1 + s.start // 60 - first
        bottom = GRID_HEADER_ROW + s.end // 60 - first
        rooms = [g.sessions[k].room for g in ch.option.groups if g.sessions[k].room]
        instructors = [g.instructor for g in ch.option.groups if g.instructor]
        lines = [f"{ch.course} {ch.component} {either(ch.option.group_ids)}",
                 f"{format_time(s.start)}-{format_time(s.end)}",
                 either(list(dict.fromkeys(rooms))) if rooms else None,
                 either(list(dict.fromkeys(instructors))) if instructors else None]
        cell = ws.cell(top, col, "\n".join(x for x in lines if x))
        cell.fill = PatternFill("solid", fgColor=colours["fill"])
        cell.font = Font(color=colours["text"])
        cell.alignment = TOP_LEFT
        side = Side(style="thin", color=colours["border"])
        cell.border = Border(left=side, right=side, top=side, bottom=side)
        if bottom > top:
            ws.merge_cells(start_row=top, start_column=col, end_row=bottom, end_column=col)


def _sessions(ws, catalog: Catalog, schedule: Schedule) -> None:
    ws.append(["Course", "Name", "Component", "Group", "Day", "Start", "End", "Room", "Instructor"])
    names = {c.id: c.name for c in catalog.courses}
    order = {d: i for i, d in enumerate(catalog.week)}
    rows = []
    for ch in schedule.choices:
        for k, s in enumerate(ch.option.sessions):
            rooms = list(dict.fromkeys(g.sessions[k].room for g in ch.option.groups if g.sessions[k].room))
            instructors = list(dict.fromkeys(g.instructor for g in ch.option.groups if g.instructor))
            rows.append(((order[s.day], s.start),
                         [ch.course, names[ch.course], ch.component, either(ch.option.group_ids), s.day,
                          format_time(s.start), format_time(s.end), either(rooms) if rooms else None,
                          either(instructors) if instructors else None]))
    for _, row in sorted(rows, key=lambda r: r[0]):
        ws.append(row)
    _header(ws, [10, 28, 12, 10, 6, 7, 7, 14, 18])


def _rules(ws, view: dict) -> None:
    ws.append(["Rule", "Met", "Cost"])
    for r in view["rules"]:
        ws.append([r["text"], "met" if r["met"] else "broken", r["cost"]])
    for title, lines in (("Notes", view["notes"]), ("Differs from schedule 1", view["differs"])):
        if lines:
            ws.append([])
            ws.append([title])
            ws.cell(ws.max_row, 1).font = BOLD
            for line in lines:
                ws.append([line])
    _header(ws, [70, 8, 6])


def _header(ws, widths: list[int]) -> None:
    for i, w in enumerate(widths, 1):
        ws.cell(1, i).font = BOLD
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = "A2"


# ---- files (the CLI) ----

def format_of(path) -> str:
    suffix = Path(path).suffix.lower()
    if suffix not in FORMATS:
        raise ExportError(f"can export to .xlsx or .json, not {suffix or 'a file with no extension'} ({path})")
    return suffix


def write(path, catalog: Catalog, schedule: Schedule, rank: int, constraints: list[Constraint],
          best: Schedule | None = None, courses: str = "") -> None:
    """Write the schedule to `path`, in the format its extension names. OSError if it can't be written."""
    path = Path(path)
    if format_of(path) == ".json":
        text = json.dumps(to_json(catalog, schedule, rank, constraints, best, courses), indent=2, ensure_ascii=False)
        path.write_text(text + "\n", encoding="utf-8")
    else:
        path.write_bytes(to_xlsx(catalog, schedule, rank, best, courses))
