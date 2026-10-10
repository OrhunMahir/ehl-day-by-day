// Renders one frame's DOM as compact text for the model. Interactive elements get a number in brackets,
// stamped on the element as data-aid so an action can find it again. Tables become " | " rows.
// Called with {prefix, start, full}; returns {text, next}.
({prefix, start, full}) => {
  let n = start;
  const lines = [];
  const SKIP = new Set(["SCRIPT", "STYLE", "NOSCRIPT", "TEMPLATE", "SVG", "HEAD", "META", "LINK"]);
  const BLOCK = new Set(["DIV", "P", "SECTION", "ARTICLE", "MAIN", "HEADER", "FOOTER", "NAV", "ASIDE", "UL", "OL",
    "LI", "H1", "H2", "H3", "H4", "H5", "H6", "FORM", "FIELDSET", "LEGEND", "TR", "DL", "DT", "DD", "PRE", "BLOCKQUOTE",
    "DETAILS", "SUMMARY", "BR", "HR", "TABLE", "CAPTION", "FIGURE", "FIGCAPTION", "LABEL", "DIALOG"]);
  const clean = (s) => (s || "").replace(/[​-‏⁠﻿­]/g, "").replace(/\s+/g, " ").trim();
  // text the eye would get from hovering: a hidden tooltip, or a title/aria-label on a plain element
  const hint = (el) => {
    const parts = [];
    const own = clean(el.innerText || "") + " " + clean(el.textContent || "");
    for (const a of ["aria-label", "title", "data-tooltip", "datetime"]) {
      const v = clean(el.getAttribute(a));
      if (v && !own.includes(v) && !parts.includes(v)) parts.push(v);
    }
    const ref = referenced(el);
    if (ref) parts.push(ref);
    return parts.length ? ` (${parts.join("; ")})` : "";
  };
  // text of elements this one points to (tooltips, descriptions), even when they are hidden until hover
  const shownRefs = new Set();
  function referenced(el) {
    const out = [];
    for (const a of ["aria-describedby", "aria-labelledby", "aria-details"]) {
      for (const id of (el.getAttribute(a) || "").split(/\s+/).filter(Boolean)) {
        const target = document.getElementById(id);
        if (target && !target.contains(el) && !shownRefs.has(target)) {
          shownRefs.add(target);
          const v = clean(target.textContent);
          if (v) out.push(`tooltip: ${v}`);
        }
      }
    }
    return out.join("; ");
  }

  const hidden = (el) => {
    if (el.hidden || el.getAttribute("aria-hidden") === "true") return true;
    const cs = getComputedStyle(el);
    if (cs.display === "none") return true;
    if (cs.visibility === "hidden" && el.tagName !== "DETAILS") return true;
    return false;
  };
  const interactive = (el) => {
    const t = el.tagName;
    if (t === "A" && el.hasAttribute("href")) return true;
    if (["BUTTON", "SELECT", "TEXTAREA", "SUMMARY"].includes(t)) return true;
    if (t === "INPUT") return el.type !== "hidden";
    const role = el.getAttribute("role");
    if (["button", "link", "tab", "menuitem", "menuitemcheckbox", "menuitemradio", "checkbox", "radio", "option",
         "switch", "combobox", "treeitem", "searchbox", "textbox", "spinbutton", "slider"].includes(role)) return true;
    if (el.hasAttribute("onclick") || el.hasAttribute("data-action")) return true;
    return false;
  };
  const tag = (el) => {
    const id = `${prefix}${n++}`;
    el.setAttribute("data-aid", id);
    return id;
  };
  const describe = (el) => {
    const t = el.tagName;
    const id = tag(el);
    const extra = [];
    const title = el.getAttribute("title"), aria = el.getAttribute("aria-label");
    if (aria) extra.push(`aria="${clean(aria)}"`);
    if (title && title !== aria) extra.push(`title="${clean(title)}"`);
    if (el.disabled) extra.push("disabled");
    const ref = referenced(el);
    if (ref) extra.push(`(${ref})`);
    if (t === "A") {
      const href = el.getAttribute("href") || "";
      const dl = el.hasAttribute("download") ? " download" : "";
      return `[${id}] link "${clean(el.innerText)}"${extra.length ? " " + extra.join(" ") : ""} -> ${href.slice(0, 160)}${dl}`;
    }
    if (t === "INPUT") {
      const type = el.type || "text";
      const lbl = el.labels && el.labels[0] ? clean(el.labels[0].innerText) : "";
      if (type === "checkbox" || type === "radio")
        return `[${id}] ${type} name=${el.name} value="${el.value}" ${el.checked ? "CHECKED" : "unchecked"}${lbl ? ` label="${lbl}"` : ""} ${extra.join(" ")}`;
      if (type === "submit" || type === "button") return `[${id}] button "${el.value}" ${extra.join(" ")}`;
      if (type === "file") return `[${id}] file-input name=${el.name}`;
      const ph = el.placeholder ? ` placeholder="${el.placeholder}"` : "";
      return `[${id}] input(${type}) name=${el.name}${lbl ? ` label="${lbl}"` : ""}${ph} value="${el.value}"${el.readOnly ? " readonly" : ""} ${extra.join(" ")}`;
    }
    if (t === "TEXTAREA") return `[${id}] textarea name=${el.name} value="${clean(el.value).slice(0, 300)}"`;
    if (t === "SELECT") {
      const opts = [...el.options].map((o) => `${o.selected ? "*" : ""}${clean(o.text)}${o.value !== o.text ? `(${o.value})` : ""}`);
      return `[${id}] select name=${el.name} options: ${opts.join(" ; ").slice(0, 1500)}`;
    }
    if (t === "SUMMARY") return `[${id}] summary "${clean(el.innerText)}" (${el.parentElement.open ? "open" : "closed"})`;
    return `[${id}] ${t === "BUTTON" ? "button" : (el.getAttribute("role") || "clickable")} "${clean(el.innerText || el.value)}" ${extra.join(" ")}`;
  };

  // inline text of a node, with interactive descendants inlined as [id] markers
  // children of a node, including the content of an open shadow root (web components)
  const kids = (node) => (node.shadowRoot ? [...node.shadowRoot.childNodes, ...node.childNodes] : [...node.childNodes]);
  const inline = (node, out) => {
    for (const c of kids(node)) {
      if (c.nodeType === 3) { out.push(c.textContent); continue; }
      if (c.nodeType !== 1 || SKIP.has(c.tagName)) continue;
      if (hidden(c)) {
        if (c.getAttribute("role") === "tooltip") out.push(` (tooltip: ${clean(c.textContent)}) `);
        continue;
      }
      if (c.tagName === "IFRAME") { out.push(" [iframe] "); continue; }
      if (interactive(c)) { out.push(` ${describe(c)} `); continue; }
      inline(c, out);
      const h = hint(c);
      if (h) out.push(h);
    }
    return out;
  };

  const walk = (el, depth) => {
    if (el.nodeType === 3) { const s = clean(el.textContent); if (s) lines.push(s); return; }
    if (el.nodeType !== 1 || SKIP.has(el.tagName)) return;
    if (hidden(el)) {
      // tooltips and collapsed hints often carry data; keep their text, flagged
      const role = el.getAttribute("role");
      if (role === "tooltip" || el.tagName === "DETAILS") { const s = clean(el.textContent); if (s) lines.push(`(hidden ${role || "details"}: ${s.slice(0, 600)})`); }
      return;
    }
    const t = el.tagName;
    if (t === "IFRAME") { lines.push(`[iframe name="${el.name || ""}" src="${(el.getAttribute("src") || "").slice(0, 120)}" — its content follows as a separate frame]`); return; }
    if (t === "CANVAS") { const s = el.getAttribute("aria-label") || el.getAttribute("title") || ""; lines.push(`[canvas${s ? ": " + clean(s) : ""}]`); return; }
    if (t === "IMG") { const a = el.getAttribute("alt") || el.getAttribute("title"); if (a) lines.push(`[image: ${clean(a)}]`); return; }
    const tableRole = el.getAttribute("role");
    if (t !== "TABLE" && (tableRole === "table" || tableRole === "grid" || tableRole === "treegrid")) {
      // a table built from elements with ARIA roles: render its rows like a table's
      lines.push("<table>");
      for (const row of el.querySelectorAll('[role="row"]')) {
        if (row.closest('[role="table"], [role="grid"], [role="treegrid"]') !== el || hidden(row)) continue;
        const cells = [...row.querySelectorAll('[role="cell"], [role="gridcell"], [role="columnheader"], [role="rowheader"]')]
          .filter((c) => c.closest('[role="row"]') === row);
        lines.push("| " + cells.map((c) => clean(inline(c, []).join(" "))).join(" | ") + " |");
      }
      lines.push("</table>");
      return;
    }
    if (t === "TABLE") {
      const cap = el.querySelector(":scope > caption");
      if (cap) lines.push(`## ${clean(cap.innerText)}`);
      lines.push("<table>");
      for (const row of el.rows) {
        if (hidden(row)) continue;
        const cells = [...row.cells].map((c) => clean(inline(c, []).join(" ")));
        lines.push("| " + cells.join(" | ") + " |");
      }
      lines.push("</table>");
      return;
    }
    if (interactive(el)) {
      // a link/button that wraps block content: show its own text inline
      lines.push(describe(el));
      return;
    }
    if (t === "DT" && el.nextElementSibling && el.nextElementSibling.tagName === "DD") {
      lines.push(`${clean(inline(el, []).join(" "))}: ${clean(inline(el.nextElementSibling, []).join(" "))}`);
      el.nextElementSibling.setAttribute("data-agent-done", "1");
      return;
    }
    if (el.getAttribute("data-agent-done")) { el.removeAttribute("data-agent-done"); return; }
    if (shownFirst.has(el)) return;
    if (el.shadowRoot) { for (const c of kids(el)) walk(c, depth + 1); return; }
    const hasBlockChild = [...el.children].some((c) => BLOCK.has(c.tagName) || c.tagName === "TABLE" || c.tagName === "IFRAME" || c.tagName === "CANVAS");
    if (!hasBlockChild) {
      const s = clean(inline(el, []).join(" "));
      if (s) lines.push(/^H[1-6]$/.test(t) ? `${"#".repeat(+t[1])} ${s}` : s);
      return;
    }
    for (const c of kids(el)) walk(c, depth + 1);
  };

  document.querySelectorAll("[data-aid]").forEach((e) => e.removeAttribute("data-aid"));
  // open dialogs and modals sit in front of the page: show them first, so the model deals with them first
  const DIALOG = 'dialog[open], [role="dialog"], [role="alertdialog"], [aria-modal="true"]';
  const shownFirst = new Set();
  for (const d of document.querySelectorAll(DIALOG)) {
    if (d.parentElement && d.parentElement.closest(DIALOG)) continue;
    if (!(d.checkVisibility ? d.checkVisibility() : d.getClientRects().length)) continue;
    lines.push("=== OPEN DIALOG, in front of the page (deal with it first: close it or answer it) ===");
    walk(d, 0);
    lines.push("=== END OF DIALOG ===");
    shownFirst.add(d);
  }
  if (document.body) walk(document.body, 0);
  // collapse duplicate consecutive lines
  const out = [];
  for (const l of lines) if (out[out.length - 1] !== l) out.push(l);
  return {text: out.join("\n"), next: n, title: document.title};
}
