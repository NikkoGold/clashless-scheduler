// The Courses card: catalog choice at the top, search, each course's Required / Pool / Off switch, and pick N.
// Clicking a course expands its groups; hovering shows the same details in a bubble after a short pause.

import {h, icon, fill, bubble, courseColours} from "./dom.js";
import {modeOf, pool, pickCount} from "./app.js";

const open = new Set();   // expanded rows (not remembered: a reload starts collapsed)

export function render(state, actions) {
  const file = h("input", {type: "file", accept: ".json,application/json", hidden: true,
                           onchange: (e) => { if (e.target.files[0]) actions.chooseFile(e.target.files[0]); e.target.value = ""; }});
  const cat = state.catalog;
  fill("courses",
    h("div", {class: "card-head"},
      h("h2", {class: "h2"}, "Courses"),
      h("span", {class: "muted small", id: "course-count"})),
    h("div", {class: "catbar"},
      h("button", {class: "btn", onclick: () => file.click()}, icon("upload"), "Choose catalog file"), file,
      h("button", {class: "btn", onclick: actions.useSample}, "Try the sample catalog"),
      cat && h("span", {class: "muted catinfo"},
        `${state.catalogName} · term ${cat.term} · ${cat.courses.length} courses · ${cat.week[0]}–${cat.week[cat.week.length - 1]}`),
      state.catalogError && h("div", {class: "notice catinfo", role: "alert"}, icon("alert"), h("span", {}, state.catalogError))),
    cat
      ? [h("label", {class: "search"},
           icon("search", {cls: "muted"}),
           h("input", {type: "search", placeholder: "Search by ID, name or instructor", "aria-label": "Search courses",
                       value: state.query, oninput: (e) => actions.setQuery(e.target.value)})),
         h("div", {class: "list", id: "course-list"}),
         h("div", {id: "pool"})]
      : h("p", {class: "muted empty"}, "Choose a catalog file (JSON) to see its courses, or try the sample."));
  if (cat) renderList(state, actions);
}

function matches(c, q) {
  if (!q) return true;
  return `${c.id} ${c.name} ${c.instructors.join(" ")}`.toLowerCase().includes(q);
}

function details(c) {
  return h("div", {class: "tip-body"},
    h("strong", {}, `${c.id} · ${c.name}`),
    h("span", {}, `${c.credits} credits · ${c.required ? "required in the catalog" : "elective"}`),
    h("span", {}, `Instructors: ${c.instructors.join(", ") || "none listed"}`),
    h("span", {}, `${c.groups.length} groups: ${c.groups.map((g) => `${g.component} ${g.group}`).join(", ")}`));
}

export function renderList(state, actions) {
  const cat = state.catalog;
  if (!cat || !document.getElementById("course-list")) return;
  const q = state.query.trim().toLowerCase();
  const shown = cat.courses.filter((c) => matches(c, q));
  const modes = cat.courses.map((c) => modeOf(c.id));
  const p = pool();

  document.getElementById("course-count").textContent =
    `${modes.filter((m) => m === "required").length} required · ${p.length} in pool · ${shown.length} shown`;

  fill("course-list", shown.length ? shown.map((c) => {
    const mode = modeOf(c.id), isOpen = open.has(c.id);
    const colours = courseColours(cat.courses.indexOf(c));
    const seg = (m, label) => h("button", {class: `seg-btn${mode === m ? " on" : ""}`, "aria-pressed": mode === m ? "true" : "false",
                                           onclick: () => actions.setMode(c.id, m)}, label);
    const row = h("div", {class: `course${isOpen ? " open" : ""}${mode === "off" ? " off" : ""}`},
      h("div", {class: "course-head"},
        h("button", {class: "course-btn", "aria-expanded": isOpen ? "true" : "false",
                     onclick: () => { isOpen ? open.delete(c.id) : open.add(c.id); renderList(state, actions); }},
          h("span", {class: "swatch", style: {background: colours.background, borderColor: colours.borderColor}}),
          h("span", {class: "cid"}, c.id),
          h("span", {class: "cname"}, c.name),
          h("span", {class: "ccred"}, `${c.credits} cr`),
          icon("chevron", {cls: `chev${isOpen ? " up" : ""}`})),
        h("div", {class: "seg", role: "group", "aria-label": `Mode for ${c.id}`},
          seg("required", "Required"), seg("pool", "Pool"), seg("off", "Off"))),
      isOpen && h("ul", {class: "groups"}, c.groups.map((g) =>
        h("li", {}, h("span", {}, `${g.component} ${g.group}`),
          h("span", {class: "muted"}, g.instructor || "no instructor listed"),
          h("span", {class: "mono"}, g.times)))));
    bubble(row, () => details(c));
    return row;
  }) : h("p", {class: "muted empty"}, `No course matches "${state.query}".`));

  const n = pickCount();
  fill("pool", p.length > 0 && h("div", {class: "poolbox"},
    h("span", {}, "Pick"),
    h("span", {class: "stepper"},
      h("button", {"aria-label": "Pick one fewer", disabled: n <= 1, onclick: () => actions.setPick(n - 1)}, icon("minus")),
      h("span", {"aria-live": "polite"}, n),
      h("button", {"aria-label": "Pick one more", disabled: n >= p.length, onclick: () => actions.setPick(n + 1)}, icon("plus"))),
    h("span", {}, `of the ${p.length} course${p.length === 1 ? "" : "s"} in the pool: ${p.join(", ")}`)));
}
