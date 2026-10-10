"""Entry point: agent.json runs `uv run --no-sync python agent.py`, in the sandbox and on your machine.

An orchestrating model reads the brief and directs browsing sub-agents, one browser per website, in parallel. It
decides what to investigate where, follows up on gaps, works out the bid with exact arithmetic helpers, and finally
has a sub-agent enter and sign the bid on the submission website.
"""
import asyncio
import dataclasses
import json
import os
import sys
import time

from playwright.async_api import async_playwright

import browsers
import engine
import facts
import run as contract
from browser import Tab, origin
from browsing import BrowsingAgent
from calc import calculate
from llm import Model
from submission import SubmissionAgent

ORCHESTRATOR = """You are the lead sales engineer preparing exactly one tender response. You cannot browse yourself:
you direct browsing assistants, one per website, and they report back. You make the decisions.

Approach:
1. Read the brief and its rules closely; they define the one correct answer.
2. `investigate` starts a browsing assistant on one website and returns at once; it works in the background, up to
   {tabs} on the same website at the same time, each in a browser tab of its own (about {round_minutes} minutes at
   most each). As soon as a report is back you get it, with the state of the bid priced from all facts recorded so
   far and the list of investigations still running. Start every job the moment what it needs is known; never
   wait for other jobs. Do not start a job that is already running.
3. In your first turn start, all at once, one narrow job per source the others depend on:
   - the tender's positions (number, quantity, unit, requirement identifier) and every bidder Q&A answer, all pages;
   - the current specification revision: every requirement with all its attributes, pieces per set, usable travel
     and end-reserve key, and its catalogue group;
   - every message that names this exact tender reference, in every folder: approvals, rule mails with their keys,
     end reserves, rates;
   - in the ERP: signing in and how materials are searched by catalogue group.
4. As soon as a report or the state names catalogue groups, start one ERP job per group (`needed` lists them with
   the attributes to match): search by the group, read every result, and in the same job open the details of each
   result whose attributes match (status, stock, lead times, price conditions with validity and all scales, carton
   size, masses, metal index, setup fee).
5. Then start a job for each `needed` entry of kind "find" as soon as it appears, the ones that name positions
   first. Entries of kind "verify" are checks, not blockers. If the answer is a no bid, have the values named after
   "closest:" in `issues` re-read once.
6. Give each assistant a precise task: what to find, which exact reference, which details (dates, units, status,
   validity, senders), and to report verbatim and to finish as soon as it has them. Keep each task short, at most
   about 80 words plus at most one sentence of what an earlier report taught about that site: the assistants sign
   in, handle cookie walls and gates, page through lists and note sources by themselves.
7. The facts of every report are recorded for you and the bid is priced from them in code; do not price anything
   yourself. The answer is always complete: every position is in it with what is known so far, and every new fact
   improves it. Use `add_facts` only to correct a record, or to add records of a report the state names as not
   recorded. Call `submit_bid` only when `complete` is true; when the time to investigate is over, the latest
   priced answer is submitted for you.

Filling the facts JSON:
- Copy values exactly as reported; a fact you never saw stays out. Do not invent values.
- Numbers as JSON numbers. Masses in kg (625 g is 0.625).
- Give every fact its `source`: site, page or document, date, and the revision of a versioned document. For a
  value from a message also give the sender's address in `source` and the exact tender reference the message
  names in the fact's `reference`.
- `searched_groups` lists a catalogue group only when the ERP was searched by that group and every result of
  the search was read; then also list a search that found nothing. A group of which you only happen to know some
  materials is not searched: leave it out, and leave those materials out too.
- Keys that tie a position to a rule (condition group, packaging, delivery, setup pool, project bonus) go into
  `assignments`, wherever they were read.
- Set a setup pool's `allocation` from the wording of the message that defines the pool.

`calculate` is only for checks of your own; the numbers of the bid come from the engine.

The clock: about {minutes} minutes to investigate. Then the answer priced from the recorded facts is submitted;
entering and signing the bid takes the rest of the time."""

# Appended to every investigation task, so each reported value can be turned into a fact with its source.
PROVENANCE = ("For every value you report, say where it is from: the website, the page or document, its date, the "
              "sender's address if it is a message, and the revision if the document has revisions.")

