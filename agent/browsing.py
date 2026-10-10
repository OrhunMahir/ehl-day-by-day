"""A browsing sub-agent: a model works one or more websites through numbered page views until it can report.

The model picks every navigation target, search and document. Code only executes the action it chose, renders the
page as text, and keeps the conversation small: older page views are dropped once newer ones exist, while the
facts the model saved with `note` stay.
"""
import json
import time

from browser import Tab

TOOLS = [
    ("look", "Show the current page again (after waiting for it to update).", {}),
    ("goto", "Open a URL (absolute, or a path on the site).", {"url": "string"}),
    ("click", "Click the element with this number from the page view (e.g. '12' or 'f1.3' inside a frame).", {"element": "string"}),
    ("type", "Replace the text of an input/textarea; set enter=true to press Enter afterwards (submits searches/forms).",
     {"element": "string", "text": "string", "enter": "boolean"}),
    ("select", "Choose an option of a select element by its visible text or value.", {"element": "string", "option": "string"}),
    ("press", "Press a keyboard key on the page, e.g. Escape, Enter, F4, Tab.", {"key": "string"}),
    ("back", "Go back in the browser history.", {}),
    ("read_more", "Continue reading a long page view from a character offset.", {"offset": "integer"}),
    ("download", "Download a link target with this site's login (ZIP, CSV, PDF, document pages) and read its text.", {"url": "string"}),
    ("read_file", "Read a file that was downloaded earlier, from an optional offset.", {"name": "string", "offset": "integer"}),
    ("login", "Fill in this website's username and password on the current login form and submit it.", {}),
    ("note", "Save facts you read, verbatim with their exact numbers, dates, units, references and senders. Notes are "
     "the only thing you keep from pages you leave; your final report is built from them.", {"facts": "string"}),
    ("finish", "End your investigation with your report: every fact the task asked for, with where you found it, and "
     "what you could not find.", {"report": "string"}),
]
OPTIONAL = {"enter", "offset"}


def tool_schemas(multi_site: bool) -> list[dict]:
    schemas = []
    for name, description, params in TOOLS:
        properties = {key: {"type": kind} for key, kind in params.items()}
        required = [key for key in params if key not in OPTIONAL]
        if multi_site and name not in ("note", "finish"):
            properties["site"] = {"type": "string", "description": "which website (siteId)"}
            required.append("site")
        schemas.append({"type": "function", "function": {
            "name": name, "description": description,
            "parameters": {"type": "object", "properties": properties, "required": required}}})
    return schemas


SYSTEM = """You are a careful sales-engineering assistant operating real business websites through a text view of the browser.

How the page view works: each page is shown as text; elements you can use carry a number in brackets, like [12] or
[f1.3] (inside a frame). Act by number. Tables show as | cell | rows. "(tooltip: ...)" and "(hidden ...)" mark text
a person would see by hovering or expanding. Content of embedded frames (document viewers, mail bodies) follows the
page as "--- frame fN ---".

Working rules:
- The websites are deliberately hostile: cookie walls, surveys and popups (close them; never accept surveys or
  unrelated offers), decoy buttons that do nothing, error pages that work on a retry, session timeouts that send you
  back to the login (use the login tool again), paging and "load more" controls, superseded documents shown first,
  look-alike records for other references. Verify that what you read belongs to the exact reference you need.
- Prefer direct links: if an element shows "-> /some/path", you may goto that path. For files (ZIP, CSV, PDF), use
  download. Use search forms with specific terms; over-broad searches may list nothing.
- Save every relevant fact with note as soon as you read it (verbatim numbers, units, dates, senders, record ids,
  status). Pages you leave are removed from your memory; notes are kept.
- Do not guess values. If something is missing, say so in the report.
- Be quick: time is limited. Several tool calls in one turn are fine when they don't depend on each other.
- Finish with finish(report) once you have what the task needs.
"""


