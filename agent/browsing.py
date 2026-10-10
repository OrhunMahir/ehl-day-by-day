"""A browsing sub-agent: a model works one or more websites through numbered page views until it can report.

The model picks every navigation target, search and document. Code only executes the action it chose, renders the
page as text, and keeps the conversation small: older page views are dropped once newer ones exist, while the
facts the model saved with `note` stay.
"""
import asyncio
import json
import time

from browser import Tab, origin

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
    ("read_pages", "Read up to 6 pages at once whose addresses you see (links '-> /path' of records, tabs, messages, "
     "documents): they are opened in the background and returned together. Much faster than visiting them one by "
     "one; use goto when you need to act on a page.", {"urls": "array"}),
    ("collect_pages", "Read a paged list in one step: give the number of its 'next page' or 'load more' control; it "
     "is followed until it disappears (or max_pages) and the pages are returned together.",
     {"element": "string", "max_pages": "integer"}),
    ("download", "Download a link target with this site's login (ZIP, CSV, PDF, document pages) and read its text.", {"url": "string"}),
    ("read_file", "Read a file that was downloaded earlier, from an optional offset.", {"name": "string", "offset": "integer"}),
    ("login", "Fill in this website's username and password on the current login form and submit it.", {}),
    ("note", "Save facts you read, verbatim with their exact numbers, units and identifiers, with where they come "
     "from: `document` (title, number or id of the document, record or message), `date` (its issue date as shown, "
     "with time and zone if shown), `sender` (mail address or author), `revision` (version or revision id), "
     "`status` (as the site marks it: e.g. current, valid, superseded, draft, blocked, withdrawn) and `reference` "
     "(the exact tender or case reference the record names, character for character). Leave out what does not "
     "apply. The page you are on is added automatically. Notes are the only thing you keep from pages you leave.",
     {"facts": "string", "document": "string", "date": "string", "sender": "string", "revision": "string",
      "status": "string", "reference": "string", "source": "string"}),
    ("finish", "End your investigation with your report in four parts. FOUND: every fact the task asked for, one per "
     "line, as `value (unit) — document, date, sender, revision, status, reference`. SUPERSEDED / LOOK-ALIKE: "
     "records that resemble what was asked but do not apply (older versions, other references, other senders, "
     "dated after the scenario date, drafts), each with why. CHECKED, NOT PRESENT: what you searched for completely "
     "and found does not exist, with the evidence (search terms, number of results, how many you opened). NOT "
     "CHECKED: what you did not reach or only partly checked (e.g. 2 of 5 results opened, a tab not opened), so "
     "nobody mistakes it for absent.", {"report": "string"}),
]
SOURCE_FIELDS = ("document", "date", "sender", "revision", "status", "reference")
OPTIONAL = {"enter", "offset", "source", "max_pages", *SOURCE_FIELDS}
WRAP_UP_SECONDS = 90  # the wrap-up report may take at most this long
# and at most this many tokens: Qwen writes about 40 tokens per second, so 1000 tokens fit into WRAP_UP_RESERVE
# (2000 took about 50 s and were cut off); the notes themselves are appended to the report anyway
WRAP_UP_TOKENS = 1000
WRAP_UP_RESERVE = 45  # seconds before the deadline at which browsing stops, so the wrap-up fits inside it
CALL_SECONDS = 75  # one model call may take at most this long before it is retried
# a model without thinking answers a step in 2-3 s, so a call still open after this long has stalled
QUICK_CALL_SECONDS = 30
CALL_ATTEMPTS = 5  # attempts per step, as far as the deadline allows
UNNOTED_KEPT = 2  # results of the last actions without a note after them, kept for a report written in a hurry
UNNOTED_CHARS = 5000  # of each of them
ACTION_SECONDS = 60  # one browser action may take at most this long
PREVIOUS_VIEW_CHARS = 4000  # the page view before the newest one is cut to this length
SHORTENED = "\n... (older page view shortened to save space; your notes keep what mattered)"
NOTE_EVERY = 6  # actions without a note before the agent is reminded to save what it read
WAIT_SECONDS = 45  # one `wait` for the shared board to change; under ACTION_SECONDS
HUNG_BEFORE_REOPEN = 2  # page loads in a row that the website did not answer before it gets a fresh session
REOPENS = 2  # fresh sessions per website and run at most


