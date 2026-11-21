// The page: one state object, actions that change it, and regions that redraw from it.
// All wording about rules, parses and results comes from the server (scheduler/present.py), like the CLI.

import {api, ApiError} from "./api.js";
import {hideBubble} from "./dom.js";
import * as store from "./store.js";
import * as header from "./header.js";
import * as picker from "./picker.js";
import * as prefs from "./prefs.js";
import * as results from "./results.js";

const FRESH = () => ({
  catalogRaw: null, catalogName: null, catalog: null,
  modes: {}, pickN: null, query: "",
  prefs: "", review: true, top: 5,
  parsed: null,       // {text, offline, path_label, note, unparsed, rules: [{constraint, text}]}; rules are the chips
  phraseNote: null,   // what "Add" couldn't read
  results: null, tab: 0, listOpen: false,
  offline: false, noticeDismissed: false,
});

export const state = {...FRESH(), setup: null, busy: null, error: null, catalogError: null};

// ---- redrawing ----

const REGIONS = {header: header.render, courses: picker.render, prefs: prefs.render, results: results.render};

export function draw(...names) {
  for (const name of names.length ? names : Object.keys(REGIONS)) REGIONS[name](state, actions);
}

function update(patch, ...regions) {
  Object.assign(state, patch);
  store.save(state);
  draw(...regions);
}

// ---- course choice (mirrors scheduler/selection.py, which checks it again) ----

export function modeOf(id) {
  if (state.modes[id]) return state.modes[id];
  const c = state.catalog.courses.find((x) => x.id === id);
  return c && c.required ? "required" : "off";
}

export function pool() {
  return state.catalog ? state.catalog.courses.filter((c) => modeOf(c.id) === "pool").map((c) => c.id) : [];
}

export function pickCount() {
  const n = pool().length;
  return state.pickN === null ? n : Math.min(Math.max(state.pickN, 1), n);
}

// ---- actions ----

let inflight = null;   // AbortController of the running parse or solve
let ticker = null;

function startBusy(label) {
  inflight = new AbortController();
  const t0 = Date.now();
  state.busy = {label, elapsed: "0.0"};
  draw("prefs");
  ticker = setInterval(() => {
    state.busy.elapsed = ((Date.now() - t0) / 1000).toFixed(1);
    prefs.tick(state, actions);
  }, 100);
  return inflight.signal;
}

function stopBusy() {
  clearInterval(ticker);
  inflight = null;
  state.busy = null;
}

function fail(e, region = "prefs") {
  if (e.name === "AbortError") return;
  state.error = e instanceof ApiError ? e.message : `Something went wrong: ${e.message}`;
  draw(region);
}

async function loadCatalog(raw, name) {
  try {
    const catalog = await api.catalog(raw);
    update({...FRESH(), prefs: state.prefs, review: state.review, top: state.top, offline: state.offline,
            noticeDismissed: state.noticeDismissed, catalogRaw: raw, catalogName: name, catalog, catalogError: null,
            error: null});
  } catch (e) {
    state.catalogError = e instanceof ApiError ? e.message : `Couldn't load it: ${e.message}`;
    draw("courses");
  }
}

function solveBody() {
  return {
    catalog: state.catalogRaw,
    constraints: state.parsed ? state.parsed.rules.map((r) => r.constraint) : [],
    modes: state.modes,
    pick_n: pool().length ? pickCount() : null,
    top: state.top,
  };
}

function parseIsCurrent() {
  const p = state.parsed;
  return p && p.text === state.prefs.trim() && p.offline === usingOffline();
}

export function usingOffline() {
  return state.offline || !llmReady();
}

export function llmReady() {
  const s = state.setup;
  return !!s && s.provider !== "none" && !s.needs_setup;
}

