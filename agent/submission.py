"""Entering and signing the bid: a model works the submission website with a tool that fills many fields at once.

The model still chooses every field, value and button from the page it reads. Code only carries out a whole group
of those choices in one go: asking the model again after every single field makes a long form take longer than
the run lasts.
"""
import asyncio
import hashlib
import json
import re
import time

from browser import Tab
from browsing import BrowsingAgent

SIGN_NOW_SECONDS = 150  # this long before the end the agent is told to stop filling and get the bid signed
STOP_SECONDS = 10
MODEL_CALL_SECONDS = 45  # a step normally takes under 10 s; a call that stalls is dropped and asked again
TOOL_SECONDS = 90
FIRST_VIEW_SECONDS = 30
OPTION_WAIT_SECONDS = 6  # dependent dropdowns load their options after the one before them was chosen
REPEATS_BEFORE_WARNING = 3

SYSTEM = """You enter one finished bid into a website's submission form and get it signed and confirmed.
A bid that is entered but not signed and confirmed counts for nothing, so reaching the confirmation matters more
than any single field.

How the page view works: each page is shown as text; elements you can use carry a number in brackets, like [12] or
[f1.3] (inside a frame). Selects list their options, the chosen one marked with *.

How to work:
- Use fill_form for every group of fields you can see: give all of them in one call, in the order they appear on
  the page, and name the button that continues in click_after. Dropdowns that depend on each other (one choice
  changes the options of the next) work in one call too: list them in order, each with the option text you want.
- A field that is not on the page yet, because it only appears after an earlier choice, can be named by its
  label instead of a number (`label`); it is looked up when its turn comes. The button in click_after can be named
  by its text in the same way.
- When a step repeats (e.g. one step per position), you know its fields after the first time: from then on give
  the whole step in one call, with labels for the fields that appear later, and the button that continues.
- If a choice only enables the next field after a button applies it, put that button between the fields as
  {"click": "<the button's exact text>"}. Name controls that appear later by their labels.
- Read notices before acting. Continue from the current step of a partly entered form. Never go back, reload or
  reopen a form that discards entered values; continue forward even if one earlier value was imperfect.
- Element numbers belong to one page view. After an action changes it, use the newest numbers. Separate clicks
  that change a page into separate turns; fill_form can carry out dependent choices safely in one call.
- A repeated page may be another position: check its position number and values. If the same action leaves the
  same page unchanged repeatedly, read the error and button text and choose another action from the current view.
- A fill_form result says for every field what was set or why it failed, then shows the page as it is now. Read
  it before you go on. After a failure, continue from that field with the numbers of the new page view.
- Enter the values of the answer exactly. Write numbers the way the form expects them (look at its examples,
  placeholders and error messages). Leave a field empty when the answer has no value for it.
- If the form has its own option to leave a position out and you were told to leave one out, use that option.
- Follow the brief's instructions for submitting. If a signing code is needed, get it where the brief says; that
  may be another of your websites.
- The websites are deliberately awkward: cookie notices, popups, error pages that work on a retry, sessions that
  expire and return you to the sign-in page (use the login tool). Never accept surveys or unrelated offers.
- Do not change or recompute any value of the answer.
- When you see a successful signed-submission receipt, call finish with accepted=true and receipt exactly as
  shown. A saved draft, an expired session or an inaccessible page is not a receipt. If you cannot confirm it,
  call finish with accepted=false, receipt="", and report what is entered, missing and what stopped you.
"""

SIGN_NOW = ("Time is nearly over. Stop filling optional fields: go on to signing and confirming the bid now, with "
            "what is entered. If positions are still empty and the form can leave a position out, use that.")

# The label is the label's own words, without the text of the control it wraps.
IDENTITY_JS = """el => {
  const label = el.labels && el.labels[0];
  const words = label ? [...label.childNodes].filter((n) => n.nodeType === 3).map((n) => n.textContent).join(" ") : "";
  return {
    tag: el.tagName, type: (el.type || "").toLowerCase(), name: el.name || "", id: el.id || "",
    document: (el.ownerDocument.__submissionIdentity ||= Date.now().toString(36) + Math.random().toString(36)),
    text: (el.innerText || el.value || el.getAttribute("aria-label") || "").replace(/\\s+/g, " ").trim().slice(0, 120),
    label: (words.trim() || el.getAttribute("aria-label") || el.name || "").replace(/\\s+/g, " ").trim().slice(0, 60),
  };
}"""