def tool_schemas(multi_site: bool) -> list[dict]:
    schemas = []
    for name, description, params in TOOLS:
        properties = {key: ({"type": "array", "items": {"type": "string"}} if kind == "array" else {"type": kind})
                      for key, kind in params.items()}
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
- Several pages to read whose addresses you can see (a record's tabs, the messages in a result list, documents):
  use read_pages with all of them in one call instead of visiting each.
- Paged lists and "load more": use collect_pages instead of clicking through page by page. A search that shows
  no results may be too broad or too narrow: refine it before concluding that nothing exists.
- Prefer direct links: if an element shows "-> /some/path", you may goto that path. For files (ZIP, CSV, PDF), use
  download. Use search forms with specific terms; over-broad searches may list nothing.
- Save every relevant fact with note as soon as you read it (verbatim numbers, units, dates, senders, record ids,
  status), with its source. Pages you leave are removed from your memory; notes are kept.
- Stay on your own website(s). Facts that live on another website are another assistant's job: list them under
  NOT FOUND instead of going there.
- Do not guess values. If something is missing, say so in the report.
- Records often keep their details in separate tabs, registers or sub-views (details, scales, history, items):
  open them before you report a value as missing.
- Versions: documents, approvals, rates and answers come in versions (revisions, newer mails, later answers,
  other folders or pages). Find all versions, note each with its date and status, and say which one the site marks
  as current or valid and on what basis (an explicit marker, or the latest date). Say which version each value comes
  from. A version that opens first is not necessarily the current one.
- Look-alikes: check every record's reference character for character against the one you need (a suffix such as
  "-A" or a different number makes it another case), its sender against the brief's authorised senders, and its
  date against the scenario date. A record that fails any check goes under SUPERSEDED / LOOK-ALIKE, never under
  FOUND. Unsent drafts, previews and hidden texts are not records.
- Searches: note how complete a search was (terms, number of results, how many you opened). Report something as
  absent only after a complete search; otherwise it is NOT CHECKED.