SCORED_RUN_SECONDS = 20 * 60
# How the working time is spent, as shares of it. Entering and signing the bid gets everything after SUBMIT_SHARE.
# Entering and signing five priced positions took 487 s with the scouting logins (two re-logins and two stalled
# model calls included) and about 165 s in a sandbox run that ended 8 minutes early, so investigations end at
# minute 10 and the submission keeps 576 s.
COLLECT_SHARE = 0.50  # investigations end here
SUBMIT_SHARE = 0.52  # from here on the answer is priced and submitted
# One investigation may take this long. Investigations run in the background and start as soon as what they need
# is known, so there are no rounds to fit: a narrow job finishes long before this.
INVESTIGATION_SECONDS = 240
# How many investigations may work one website at the same time, each in a tab with a session of its own.
TABS_PER_SITE = 4
# How much each role's model thinks before it answers; None leaves the provider's default. Qwen writes about 40
# tokens per second: at "xhigh" one lead answer took 12,000 tokens and 339 s, and browsing steps that thought took
# about 50 s instead of 3 s. The sub-agents (browsing, submission) and the recording of facts are routine steps
# without thinking. The lead thinks neither: at "low" with 4000 tokens its turn after the first round took 118 s and
# was cut off inside its tool calls, three turns in a row, so the second round never started. The engine's `needed`
# list is its plan; its answer is only the tool calls.
LEAD_REASONING: str | None = None
LEAD_THINKING = False
LEAD_MAX_TOKENS = 8000  # six investigation tasks of a few hundred tokens each fit
LEAD_CALL_SECONDS = 180  # a lead call that has not answered by then is dropped and asked again
RECORDER_REASONING: str | None = None  # the calls that turn a report into facts
RECORDER_THINKING = False
RECORD_MAX_TOKENS = 8000  # the facts of one report as JSON
WORKER_REASONING: str | None = None  # browsing sub-agents and the submission agent
WORKER_THINKING = False
# A sub-agent checks the clock between its steps; one step that hangs is cut off this long after its time is up.
INVESTIGATION_GRACE_SECONDS = 30
# An investigation that would get less time than this is not started: after its sign-in and its wrap-up nothing
# is left for the site (in a test run such late ones opened tabs and stopped at once)
MIN_INVESTIGATION_SECONDS = 120
RECORD = """You record facts for a bid engine. Below are the brief of a tender and one report of an assistant who
read a website. Put what the report states into the facts and call `add_facts` once. Take the tender reference,
the scenario date, the sales director's address and our own mailbox domain from the brief.

- Record every record the report states that the facts have a place for, exactly as stated, each with its source.
- Write identifiers character for character as the websites show them: position numbers, requirement identifiers,
  revision names, material numbers, catalogue groups, keys. A tender position's `requirement` and the
  specification requirement it points to must carry the same identifier.
- For every specification requirement record its catalogue group when the report states one.
- Also record what the report lists as superseded, withdrawn, a look-alike or dated later, with its own source:
  the engine picks the record that counts.
- A catalogue group belongs in `searched_groups` only when the report says the ERP was searched by that group and
  every result was read, also when nothing was found. Leave it out when the report says it was not checked or not
  finished.
- Do not record guesses, and nothing the report says was not checked.
- If the report states nothing that fits, call `add_facts` with an empty object.
"""
RECORD_SECONDS = 240  # one report's facts must be recorded within this
# A recording call that has not answered by then is dropped and asked again. Without thinking the recorder writes
# about 40 tokens per second: a report with many records needs 3000-4000 tokens, which 75 s cut off every time.
RECORD_CALL_SECONDS = 120
RECORD_WAIT_SECONDS = 25  # a report that is back waits this long for its facts before the lead is told
SUBMISSION_RESERVE_SECONDS = 540  # recording never eats into this much time for entering and signing the bid

FORCE_SUBMIT = {"type": "function", "function": {"name": "submit_bid"}}
FORCE_ADD = {"type": "function", "function": {"name": "add_facts"}}
ADD_ATTEMPTS = 2  # forced add_facts calls that may fail before the lead has to hand in an answer of its own
RUN_CLOSED_CHECKS = 3  # the platform closes a run a moment after it accepts the bid; ask this often, 2 s apart


