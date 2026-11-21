// Results: hidden until the first solve. Tabs for the top-K schedules, the weekly grid (one colour per course; hovering
// a class highlights that course's other sessions and dims the rest), the session list (collapsed by default), and
// every rule met or broken, what differs from schedule 1, and the notes. When nothing fits, the conflicting rules.

import {h, icon, fill, courseColours} from "./dom.js";

const HOUR_PX = 50;

function hourOf(t) { return parseInt(t.slice(0, 2), 10); }
function endHourOf(t) { const [hh, mm] = t.split(":").map(Number); return mm ? hh + 1 : hh; }
function pad(hh) { return `${hh < 10 ? "0" : ""}${hh}:00`; }

export function render(state, actions) {
  const r = state.results;
  const section = document.getElementById("results");
  section.hidden = !r;
  if (!r) { section.replaceChildren(); return; }

  const schedules = r.schedules;
  if (!schedules.length) {
    fill("results",
      h("h2", {class: "h2"}, "No schedule found"),
      h("p", {}, `Courses: ${r.courses}`),
      r.note && h("p", {}, r.note),
      r.timed_out && h("p", {}, "The 3-minute limit was reached before any schedule was found."),
      r.conflict.length > 0 && h("div", {class: "notice", role: "status"}, icon("alert"),
        h("div", {}, h("strong", {}, "These rules can't all hold together:"),
          h("ul", {class: "plain"}, r.conflict.map((c) => h("li", {}, c))),
          h("span", {class: "line"}, "Switch one of them to soft, remove it, or change the course choice."))));
    return;
  }

  const tab = Math.min(state.tab, schedules.length - 1);
  const s = schedules[tab];
  const missed = (x) => x.rules.filter((rule) => !rule.met).length;

  fill("results",
    h("div", {class: "tabs", role: "tablist", "aria-label": "Schedules"},
      schedules.map((x, i) => {
        const m = missed(x);
        return h("button", {class: `tab${i === tab ? " on" : ""}`, role: "tab", "aria-selected": i === tab ? "true" : "false",
                            onclick: () => actions.pickTab(i)},
          `Schedule ${i + 1} `,
          h("span", {class: `tag${m ? " miss" : ""}`}, m ? `${m} preference${m === 1 ? "" : "s"} missed` : "every rule met"));
      }),
      h("span", {class: "res-note"}, [r.note, r.timed_out ? "Stopped at the 3-minute limit; each result shown is the best for its place." : null]
        .filter(Boolean).join(" "))),
    h("p", {class: "muted small"}, `Courses: ${r.courses}`),
    h("div", {class: "res-body"},
      h("div", {class: "gridcard"},
        h("div", {class: "scroll"}, grid(state, schedules, s)),
        h("button", {class: "listtoggle", "aria-expanded": state.listOpen ? "true" : "false", onclick: actions.toggleList},
          icon("chevron", {cls: `chev${state.listOpen ? " up" : ""}`}),
          state.listOpen ? "Hide the list view" : "Show as a list"),
        state.listOpen && table(s)),
      h("div", {class: "side"},
        s.differs.length > 0 && h("div", {},
          h("h3", {class: "h2"}, "Differs from schedule 1"),
          h("ul", {class: "plain"}, s.differs.map((d) => h("li", {}, d)))),
        h("div", {},
          h("h3", {class: "h2"}, "Every rule"),
          h("ul", {class: "rules"}, s.rules.slice().sort((a, b) => Number(a.met) - Number(b.met)).map((rule) =>
            h("li", {}, rule.met ? icon("check", {cls: "ok", label: "Met"}) : icon("x", {cls: "bad", label: "Broken"}),
              h("span", {}, rule.text))))),
        s.notes.length > 0 && h("div", {},
          h("h3", {class: "h2"}, "Notes"),
          h("ul", {class: "plain muted"}, s.notes.map((n) => h("li", {}, n)))))));
}

function grid(state, schedules, s) {
  const week = state.catalog ? state.catalog.week : ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri"];
  const all = schedules.flatMap((x) => x.sessions);
  // the same hours on every tab, so switching tabs doesn't move the grid
  const first = Math.min(...all.map((x) => hourOf(x.start)));
  const last = Math.max(...all.map((x) => endHourOf(x.end)));
  const index = (id) => state.catalog ? state.catalog.courses.findIndex((c) => c.id === id) : 0;

  const el = h("div", {class: "week", "aria-label": "Weekly timetable",
                       style: {gridTemplateColumns: `52px repeat(${week.length}, minmax(96px, 1fr))`,
                               gridTemplateRows: `30px repeat(${last - first}, ${HOUR_PX}px)`}},
    week.map((d, i) => h("div", {class: "dayhead", style: {gridColumn: i + 2, gridRow: 1}}, d)));
  for (let hh = first; hh < last; hh++) {
    el.append(h("div", {class: "hour", style: {gridColumn: 1, gridRow: hh - first + 2}}, pad(hh)));
    for (let d = 0; d < week.length; d++) el.append(h("div", {class: "cell", style: {gridColumn: d + 2, gridRow: hh - first + 2}}));
  }
  for (const x of s.sessions) {
    const col = week.indexOf(x.day) + 2;
    const block = h("button", {class: "block", "data-course": x.course,
                               style: {gridColumn: col, gridRow: `${hourOf(x.start) - first + 2} / ${endHourOf(x.end) - first + 2}`,
                                       ...courseColours(index(x.course))},
                               title: `${x.label}, ${x.day} ${x.start}-${x.end}${x.rooms ? `, ${x.rooms}` : ""}${x.instructors ? `, ${x.instructors}` : ""}`},
      h("span", {class: "b-title"}, x.label),
      h("span", {class: "b-meta"}, `${x.start}-${x.end}${x.rooms ? ` · ${x.rooms}` : ""}`));
    const on = () => highlight(x.course), off = () => highlight(null);
    block.addEventListener("mouseenter", on); block.addEventListener("focus", on);
    block.addEventListener("mouseleave", off); block.addEventListener("blur", off);
    el.append(block);
  }
  return el;
}

function table(s) {
  return h("table", {class: "sessions"},
    h("thead", {}, h("tr", {}, ["Day", "Time", "Class", "Room", "Instructor"].map((t) => h("th", {}, t)))),
    h("tbody", {}, s.sessions.map((x) =>
      h("tr", {"data-course": x.course}, h("td", {}, x.day), h("td", {class: "mono"}, `${x.start}-${x.end}`),
        h("td", {}, x.label), h("td", {}, x.rooms), h("td", {}, x.instructors)))));
}

// Highlight one course's sessions in the grid and the list, dim the others; null clears it.
function highlight(course) {
  for (const el of document.querySelectorAll("#results [data-course]")) {
    el.classList.toggle("hl", course !== null && el.dataset.course === course);
    el.classList.toggle("dim", course !== null && el.dataset.course !== course);
  }
}
