"""Entering and signing the bid: a model works the submission website with a tool that fills many fields at once.

The model still chooses every field, value and button from the page it reads. Code only carries out a whole group
of those choices in one go: asking the model again after every single field makes a long form take longer than
the run lasts.
"""
import asyncio
import json
import time

from browser import Tab
from browsing import BrowsingAgent

SIGN_NOW_SECONDS = 150  # this long before the end the agent is told to stop filling and get the bid signed
STOP_SECONDS = 10
MODEL_CALL_SECONDS = 45  # a step normally takes under 10 s; a call that stalls is dropped and asked again
TOOL_SECONDS = 90
FIRST_VIEW_SECONDS = 30
OPTION_WAIT_SECONDS = 6  # dependent dropdowns load their options after the one before them was chosen

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
- Selections that depend on each other (each one enables the next) go in one fill_form call in their order. When
  a form enables the next field only after a button applies the choice, put that button between the fields as
  {"click": "<button text>"}. Name such fields by their label: numbers shift when a popup appears.
- Element numbers belong to one page view: after every step take them from the newest view, never from an earlier
  page, where the same number may be another button. Name the button that continues by its visible text in
  click_after; it is looked up on the page as it is then. A button that goes back discards the later steps: use
  it only to correct an earlier step on purpose.
- If you come back to a page you have already filled, you are going round in a circle: read the newest view and
  the button texts before you act again.
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
- When the site confirms the bid, call finish with the receipt text exactly as shown. If you cannot get there,
  call finish and say exactly what is entered, what is missing and what stopped you.
"""

BUTTON_TEXT_JS = "el => (el.innerText || el.value || el.getAttribute('aria-label') || '').replace(/\\s+/g, ' ').trim()"
REPEATS_BEFORE_WARNING = 3  # the same action this often means the agent is going round in a circle
CIRCLE = ("\n\nNote: this is the {n}th time you did exactly this, so the form keeps bringing you back here: you are "
          "going round in a circle. Element numbers change from page to page; check in the newest view what the "
          "button you click says (a button that goes back discards the later steps), then continue forward.")

SIGN_NOW = ("Time is nearly over. Stop filling optional fields: go on to signing and confirming the bid now, with "
            "what is entered. If positions are still empty and the form can leave a position out, use that.")

# The label is the label's own words, without the text of the control it wraps.
IDENTITY_JS = """el => {
  const label = el.labels && el.labels[0];
  const words = label ? [...label.childNodes].filter((n) => n.nodeType === 3).map((n) => n.textContent).join(" ") : "";
  return {
    tag: el.tagName, type: (el.type || "").toLowerCase(), name: el.name || "", id: el.id || "",
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
  const hit = options.find((o) => norm(o.text) === w || norm(o.value) === w)
    || only((o) => norm(o.text).startsWith(w)) || only((o) => norm(o.text).includes(w));
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
             "An entry {\"click\": \"<button text>\"} presses that button at its place in the sequence. "
             "Stops at the first field that fails and reports every field.",
             {"site": site,
              "fields": {"type": "array", "items": {"type": "object", "properties": {
                  "element": element,
                  "label": {"type": "string", "description": "the field's label, instead of element, for a field "
                                                             "that appears only after an earlier choice"},
                  "value": {"type": "string"},
                  "click": {"type": "string", "description": "instead of a field: the text (or number) of a button "
                            "to press at this point, e.g. one that applies a choice so that the next field is "
                            "enabled"}}}},
              "click_after": {"type": "string", "description": "element number, or text, of the button to click "
                                                                "once every field is set, e.g. the one that continues"}},
             ["site", "fields"]),
        tool("read_more", "Continue reading a long page view from a character offset.",
             {"site": site, "offset": {"type": "integer"}}, ["site", "offset"]),
        tool("login", "Sign in to a website with its own login on the current sign-in form.", {"site": site},
             ["site"]),
        tool("finish", "End with your report: the receipt exactly as shown, or what is entered and what stopped you.",
             {"report": {"type": "string"}}, ["report"]),
    ]