# Finds the form control the model named by its label: the label's own words, aria-label, placeholder, name or id;
# exact first, then one that starts with it or contains it. Marks it so it can be picked up, or lists what is there.
LABEL_JS = """(wanted) => {
  const norm = (s) => (s || "").replace(/\\s+/g, " ").trim().toLowerCase().replace(/[:*]+$/, "").trim();
  const w = norm(wanted);
  const controls = [...document.querySelectorAll(
    "input:not([type=hidden]):not([type=submit]):not([type=button]):not([type=image]), select, textarea")]
    .filter((el) => el.checkVisibility({visibilityProperty: true}));
  const names = (el) => {
    const label = el.labels && el.labels[0];
    const words = label ? [...label.childNodes].filter((n) => n.nodeType === 3).map((n) => n.textContent).join(" ") : "";
    return [words.trim() ? words : (label ? label.innerText : ""), el.getAttribute("aria-label"),
            el.getAttribute("placeholder"), el.name, el.id].map(norm).filter(Boolean);
  };
  const score = (el) => {
    const all = names(el);
    return all.includes(w) ? 3 : all.some((n) => n.startsWith(w)) ? 2 : all.some((n) => n.includes(w)) ? 1 : 0;
  };
  let best = null, top = 0, ties = 0;
  for (const el of controls) {
    const value = score(el);
    if (value > top) { best = el; top = value; ties = 1; } else if (value === top && value > 0) { ties += 1; }
  }
  document.querySelectorAll("[data-fill-target]").forEach((el) => el.removeAttribute("data-fill-target"));
  if (!best || ties > 1) {
    return {found: false, several: ties > 1, labels: controls.map((el) => names(el)[0]).filter(Boolean).slice(0, 40)};
  }
  best.setAttribute("data-fill-target", "1");
  return {found: true};
}"""

# Finds the option the model named: exact text or value first, then a text that starts with it or contains it,
# as long as only one option does.
OPTION_JS = """(el, wanted) => {
  const norm = (s) => (s || "").replace(/\\s+/g, " ").trim().toLowerCase();
  const w = norm(wanted);
  const options = [...el.options].filter((o) => !o.disabled);
  const only = (test) => { const hits = options.filter(test); return hits.length === 1 ? hits[0] : null; };
  const exact = options.filter((o) => norm(o.text) === w || norm(o.value) === w);
  const starts = (s) => s.startsWith(w) && (s.length === w.length || /[\\s,;:()/-]/.test(s[w.length]));
  const hit = exact.length === 1 ? exact[0] : exact.length > 1 ? null : only((o) => starts(norm(o.text)));
  return {
    disabled: el.disabled,
    match: hit ? {value: hit.value, text: hit.text.replace(/\\s+/g, " ").trim()} : null,
    options: options.map((o) => o.text.replace(/\\s+/g, " ").trim()).filter(Boolean).slice(0, 60),
  };
}"""


