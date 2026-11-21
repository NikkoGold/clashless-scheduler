// The header: parser label with the LLM | Offline switch, the first-run notice, Start over, Setup and the theme.
// Also the setup dialog. The page never receives the API key: it only learns whether one is saved.

import {h, icon, fill} from "./dom.js";
import {llmReady, usingOffline} from "./app.js";

function parserText(state) {
  const s = state.setup;
  if (!s) return "Parser: …";
  if (usingOffline()) return "Parser: rules (offline)";
  return `Parser: LLM (${s.model || s.provider})`;
}

export function render(state, actions) {
  const s = state.setup;
  const ready = llmReady();
  const needsSetup = !!s && s.needs_setup;
  const dark = document.documentElement.classList.contains("dark");
  const seg = (label, on, click) =>
    h("button", {class: `seg-btn${on ? " on" : ""}`, "aria-pressed": on ? "true" : "false", onclick: click}, label);

  fill("header",
    h("span", {class: "brand"}, "Clashless"),
    h("span", {class: "parser"},
      h("span", {class: "pill", title: s && s.problem ? s.problem : null}, parserText(state)),
      ready && h("div", {class: "seg", role: "group", "aria-label": "Parser"},
        seg("LLM", !state.offline, () => actions.setOffline(false)),
        seg("Offline", state.offline, () => actions.setOffline(true)))),
    h("span", {class: "spacer"}),
    needsSetup && h("div", {class: "hnotice", role: "status"},
      icon("info"),
      h("span", {}, h("strong", {}, "No LLM is set up."),
        " Setup is required to understand anything beyond basic phrases like \"no classes before 10\" or \"Fridays off\"."),
      h("button", {class: "link-btn", onclick: actions.continueOffline}, "Continue offline")),
    h("button", {class: "btn btn-ghost", onclick: actions.startOver, title: "Clear this tab and start again"},
      icon("restart"), "Start over"),
    h("button", {class: `btn ${needsSetup ? "btn-primary" : "btn-ghost"}`, onclick: () => openSetup(state, actions)},
      "Setup"),
    h("button", {class: "icon-btn", "aria-label": dark ? "Switch to light mode" : "Switch to dark mode",
                 title: dark ? "Switch to light mode" : "Switch to dark mode", onclick: actions.toggleTheme},
      icon(dark ? "sun" : "moon")));
}

// ---- setup dialog ----

function openSetup(state, actions) {
  const s = state.setup;
  if (!s) { actions.refreshSetup(); return; }
  const dialog = document.getElementById("setup");
  let provider = s.provider === "none" && s.needs_setup ? s.providers[0].name : s.provider;
  const message = h("p", {class: "dlg-msg", role: "status"});

  const model = h("input", {type: "text", id: "setup-model", autocomplete: "off"});
  const key = h("input", {type: "password", id: "setup-key", autocomplete: "off"});
  const ollama = h("input", {type: "text", id: "setup-ollama", autocomplete: "off",
                             placeholder: "http://localhost:11434"});
  const fields = h("div", {class: "dlg-fields"});

  function drawFields() {
    const p = s.providers.find((x) => x.name === provider);
    model.placeholder = p && p.default_model ? `${p.default_model} (default)` : "";
    model.value = provider === s.provider && s.model && s.model !== (p && p.default_model) ? s.model : "";
    key.value = "";
    key.placeholder = s.has_key && provider === s.provider ? "Leave empty to keep the saved key" : "Paste your API key";
    fields.replaceChildren(
      provider === "none"
        ? h("p", {class: "muted"}, "Offline: only simple, direct phrasing is understood (\"no classes before 10\", "
            + "\"Fridays off\"). Anything else is listed as not understood.")
        : h("div", {class: "dlg-fields"},
            h("label", {for: "setup-model"}, "Model"), model,
            p && p.needs_key && [h("label", {for: "setup-key"}, "API key"), key,
                                 h("span", {class: "muted small"}, "Saved on this computer only. The page never shows it again.")],
            provider === "ollama" && [h("label", {for: "setup-ollama"}, "Ollama address"), ollama]));
  }

  const radios = h("div", {class: "dlg-providers", role: "radiogroup", "aria-label": "Provider"},
    s.providers.map((p) => h("label", {class: "radio"},
      h("input", {type: "radio", name: "provider", value: p.name, checked: p.name === provider,
                  onchange: () => { provider = p.name; drawFields(); }}),
      p.label)));

  const save = h("button", {class: "btn btn-primary", type: "submit"}, "Save");
  const form = h("form", {method: "dialog", class: "dlg", onsubmit: async (e) => {
      e.preventDefault();
      save.disabled = true;
      message.className = "dlg-msg";
      message.textContent = "Saving…";
      try {
        const answer = await actions.saveSetup({provider, model: model.value.trim() || null,
                                                api_key: key.value || null, ollama_url: ollama.value.trim() || null});
        key.value = "";
        message.textContent = answer.message + (answer.warning ? ` ${answer.warning}` : "");
        if (answer.warning) message.className = "dlg-msg warn";
        else setTimeout(() => dialog.close(), 900);
      } catch (err) {
        message.className = "dlg-msg warn";
        message.textContent = err.message;
      } finally {
        save.disabled = false;
      }
    }},
    h("h2", {class: "h2"}, "Set up the parser"),
    h("p", {class: "muted small"}, "An LLM reads free-form text the built-in rules can't. ",
      "It's only called when the rules leave something unread."),
    radios, fields,
    s.source && h("p", {class: "muted small"}, `Settings file: ${s.source}`),
    s.from_environment.length > 0 && h("p", {class: "dlg-msg warn"},
      `Set by environment variables, which win over the file: ${s.from_environment.join(", ")}.`),
    message,
    h("div", {class: "dlg-actions"},
      h("button", {class: "btn", type: "button", onclick: () => dialog.close()}, "Close"), save));

  dialog.replaceChildren(form);
  drawFields();
  dialog.showModal();
}
