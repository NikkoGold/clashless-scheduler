// Remembering the session. Each tab keeps its own copy in sessionStorage (a reload restores it); the latest one is
// also written to localStorage and read only when a new tab opens, so a new tab starts where you left off and then
// lives on its own. The API key is never stored: the page never has it.

const TAB_KEY = "clashless-session";
const LAST_KEY = "clashless-last";
const THEME_KEY = "clashless-theme";

// The only fields saved. Anything else (setup status, busy state, errors) is fetched or rebuilt on load.
export const SAVED = ["catalogRaw", "catalogName", "catalog", "modes", "pickN", "query", "prefs", "review", "top",
                      "parsed", "phraseNote", "results", "tab", "listOpen", "offline", "noticeDismissed"];

export function load() {
  for (const storage of [sessionStorage, localStorage]) {
    try {
      const raw = storage.getItem(storage === sessionStorage ? TAB_KEY : LAST_KEY);
      if (raw) return JSON.parse(raw);
    } catch { /* unreadable: start fresh */ }
  }
  return null;
}

export function save(state) {
  const picked = {};
  for (const k of SAVED) picked[k] = state[k];
  const raw = JSON.stringify(picked);
  try { sessionStorage.setItem(TAB_KEY, raw); } catch { /* full or blocked: keep working without it */ }
  try { localStorage.setItem(LAST_KEY, raw); } catch { /* same */ }
}

export function forget() {
  try { sessionStorage.removeItem(TAB_KEY); } catch { /* ignore */ }
  try { localStorage.removeItem(LAST_KEY); } catch { /* ignore */ }
}

export function savedTheme() {
  try { return localStorage.getItem(THEME_KEY); } catch { return null; }
}

export function saveTheme(theme) {
  try { localStorage.setItem(THEME_KEY, theme); } catch { /* ignore */ }
}