- Be quick: time is limited. Several tool calls in one turn are fine when they don't depend on each other.
- Finish with finish(report) once you have what the task needs.
"""


NAVIGATING = {"goto", "click", "type", "select", "press", "back", "look", "collect_pages"}


async def _has_password_field(tab: Tab) -> bool:
    try:
        return await tab.page.main_frame.locator("input[type=password]:visible").count() > 0
    except Exception:  # noqa: BLE001
        return False


async def _shows_sign_in(tab: Tab) -> bool:
    """Whether the page now shows a sign-in form: a visible password field together with a user-name field, or on
    a sign-in address. A password-type field alone (e.g. a signing code) is not a sign-in form."""
    if not await _has_password_field(tab):
        return False
    if _looks_like_sign_in_url(tab.page.url):
        return True
    try:
        user = ("form:has(input[type=password]:visible) input:visible:not([type=password]):not([type=hidden])"
                ":not([type=checkbox]):not([type=radio]):not([type=submit]):not([type=button])")
        return await tab.page.main_frame.locator(user).count() > 0
    except Exception:  # noqa: BLE001
        return False


def _looks_like_sign_in_url(url: str) -> bool:
    return any(word in url.lower() for word in ("login", "logon", "signin", "sign-in", "anmeld"))


def _no_thinking(model) -> dict:
    """Qwen reasons before it answers; a wrap-up only reformats notes, so its reasoning is switched off (otherwise
    the token limit can be spent on reasoning and the answer comes back empty). Other providers get nothing."""
    if getattr(model, "provider", None) == "qwen":
        return {"extra_body": {"chat_template_kwargs": {"enable_thinking": False}}}
    return {}


BOARD = """
You are one of several assistants, one per website, connected only by a shared board. Record on the board every
fact the bid rules need that your website shows, whatever kind it is. Before each of your steps you see what changed
on the board: what the other assistants recorded and what the bid still needs. Take on every open item your
website can answer, the ones naming positions first, e.g. a search by a key another website gave. Never try to open
another website. When nothing on your website is left for now, call wait: it returns as soon as the board changes."""

RECORDING = """
In this job you save facts with `record`, as typed records, not with notes: wherever these rules say note, use
record. A record is the only thing that counts and it is kept at once; record what you read before you leave the
page. Fill each record from the page exactly, with its source (site, document, date, sender, revision); a record
that does not fit is refused with the reason, then correct it and record it again. When your job is done, call
finish with a one-line summary."""


class BrowsingAgent:
    def __init__(self, model, tabs: dict[str, Tab], credentials: dict[str, dict], task: str, context: str,
                 name: str, deadline: float, max_steps: int = 60, log=print, record=None,
                 record_schema: dict | None = None, board=None, browsers: list | None = None):
        """`record`, when given, is a function that saves typed facts (`record_schema` describes them) and returns
        what it made of them: the agent then records facts with it instead of taking notes, and needs no wrap-up
        report at the end, because whatever it recorded is already saved. `board`, when given, is the shared board
        (board.Board): the agent records there, sees before each step what changed on it, and can wait for it."""
        self.board, self.seen = board, 0
        # browsers a website that stops answering may be opened again in, besides its own (e.g. the run's others)
        self.browsers = list(browsers or [])
        self.hung: dict[str, int] = {}  # website -> page loads in a row it did not answer
        self.reopened: dict[str, int] = {}
        if board is not None and record is None:
            record = lambda facts: board.record(name, facts)  # noqa: E731
        self.record, self.record_schema = record, record_schema
        self.model, self.tabs, self.credentials = model, tabs, credentials
        self.task, self.context, self.name = task, context, name
        self.deadline, self.max_steps, self.log = deadline, max_steps, log
        self.notes: list[str] = []
        self.last_tab: Tab = next(iter(tabs.values()))
        self.refusals = 0  # requests to open another website's pages, refused
        self.visited: list[str] = []  # pages the agent saw, in order, for the wrap-up report
        self.signed_in: set[str] = set()  # sites where login succeeded, so a later sign-in form means an expired session
        self.relogins = 0
        self.stalls = 0  # model calls that hit CALL_SECONDS
        self.multi = len(tabs) > 1
        # what the last actions showed, since the last note: when time runs out before the agent noted it, the
        # report carries it instead of losing it
        self.unnoted: list[str] = []
        self.finished = False

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
        if name == "wait":
            if self.board is None:
                raise ValueError("Unknown tool wait")
            before = self.seen
            changed = await self.board.wait(before, min(WAIT_SECONDS, max(1.0, self.deadline - time.time() - 10)))
            if not changed:
                return "No change on the board yet. Explore your website for what the open items will need, or wait again."
            self.seen = self.board.version
            return "The board changed:\n" + self.board.news(self.name, before)
        if name == "record":
            if not self.record:
                raise ValueError("Unknown tool record")
            facts = args.get("facts", args) if isinstance(args, dict) else args
            try:
                return self.record(facts)
            except Exception as error:  # noqa: BLE001 - the model reads why and records it again
                return f"Not recorded: {error}. Correct it and record it again."
        tab = self._tab(args) if name not in ("note", "finish") else None
        if tab is not None:
            self.last_tab = tab
        if name == "read_pages":
            urls = args.get("urls") or []
            if isinstance(urls, str):
                urls = [u.strip() for u in urls.replace("\n", ",").split(",") if u.strip()]
            refused = [u for u in urls if self._foreign_site(tab, u)]
            if refused:
                self.refusals += len(refused)
            args = {**args, "urls": [u for u in urls if u not in refused]}
            if not args["urls"]:
                return "Refused: those pages belong to other websites; stay on your own and list them under NOT CHECKED."
        if name in ("goto", "download"):
            refusal = self._foreign_site(tab, args.get("url", ""))
            if refusal:
                self.refusals += 1
                return refusal
        if name in NAVIGATING and tab.site_id in self.signed_in:
            before = tab.page.url
            had_password = await _has_password_field(tab)
            result = await self._dispatch(name, args, tab)
            if not had_password and await _shows_sign_in(tab):
                return await self._sign_in_again(tab, before)
            return result
        return await self._dispatch(name, args, tab)

    async def _sign_in_again(self, tab: Tab, before: str) -> str:
        """The session expired and the site sent the agent back to its sign-in form: sign in again and return to the
        page the agent was on, so it can repeat its last action."""
        self.relogins += 1
        self.log(f"[{self.name}] session expired on {tab.site_id}; signing in again")
        await self._login(tab)
        if before and not _looks_like_sign_in_url(before) and tab.page.url != before:
            await tab.goto(before)
        return ("(Your session had expired: you were signed in again automatically and are back on the page before "
                "your last action. Repeat that action if it did not take effect.)\n" + await tab.observe())

    async def _dispatch(self, name: str, args: dict, tab):
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
        if name == "read_pages":
            return await tab.read_pages([str(u) for u in args["urls"]])
        if name == "collect_pages":
            return await tab.collect_pages(str(args["element"]), int(args.get("max_pages") or 10))
        if name == "read_more":
            return tab.read_more(int(args.get("offset") or 0))
        if name == "download":
            return await tab.fetch_file(args["url"])
        if name == "read_file":
            return tab.read_file(args["name"], int(args.get("offset") or 0))
        if name == "login":
            result = await self._login(tab)
            if not await _shows_sign_in(tab):
                self.signed_in.add(tab.site_id)
            return result
        if name == "note":
            page = self.last_tab.page
            try:
                title = await page.title()
            except Exception:  # noqa: BLE001
                title = ""
            where = f"{self.last_tab.site_id} · page \"{title}\" · {page.url}"
            parts = [f"{key}: {str(args[key]).strip()}" for key in SOURCE_FIELDS if str(args.get(key) or "").strip()]
            if str(args.get("source") or "").strip():
                parts.append(str(args["source"]).strip())
            source = "; ".join(parts)
            self.notes.append(f"{args.get('facts', '').strip()}\n  source: {source or '(not given)'}\n  read on: {where}")
            return "Saved."
        raise ValueError(f"Unknown tool {name}")

    async def run(self) -> str:
        sites = "\n".join(f"- {site_id}: {tab.page.url if tab.page.url != 'about:blank' else ''} (login with the login tool)"
                          for site_id, tab in self.tabs.items())
        messages = [
            {"role": "system", "content": SYSTEM + (RECORDING if self.record else "") + (BOARD if self.board else "")},
            {"role": "user", "content": f"{self.context}\n\n# Your websites\n{sites}\n\n# Your task\n{self.task}"},
        ]
        if self.board is not None:
            self.seen = self.board.version
            messages[1]["content"] += "\n\n# The board now\n" + self.board.news(self.name, 0)
        # open each site so the model starts from a real page view
        for tab in self.tabs.values():
            try:
                view = await asyncio.wait_for(tab.observe(), timeout=ACTION_SECONDS)
            except asyncio.TimeoutError:
                view = f"[{tab.site_id}] (the page did not respond in time; use look or goto)"
            messages[1]["content"] += "\n\n# Current page\n" + view
        tools = tool_schemas(self.multi)
        if self.record:
            tools = [t for t in tools if t["function"]["name"] != "note"] + [{"type": "function", "function": {
                "name": "record", "description": "Save facts you read as typed records, at once; returns what the "
                "facts hold now, or why the update was refused.",
                "parameters": {"type": "object", "properties": {"facts": self.record_schema or {"type": "object"}},
                               "required": ["facts"]}}}]
        if self.board is not None:  # the agent stays for the whole run: no finish, a wait instead
            tools = [t for t in tools if t["function"]["name"] != "finish"] + [{"type": "function", "function": {
                "name": "wait", "description": "Wait until the shared board changes (at most a minute) and see what "
                "changed; for when nothing on your website is left for now.",
                "parameters": {"type": "object", "properties": {}, "required": []}}}]
        save = "record" if self.record else "note"
        reserve = 5 if self.record else WRAP_UP_RESERVE  # nothing to write at the end: what is recorded is saved
        self.reserve = reserve
        warned_time = warned_steps = False
        since_note = 0
        for step in range(self.max_steps):
            remaining = self.deadline - time.time()
            if remaining < reserve:  # leave the wrap-up its time inside the deadline
                break
            if remaining < 90 and not warned_time:
                warned_time = True
                messages.append({"role": "user", "content": f"Time is almost up: save unsaved facts with {save}, "
                                                            "then call finish with what you have."})
            if self.max_steps - step <= 3 and not warned_steps:
                warned_steps = True
                messages.append({"role": "user", "content": f"Only {self.max_steps - step} steps left: save unsaved "
                                                            f"facts with {save}, then call finish."})
            if self.board is not None and self.board.version > self.seen:
                messages.append({"role": "user", "content": "Board update:\n" + self.board.news(self.name, self.seen)})
                self.seen = self.board.version
            if since_note >= NOTE_EVERY:
                since_note = 0
                messages.append({"role": "user", "content": f"You have taken {NOTE_EVERY} actions without saving. If "
                                 f"the pages you read held facts the task needs, save them with {save} now, with "
                                 "source."})
            message = await self._ask(messages, tools)
            if message is None:
                break
            calls = message.tool_calls or []
            entry = {"role": "assistant", "content": message.content or ""}
            if calls:
                entry["tool_calls"] = [{"id": c.id, "type": "function",
                                        "function": {"name": c.function.name, "arguments": c.function.arguments}} for c in calls]
            messages.append(entry)
            if not calls:
                messages.append({"role": "user", "content": "Use a tool: continue the task, or call finish with your report."})
                continue
            # finish last: notes and actions sent in the same turn must not be lost
            for call in sorted(calls, key=lambda c: c.function.name == "finish"):
                try:
                    args = json.loads(call.function.arguments or "{}")
                except ValueError:
                    args = {}
                if call.function.name == "finish":
                    report = args.get("report", "")
                    self.finished = True
                    self.log(f"[{self.name}] finished after {step + 1} steps")
                    return self._report(report)
                self.log(f"[{self.name}] {call.function.name} {json.dumps(args, ensure_ascii=False)[:200]}")
                limit = min(ACTION_SECONDS, max(5.0, self.deadline - time.time()))
                try:  # a browser action that hangs (a stuck page, a reload that never ends) must not hold the agent
                    result = await asyncio.wait_for(self._run_tool(call.function.name, args), timeout=limit)
                except asyncio.TimeoutError:
                    self.log(f"[{self.name}] {call.function.name} did not finish within {limit:.0f} s")
                    result = (f"Error: the action did not finish within {limit:.0f} s; the page may be stuck. Use "
                              "look to see the page, or goto to load it again.")
                except Exception as error:  # noqa: BLE001
                    result = f"Error: {str(error).splitlines()[0][:400]}"
                messages.append({"role": "tool", "tool_call_id": call.id, "content": result})
                fresh = await self._after_action(call.function.name, args, result)
                if fresh:
                    messages.append({"role": "user", "content": fresh})
                if call.function.name in ("note", "record", "wait"):
                    since_note = 0
                    self.unnoted = []
                else:
                    if call.function.name != "login" and not result.startswith(("Error", "Refused")):
                        self.unnoted = [*self.unnoted, f"{call.function.name} -> {result[:UNNOTED_CHARS]}"][-UNNOTED_KEPT:]
                    since_note += 1
                    url = self.last_tab.page.url
                    if url not in self.visited:
                        self.visited.append(url)
            self._forget_old_views(messages)
        if self.record:
            self.log(f"[{self.name}] stopped without finish; its records are saved")
            return self._report("(stopped: out of steps or time; everything recorded is saved)")
        self.log(f"[{self.name}] stopped without finish; writing the report from its notes")
        return self._report(await self._wrap_up())

    async def _after_action(self, name: str, args: dict, result: str) -> str:
        """Counts the page loads in a row a website did not answer; after HUNG_BEFORE_REOPEN of them it gets a fresh
        session, in its own browser first, then in another of the run's browsers. Returns what to tell the model."""
        if name not in NAVIGATING or name == "look":
            return ""
        tab = self.last_tab
        hung = (result.startswith("Error: the action did not finish") or "Navigation error" in result[:600]
                or "\n(still loading " in result[:800])
        self.hung[tab.site_id] = self.hung.get(tab.site_id, 0) + 1 if hung else 0
        if self.hung[tab.site_id] < HUNG_BEFORE_REOPEN or self.reopened.get(tab.site_id, 0) >= REOPENS:
            return ""
        self.hung[tab.site_id] = 0
        self.reopened[tab.site_id] = self.reopened.get(tab.site_id, 0) + 1
        own = tab.page.context.browser
        for browser in [own, *(b for b in self.browsers if b is not own)]:
            try:
                view = await asyncio.wait_for(tab.reopen(browser), timeout=ACTION_SECONDS)
            except Exception as error:  # noqa: BLE001 - try the next browser
                self.log(f"[{self.name}] {tab.site_id} did not answer in a fresh session either: "
                         f"{str(error).splitlines()[0][:120]}")
                continue
            self.signed_in.discard(tab.site_id)
            self.log(f"[{self.name}] {tab.site_id} did not answer; opened again in a fresh session")
            return ("Your website did not answer several times; it is open again in a fresh session. Sign in again "
                    "if it asks, then go on.\n" + view)
        return "Your website still does not answer, not even in a fresh session. Try again later; meanwhile wait."

    async def _ask(self, messages: list[dict], tools: list[dict]):
        """One model step. A call can stall for minutes (a full queue, a stuck request); a fresh attempt usually
        returns quickly, so each attempt is capped and retried while the deadline allows. None: no answer in time."""
        cap = QUICK_CALL_SECONDS if getattr(self.model, "thinking", None) is False else CALL_SECONDS
        for attempt in range(1, CALL_ATTEMPTS + 1):
            budget = self.deadline - getattr(self, "reserve", WRAP_UP_RESERVE) - time.time()
            if budget < 10:
                return None
            limit = min(cap, budget)
            try:
                return await asyncio.wait_for(self.model.chat(messages, tools, temperature=0.2), timeout=limit)
            except asyncio.TimeoutError:
                self.stalls += 1
                self.log(f"[{self.name}] model call stalled for {limit:.0f} s (attempt {attempt} of {CALL_ATTEMPTS})")
        return None

    async def _wrap_up(self) -> str:
        """The agent ran out of steps or time: one call without tools turns its notes into the usual report, so the
        lead model learns what was found and, as important, what was not checked."""
        notes = "\n".join(f"- {n}" for n in self.notes) or "(none)"
        pages = "\n".join(f"- {u}" for u in self.visited[-40:]) or "(none)"
        unnoted = "\n\n".join(self.unnoted) or "(none)"
        prompt = (f"# Task\n{self.task}\n\n# Notes you saved\n{notes}\n\n# Pages you visited\n{pages}\n\n"
                  f"# What your last actions showed, not noted yet\n{unnoted}\n\n"
                  "You ran out of steps or time before finishing. Write your final report from the notes and the "
                  "not-yet-noted results above only, in four parts. FOUND: each fact the task asked for, one line "
                  "each; for a fact from the notes give only what it is and its document (the notes are appended to "
                  "this report word for word), for a fact from the not-yet-noted results write it out in full as "
                  "`value (unit) — source`. SUPERSEDED / LOOK-ALIKE: records that do not apply, with why. CHECKED, "
                  "NOT PRESENT: only what was searched completely and does not exist, with the evidence. NOT "
                  "CHECKED: everything else the task asked for, including newer versions, other folders, further "
                  "pages and unopened results, so nobody mistakes it for absent. Do not add facts that are not "
                  "above. One short line per fact; no introduction.")
        timeout = min(WRAP_UP_SECONDS, max(30.0, self.deadline - time.time()))
        try:
            message = await asyncio.wait_for(
                self.model.chat([{"role": "user", "content": prompt}], temperature=0.2, max_tokens=WRAP_UP_TOKENS,
                                **_no_thinking(self.model)),
                timeout=timeout)
            if message.content:
                return "(written from notes after running out of steps or time)\n" + message.content
        except Exception as error:  # noqa: BLE001
            self.log(f"[{self.name}] wrap-up failed: {type(error).__name__} {error}")
        return ("(stopped before finishing: out of steps or time; no report could be written, see the notes)\n"
                "NOT FOUND: nothing beyond the notes below was verified. Pages visited:\n" + pages)

    def _foreign_site(self, tab: Tab, url: str) -> str:
        """A refusal when the URL belongs to another website of this run that this agent was not given."""
        from urllib.parse import urljoin
        target = origin(urljoin(tab.page.url, url))
        mine = {t.home for t in self.tabs.values()} | {origin(t.page.url) for t in self.tabs.values()}
        if not target or target in mine:
            return ""
        for site_id, home in Tab.homes.items():
            if home == target and site_id not in self.tabs:
                return (f"Refused: {url} belongs to the website {site_id!r}, which another assistant handles. Stay on "
                        f"{', '.join(self.tabs)}; list what you need from {site_id!r} under NOT FOUND in your report.")
        return ""

    def _report(self, report: str) -> str:
        """The report as the lead gets it: the agent's own words, then every note. An agent that did not finish
        (out of steps or time, or cut off) also passes on what its last actions showed and it had not noted yet."""
        notes = "\n".join(f"- {n}" for n in self.notes)
        text = f"REPORT:\n{report}\n\nNOTES TAKEN WHILE BROWSING:\n{notes or '(none)'}"
        if self.unnoted and not self.finished:
            text += ("\n\nLAST PAGES READ, NOT NOTED YET (verbatim, possibly cut; check the source before using a "
                     "value):\n" + "\n\n".join(self.unnoted))
        return text

    @staticmethod
    def _forget_old_views(messages: list[dict], keep: int = 2):
        views = [i for i, m in enumerate(messages) if m["role"] == "tool" and len(m["content"]) > 1500]
        for i in views[:-keep]:
            first = messages[i]["content"].splitlines()[0][:200]
            messages[i]["content"] = f"(older page view removed to save space; it began: {first})"
        for i in views[-keep:-1]:  # the previous view is kept short; only the newest one stays whole
            if len(messages[i]["content"]) > PREVIOUS_VIEW_CHARS and not messages[i]["content"].endswith(SHORTENED):
                messages[i]["content"] = messages[i]["content"][:PREVIOUS_VIEW_CHARS] + SHORTENED