class BrowsingAgent:
    def __init__(self, model, tabs: dict[str, Tab], credentials: dict[str, dict], task: str, context: str,
                 name: str, deadline: float, max_steps: int = 60, log=print):
        self.model, self.tabs, self.credentials = model, tabs, credentials
        self.task, self.context, self.name = task, context, name
        self.deadline, self.max_steps, self.log = deadline, max_steps, log
        self.notes: list[str] = []
        self.multi = len(tabs) > 1

    def _tab(self, args: dict) -> Tab:
        if not self.multi:
            return next(iter(self.tabs.values()))
        site = args.get("site") or next(iter(self.tabs))
        if site not in self.tabs:
            raise ValueError(f"Unknown site {site!r}; use one of {', '.join(self.tabs)}")
        return self.tabs[site]

    async def _login(self, tab: Tab) -> str:
        creds = self.credentials[tab.site_id]
        page_text = await tab.observe()
        frame = tab.page.main_frame
        password = frame.locator("input[type=password]").first
        if not await password.count():
            return "No password field on this page. " + page_text
        user = frame.locator("form:has(input[type=password]) input:not([type=hidden]):not([type=password]):not([type=checkbox])").first
        if await user.count():
            await user.fill(creds["username"])
        await password.fill(creds["password"])
        await password.press("Enter")
        await tab.settle()
        return await tab.observe()

    async def _run_tool(self, name: str, args: dict) -> str:
        tab = self._tab(args) if name not in ("note", "finish") else None
        if name == "look":
            return await tab.observe()
        if name == "goto":
            url = args["url"]
            if "://" not in url:
                from urllib.parse import urljoin
                url = urljoin(tab.page.url, url)
            return await tab.goto(url)
        if name == "click":
            return await tab.click(str(args["element"]))
        if name == "type":
            return await tab.fill(str(args["element"]), str(args.get("text", "")), bool(args.get("enter")))
        if name == "select":
            return await tab.select(str(args["element"]), str(args["option"]))
        if name == "press":
            return await tab.press(args["key"])
        if name == "back":
            return await tab.back()
        if name == "read_more":
            return tab.read_more(int(args.get("offset") or 0))
        if name == "download":
            return await tab.fetch_file(args["url"])
        if name == "read_file":
            return tab.read_file(args["name"], int(args.get("offset") or 0))
        if name == "login":
            return await self._login(tab)
        if name == "note":
            self.notes.append(args.get("facts", ""))
            return "Saved."
        raise ValueError(f"Unknown tool {name}")

    async def run(self) -> str:
        sites = "\n".join(f"- {site_id}: {tab.page.url if tab.page.url != 'about:blank' else ''} (login with the login tool)"
                          for site_id, tab in self.tabs.items())
        messages = [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": f"{self.context}\n\n# Your websites\n{sites}\n\n# Your task\n{self.task}"},
        ]
        # open each site so the model starts from a real page view
        for tab in self.tabs.values():
            messages[1]["content"] += "\n\n# Current page\n" + await tab.observe()
        tools = tool_schemas(self.multi)
        for step in range(self.max_steps):
            remaining = self.deadline - time.time()
            if remaining < 20:
                break
            if remaining < 90:
                messages.append({"role": "user", "content": "Time is almost up: call finish now with what you have."})
            message = await self.model.chat(messages, tools, temperature=0.2)
            calls = message.tool_calls or []
            entry = {"role": "assistant", "content": message.content or ""}
            if calls:
                entry["tool_calls"] = [{"id": c.id, "type": "function",
                                        "function": {"name": c.function.name, "arguments": c.function.arguments}} for c in calls]
            messages.append(entry)
            if not calls:
                messages.append({"role": "user", "content": "Use a tool: continue the task, or call finish with your report."})
                continue
            for call in calls:
                try:
                    args = json.loads(call.function.arguments or "{}")
                except ValueError:
                    args = {}
                if call.function.name == "finish":
                    report = args.get("report", "")
                    self.log(f"[{self.name}] finished after {step + 1} steps")
                    return self._report(report)
                self.log(f"[{self.name}] {call.function.name} {json.dumps(args, ensure_ascii=False)[:200]}")
                try:
                    result = await self._run_tool(call.function.name, args)
                except Exception as error:  # noqa: BLE001
                    result = f"Error: {str(error).splitlines()[0][:400]}"
                messages.append({"role": "tool", "tool_call_id": call.id, "content": result})
            self._forget_old_views(messages)
        self.log(f"[{self.name}] stopped without finish")
        return self._report("(stopped before finishing: out of steps or time)")

    def _report(self, report: str) -> str:
        notes = "\n".join(f"- {n}" for n in self.notes)
        return f"REPORT:\n{report}\n\nNOTES TAKEN WHILE BROWSING:\n{notes or '(none)'}"

    @staticmethod
    def _forget_old_views(messages: list[dict], keep: int = 2):
        views = [i for i, m in enumerate(messages) if m["role"] == "tool" and len(m["content"]) > 1500]
        for i in views[:-keep]:
            first = messages[i]["content"].splitlines()[0][:200]
            messages[i]["content"] = f"(older page view removed to save space; it began: {first})"
