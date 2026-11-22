// Small DOM helpers and the icons. Icons are from Lucide (https://lucide.dev, ISC licence), inlined as SVG paths.

const SVG_NS = "http://www.w3.org/2000/svg";

// h("button", {class: "btn", onclick: fn}, "text", child, [more children]) -> element
export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k.startsWith("on")) el.addEventListener(k.slice(2).toLowerCase(), v);
    else if (k === "class") el.className = v;
    else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
    else if (k === "value") el.value = v;
    else if (k === "checked") el.checked = !!v;
    else el.setAttribute(k, v === true ? "" : String(v));
  }
  append(el, children);
  return el;
}

function append(el, children) {
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
}

const PATHS = {
  sun: ["c:12,12,4", "M12 2v2", "M12 20v2", "m4.93 4.93 1.41 1.41", "m17.66 17.66 1.41 1.41", "M2 12h2", "M20 12h2",
        "m6.34 17.66-1.41 1.41", "m19.07 4.93-1.41 1.41"],
  moon: ["M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"],
  info: ["c:12,12,10", "M12 16v-4", "M12 8h.01"],
  alert: ["m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3", "M12 9v4", "M12 17h.01"],
  upload: ["M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4", "m17 8-5-5-5 5", "M12 3v12"],
  search: ["c:11,11,8", "m21 21-4.3-4.3"],
  chevron: ["m6 9 6 6 6-6"],
  minus: ["M5 12h14"],
  plus: ["M5 12h14", "M12 5v14"],
  x: ["M18 6 6 18", "m6 6 12 12"],
  check: ["M20 6 9 17l-5-5"],
  spinner: ["M21 12a9 9 0 1 1-6.219-8.56"],
  restart: ["M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8", "M3 3v5h5"],
  download: ["M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4", "m7 10 5 5 5-5", "M12 15V3"],
  printer: ["M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2",
            "M6 9V3a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v6", "M6 14h12v8H6z"],
};

// icon("check", {class: "ok", label: "Met"}): decorative unless a label is given
export function icon(name, {cls = "", label = null} = {}) {
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("class", `icon ${cls}`.trim());
  if (label) { svg.setAttribute("role", "img"); svg.setAttribute("aria-label", label); }
  else svg.setAttribute("aria-hidden", "true");
  for (const d of PATHS[name]) {
    let el;
    if (d.startsWith("c:")) {
      const [cx, cy, r] = d.slice(2).split(",");
      el = document.createElementNS(SVG_NS, "circle");
      el.setAttribute("cx", cx); el.setAttribute("cy", cy); el.setAttribute("r", r);
    } else {
      el = document.createElementNS(SVG_NS, "path");
      el.setAttribute("d", d);
    }
    svg.append(el);
  }
  return svg;
}

// Replace a region's contents.
export function fill(id, ...children) {
  const el = document.getElementById(id);
  el.replaceChildren();
  append(el, children);
  return el;
}

// 15 course hues; the theme sets saturation and lightness (pastel in light mode, deep in dark).
// A copy of scheduler/export.py HUES (the Excel colours); tests/test_export.py checks the two match.
export const HUES = [262, 200, 160, 32, 330, 95, 12, 285, 186, 48, 222, 138, 350, 305, 70];

export function courseColours(index) {
  const hue = HUES[index % HUES.length];
  return {
    background: `hsl(${hue} var(--cb-s) var(--cb-l))`,
    borderColor: `hsl(${hue} var(--cbd-s) var(--cbd-l))`,
    color: `hsl(${hue} var(--ct-s) var(--ct-l))`,
  };
}

// ---- the hover bubble: shown after a pause, follows the cursor, hidden on leave ----

const TIP_DELAY_MS = 600;
let tipTimer = null, tipFor = null, px = 0, py = 0;

function tipEl() { return document.getElementById("tip"); }

function place() {
  const t = tipEl();
  t.style.left = `${px}px`;
  t.style.top = `${py}px`;
}

// Attach a delayed bubble to `el`; `content()` returns its children when it opens.
export function bubble(el, content) {
  el.addEventListener("mouseenter", (e) => {
    px = e.clientX; py = e.clientY;
    clearTimeout(tipTimer);
    tipTimer = setTimeout(() => {
      const t = tipEl();
      t.replaceChildren();
      append(t, [content()]);
      tipFor = el;
      place();
      t.hidden = false;
    }, TIP_DELAY_MS);
  });
  el.addEventListener("mousemove", (e) => {
    px = e.clientX; py = e.clientY;
    if (tipFor === el) place();
  });
  el.addEventListener("mouseleave", hideBubble);
}

export function hideBubble() {
  clearTimeout(tipTimer);
  tipFor = null;
  const t = tipEl();
  if (t) t.hidden = true;
}

// ---- saving a file the server sent (exports) ----

export function saveFile(blob, name) {
  const url = URL.createObjectURL(blob);
  const a = h("a", {href: url, download: name, hidden: true});
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