class Orchestrator:
    def __init__(self, task: dict, token: str | None, tabs: dict[str, Tab], model: Model, started: float,
                 worker_model: Model | None = None, recorder_model: Model | None = None):
        self.task, self.token, self.tabs, self.model = task, token, tabs, model
        self.worker_model = worker_model or model  # the model of the sub-agents that work the websites
        self.recorder_model = recorder_model or model  # the model that turns reports into facts
        self.credentials = {a["siteId"]: a.get("credentials", {}) for a in task["access"]}
        self.started = started
        now = time.time()
        # Every run works with the length of a scored run: a test run expires much later, a local run never.
        left = min(contract.seconds_left(task), SCORED_RUN_SECONDS - (now - started))
        self.collect_until = now + left * COLLECT_SHARE
        self.submit_from = now + left * SUBMIT_SHARE
        self.deadline = now + left
        self.urls = {a["siteId"]: a["url"] for a in task["access"]}
        self.free_tabs = {site: [tab] for site, tab in tabs.items()}  # tabs no investigation is using right now
        self.tab_count = {site: 1 for site in tabs}
        self.tab_slots = {site: asyncio.Semaphore(TABS_PER_SITE) for site in tabs}
        self.builder = facts.FactsBuilder()  # the facts recorded so far
        self.jobs: dict[asyncio.Task, tuple[int, str, str]] = {}  # investigations running: number, site, task
        self.job_count = 0
        self.news: list[str] = []  # reports and recordings the lead has not been told about yet
        self.news_arrived = asyncio.Event()
        self.records: set[asyncio.Task] = set()  # reports whose facts are being recorded right now
        self.unrecorded: list[str] = []  # reports whose facts could not be recorded
        self.priced: engine.BidResult | None = None  # what the bid engine made of the latest facts
        self.add_failures = 0
        self.submitted = None  # the last answer a submission was attempted with
        self.sent_without_lead = False
        self.accepted = False  # the platform took the bid; nothing more to do

    def log(self, text: str):
        print(f"[{time.time() - self.started:6.1f}s] {text}", flush=True)

    def context(self) -> str:
        return (f"# The brief (rules apply exactly)\n{self.task['brief']}\n\n"
                f"# Answer schema\n{json.dumps(self.task['answer_schema'], ensure_ascii=False)}")

    def tools(self) -> list[dict]:
        sites = list(self.tabs)
        return [
            {"type": "function", "function": {
                "name": "investigate",
                "description": "Send a browsing assistant to one website with a precise task; returns its report. "
                               "Several investigations of the same website in one turn run side by side.",
                "parameters": {"type": "object", "properties": {
                    "site": {"type": "string", "enum": sites},
                    "task": {"type": "string"}}, "required": ["site", "task"]}}},
            {"type": "function", "function": {
                "name": "calculate",
                "description": "Exact decimal arithmetic. Python-like expressions with + - * / ( ), and functions "
                               "round_half_up(x, places), ceil(x), floor(x), min, max, sum([..]). "
                               "Several named lines allowed: 'a = 2*3\\nb = a/7\\nround_half_up(b, 2)'. Returns every value.",
                "parameters": {"type": "object", "properties": {"expression": {"type": "string"}},
                               "required": ["expression"]}}},
            {"type": "function", "function": {
                "name": "add_facts",
                "description": "Add facts read from the reports and price the bid from all facts so far. Returns "
                               "the priced answer, `issues`, `needed` (what to find or verify next), `incomplete` "
                               "and `omitted` positions, and `complete`. Pass only new or changed records.",
                "parameters": {"type": "object", "properties": {"facts": facts_schema()},
                               "required": ["facts"]}}},
            {"type": "function", "function": {
                "name": "price_bid",
                "description": "Price the bid again from the facts added so far, without adding any.",
                "parameters": {"type": "object", "properties": {}, "required": []}}},
            {"type": "function", "function": {
                "name": "submit_bid",
                "description": "Have an assistant enter and sign the last priced answer on the submission "
                               "website. Give any form hints you learned. Pass `answer` only if add_facts "
                               "could not be made to return one.",
                "parameters": {"type": "object", "properties": {
                    "hints": {"type": "string"},
                    "answer": {"type": "object"}}, "required": []}}},
        ]

    async def investigate(self, site: str, task: str) -> str:
        if site not in self.tabs:
            return f"Unknown site {site}; choose one of {', '.join(self.tabs)}"
        async with self.tab_slots[site]:
            if time.time() >= self.collect_until:
                return "No time left to investigate: call submit_bid now with what you have."
            until = min(self.collect_until, time.time() + INVESTIGATION_SECONDS)
            if until - time.time() < MIN_INVESTIGATION_SECONDS:
                return "Too little time left for another investigation: work with what the reports gave."
            tab = await self.take_tab(site)
            agent = BrowsingAgent(self.worker_model, {site: tab}, self.credentials, f"{task}\n\n{PROVENANCE}",
                                  self.context(), name=site, deadline=until, log=self.log)
            try:
                report = await asyncio.wait_for(agent.run(), timeout=until - time.time() + INVESTIGATION_GRACE_SECONDS)
            except TimeoutError:
                self.log(f"[{site}] cut off: a step did not return in time")
                report = agent._report("(cut off: out of time; only the notes below were saved)")
            finally:
                self.free_tabs[site].append(tab)
        self.record_later(site, report)
        return report

    def launch(self, site: str, task: str) -> str:
        """Starts an investigation in the background and returns at once; its report becomes news for the lead."""
        if site not in self.tabs:
            return f"Unknown site {site}; choose one of {', '.join(self.tabs)}"
        if self.collect_until - time.time() < MIN_INVESTIGATION_SECONDS:
            return "Too little time left for another investigation: work with what the reports gave."
        self.job_count += 1
        number = self.job_count
        job = asyncio.ensure_future(self._job(number, site, task))
        self.jobs[job] = (number, site, task)
        job.add_done_callback(lambda done: self.jobs.pop(done, None))
        return f"Investigation {number} started on {site}; its report comes to you as soon as it is back."

    async def _job(self, number: int, site: str, task: str) -> None:
        try:
            report = await self.investigate(site, task)
        except Exception as error:  # noqa: BLE001 - the lead hears about it and can start it again
            report = f"(the investigation failed: {type(error).__name__}: {str(error)[:300]})"
        self.tell(f"## Report of investigation {number} ({site}: {task[:160]})\n{report}")

    def tell(self, news: str) -> None:
        self.news.append(news)
        self.news_arrived.set()

    async def wait_for_news(self, until: float) -> None:
        """Waits until a report is back (then a little longer for its facts to be recorded) or a recording is done,
        at most until `until`; returns at once when nothing is running."""
        while not self.news and time.time() < until:
            if not self.jobs and not any(not task.done() for task in self.records):
                return
            self.news_arrived.clear()
            try:
                await asyncio.wait_for(self.news_arrived.wait(), timeout=max(0.1, until - time.time()))
            except TimeoutError:
                return
        if any(news.startswith("## Report") for news in self.news):
            await self.records_done(min(RECORD_WAIT_SECONDS, until - time.time()))

    def news_message(self) -> str:
        reports, self.news = self.news, []
        running = [f"- {number}: {site}: {task[:120]}" for number, site, task in sorted(self.jobs.values())]
        state = self.state()
        self.log(f"[lead] state of the bid: {state[:3000]}")
        return ("\n\n".join(reports) + ("\n\n" if reports else "") + "# State of the bid\n" + state
                + "\n\n# Investigations still running\n" + ("\n".join(running) or "none"))

    def record_later(self, source: str, report: str) -> None:
        task = asyncio.ensure_future(self.record(source, report))
        self.records.add(task)
        task.add_done_callback(self.records.discard)

    async def record(self, source: str, report: str) -> None:
        """Has a model put one report's facts into the facts, apart from the lead, so the lead does not have to
        write them out itself. A bad update is shown to the model once to correct."""
        tool = next(t for t in self.tools() if t["function"]["name"] == "add_facts")
        messages = [{"role": "system", "content": RECORD + "\n" + facts.FILLING_GUIDE},
                    {"role": "user", "content": f"{self.context()}\n\n# Report from {source}\n{report}"}]
        until = min(time.time() + RECORD_SECONDS, self.deadline)
        started = time.time()
        for _ in range(3):
            left = until - time.time()
            if left < 5:
                break
            try:
                message = await asyncio.wait_for(self.ask(messages, [tool], FORCE_ADD, self.recorder_model),
                                                 timeout=min(RECORD_CALL_SECONDS, left))
            except TimeoutError:
                self.log(f"[{source}] a recording call took too long; asking again")
                continue
            except Exception as error:  # noqa: BLE001 - no model: the lead can add the facts itself
                self.log(f"[{source}] facts not recorded: {type(error).__name__}")
                break
            call = next((c for c in message.tool_calls or [] if c.function.name == "add_facts"), None)
            if call is None:
                break
            try:
                arguments = json.loads(call.function.arguments or "{}")
            except ValueError:
                arguments = None
            outcome = self.add(arguments.get("facts", arguments) if isinstance(arguments, dict) else arguments,
                               by_lead=False)
            if not outcome.startswith("The update was not added"):
                self.log(f"[{source}] facts recorded in {time.time() - started:.0f} s: {outcome.splitlines()[0][:300]}")
                self.tell(f"(The facts of the report from {source} are recorded; the state below includes them.)")
                return
            messages += [{"role": "assistant", "content": "", "tool_calls": [{"id": call.id, "type": "function", "function": {
                "name": "add_facts", "arguments": call.function.arguments}}]},
                {"role": "tool", "tool_call_id": call.id, "content": outcome}]
        self.unrecorded.append(source)
        self.tell(f"(The facts of the report from {source} could not be recorded.)")

    async def records_done(self, timeout: float) -> None:
        """Waits, at most `timeout` seconds, for the reports that are still being recorded."""
        pending = {task for task in self.records if not task.done()}
        if pending and timeout > 0:
            await asyncio.wait(pending, timeout=timeout)

    def state(self) -> str:
        """The bid as the recorded facts price it, for the lead's next decision."""
        parts = [self.price() if self.builder.data.get("tender_reference") else "No facts are recorded yet."]
        still = sum(not task.done() for task in self.records)
        if still:
            parts.append(f"{still} report(s) are still being recorded.")
        if self.unrecorded:
            parts.append("Not recorded; add their facts yourself with add_facts: " + ", ".join(self.unrecorded))
            self.unrecorded = []
        return "\n".join(parts)

    async def take_tab(self, site: str) -> Tab:
        """A tab of the website that no other investigation is using. Further tabs get a browser context of their
        own in the same browser, so their sessions and pop-ups do not touch each other; the sub-agent signs in."""
        if self.free_tabs[site]:
            return self.free_tabs[site].pop()
        first = self.tabs[site]
        try:
            context = await first.page.context.browser.new_context()
            page = await context.new_page()
            await page.goto(self.urls[site], wait_until="domcontentloaded", timeout=30000)
            tab = Tab(site, page)
        except Exception as error:  # noqa: BLE001 - without a further tab the investigation waits for a free one
            self.log(f"[{site}] no further tab ({str(error).splitlines()[0][:120]}); waiting for a free one")
            while not self.free_tabs[site]:
                await asyncio.sleep(0.5)
            return self.free_tabs[site].pop()
        self.tab_count[site] += 1
        self.log(f"[{site}] opened tab {self.tab_count[site]}")
        return tab

    def add(self, update, by_lead: bool = True) -> str:
        """Adds an update to the facts and prices the bid; a bad update is rejected whole and named."""
        if isinstance(update, dict):
            update = {key: value for key, value in update.items() if key != "authority"}  # the engine's default
        try:
            summary = self.builder.add(update)
        except Exception as error:  # noqa: BLE001 - a ValueError names the bad field; the model corrects it
            self.add_failures += by_lead
            return f"The update was not added: {error}. Correct this and call add_facts again; earlier facts are kept."
        return summary + "\n" + self.price()

    def price(self) -> str:
        """Prices the bid from the facts added so far and remembers the result for the submission."""
        if not self.builder.data.get("tender_reference"):
            return "No facts yet: call add_facts first, starting with tender_reference and scenario_date."
        try:
            collected = self.builder.facts()
            result = self.without_hasty_no_bid(collected, engine.evaluate(collected))
        except Exception as error:  # noqa: BLE001
            return f"The facts could not be priced: {error}."
        self.priced = result
        self.log("[lead] facts " + json.dumps(self.builder.data, ensure_ascii=False, default=str))
        try:
            self.log("[lead] engine working:\n" + result.explain())
        except Exception as error:  # noqa: BLE001 - the explanation is for the log only
            self.log(f"[lead] engine working not available: {error}")
        return json.dumps({"answer": result.answer, "issues": result.issues, "needed": result.needed(),
                           "incomplete": result.incomplete, "omitted": result.omitted,
                           "complete": result.complete}, ensure_ascii=False, default=str)

    def without_hasty_no_bid(self, collected: facts.Facts, result: engine.BidResult) -> engine.BidResult:
        """A "no bid" is final only when every position without a material had its catalogue group searched on
        purpose and the engine asks for nothing more about it. Otherwise every position is bid with what is known
        (the nearest candidate, or a material chosen in the form): a candidate that happens to be known is not a
        completed search."""
        if result.answer.get("decision") != "no_bid":
            return result
        unresolved = [line.position for line in result.lines if line.material is None]
        searched = {facts.norm_key(group) for group in collected.searched_groups}
        still_to_find = {position for need in result.needed() if need.get("kind") == "find"
                         for position in need.get("positions", [])}

        def conclusive(position: facts.TenderPosition) -> bool:
            groups = {facts.norm_key(requirement.catalogue_group)
                      for revision in collected.revisions for requirement in revision.requirements
                      if facts.norm_key(requirement.requirement) == facts.norm_key(position.requirement)}
            return bool(groups) and groups <= searched and position.position not in still_to_find

        if all(conclusive(p) for p in collected.positions if p.position in unresolved):
            return result
        bid = engine.evaluate(collected, allow_no_bid=False)
        note = ("no bid was not taken as final: a catalogue group was not searched completely or facts are missing; "
                "position(s) " + ", ".join(unresolved) + " are bid with what is known instead")
        return dataclasses.replace(bid, issues=[*bid.issues, note])

    def answer_to_submit(self, own: dict | None) -> dict | None:
        """The engine's last answer; the lead's own only when the engine has none that fits the schema."""
        if self.priced and not validate(self.priced.answer, self.task["answer_schema"]):
            return self.priced.answer
        return own if isinstance(own, dict) else None

    async def submit(self, answer: dict, hints: str) -> str:
        problems = validate(answer, self.task["answer_schema"])
        if problems:
            return "The answer does not match the schema: " + "; ".join(problems)
        submission = self.task["submission"]
        if submission.get("kind") == "api":
            if not self.token:
                self.submitted, self.accepted = answer, True
                return "Local run: answer recorded, nothing is submitted."
            status, body = contract.submit_answer(self.task, self.token, answer)
            if status == 202:
                self.submitted, self.accepted = answer, True
            return f"HTTP {status}: {body[:1500]}"
        target = submission.get("siteId")
        sites = {target: self.tabs[target]}
        sites.update({s: t for s, t in self.tabs.items() if s != target})  # e.g. the mailbox for a signing code
        task = ("Enter this bid on the submission website exactly as given and submit it, following the brief's "
                "Submission section step by step (register/express interest if needed, configure every position, "
                "enter numbers in the site's number format, acknowledge documents, get the signing code where the "
                "brief says, review the summary against the answer, confirm). Use each value exactly; do not "
                "recompute. Report the receipt or any validation message verbatim. A bid that is entered but not "
                "signed and confirmed counts for nothing: if you are told that time is almost up, do not stop; sign "
                "and confirm what is entered, then report.\n\nANSWER:\n"
                + json.dumps(answer, ensure_ascii=False, indent=1) + self.left_out(answer) + self.materials(answer)
                + (f"\n\nHINTS:\n{hints}" if hints else ""))
        agent = SubmissionAgent(self.worker_model, sites, self.credentials, task, self.context(),
                                deadline=self.deadline, log=self.log)
        self.submitted = answer
        report = await agent.run()
        self.accepted = await self.run_is_over()
        if self.accepted:
            return report + "\n\nThe platform has closed the run: the bid was accepted."
        return report + ("\n\nThe run is still open, so no bid has been accepted yet. Call submit_bid again and say "
                         "in the hints what is already entered and what went wrong.")

    def materials(self, answer: dict) -> str:
        """How the form gets each position's material. A form may choose it through its attributes (one selection
        after another) rather than by its number: a confirmed material gets its ERP attributes; a position without
        one gets the requirement's attributes, so that the form itself offers the matching catalogue item."""
        if not self.priced or answer is not self.priced.answer:
            return ""
        wanted = {str(p.get("position")) for p in answer.get("positions", [])}
        lines = []
        for line in self.priced.lines:
            if line.position not in wanted:
                continue
            m, req = line.material, line.requirement
            if m is not None and not line.guessed:
                lines.append(f"- position {line.position}: material {m.material_number}: " + _attributes(m))
                continue
            if req is None:
                hint = "material not known; if the form insists on one, leave the position out with its own option"
            else:
                stroke = (f"stroke: usable travel {line.travel} mm plus two end reserves not known yet, so the smallest "
                          f"offered stroke above {line.travel} mm" if line.travel is not None else None)
                hint = ("material not confirmed in the ERP: choose the requirement's attributes, "
                        + _attributes(req, stroke) + ", and take the catalogue item the form then offers (the one with "
                        "that pressure); enter it even if it differs from the material number in the answer")
                if m is not None:
                    hint += f"; if that combination is not offered, choose {m.material_number}: " + _attributes(m)
            lines.append(f"- position {line.position}: {hint}")
        if not lines:
            return ""
        return ("\n\nMATERIALS (if the form selects a material through such attributes, choose these so that the "
                "position's material is offered):\n" + "\n".join(lines))

    def left_out(self, answer: dict) -> str:
        if not self.priced or answer is not self.priced.answer or not self.priced.omitted:
            return ""
        return ("\n\nLEFT OUT: position(s) " + ", ".join(self.priced.omitted) + " are not part of this bid. Mark each "
                "of them as left out with the form's own option for that, as the brief describes, and sign the bid "
                "anyway.")

    async def run_is_over(self) -> bool:
        """Whether the platform has ended the run, which it does the moment it accepts a bid."""
        if not self.token:
            return True  # a local run has nothing to ask; one attempt is the end of it
        url = os.environ["CHALLENGE_API_URL"].rstrip("/") + "/v1/runs/current"
        for attempt in range(RUN_CLOSED_CHECKS):
            if attempt:
                await asyncio.sleep(2)
            try:
                status, _ = await asyncio.to_thread(contract.http, url, self.token)
            except OSError:
                continue
            if status in (401, 403, 404, 409, 410):  # the token no longer reads the run
                return True
        return False

    async def ask(self, messages: list[dict], tools: list[dict], force: dict | None, model: Model | None = None):
        model = model or self.model
        if force:
            try:
                return await model.chat(messages, tools, temperature=0.2, tool_choice=force)
            except Exception as error:  # noqa: BLE001 - a model that refuses a forced tool still gets asked plainly
                self.log(f"[lead] forced {force['function']['name']} call failed ({str(error)[:200]}); asking without it")
        return await model.chat(messages, tools, temperature=0.2)

    async def time_is_up(self, messages: list[dict]) -> dict | None:
        """What happens once the time to investigate is over: the answer priced from the facts is submitted without
        asking the lead; without any facts the lead is made to add them, and after that to submit. Returns the tool
        to force."""
        if self.submitted is None:
            if self.jobs:  # they end at the end of investigating; one more grace period for their reports
                await asyncio.wait(set(self.jobs), timeout=max(0.0, min(
                    INVESTIGATION_GRACE_SECONDS + 5, self.deadline - time.time() - SUBMISSION_RESERVE_SECONDS)))
            await self.records_done(min(RECORD_SECONDS, self.deadline - time.time() - SUBMISSION_RESERVE_SECONDS))
            if self.builder.data.get("tender_reference"):
                self.price()
        if self.priced and self.submitted is None and not self.sent_without_lead:
            self.sent_without_lead = True
            self.log("[lead] time to submit: sending the last priced answer")
            report = await self.submit(self.priced.answer, "")
            self.log(f"[lead] <- submit: {report[:3000]}")
            messages.append({"role": "user", "content": "The time to investigate was over, so the last priced answer "
                             "was submitted for you. The result:\n\n" + report})
        if self.accepted:
            return None
        if self.priced or self.add_failures >= ADD_ATTEMPTS:
            if self.submitted is None:
                messages.append({"role": "user", "content": "Investigation time is over: call submit_bid now" + (
                    "." if self.priced else ", passing your best answer yourself. Leave out positions you could not "
                    "complete.")})
            return FORCE_SUBMIT
        messages.append({"role": "user", "content": "Investigation time is over: call add_facts now with every fact "
                         "you have."})
        return FORCE_ADD

    async def run(self):
        try:
            await self.direct()
        finally:
            for task in [*self.records, *self.jobs]:
                task.cancel()

    async def direct(self):
        minutes = round((self.submit_from - time.time()) / 60)
        messages = [
            {"role": "system", "content": ORCHESTRATOR.format(
                minutes=minutes, round_minutes=round(INVESTIGATION_SECONDS / 60), tabs=TABS_PER_SITE)
                + "\n\n" + facts.FILLING_GUIDE},
            {"role": "user", "content": self.context() + "\n\n# Websites\n" + "\n".join(
                f"- {a['siteId']}: {a['url']}" for a in self.task["access"]) + "\n\nBegin."},
        ]
        tools = self.tools()
        while time.time() < self.deadline and not self.accepted:
            force = await self.time_is_up(messages) if time.time() >= self.submit_from else None
            if self.accepted:
                return
            limit = min(LEAD_CALL_SECONDS, self.deadline - time.time())
            try:
                message = await asyncio.wait_for(self.ask(messages, tools, force), timeout=limit)
            except TimeoutError:
                if self.deadline - time.time() > 5:
                    self.log(f"[lead] a lead call took more than {limit:.0f} s; asking again")
                    continue
                self.log("[lead] out of time")
                return
            except Exception as error:  # noqa: BLE001
                if self.submitted is None:
                    raise
                # after a submission the usual reason is that the run was closed, and its token with it
                self.log(f"[lead] no model after the submission ({type(error).__name__}); the run seems closed")
                return
            calls = message.tool_calls or []
            if message.content:
                self.log(f"[lead] {message.content[:1500]}")
            entry = {"role": "assistant", "content": message.content or ""}
            if calls:
                entry["tool_calls"] = [{"id": c.id, "type": "function",
                                        "function": {"name": c.function.name, "arguments": c.function.arguments}} for c in calls]
            messages.append(entry)
            if not calls:
                if self.submitted:
                    messages.append({"role": "user", "content": "No bid has been accepted yet: call submit_bid again."})
                    continue
                await self.wait_for_news(self.submit_from)
                messages.append({"role": "user", "content": self.news_message() + (
                    "" if self.jobs else "\n\nNothing is running: start the jobs `needed` names, or submit_bid.")})
                continue

            async def execute(call):
                try:
                    args = json.loads(call.function.arguments or "{}")
                except ValueError:
                    return "Error: arguments were not valid JSON"
                self.log(f"[lead] {call.function.name} {json.dumps(args, ensure_ascii=False)[:600]}")
                try:
                    if call.function.name == "investigate":
                        return self.launch(args["site"], args["task"])
                    if call.function.name == "calculate":
                        return calculate(args["expression"])
                    if call.function.name == "add_facts":
                        return self.add(args.get("facts", args))
                    if call.function.name == "price_bid":
                        return self.price()
                    if call.function.name == "submit_bid":
                        if self.submitted is None:
                            await self.records_done(min(RECORD_SECONDS,
                                                        self.deadline - time.time() - SUBMISSION_RESERVE_SECONDS))
                            if self.builder.data.get("tender_reference"):
                                self.price()
                        answer = self.answer_to_submit(args.get("answer"))
                        if answer is None:
                            return ("There is no priced answer yet: call add_facts first. Pass `answer` yourself "
                                    "only if add_facts cannot be made to return one.")
                        return await self.submit(answer, args.get("hints", ""))
                    return f"Unknown tool {call.function.name}"
                except Exception as error:  # noqa: BLE001
                    return f"Error: {error}"

            results = await asyncio.gather(*(execute(c) for c in calls))
            for call, result in zip(calls, results):
                self.log(f"[lead] <- {call.function.name}: {result[:3000]}")
                messages.append({"role": "tool", "tool_call_id": call.id, "content": result})
            if not self.accepted and self.submitted is None and time.time() < self.submit_from:
                await self.wait_for_news(self.submit_from)  # the next report, not the end of a round
                messages.append({"role": "user", "content": self.news_message()})