class SubmissionAgent:
    def __init__(self, model, tabs: dict[str, Tab], credentials: dict[str, dict], task: str, context: str,
                 deadline: float, max_steps: int = 80, log=print):
        self.model, self.tabs, self.task, self.context = model, tabs, task, context
        self.deadline, self.max_steps, self.log = deadline, max_steps, log
        # the sign-in helper of the browsing sub-agent, so there is one place that knows how to sign in
        self._browsing = BrowsingAgent(model, tabs, credentials, "", "", name="submit", deadline=deadline, log=log)

    async def run(self) -> str:
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
        done: dict[str, int] = {}  # how often each exact action led to the same page
        for step in range(self.max_steps):
            remaining = self.deadline - time.time()
            if remaining < STOP_SECONDS:
                break
            if remaining < SIGN_NOW_SECONDS and not told_to_sign:
                messages.append({"role": "user", "content": SIGN_NOW})
                told_to_sign = True
            try:
                message = await asyncio.wait_for(self.model.chat(messages, tools, temperature=0.2),
                                                 timeout=min(remaining, MODEL_CALL_SECONDS))
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
            for call in calls:
                try:
                    args = json.loads(call.function.arguments or "{}")
                except ValueError:
                    args = {}
                if call.function.name == "finish":
                    self.log(f"[submit] finished after {step + 1} steps")
                    return "REPORT:\n" + str(args.get("report", ""))
                self.log(f"[submit] {call.function.name} {_summary(args)}")
                try:
                    result = await asyncio.wait_for(self._run_tool(call.function.name, args), timeout=TOOL_SECONDS)
                except TimeoutError:
                    result = "Error: the action did not finish in time. Look at the page again."
                except Exception as error:  # noqa: BLE001 - the model reads the failure and decides what to do
                    result = f"Error: {str(error).splitlines()[0][:400]}"
                # the same action that brings the agent to the same page again; leaving out one position after
                # another repeats the action too, but each time on the next position's page
                action = (call.function.name + json.dumps(args, sort_keys=True, ensure_ascii=False)
                          + _where(result))
                done[action] = done.get(action, 0) + 1
                if done[action] >= REPEATS_BEFORE_WARNING and call.function.name in ("click", "fill_form"):
                    self.log(f"[submit] the same {call.function.name} for the {done[action]}th time: told it is going in a circle")
                    result += CIRCLE.format(n=done[action])
                messages.append({"role": "tool", "tool_call_id": call.id, "content": result})
            _forget_old_views(messages)
        self.log("[submit] stopped without finish")
        return "REPORT:\n(stopped before the bid was confirmed: out of steps or time)"

    async def _first_view(self, site: str) -> str:
        self.log(f"[submit] reading {site}")
        started = time.time()
        try:
            view = await asyncio.wait_for(self.tabs[site].observe(), timeout=FIRST_VIEW_SECONDS)
        except TimeoutError:
            self.log(f"[submit] {site} was not read within {FIRST_VIEW_SECONDS} s")
            return "(the page could not be read in time; use look or goto)"
        self.log(f"[submit] read {site} in {time.time() - started:.1f} s")
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
            return await tab.goto(url if "://" in url else tab._absolute(url))
        if name == "click":
            element = str(args["element"])
            try:
                text = await _text_of(await (await tab.find(element)).element_handle(timeout=3000))
            except Exception:  # noqa: BLE001 - the click reports what is wrong
                text = "?"
            return f'Clicked [{element}] "{text}".\n' + await tab.click(element)
        if name == "read_more":
            return tab.read_more(int(args.get("offset") or 0))
        if name == "login":
            return await self._browsing._login(tab)
        if name == "fill_form":
            return await fill_form(tab, args.get("fields") or [], args.get("click_after"))
        return f"Unknown tool {name}"


async def fill_form(tab: Tab, fields: list[dict], click_after: str | None = None) -> str:
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
        if _is_click(field):  # a button pressed between fields, e.g. one that applies a choice
            outcome = await _click(tab, target)
            outcomes.append(f"[{target['name']}] {outcome}")
        else:
            outcome = await _set(target, str(field.get("value", "")))
            outcomes.append(f"[{target['name']}] {target['identity'].get('label') or 'field'}: {outcome}")
        if "FAILED" in outcome:
            complete = False
            skipped = len(fields) - len(outcomes)
            if skipped:
                outcomes.append(f"({skipped} later field(s) were not touched)")
            break

    if button and complete:
        outcomes.append(f"[{click_after}] {await _click(tab, button)}")
    elif button:
        outcomes.append(f"[{click_after}] not clicked, because a field failed")
    return "\n".join(outcomes) + "\n\n# The page now\n" + await tab.observe()