def tool_schemas(sites: list[str]) -> list[dict]:
    site = {"type": "string", "enum": sites, "description": "which website"}
    element = {"type": "string", "description": "element number from the latest page view of that website"}

    def tool(name: str, description: str, properties: dict, required: list[str]) -> dict:
        return {"type": "function", "function": {"name": name, "description": description, "parameters": {
            "type": "object", "properties": properties, "required": required}}}

    return [
        tool("look", "Show the current page of a website again.", {"site": site}, ["site"]),
        tool("goto", "Open a URL (absolute, or a path on the site).", {"site": site, "url": {"type": "string"}},
             ["site", "url"]),
        tool("click", "Click one element.", {"site": site, "element": element}, ["site", "element"]),
        tool("fill_form",
             "Set several fields in one go, in the order given, then optionally click a button. Name each field "
             "by its element number, or by its label when it is not on the page yet. For a text field the value "
             "is typed; for a select it is the option's text; for a checkbox or radio button it is true or false. "
             "Stops at the first field that fails and reports every field.",
             {"site": site,
              "fields": {"type": "array", "items": {"type": "object", "properties": {
                  "element": element,
                  "label": {"type": "string", "description": "the field's label, instead of element, for a field "
                                                             "that appears only after an earlier choice"},
                  "value": {"type": "string"},
                  "click": {"type": "string", "description": "an intermediate button's exact text or number; "
                            "this entry clicks instead of filling a field"}},
                  "anyOf": [{"required": ["value"]}, {"required": ["click"]}]}},
              "click_after": {"type": "string", "description": "element number, or text, of the button to click "
                                                                "once every field is set, e.g. the one that continues"}},
             ["site", "fields"]),
        tool("read_more", "Continue reading a long page view from a character offset.",
             {"site": site, "offset": {"type": "integer"}}, ["site", "offset"]),
        tool("login", "Sign in to a website with its own login on the current sign-in form.", {"site": site},
             ["site"]),
        tool("finish", "End with a report. accepted=true only after observing a successful signed-submission "
             "receipt; copy it exactly into receipt. Otherwise accepted=false and receipt empty.",
             {"report": {"type": "string"}, "accepted": {"type": "boolean"},
              "receipt": {"type": "string"}}, ["report", "accepted", "receipt"]),
    ]