def _attributes(item, stroke: str | None = None) -> str:
    """bore, stroke, pressure, mounting and certification of a material or requirement, as far as they are known."""
    values = (("bore", f"{item.bore_mm} mm" if item.bore_mm is not None else None),
              ("stroke", f"{item.stroke_mm} mm" if item.stroke_mm is not None else None),
              ("pressure", f"{item.pressure_bar} bar" if item.pressure_bar is not None else None),
              ("mounting", item.mounting), ("certification", item.certification or None))
    parts = [f"{name} {value}" for name, value in values if value]
    if stroke and item.stroke_mm is None:
        parts.insert(1, stroke)
    return ", ".join(parts)


def facts_schema() -> dict:
    """What the lead may add. Which sender may decide what is the engine's own default, not the model's to set."""
    schema = facts.update_schema()
    return {**schema, "properties": {name: rule for name, rule in schema["properties"].items() if name != "authority"}}


def validate(answer: dict, schema: dict) -> list[str]:
    """A small JSON-schema check for the answer's shape: required fields, types, enums, extra fields."""
    problems = []

    def check(value, rule, path):
        kind = rule.get("type")
        types = {"string": str, "number": (int, float), "integer": int, "object": dict, "array": list, "boolean": bool}
        if kind and not isinstance(value, types[kind]) or (kind in ("number", "integer") and isinstance(value, bool)):
            problems.append(f"{path}: expected {kind}")
            return
        if "enum" in rule and value not in rule["enum"]:
            problems.append(f"{path}: must be one of {rule['enum']}")
        if "const" in rule and value != rule["const"]:
            problems.append(f"{path}: must be {rule['const']!r}")
        if kind in ("number", "integer") and "minimum" in rule and value < rule["minimum"]:
            problems.append(f"{path}: below {rule['minimum']}")
        if kind == "object":
            for key in rule.get("required", []):
                if key not in value:
                    problems.append(f"{path}.{key}: required")
            props = rule.get("properties", {})
            for key, item in value.items():
                if key in props:
                    check(item, props[key], f"{path}.{key}")
                elif rule.get("additionalProperties") is False:
                    problems.append(f"{path}.{key}: not allowed")
        if kind == "array":
            for i, item in enumerate(value):
                check(item, rule.get("items", {}), f"{path}[{i}]")

    check(answer, schema, "answer")
    return problems