def _where(view: str) -> str:
    """Which page a result ends on: its address and headings (numbers and values left out)."""
    page = view.split("# The page now", 1)[-1]
    lines = page.splitlines()
    address = next((line for line in lines if " URL: " in line), "")
    return address + "|" + "|".join(line for line in lines if line.startswith("#"))


def _is_click(field: dict) -> bool:
    return bool(str(field.get("click") or "").strip()) and not str(field.get("value") or "").strip()


def _is_number(aid: str) -> bool:
    return bool(aid) and all(part.isdigit() for part in aid.replace("f", "", 1).split(".")) and aid[-1].isdigit()


async def _target(tab: Tab, field: dict, button: bool = False) -> dict:
    aid = str(field.get("element") or "").strip().strip("[]")
    label = str(field.get("label") or "").strip()
    if aid and not _is_number(aid):  # the model named it by its text
        aid, label = "", aid
    prefix = aid[: aid.index(".") + 1] if "." in aid else ""
    target = {"tab": tab, "frame": tab.aid_frames.get(prefix, tab.page.main_frame), "handle": None, "identity": {},
              "label": label, "button": button, "name": aid or f'"{label}"'}
    if aid:
        try:
            target["handle"] = await tab._locate(aid).element_handle(timeout=3000)
            target["identity"] = await target["handle"].evaluate(IDENTITY_JS)
        except Exception:  # noqa: BLE001 - reported as a failed field when it is this field's turn
            pass
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
    if handle is not None:
        try:
            if await handle.evaluate("el => el.isConnected"):
                return handle
        except Exception:  # noqa: BLE001 - the frame navigated; look the element up again
            pass
    tag = (identity.get("tag") or "").lower()
    for attribute in ("id", "name"):
        value = identity.get(attribute)
        if tag and value:
            found = await target["frame"].query_selector(f"{tag}[{attribute}={json.dumps(value)}]")
            if found:
                target["handle"] = found
                return found
    return None


async def _set(target: dict, value: str) -> str:
    if target["handle"] is None and target["label"]:
        problem = await _by_label(target)
        if problem:
            return problem
    if target["handle"] is None:
        return "FAILED: no such element in the latest page view"
    error = ""
    for _ in range(2):  # once more if the page rebuilt the field while it was being set
        handle = await _current(target)
        if handle is None:
            return "FAILED: the field is no longer on the page"
        identity = target["identity"]
        try:
            if identity.get("tag") == "SELECT":
                return await _choose(target, value)
            if identity.get("type") in ("checkbox", "radio"):
                await handle.set_checked(value.strip().lower() in ("true", "1", "yes", "on", "checked"), timeout=4000)
                return "set"
            await handle.fill(value, timeout=4000)
            return f'filled "{value}"'
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
            await handle.select_option(value=state["match"]["value"], timeout=4000)
            await _after_change(target["frame"])
            return f'selected "{state["match"]["text"]}"'
        await asyncio.sleep(0.5)
        waited += 0.5
    if state.get("disabled"):
        return ('FAILED: the select is disabled. An earlier choice enables it, often only once a button applies that '
                'choice: put that button between the fields as {"click": "<its text>"}')
    return f'FAILED: no option "{wanted}"; the options are: ' + " ; ".join(state.get("options", []))


async def _click(tab: Tab, target: dict) -> str:
    handle = await _current(target)
    if handle is None and target["label"]:  # named by its text: look it up on the page as it is now
        try:
            handle = await (await tab.find(target["label"])).element_handle(timeout=3000)
        except Exception:  # noqa: BLE001
            handle = None
    if handle is None:
        return "click: FAILED: the button is not on the page"
    text = await _text_of(handle)
    try:
        await handle.click(timeout=6000)
    except Exception:  # noqa: BLE001 - covered or off-screen: a DOM click still works
        await handle.evaluate("el => el.click()")
    await _after_change(tab.page.main_frame)
    return f'clicked "{text}"'


async def _text_of(handle) -> str:
    """What a button or link says, so a result can name what was clicked."""
    try:
        return (await handle.evaluate(BUTTON_TEXT_JS))[:80]
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