class SubmissionAgent:
    def __init__(self, model, tabs: dict[str, Tab], credentials: dict[str, dict], task: str, context: str,
                 deadline: float, max_steps: int = 400, log=print):
        self.model, self.tabs, self.task, self.context = model, tabs, task, context
        self.deadline, self.max_steps, self.log = deadline, max_steps, log
        self.views: dict[str, str] = {}
        self.primary_site = next(iter(tabs))
        self.entered_sites: set[str] = set()
        self.repeats: dict[tuple, int] = {}
        self.accepted = False
        self.receipt = ""
        # Automatic reload/recovery can erase a partly entered wizard. The model continues from its current view.
        for tab in tabs.values():
            tab.allow_recovery = False
        # the sign-in helper of the browsing sub-agent, so there is one place that knows how to sign in
        self._browsing = BrowsingAgent(model, tabs, credentials, "", "", name="submit", deadline=deadline, log=log)

    async def run(self) -> str:
        self.accepted, self.receipt = False, ""
        # Only the submission website is read up front; the others are opened when the model asks for them.
        first = next(iter(self.tabs))
        others = [site for site in self.tabs if site != first]
        messages = [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": f"{self.context}\n\n# Your websites\n- {first}: the bid is entered here\n"
             + "".join(f"- {site}: not opened yet; use look when you need it\n" for site in others)
             + f"\n# Your task\n{self.task}\n\n# Current page of {first}\n{await self._first_view(first)}"},
        ]
        tools = tool_schemas(list(self.tabs))
        told_to_sign = False
        for step in range(self.max_steps):
            remaining = self.deadline - time.time()
            if remaining < STOP_SECONDS:
                break
            if remaining < SIGN_NOW_SECONDS and not told_to_sign:
                messages.append({"role": "user", "content": SIGN_NOW})
                told_to_sign = True
            try:
                message = await asyncio.wait_for(self.model.chat(messages, tools, temperature=0.2),
                                                 timeout=self._budget(MODEL_CALL_SECONDS))
            except TimeoutError:
                self.log("[submit] a model call took too long; asking again")
                continue
            calls = message.tool_calls or []
            entry = {"role": "assistant", "content": message.content or ""}
            if calls:
                entry["tool_calls"] = [{"id": c.id, "type": "function", "function": {
                    "name": c.function.name, "arguments": c.function.arguments}} for c in calls]
            messages.append(entry)
            if not calls:
                messages.append({"role": "user", "content": "Use a tool: continue the submission, or call finish."})
                continue
            changed_sites = set()
            for call in calls:
                try:
                    args = json.loads(call.function.arguments or "{}")
                except ValueError:
                    args = {}
                if not isinstance(args, dict):
                    args = {}
                if call.function.name == "finish":
                    if args.get("accepted") is True and changed_sites:
                        messages.append({"role": "tool", "tool_call_id": call.id, "content":
                                         "NOT EXECUTED: read the results of this turn's actions before declaring "
                                         "acceptance. Finish with accepted=true only after observing the signed receipt."})
                        continue
                    receipt = args.get("receipt")
                    if args.get("accepted") is True and isinstance(receipt, str) and receipt.strip():
                        observed = self.views.get(self.primary_site, "")
                        normalize = lambda text: " ".join(text.split())
                        if normalize(receipt) not in normalize(observed):
                            messages.append({"role": "tool", "tool_call_id": call.id, "content":
                                             "NOT EXECUTED: this receipt was not in an observed submission page. "
                                             "Use look or read_more, then copy the signed receipt exactly. "
                                             "Otherwise finish with accepted=false."})
                            continue
                    self.accepted = args.get("accepted") is True and isinstance(receipt, str) and bool(receipt.strip())
                    self.receipt = receipt if self.accepted else ""
                    self.log(f"[submit] finished after {step + 1} steps")
                    return "REPORT:\n" + str(args.get("report", ""))
                self.log(f"[submit] {call.function.name} {_summary(args)}")
                site = args.get("site")
                if site in changed_sites and _uses_numbers(call.function.name, args):
                    result = ("NOT EXECUTED: an earlier tool in this response changed this website's page view. "
                              "Use the latest view and choose the action again; the old numbers may target "
                              "another control.")
                elif self._budget(TOOL_SECONDS) <= 0:
                    result = "NOT EXECUTED: the submission deadline has been reached."
                else:
                    try:
                        result = await asyncio.wait_for(self._run_tool(call.function.name, args),
                                                        timeout=self._budget(TOOL_SECONDS))
                    except TimeoutError:
                        result = ("Error: the action did not finish in time. Continue from the current page; "
                                  "look again before retrying, without reopening or reloading the form.")
                    except Exception as error:  # noqa: BLE001 - the model reads the failure and decides what to do
                        result = f"Error: {str(error).splitlines()[0][:400]}"
                    if call.function.name != "read_more":
                        changed_sites.add(site)
                if call.function.name == "fill_form":
                    self.log("[submit] -> " + result.split("\n\n# The page now")[0].replace("\n", " | ")[:900])
                result = self._progress(call.function.name, args, result)
                messages.append({"role": "tool", "tool_call_id": call.id, "content": result})
            _forget_old_views(messages)
        self.log("[submit] stopped without finish")
        return "REPORT:\n(stopped before the bid was confirmed: out of steps or time)"

    def _budget(self, limit: float) -> float:
        return max(0.0, min(limit, self.deadline - time.time() - STOP_SECONDS))

    def _progress(self, name: str, args: dict, result: str) -> str:
        site = args.get("site")
        view = _page_text(result)
        if view:
            self.views[site] = view
        if name not in ("click", "fill_form") or not view:
            return result
        key = (site, name, json.dumps(args, sort_keys=True, ensure_ascii=False), _page_fingerprint(view))
        count = self.repeats[key] = self.repeats.get(key, 0) + 1
        if count >= REPEATS_BEFORE_WARNING:
            self.log(f"[submit] the same action returned the same page {count} times")
            result += (f"\n\nThe same action has returned this same page {count} times. Read its errors and the "
                       "button's actual text, then choose another action from this current view. Do not go back "
                       "or reopen the form. A different position or changed field values would count as progress.")
        return result

    async def _first_view(self, site: str) -> str:
        self.log(f"[submit] reading {site}")
        started = time.time()
        budget = self._budget(FIRST_VIEW_SECONDS)
        if budget <= 0:
            return "(the submission deadline has been reached)"
        try:
            view = await asyncio.wait_for(self.tabs[site].observe(), timeout=budget)
        except TimeoutError:
            self.log(f"[submit] {site} was not read within {budget:.1f} s")
            return "(the page could not be read in time; use look on the current page, without reopening the form)"
        self.log(f"[submit] read {site} in {time.time() - started:.1f} s")
        self.views[site] = view
        return view

    async def _run_tool(self, name: str, args: dict) -> str:
        site = args.get("site")
        if site not in self.tabs:
            return f"Unknown site {site!r}; use one of {', '.join(self.tabs)}"
        tab = self.tabs[site]
        if name == "look":
            return await tab.observe()
        if name == "goto":
            url = str(args["url"])
            if site == self.primary_site and site in self.entered_sites:
                return ("NOT EXECUTED: reopening a submission address can discard entered steps. Continue from "
                        "the current form using its visible controls; use look to read it again.")
            return await tab.goto(url if "://" in url else tab._absolute(url))
        if name == "click":
            target = await _target(tab, {"element": str(args["element"])}, button=True)
            outcome = await _click(tab, target)
            return outcome + "\n\n# The page now\n" + await tab.observe()
        if name == "read_more":
            text = tab.read_more(int(args.get("offset") or 0))
            self.views[site] = self.views.get(site, "") + "\n" + text
            return text
        if name == "login":
            return await self._browsing._login(tab)
        if name == "fill_form":
            result = await fill_form(tab, args.get("fields") or [], args.get("click_after"),
                                     deadline=self.deadline - STOP_SECONDS)
            if any(marker in result.split("\n\n# The page now")[0] for marker in (": filled", ": selected", ": set")):
                self.entered_sites.add(site)
            return result
        return f"Unknown tool {name}"