export const actions = {
  toggleTheme() {
    const dark = !document.documentElement.classList.contains("dark");
    document.documentElement.classList.toggle("dark", dark);
    store.saveTheme(dark ? "dark" : "light");
    draw("header");
  },

  startOver() {
    if (inflight) inflight.abort();
    stopBusy();
    store.forget();
    Object.assign(state, FRESH(), {error: null, catalogError: null, noticeDismissed: state.noticeDismissed});
    store.save(state);
    draw();
  },

  async refreshSetup() {
    try { state.setup = await api.setupStatus(); } catch (e) { state.setup = null; state.error = e.message; }
    draw("header", "prefs");
  },

  async saveSetup(choice) {
    const answer = await api.setup(choice);   // errors go back to the dialog
    state.setup = answer.setup;
    if (choice.provider !== "none") state.offline = false;
    store.save(state);
    draw("header", "prefs");
    return answer;
  },

  async continueOffline() {
    try {
      await actions.saveSetup({provider: "none"});
      update({noticeDismissed: true}, "header");
    } catch (e) { fail(e, "header"); }
  },

  setOffline(offline) { update({offline}, "header", "prefs"); },

  // catalog
  async chooseFile(file) {
    let raw;
    try { raw = JSON.parse(await file.text()); }
    catch { state.catalogError = `${file.name} isn't valid JSON.`; draw("courses"); return; }
    await loadCatalog(raw, file.name);
  },
  async useSample() {
    try { await loadCatalog(await api.sample(), "sample_courses.json"); }
    catch (e) { state.catalogError = e.message; draw("courses"); }
  },

  // picker
  setQuery(query) { state.query = query; store.save(state); picker.renderList(state, actions); },
  setMode(id, mode) {
    state.modes = {...state.modes, [id]: mode};
    store.save(state);
    picker.renderList(state, actions);
  },
  setPick(n) { state.pickN = n; store.save(state); picker.renderList(state, actions); },

  // preferences
  setPrefs(text) { state.prefs = text; store.save(state); prefs.markStale(state); },
  setReview(on) { update({review: on}, "prefs"); },
  setTop(n) { state.top = Math.min(20, Math.max(1, n || 5)); store.save(state); },

  async findSchedule() {
    if (state.busy) return;
    state.error = null;
    if (!state.catalog) { state.error = "Choose a catalog first (or try the sample)."; draw("prefs"); return; }
    try {
      if (!parseIsCurrent() && state.prefs.trim()) {
        const signal = startBusy("Reading your preferences…");
        const parsed = await api.parse(state.catalogRaw, state.prefs, usingOffline(), signal);
        stopBusy();
        update({parsed: {...parsed, text: state.prefs.trim()}, phraseNote: null}, "prefs");
        if (state.review) return;   // show the rules; the next press solves
      } else if (!state.prefs.trim()) {
        state.parsed = null;
      }
      const signal = startBusy("Finding the best schedules…");
      const answer = await api.solve(solveBody(), signal);
      stopBusy();
      update({results: answer, tab: 0}, "prefs", "results");
      document.getElementById("results").scrollIntoView({behavior: "smooth", block: "start"});
    } catch (e) {
      stopBusy();
      fail(e);
    }
  },

  cancel() {
    if (inflight) inflight.abort();
    stopBusy();
    draw("prefs");
  },

  // review chips: edits never re-parse; the server checks them and words them
  async editRule(i, change) {
    const rules = state.parsed.rules.map((r) => r.constraint);
    rules[i] = {...rules[i], ...change};
    try {
      const answer = await api.rules(rules);
      update({parsed: {...state.parsed, rules: answer.rules}, error: null}, "prefs");
    } catch (e) { fail(e); }
  },
  removeRule(i) {
    const rules = state.parsed.rules.slice();
    rules.splice(i, 1);
    update({parsed: {...state.parsed, rules}}, "prefs");
  },
  async addPhrase(text) {
    if (!text.trim() || !state.catalog) return;
    try {
      const got = await api.parse(state.catalogRaw, text, usingOffline());
      const base = state.parsed || {text: state.prefs.trim(), offline: usingOffline(), rules: [], unparsed: [],
                                    note: null, path_label: got.path_label};
      update({parsed: {...base, rules: base.rules.concat(got.rules)},
              phraseNote: got.unparsed.length ? got.unparsed.map((u) => u.message).join(" ") : null,
              error: null}, "prefs");
    } catch (e) { fail(e); }
  },

  // results
  pickTab(i) { hideBubble(); update({tab: i}, "results"); },
  toggleList() { update({listOpen: !state.listOpen}, "results"); },
};

// ---- start ----

function start() {
  const theme = store.savedTheme();
  const dark = theme ? theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
  document.documentElement.classList.toggle("dark", dark);
  const saved = store.load();
  if (saved) for (const k of store.SAVED) if (k in saved && saved[k] !== undefined) state[k] = saved[k];
  draw();
  actions.refreshSetup();
}

start();
