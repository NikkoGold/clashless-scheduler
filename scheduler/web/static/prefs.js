// The preferences card: the text box, what wasn't understood, the review option, Find a schedule, the loading row,
// and (in review mode) the parsed rules as chips: hard or soft, weight, remove, and "add a phrase".

import {h, icon, fill, bubble} from "./dom.js";
import {parseIsCurrent} from "./app.js";

const HINTS = {
  hard: ["Hard: a must", "Every schedule keeps this rule. If none can, you're told which rules clash."],
  soft: ["Soft: try to, or prefer", "A schedule may break it if it has to. The weight says how much it matters."],
};

function hint(kind) {
  const [title, body] = HINTS[kind];
  return h("div", {class: "tip-body"}, h("strong", {}, title), h("span", {}, body));
}

export function render(state, actions) {
  const p = state.parsed;
  const stale = p && p.text !== state.prefs.trim();
  fill("prefs",
    h("label", {class: "h2", for: "prefs-box"}, "Your preferences"),
    h("textarea", {id: "prefs-box", class: "prefs-box", value: state.prefs,
                   placeholder: "e.g. no classes before 10, keep 12-13 free, prefer Cohen, Fridays off",
                   oninput: (e) => actions.setPrefs(e.target.value)}),
    unparsed(state),
    h("div", {class: "controls"},
      h("label", {class: "check"},
        h("input", {type: "checkbox", checked: state.review, onchange: (e) => actions.setReview(e.target.checked)}),
        "Review constraints before solving"),
      h("label", {class: "topk"}, "Results",
        h("input", {type: "number", min: 1, max: 20, value: state.top, "aria-label": "How many schedules to show",
                    onchange: (e) => actions.setTop(parseInt(e.target.value, 10))})),
      h("span", {class: "spacer"}),
      h("button", {id: "find", class: "btn btn-primary", onclick: actions.findSchedule, disabled: !!state.busy},
        findLabel(state))),
    h("div", {id: "busy"}),
    state.error && h("div", {class: "notice", role: "alert"}, icon("alert"), h("span", {}, state.error)),
    h("p", {id: "stale", class: "muted small", hidden: !stale},
      state.review ? "The text changed: Process constraints reads it again (rule edits below will be replaced)."
                   : "The text changed: Find a schedule reads it again."),
    state.review && p && chips(state, actions));
  tick(state, actions);
}

function unparsed(state) {
  const p = state.parsed;
  if (!p) return null;
  const items = p.unparsed || [];
  return h("div", {class: "parse-info"},
    h("span", {class: "muted small"}, `Read by: ${p.path_label}`),
    items.length > 0 && h("div", {class: "notice", role: "status"},
      icon("alert"),
      h("div", {},
        h("strong", {}, "Not understood:"),
        h("ul", {class: "plain"}, items.map((u) => h("li", {}, u.message))),
        p.note && h("span", {class: "line"}, p.note))),
    !items.length && p.note && h("span", {class: "muted small"}, p.note));
}

// The loading row redraws on its own every 100 ms, without touching the text box.
export function tick(state, actions) {
  const el = document.getElementById("busy");
  if (!el) return;
  if (!state.busy) { el.replaceChildren(); return; }
  const secs = el.querySelector(".mono");
  if (secs) { secs.textContent = state.busy.elapsed; return; }
  fill("busy", h("div", {class: "busy", role: "status"},
    icon("spinner", {cls: "spin"}),
    h("span", {}, `${state.busy.label} `, h("span", {class: "mono"}, state.busy.elapsed), " s (stops by itself after 3 minutes)"),
    h("span", {class: "spacer"}),
    h("button", {class: "btn", onclick: actions.cancel}, "Cancel")));
}

export function markStale(state) {
  const el = document.getElementById("stale");
  if (el) el.hidden = !(state.parsed && state.parsed.text !== state.prefs.trim());
  const find = document.getElementById("find");
  if (find) find.textContent = findLabel(state);
}

// With Review on, the first press only reads the text; the label says so, then changes once the rules are shown.
function findLabel(state) {
  return state.review && state.prefs.trim() && !parseIsCurrent() ? "Process constraints" : "Find a schedule";
}

function chips(state, actions) {
  const rules = state.parsed.rules;
  const phrase = h("input", {type: "text", placeholder: "Add a phrase, e.g. compact schedule", "aria-label": "Add a phrase",
                             onkeydown: (e) => { if (e.key === "Enter") actions.addPhrase(phrase.value); }});
  return h("div", {class: "chips"},
    h("span", {class: "muted small"},
      rules.length ? "Parsed rules: switch hard or soft, set the weight, or remove one. Editing never re-reads the text. "
                     + "Press Find a schedule to solve with these."
                   : "No rules were read from the text. Add one below, or press Find a schedule to solve without any."),
    rules.map((r, i) => {
      const c = r.constraint, soft = c.strength === "soft", softOnly = c.kind === "compact";
      const hardBtn = h("button", {class: `seg-btn${soft ? "" : " on"}`, "aria-pressed": soft ? "false" : "true",
                                   disabled: softOnly, onclick: () => actions.editRule(i, {strength: "hard"})}, "Hard");
      const softBtn = h("button", {class: `seg-btn${soft ? " on" : ""}`, "aria-pressed": soft ? "true" : "false",
                                   onclick: () => actions.editRule(i, {strength: "soft"})}, "Soft");
      bubble(hardBtn, () => softOnly ? h("div", {class: "tip-body"}, h("strong", {}, "Soft only"),
                                         h("span", {}, "A compact schedule can only be a preference.")) : hint("hard"));
      bubble(softBtn, () => hint("soft"));
      return h("div", {class: "chip"},
        h("span", {class: "chip-text"}, r.text),
        h("div", {class: "seg", role: "group", "aria-label": "Hard or soft"}, hardBtn, softBtn),
        h("span", {class: `stepper${soft ? "" : " off"}`, title: soft ? "Weight 1-10" : "A weight only counts for soft rules"},
          h("button", {"aria-label": "Lower weight", disabled: !soft || c.weight <= 1,
                       onclick: () => actions.editRule(i, {weight: c.weight - 1})}, icon("minus")),
          h("span", {}, c.weight),
          h("button", {"aria-label": "Raise weight", disabled: !soft || c.weight >= 10,
                       onclick: () => actions.editRule(i, {weight: c.weight + 1})}, icon("plus"))),
        h("button", {class: "icon-btn sm", "aria-label": `Remove ${r.text}`, onclick: () => actions.removeRule(i)}, icon("x")));
    }),
    h("div", {class: "addrow"}, phrase,
      h("button", {class: "btn", onclick: () => actions.addPhrase(phrase.value)}, icon("plus"), "Add")),
    state.phraseNote && h("div", {class: "notice", role: "status"}, icon("alert"), h("span", {}, state.phraseNote)));
}