async def fill_form(tab: Tab, fields: list[dict], click_after: str | None = None, *, deadline: float | None = None) -> str:
    if deadline is None:
        return await _fill_form(tab, fields, click_after)
    remaining = deadline - time.time()
    if remaining <= 0:
        return "FAILED: the submission deadline has been reached; no fields were touched"
    try:
        return await asyncio.wait_for(_fill_form(tab, fields, click_after), timeout=remaining)
    except TimeoutError:
        return ("FAILED: the submission deadline interrupted this step. Values already entered were kept; "
                "continue from the current form if time remains.")


async def _fill_form(tab: Tab, fields: list[dict], click_after: str | None = None) -> str:
    """Sets the fields in order and clicks `click_after` if all of them were set; returns the outcome per field and
    the page as it is afterwards.

    Every numbered element is looked up before the first one is changed, because the numbers of a page view stop
    being valid once the page changes. A field that the page has rebuilt in the meantime is found again by its id
    or name. A field named by its label is looked up when its turn comes, so it may appear after an earlier choice.
    """
    targets = [await _target(tab, {"element": str(field["click"])}, button=True) if _is_click(field)
               else await _target(tab, field) for field in fields]
    button = await _target(tab, {"element": str(click_after)}, button=True) if click_after else None

    outcomes, complete = [], True
    for field, target in zip(fields, targets):
        if _is_click(field):
            outcome = await _click(tab, target)
            outcomes.append(f"[{target['name']}] {outcome}")
            # A button can replace the form or open a popup. Do not rebind remaining old numeric targets there.
            for later in targets[len(outcomes):]:
                later["allow_rebind"] = False
            if button:
                button["allow_rebind"] = False
        elif "value" not in field or field.get("click"):
            outcome = "FAILED: give a value for a field, or a separate click entry"
            outcomes.append(outcome)
        else:
            outcome = await _set(target, str(field["value"]))
            outcomes.append(f"[{target['name']}] {target['identity'].get('label') or 'field'}: {outcome}")
        if outcome.startswith("FAILED"):
            complete = False
            skipped = len(fields) - len(outcomes)
            if skipped:
                outcomes.append(f"({skipped} later field(s) were not touched)")
            break

    if button and complete:
        outcomes.append(f"[{click_after}] click: {await _click(tab, button)}")
    elif button:
        outcomes.append(f"[{click_after}] not clicked, because a field failed")
    return "\n".join(outcomes) + "\n\n# The page now\n" + await tab.observe()