START_ATTEMPTS = 3
START_TIMEOUT_MS = 20000


async def open_site(site: str, url: str, page) -> Tab:
    """The website's tab at the start of the run. A site that does not answer right now must not end the run:
    its tab is kept, and the sub-agent that works it opens the address again."""
    for attempt in range(1, START_ATTEMPTS + 1):
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=START_TIMEOUT_MS)
            break
        except Exception as error:  # noqa: BLE001
            print(f"[start] {site} did not open (attempt {attempt}): {str(error).splitlines()[0][:120]}", flush=True)
    tab = Tab(site, page)
    if not tab.home:  # the page never loaded, so the tab does not know its own website yet
        tab.home = origin(url)
        Tab.homes[site] = tab.home
    return tab


async def main() -> int:
    started = time.time()
    task, token = contract.load_run()
    model = Model(reasoning_effort=LEAD_REASONING, thinking=LEAD_THINKING, max_tokens=LEAD_MAX_TOKENS)
    worker_model = Model(reasoning_effort=WORKER_REASONING, thinking=WORKER_THINKING)
    recorder_model = Model(reasoning_effort=RECORDER_REASONING, thinking=RECORDER_THINKING, max_tokens=RECORD_MAX_TOKENS)

    def effort(m: Model) -> str:
        return "no thinking" if m.thinking is False else m.reasoning_effort or "provider default"

    print(f"Run {task.get('run_id') or '(local scouting run)'}; model {model.provider}/{model.model}; "
          f"reasoning lead={effort(model)}, workers={effort(worker_model)}, recorder={effort(recorder_model)}; "
          f"sites: {', '.join(a['siteId'] for a in task['access'])}", flush=True)
    async with async_playwright() as playwright:
        tabs = {}
        for index, access in enumerate(task["access"][:4]):
            available = max(len(browsers.cdp_urls()), 1)
            browser, page = await browsers.connect(playwright, index if index < available else 0)
            if index >= available:  # fewer browsers than sites: a tab of its own in the first browser
                page = await page.context.new_page()
            tabs[access["siteId"]] = await open_site(access["siteId"], access["url"], page)
        lead = Orchestrator(task, token, tabs, model, started, worker_model, recorder_model)
        try:  # nothing may outlive the run's time, whatever is still waiting on a page or a model
            await asyncio.wait_for(lead.run(), timeout=lead.deadline - time.time() + 5)
        except TimeoutError:
            lead.log("[lead] stopped at the end of the run's time")
    print(f"Done in {time.time() - started:.0f}s; lead {model.usage()}; workers {worker_model.usage()}; recorder {recorder_model.usage()}; submitted: "
          f"{json.dumps(lead.submitted, ensure_ascii=False) if lead.submitted else 'nothing'}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