def _is_number(aid: str) -> bool:
    return bool(aid) and all(part.isdigit() for part in aid.replace("f", "", 1).split(".")) and aid[-1].isdigit()


def _is_click(field: dict) -> bool:
    return bool(str(field.get("click") or "").strip()) and "value" not in field


def _uses_numbers(name: str, args: dict) -> bool:
    if name == "click":
        return _is_number(str(args.get("element") or "").strip().strip("[]"))
    if name == "fill_form":
        return any(_is_number(str(field.get("element") or field.get("click") or "").strip().strip("[]"))
                   for field in args.get("fields") or []) or _is_number(str(args.get("click_after") or "").strip().strip("[]"))
    return False


def _page_text(result: str) -> str:
    if "\n\n# The page now\n" in result:
        return result.split("\n\n# The page now\n", 1)[1]
    lines = result.splitlines()
    start = next((i for i, line in enumerate(lines) if " URL: " in line), None)
    return "\n".join(lines[start:]) if start is not None else ""


def _page_fingerprint(view: str) -> str:
    # Keep position numbers and input values: identical URLs/headings can be different wizard items.
    normalized = re.sub(r"\[(?:f\d+\.)?\d+\]", "[control]", view)
    return hashlib.sha256(normalized.encode()).hexdigest()


async def _target(tab: Tab, field: dict, button: bool = False) -> dict:
    aid = str(field.get("element") or "").strip().strip("[]")
    label = str(field.get("label") or "").strip()
    if aid and not _is_number(aid):  # the model named it by its text
        aid, label = "", aid
    prefix = aid[: aid.index(".") + 1] if "." in aid else ""
    target = {"tab": tab, "page": tab.page, "frame": tab.aid_frames.get(prefix, tab.page.main_frame),
              "handle": None, "identity": {}, "allow_rebind": True,
              "label": label, "button": button, "name": aid or f'"{label}"', "problem": ""}
    if aid:
        try:
            target["handle"] = await tab.resolve_target(aid)
            target["identity"] = await target["handle"].evaluate(IDENTITY_JS)
        except Exception as error:  # noqa: BLE001 - reported as a failed field when it is this field's turn
            target["problem"] = str(error).splitlines()[0][:200]
    return target


async def _by_label(target: dict) -> str:
    """Looks a labelled field up on the page as it is now, waiting a moment for it to appear. Returns "" when it
    was found, otherwise why not."""
    state, waited = {}, 0.0
    while waited <= OPTION_WAIT_SECONDS:
        for frame in target["tab"].page.frames:
            try:
                state = await frame.evaluate(LABEL_JS, target["label"])
            except Exception:  # noqa: BLE001 - a frame that is navigating is skipped this round
                continue
            if state.get("found"):
                handle = await frame.query_selector("[data-fill-target]")
                if handle:
                    target["frame"], target["handle"] = frame, handle
                    target["page"] = target["tab"].page
                    target["identity"] = await handle.evaluate(IDENTITY_JS)
                    return ""
            if state.get("several"):
                break
        await asyncio.sleep(0.5)
        waited += 0.5
    if state.get("several"):
        return f'FAILED: several fields match "{target["label"]}"; use the element number'
    return f'FAILED: no field labelled "{target["label"]}"; the fields are: ' + " ; ".join(state.get("labels", []))


async def _current(target: dict):
    """The element as it is on the page now: the one looked up before, or its rebuilt successor."""
    handle, identity = target["handle"], target["identity"]
    if target["tab"].page is not target["page"]:
        return None
    if handle is not None:
        try:
            if await handle.evaluate("el => el.isConnected"):
                return handle
        except Exception:  # noqa: BLE001 - the frame navigated; look the element up again
            pass
    if not target["allow_rebind"]:
        return None
    tag = (identity.get("tag") or "").lower()
    for attribute in ("id", "name"):
        value = identity.get(attribute)
        if tag and value:
            found = await target["frame"].query_selector(f"{tag}[{attribute}={json.dumps(value)}]")
            if found:
                current = await found.evaluate(IDENTITY_JS)
                keys = ("document", "tag", "type", "label") + (("text",) if target["button"] else ())
                if any(current.get(key) != identity.get(key) for key in keys):
                    continue
                target["handle"] = found
                return found
    return None


async def _set(target: dict, value: str) -> str:
    if target["handle"] is None and target["label"]:
        problem = await _by_label(target)
        if problem:
            return problem
    if target["handle"] is None:
        return "FAILED: " + (target["problem"] or "no such element in the latest page view")
    error = ""
    for _ in range(2):  # once more if the page rebuilt the field while it was being set
        handle = await _current(target)
        if handle is None:
            return "FAILED: the field is no longer on the page"
        identity = target["identity"]
        try:
            if identity.get("tag") == "SELECT":
                return await _choose(target, value)
            await target["tab"].check_target(handle)
            if identity.get("type") in ("checkbox", "radio"):
                await handle.set_checked(value.strip().lower() in ("true", "1", "yes", "on", "checked"), timeout=4000)
                return "set"
            await handle.fill(value, timeout=4000)
            return "filled"  # the requested value is already in the tool call; never echo passwords or signing codes
        except Exception as failure:  # noqa: BLE001
            error = str(failure).splitlines()[0][:200]
            await asyncio.sleep(0.3)
    return f"FAILED: {error}"


async def _choose(target: dict, wanted: str) -> str:
    state, waited = {}, 0.0
    while waited <= OPTION_WAIT_SECONDS:
        handle = await _current(target)
        if handle is None:
            return "FAILED: the field is no longer on the page"
        state = await handle.evaluate(OPTION_JS, wanted)
        if state["match"] and not state["disabled"]:
            await target["tab"].check_target(handle)
            await handle.select_option(value=state["match"]["value"], timeout=4000)
            await _after_change(target["frame"])
            return f'selected "{state["match"]["text"]}"'
        await asyncio.sleep(0.5)
        waited += 0.5
    if state.get("disabled"):
        return ("FAILED: the select is disabled. Apply the earlier choice first; an intermediate "
                '{"click": "<the button text>"} can enable it before this field.')
    return f'FAILED: no option "{wanted}"; the options are: ' + " ; ".join(state.get("options", []))


async def _click(tab: Tab, target: dict) -> str:
    handle = await _current(target)
    if handle is None and target["label"]:  # named by its text: look it up on the page as it is now
        try:
            handle = await tab.resolve_target(target["label"])
        except Exception as error:  # noqa: BLE001
            target["problem"] = str(error).splitlines()[0][:200]
            handle = None
    if handle is None:
        return "FAILED: " + (target["problem"] or "the button is not on the page")
    text = await _text_of(handle)
    try:
        await tab.click_target(handle)
    except Exception as error:  # noqa: BLE001 - blocked or stale controls must not be forced
        return f'FAILED: button "{text}": {str(error).splitlines()[0][:200]}'
    return f'clicked "{text}"'


async def _text_of(handle) -> str:
    try:
        return (await handle.evaluate("el => (el.innerText || el.value || el.getAttribute('aria-label') || '')"
                                      ".replace(/\\s+/g, ' ').trim()"))[:80]
    except Exception:  # noqa: BLE001
        return "?"


async def _after_change(frame) -> None:
    """Gives the page a moment to react to a choice: dependent fields load, a step is saved."""
    await asyncio.sleep(0.3)
    try:
        await frame.page.wait_for_load_state("networkidle", timeout=1500)
    except Exception:  # noqa: BLE001 - a page that never goes idle is still usable
        pass


def _summary(args: dict) -> str:
    if "fields" in args:
        return json.dumps({**args, "fields": f"{len(args['fields'])} fields"}, ensure_ascii=False)[:200]
    return json.dumps(args, ensure_ascii=False)[:200]


def _forget_old_views(messages: list[dict], keep: int = 2) -> None:
    views = [i for i, m in enumerate(messages) if m["role"] == "tool" and len(m["content"]) > 1500]
    for i in views[:-keep]:
        messages[i]["content"] = "(older page view removed to save space)"
