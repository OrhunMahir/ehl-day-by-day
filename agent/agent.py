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
1. Read the brief and its rules closely; they define the one correct answer. Plan which facts you need.
2. Start one narrow investigation for EVERY website in the first tool-call batch; none may be left idle.
   Send assistants with `investigate` — several in one turn run in parallel, also up to
   {tabs} on the same website, each in a browser tab of its own. Split a large job on one website into several
   investigations (e.g. one catalogue group each) instead of one long one. Give each a precise
   task: what to find, which exact reference, which details (dates, units, status, validity, senders), and to report
   verbatim. Give one narrow job per investigation, not a survey of everything. Ask for an early report as soon as
   the facts needed by another site have been read; collect additional pricing details in follow-ups.
3. Investigations run in the background; the tool returns a job identifier, not a finished report. In the first
   investigations (about {first_minutes} minutes each, including signing in) give every assistant one narrow job,
   the one the other sites depend on. Typical first jobs: the
   tender's positions with their requirement identifiers and all clarifications; the current specification
   revision with every requirement's attributes and catalogue group; the messages that name this exact tender
   reference; in the ERP only how materials are searched by catalogue group and which pages a material has.
   Follow-ups (about {round_minutes} minutes each) are for what depends on another site's answer. Do not wait for
   all first jobs: when a current specification group is known, choose its ERP investigation immediately, reading
   every result of that selected search and each candidate's
   attributes, status, stock, lead times and price conditions; amendments applied to the right revision.
4. The facts of every report are recorded for you and the bid is priced from them in code. As jobs finish you get
   the state of the bid: the priced answer, `issues` (what is missing or ambiguous), `needed` (what to find or
   verify next, and on which site), `incomplete` and `omitted` positions. Do not price anything yourself. Use
   `add_facts` only to correct a record, or to add records of a report the state names as not recorded. The state
   lists running jobs: do not duplicate work already in progress. When recovering an unrecorded report with
   add_facts, acknowledge only that report's identifier in recorded_reports after copying its facts. Use
   wait_for_updates when those jobs are doing
   all the work you can currently request. Wait for pending fact recordings before selecting follow-up jobs, so
   they use the newly recorded revision, amendments and keys. New facts refresh your decision during another job.
5. Work through the `needed` entries of kind "find" in the order given: each names the site by its role, what to
   read, the rule and the positions; turn them into investigation tasks. Entries of kind "verify" are checks, not
   blockers. If the answer is a no bid, have the values named after "closest:" in `issues` re-read once.
6. Call `submit_bid` when `complete` is true and all research and recording jobs have finished, or the time is up.
   It submits the last priced answer; you only add
   hints for filling the form.

Filling the facts JSON:
- Copy values exactly as reported; a fact you never saw stays out. Do not invent values.
- Numbers as JSON numbers. Masses in kg (625 g is 0.625).
- Give every fact its `source`: site, page or document, date, and the revision of a versioned document. For a
  value from a message also give the sender's address in `source` and the exact tender reference the message
  names in the fact's `reference`.
- `searched_groups` lists a catalogue group only when the ERP was searched by that group and every result of
  the search was read; then also list a search that found nothing. A group of which you only happen to know some
  materials is not searched: leave the group out, but retain the individual candidate facts actually read.
- Keys that tie a position to a rule (condition group, packaging, delivery, setup pool, project bonus) go into
  `assignments`, wherever they were read.
- Set a setup pool's `allocation` from the wording of the message that defines the pool.

`calculate` is only for checks of your own; the numbers of the bid come from the engine.

The clock: about {minutes} minutes to investigate. Then the answer priced from the facts you added is submitted;
entering and signing the bid takes the rest of the time."""

# Appended to every investigation task, so each reported value can be turned into a fact with its source.
PROVENANCE = ("For every value you report, say where it is from: the website, the page or document, its date, the "
              "sender's address if it is a message, and the revision if the document has revisions.")

SCORED_RUN_SECONDS = 20 * 60
# How the working time is spent, as shares of it. Entering and signing the bid gets everything after SUBMIT_SHARE.
COLLECT_SHARE = 0.65  # upper bound; signing and final recording also limit research
SUBMIT_SHARE = 0.65
# One investigation may take this long, so that a round of follow-ups fits before the investigations end.
INVESTIGATION_SECONDS = 240
FIRST_ROUND_SECONDS = 240  # as long as later rounds: signing in and getting past a site's gates takes a while
# How many investigations may work one website at the same time, each in a tab with a session of its own.
TABS_PER_SITE = 2  # group searches can overlap without flooding the hosted model queue
# How much each role's model thinks before it answers; None leaves the provider's default.
LEAD_REASONING: str | None = "low"
RECORDER_REASONING: str | None = "medium"  # the calls that turn a report into facts
WORKER_REASONING: str | None = "xhigh"  # browsing sub-agents and the submission agent
# A sub-agent checks the clock between its steps; one step that hangs is cut off this long after its time is up.
INVESTIGATION_GRACE_SECONDS = 30
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
RECORD_SECONDS = 150  # one report's facts must be recorded within this
RECORD_CALL_SECONDS = 75  # a recording call that has not answered by then is dropped and asked again
RECORD_WAIT_SECONDS = 20  # how long the lead's next turn waits for reports that are still being recorded
SUBMISSION_RESERVE_SECONDS = 420  # recording never eats into this much time for entering and signing the bid
LEAD_CALL_SECONDS = 90
MIN_INVESTIGATION_SECONDS = 60
TAB_OPEN_SECONDS = 20

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
        # AGENT_RUN_SECONDS asks for a longer run where the platform allows one, to see what the agent reaches when
        # time is not the limit; a scored run never gets more than the platform's own time.
        planned = float(os.environ.get("AGENT_RUN_SECONDS") or SCORED_RUN_SECONDS)
        left = max(0.0, min(contract.seconds_left(task), planned - (now - started)))
        self.deadline = now + left
        self.signing_reserve = min(SUBMISSION_RESERVE_SECONDS, left * 0.55)
        self.submit_from = min(now + left * SUBMIT_SHARE, self.deadline - self.signing_reserve)
        # Leave room for the final browsing grace and the entire recorder budget before signing begins.
        recording_buffer = min(RECORD_SECONDS + INVESTIGATION_GRACE_SECONDS, (self.submit_from - now) * 0.25)
        self.collect_until = max(now, self.submit_from - recording_buffer)
        self.urls = {a["siteId"]: a["url"] for a in task["access"]}
        self.free_tabs = {site: [tab] for site, tab in tabs.items()}  # tabs no investigation is using right now
        self.tab_count = {site: 1 for site in tabs}
        self.tab_slots = {site: asyncio.Semaphore(TABS_PER_SITE) for site in tabs}
        self.builder = facts.FactsBuilder()  # the facts recorded so far
        self.rounds = 0  # turns of the lead that sent out investigations
        self.records: set[asyncio.Task] = set()  # reports whose facts are being recorded right now
        self.jobs: dict[str, dict] = {}
        self.progress = asyncio.Event()
        self.version = 0
        self.reports: list[tuple[str, str]] = []
        self.report_sources: dict[str, str] = {}
        self.unrecorded: list[str] = []  # reports whose facts could not be recorded
        self.research_closed = False
        self.facts_frozen = False
        self.priced: engine.BidResult | None = None  # what the bid engine made of the latest facts
        self.add_failures = 0
        self.submitted = None  # the last answer a submission was attempted with
        self.submission_entered_sites: set[str] = set()
        self.sent_without_lead = False
        self.accepted = False  # the platform took the bid; nothing more to do
        self.closed = False  # expiry, cancellation or a revoked token is not proof of acceptance

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
                "description": "Start a background browsing job on a model-selected website. Returns its job ID "
                               "and status immediately. Reports and facts arrive in later state updates. "
                               "Do not duplicate a running job. Independent jobs run in parallel.",
                "parameters": {"type": "object", "properties": {
                    "site": {"type": "string", "enum": sites},
                    "task": {"type": "string"}}, "required": ["site", "task"]}}},
            {"type": "function", "function": {
                "name": "wait_for_updates", "description": "Wait briefly for background research or recording "
                "when no independent task can be started. Returns the refreshed bid state.",
                "parameters": {"type": "object", "properties": {
                    "seconds": {"type": "number", "minimum": 1, "maximum": 30}}, "required": []}}},
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
                "parameters": {"type": "object", "properties": {"facts": facts_schema(),
                    "recorded_reports": {"type": "array", "items": {"type": "string"},
                        "description": "Identifiers of failed reports whose facts you have now recovered."}},
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

    def changed(self) -> None:
        self.version += 1
        self.progress.set()

    def pending_work(self) -> bool:
        return any(not job["future"].done() for job in self.jobs.values()) or any(
            not task.done() for task in self.records)

    def start_investigation(self, site: str, task: str) -> str:
        """Schedule only the site and task the model selected; completed facts can unlock another job early."""
        if site not in self.tabs:
            return f"Unknown site {site}; choose one of {', '.join(self.tabs)}"
        if self.research_closed:
            return "Research is closed for submission; continue entering and signing the frozen bid."
        if any(not record.done() for record in self.records):
            return "Facts from completed reports are still being recorded. Use wait_for_updates before choosing " \
                   "a follow-up; the current requirements and amendments may change."
        if self.collect_until - time.time() < MIN_INVESTIGATION_SECONDS:
            return "Too little research time for a new job. Use the current facts and prepare submission."
        for identifier, job in self.jobs.items():
            if not job["future"].done() and job["site"] == site and job["task"].strip() == task.strip():
                return f"Job {identifier} is already running; do not duplicate it."
        identifier = f"job-{len(self.jobs) + 1}"
        future = asyncio.create_task(self.investigate(site, task))
        job = {"site": site, "task": task, "status": "running", "future": future}
        self.jobs[identifier] = job

        def finished(done):
            if done.cancelled():
                job["status"] = "cancelled"
            else:
                error = done.exception()
                job["status"] = "failed" if error else "reported"
                report = f"Research failed: {type(error).__name__}: {error}" if error else done.result()
                if error:
                    self.remember_report(site, report, failed=True)
                    self.log(f"[{identifier}] {report}")
            self.changed()

        future.add_done_callback(finished)
        return f"Started {identifier} on {site}. Its report is being collected in the background."

    async def wait_for_updates(self, seconds: float = 30) -> str:
        if self.pending_work() and not self.progress.is_set():
            timeout = min(max(0.0, float(seconds)), 30, max(0.0, self.submit_from - time.time()))
            if timeout:
                try:
                    await asyncio.wait_for(self.progress.wait(), timeout)
                except TimeoutError:
                    pass
        return self.state()

    async def investigate(self, site: str, task: str) -> str:
        if site not in self.tabs:
            return f"Unknown site {site}; choose one of {', '.join(self.tabs)}"
        async with self.tab_slots[site]:
            if time.time() >= self.collect_until:
                return "No time left to investigate: call submit_bid now with what you have."
            longest = FIRST_ROUND_SECONDS if self.rounds <= 1 else INVESTIGATION_SECONDS
            until = min(self.collect_until, time.time() + longest)
            try:
                tab = await asyncio.wait_for(self.take_tab(site), timeout=max(0.01, until - time.time()))
            except TimeoutError:
                return "No browser tab became available before this job's deadline."
            agent = BrowsingAgent(self.worker_model, {site: tab}, self.credentials, f"{task}\n\n{PROVENANCE}",
                                  self.context(), name=site, deadline=until, log=self.log)
            try:
                report = await asyncio.wait_for(agent.run(), timeout=until - time.time() + INVESTIGATION_GRACE_SECONDS)
            except TimeoutError:
                self.log(f"[{site}] cut off: a step did not return in time")
                report = (agent.report_so_far() if hasattr(agent, "report_so_far") else
                          agent._report("(cut off: out of time; only the notes below were saved)"))
            except asyncio.CancelledError:
                # The final barrier preserves already-read notes, without allowing a late task to edit the bid.
                if self.research_closed and not self.facts_frozen:
                    report = (agent.report_so_far() if hasattr(agent, "report_so_far") else
                              agent._report("(research closed; only saved notes remain)"))
                    self.record_later(site, report)
                raise
            finally:
                self.free_tabs[site].append(tab)
        self.record_later(site, report)
        return report

    def remember_report(self, source: str, report: str, failed: bool = False) -> str:
        identifier = f"report-{len(self.reports) + 1}"
        self.reports.append((identifier, report))
        self.report_sources[identifier] = source
        if failed:
            self.unrecorded.append(identifier)
        return identifier

    def record_later(self, source: str, report: str) -> None:
        if self.facts_frozen:
            return
        identifier = self.remember_report(source, report)
        task = asyncio.ensure_future(self.record(identifier, report))
        self.records.add(task)

        def finished(done):
            self.records.discard(done)
            if done.cancelled() and self.research_closed and not self.facts_frozen:
                self.unrecorded.append(identifier)
            elif not done.cancelled() and done.exception():
                self.unrecorded.append(identifier)
                self.log(f"[{source}] recording failed: {type(done.exception()).__name__}")
            self.changed()

        task.add_done_callback(finished)

    async def record(self, source: str, report: str) -> None:
        """Has a model put one report's facts into the facts, apart from the lead, so the lead does not have to
        write them out itself. A bad update is shown to the model once to correct."""
        tool = next(t for t in self.tools() if t["function"]["name"] == "add_facts")
        site = self.report_sources.get(source, source)
        messages = [{"role": "system", "content": RECORD + "\n" + facts.FILLING_GUIDE},
                    {"role": "user", "content": f"{self.context()}\n\n# Report {source} from {site}\n{report}"}]
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
                return
            messages += [{"role": "assistant", "content": "", "tool_calls": [{"id": call.id, "type": "function", "function": {
                "name": "add_facts", "arguments": call.function.arguments}}]},
                {"role": "tool", "tool_call_id": call.id, "content": outcome}]
        self.unrecorded.append(source)
        self.changed()

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
            parts.append("Not recorded; recover their facts with add_facts and acknowledge recorded_reports: "
                         + ", ".join(self.unrecorded))
            for identifier, report in self.reports:
                if identifier in self.unrecorded:
                    parts.append(f"# Unrecorded {identifier} from {self.report_sources[identifier]}\n{report}")
        for identifier, job in self.jobs.items():
            parts.append(f"{identifier}: {job['status']} on {job['site']}: {job['task'][:700]}")
        parts.append(f"Facts version {self.version}; research remaining {max(0, self.collect_until - time.time()):.0f}s; "
                     f"submission starts in {max(0, self.submit_from - time.time()):.0f}s.")
        return "\n".join(parts)

    async def take_tab(self, site: str) -> Tab:
        """A tab of the website that no other investigation is using. Further tabs get a browser context of their
        own in the same browser, so their sessions and pop-ups do not touch each other; the sub-agent signs in."""
        if self.free_tabs[site]:
            return self.free_tabs[site].pop()
        first = self.tabs[site]
        context = None
        try:
            context = await first.page.context.browser.new_context(
                storage_state=await first.page.context.storage_state())
            page = await context.new_page()
            if hasattr(first, "copy_session_to"):
                await first.copy_session_to(page, self.urls[site])
            await page.goto(self.urls[site], wait_until="domcontentloaded", timeout=TAB_OPEN_SECONDS * 1000)
            tab = Tab(site, page)
        except Exception as error:  # noqa: BLE001 - without a further tab the investigation waits for a free one
            if context is not None and hasattr(context, "close"):
                await context.close()
            self.log(f"[{site}] no further tab ({str(error).splitlines()[0][:120]}); waiting for a free one")
            while not self.free_tabs[site]:
                await asyncio.sleep(0.5)
            return self.free_tabs[site].pop()
        self.tab_count[site] += 1
        self.log(f"[{site}] opened tab {self.tab_count[site]}")
        return tab

    def add(self, update, by_lead: bool = True, recorded_reports: list[str] | None = None) -> str:
        """Adds an update to the facts and prices the bid; a bad update is rejected whole and named."""
        if self.facts_frozen:
            return "The update was not added: the bid is frozen for submission. Continue signing its current values."
        if isinstance(update, dict):
            update = {key: value for key, value in update.items() if key != "authority"}  # the engine's default
        before = json.dumps(self.builder.data, sort_keys=True, default=str)
        try:
            summary = self.builder.add(update)
        except Exception as error:  # noqa: BLE001 - a ValueError names the bad field; the model corrects it
            self.add_failures += by_lead
            return f"The update was not added: {error}. Correct this and call add_facts again; earlier facts are kept."
        if before != json.dumps(self.builder.data, sort_keys=True, default=str):
            self.changed()
        if by_lead and recorded_reports:
            acknowledged = set(recorded_reports)
            self.unrecorded = [identifier for identifier in self.unrecorded if identifier not in acknowledged]
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
        purpose and the engine asks for nothing more about it. Otherwise those positions are left out and the
        others are bid: a candidate that happens to be known is not a completed search."""
        if result.answer.get("decision") != "no_bid":
            return result
        unresolved = [line.position for line in result.lines if line.material is None]
        needed = result.needed()
        still_to_find = {position for need in needed if need.get("kind") == "find"
                         for position in need.get("positions", [])}
        revision = engine.resolve.governing_revision(collected, [])
        global_missing = any(need.get("kind") == "find" and not need.get("positions") for need in needed)
        current_confirmed = sum(r.current is True for r in collected.revisions) == 1

        def conclusive(position: facts.TenderPosition) -> bool:
            requirement = engine.resolve.requirement(collected, revision, position.requirement, []) if revision else None
            return (current_confirmed and not global_missing and requirement is not None
                    and engine.resolve.searched(collected, requirement)
                    and position.position not in still_to_find)

        if all(conclusive(p) for p in collected.positions if p.position in unresolved):
            return result
        kept = [position for position in collected.positions if position.position not in unresolved]
        partial = engine.evaluate(dataclasses.replace(collected, positions=kept))
        note = ("no bid was not taken as final: a catalogue group was not searched completely or facts are missing; "
                "position(s) " + ", ".join(unresolved) + " are left out instead")
        # Keep the original facts and unresolved lines for needed(). The partial computation supplies prices for
        # retained lines: the original no_bid branch returned before calculating those prices.
        priced_lines = {facts.norm_key(line.position): line for line in partial.lines}
        lines = [priced_lines.get(facts.norm_key(line.position), line) for line in result.lines]
        return dataclasses.replace(partial, facts=collected, lines=lines,
                                   issues=[*result.issues, *partial.issues, note],
                                   omitted=list(dict.fromkeys([*partial.omitted, *unresolved])))

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
                + json.dumps(answer, ensure_ascii=False, indent=1) + self.left_out(answer)
                + self.material_details(answer)
                + (f"\n\nHINTS:\n{hints}" if hints else ""))
        if self.submitted is not None:  # an earlier attempt has left the form partly entered
            task += ("\n\nAN EARLIER ATTEMPT has already entered part of this bid. Look at the step the form shows "
                     "now and continue from there; do not go back and do not start over.")
        agent = SubmissionAgent(self.worker_model, sites, self.credentials, task, self.context(),
                                deadline=self.deadline, log=self.log)
        if hasattr(agent, "entered_sites"):
            agent.entered_sites.update(self.submission_entered_sites)
        self.submitted = answer
        try:
            report = await agent.run()
        except Exception as error:  # noqa: BLE001 - usually the run was closed under the agent: the bid is in
            self.log(f"[submit] ended by {type(error).__name__}: {str(error)[:160]}")
            report = "REPORT:\n(the submission agent was interrupted)"
        finally:
            self.submission_entered_sites.update(getattr(agent, "entered_sites", ()))
        if getattr(agent, "accepted", False) and getattr(agent, "receipt", ""):
            self.accepted = self.closed = True
            return report + "\n\nSigned submission receipt confirmed: " + agent.receipt
        await self.run_is_over()
        if self.accepted:
            return report + "\n\nThe platform has closed the run: the bid was accepted."
        if self.closed:
            return report + "\n\nThe run closed without a verified submission receipt. Acceptance is unconfirmed."
        return report + ("\n\nThe run is still open, so no bid has been accepted yet. Call submit_bid again and say "
                         "in the hints what is already entered and what went wrong.")

    def material_details(self, answer: dict) -> str:
        if not self.priced:
            return ""
        included = {facts.norm_key(p["position"]) for p in answer.get("positions", [])}
        details = []
        for line in self.priced.lines:
            material = line.material
            if material and facts.norm_key(line.position) in included:
                details.append({"position": line.position, **{field: getattr(material, field) for field in (
                    "material_number", "bore_mm", "stroke_mm", "pressure_bar", "mounting", "certification")}})
        return "\n\nMATERIAL ATTRIBUTES (for selecting this exact material in the form):\n" + json.dumps(
            details, ensure_ascii=False, default=str) if details else ""

    def left_out(self, answer: dict) -> str:
        if not self.priced or answer is not self.priced.answer or not self.priced.omitted:
            return ""
        return ("\n\nLEFT OUT: position(s) " + ", ".join(self.priced.omitted) + " are not part of this bid. Mark each "
                "of them as left out with the form's own option for that, as the brief describes, and sign the bid "
                "anyway.")

    async def run_is_over(self) -> bool:
        """Whether the platform has ended the run, which it does the moment it accepts a bid."""
        if not self.token:
            return self.accepted
        url = os.environ["CHALLENGE_API_URL"].rstrip("/") + "/v1/runs/current"
        for attempt in range(RUN_CLOSED_CHECKS):
            if attempt:
                await asyncio.sleep(2)
            try:
                status, body = await asyncio.to_thread(contract.http, url, self.token, timeout=3)
            except OSError:
                continue
            if status == 200:
                try:
                    result = json.loads(body)
                except ValueError:
                    continue
                if result.get("status") in ("submitted", "accepted"):
                    self.accepted = self.closed = True
                    return True
                if result.get("status") in ("cancelled", "expired", "failed"):
                    self.closed = True
                    return True
            if status in (401, 404, 410):
                # The token is no longer usable, but this can also mean expiry or cancellation.
                self.closed = True
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
            await self.finish_research()
            if self.builder.data.get("tender_reference"):
                self.price()
        if self.priced and self.submitted is None and not self.sent_without_lead:
            self.facts_frozen = True
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

    async def finish_research(self) -> None:
        """Stop browsing, drain remaining recorders within the signing reserve, and then stop late writers."""
        self.research_closed = True
        jobs = [job["future"] for job in self.jobs.values() if not job["future"].done()]
        for job in jobs:
            job.cancel()
        if jobs:
            await asyncio.gather(*jobs, return_exceptions=True)
        await self.records_done(min(RECORD_SECONDS, max(0, self.deadline - time.time() - self.signing_reserve)))
        records = [task for task in self.records if not task.done()]
        for task in records:
            task.cancel()
        if records:
            await asyncio.gather(*records, return_exceptions=True)

    async def run(self):
        try:
            await self.direct()
        finally:
            pending = [*self.records, *(job["future"] for job in self.jobs.values())]
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

    async def decision(self, messages: list[dict], tools: list[dict], force: dict | None):
        """Discard a planning response if new facts arrived while it was being generated."""
        self.progress.clear()
        version = self.version
        phase_end = self.deadline if force else min(self.submit_from, self.deadline)
        timeout = min(LEAD_CALL_SECONDS, max(0.01, phase_end - time.time()))
        request = asyncio.create_task(asyncio.wait_for(self.ask(messages, tools, force), timeout))
        update = asyncio.create_task(self.progress.wait())
        try:
            await asyncio.wait((request, update), return_when=asyncio.FIRST_COMPLETED)
            if self.version != version and not force:
                self.log("[lead] new research facts arrived; refreshing the pending decision")
                return None
            return await request
        finally:
            for task in (request, update):
                if not task.done():
                    task.cancel()
            await asyncio.gather(request, update, return_exceptions=True)

    async def direct(self):
        minutes = round((self.submit_from - time.time()) / 60)
        messages = [
            {"role": "system", "content": ORCHESTRATOR.format(
                minutes=minutes, first_minutes=round(FIRST_ROUND_SECONDS / 60),
                round_minutes=round(INVESTIGATION_SECONDS / 60), tabs=TABS_PER_SITE) + "\n\n" + facts.FILLING_GUIDE},
            {"role": "user", "content": self.context() + "\n\n# Websites\n" + "\n".join(
                f"- {a['siteId']}: {a['url']}" for a in self.task["access"]) + "\n\nBegin."},
        ]
        tools = self.tools()
        while time.time() < self.deadline and not self.accepted and not self.closed:
            force = await self.time_is_up(messages) if time.time() >= self.submit_from else None
            if self.accepted or self.closed:
                return
            try:
                message = await self.decision(messages, tools, force)
            except TimeoutError:
                self.log("[lead] planning call timed out; asking again with the current state")
                messages.append({"role": "user", "content": "The planning call took too long. Choose the next "
                                 "tool concisely. Current state:\n" + self.state()})
                continue
            except Exception as error:  # noqa: BLE001
                if self.token and getattr(error, "status_code", None) in (401, 403, 404):
                    await self.run_is_over()
                self.log(f"[lead] planning failed ({type(error).__name__}); accepted={self.accepted}, closed={self.closed}")
                if self.closed:
                    return
                messages.append({"role": "user", "content": "The planning call failed. Retry with the current "
                                 "state:\n" + self.state()})
                await asyncio.sleep(min(2, max(0, self.deadline - time.time())))
                continue
            if message is None:
                messages.append({"role": "user", "content": "New facts arrived while you were planning. Use "
                                 "these instead of the older state:\n" + self.state()})
                continue
            calls = message.tool_calls or []
            initial_batch = not self.jobs and any(call.function.name == "investigate" for call in calls)
            if initial_batch:
                selected = set()
                for call in calls:
                    if call.function.name == "investigate":
                        try:
                            selected.add(json.loads(call.function.arguments).get("site"))
                        except (ValueError, AttributeError):
                            pass
                missing = set(self.tabs) - selected
                if missing:
                    messages.append({"role": "user", "content": "Your first research batch left websites idle. "
                                     "Choose one narrow investigation for every website together, including: "
                                     + ", ".join(sorted(missing))})
                    continue
            if message.content:
                self.log(f"[lead] {message.content[:1500]}")
            entry = {"role": "assistant", "content": message.content or ""}
            if calls:
                entry["tool_calls"] = [{"id": c.id, "type": "function",
                                        "function": {"name": c.function.name, "arguments": c.function.arguments}} for c in calls]
            messages.append(entry)
            if not calls:
                messages.append({"role": "user", "content": (
                    "No bid has been accepted yet: call submit_bid again." if self.submitted
                    else "Continue: investigate, add_facts, or submit_bid.")})
                continue

            async def execute(call):
                try:
                    args = json.loads(call.function.arguments or "{}")
                except ValueError:
                    return "Error: arguments were not valid JSON"
                self.log(f"[lead] {call.function.name} {json.dumps(args, ensure_ascii=False)[:600]}")
                try:
                    if call.function.name == "investigate":
                        return self.start_investigation(args["site"], args["task"])
                    if call.function.name == "wait_for_updates":
                        return await self.wait_for_updates(args.get("seconds", 30))
                    if call.function.name == "calculate":
                        return calculate(args["expression"])
                    if call.function.name == "add_facts":
                        return self.add(args.get("facts", args), recorded_reports=args.get("recorded_reports"))
                    if call.function.name == "price_bid":
                        return self.price()
                    if call.function.name == "submit_bid":
                        if time.time() < self.submit_from:
                            covered = {job["site"] for job in self.jobs.values()}
                            if self.priced is None or set(self.tabs) - covered:
                                return "Do not submit an unchecked fallback while research time remains. Start " \
                                       "research on every website and obtain a priced engine answer first."
                        if time.time() < self.submit_from and (self.pending_work() or self.unrecorded):
                            return "Research or recording is still pending. Wait for all reports and recover " \
                                   "unrecorded facts before submitting; the signing reserve still guarantees a cutoff."
                        if self.submitted is None:
                            if time.time() >= self.submit_from:
                                await self.finish_research()
                            if self.builder.data.get("tender_reference"):
                                self.price()
                        if self.priced and not self.priced.complete and time.time() < self.submit_from:
                            return "The bid still lacks facts and research time remains. Continue the needed " \
                                   "research or wait for running jobs; incomplete work is submitted at the signing reserve."
                        answer = self.answer_to_submit(args.get("answer"))
                        if answer is None:
                            return ("There is no priced answer yet: call add_facts first. Pass `answer` yourself "
                                    "only if add_facts cannot be made to return one.")
                        if not self.research_closed:
                            await self.finish_research()
                        self.facts_frozen = True
                        return await self.submit(answer, args.get("hints", ""))
                    return f"Unknown tool {call.function.name}"
                except Exception as error:  # noqa: BLE001
                    return f"Error: {error}"

            self.rounds += any(c.function.name == "investigate" for c in calls)
            # Submission mutates the form; finish any preceding fact updates before using the priced answer.
            results = [None] * len(calls)
            # Start the model's independent investigations before any interleaved wait can begin recording.
            order = ([i for i, call in enumerate(calls) if call.function.name == "investigate"]
                     + [i for i, call in enumerate(calls) if call.function.name != "investigate"]
                     if initial_batch else range(len(calls)))
            for index in order:
                results[index] = await execute(calls[index])
            for call, result in zip(calls, results):
                self.log(f"[lead] <- {call.function.name}: {result[:3000]}")
                messages.append({"role": "tool", "tool_call_id": call.id, "content": result})
            if self.pending_work() and not self.accepted and time.time() < self.submit_from:
                await self.wait_for_updates(30)
            if not self.accepted and not self.closed:
                state = self.state()
                self.log(f"[lead] state of the bid: {state[:3000]}")
                messages.append({"role": "user", "content": "State of the bid after these reports:\n" + state})


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


async def open_site(site: str, url: str, page, deadline: float | None = None) -> Tab:
    """The website's tab at the start of the run. A site that does not answer right now must not end the run:
    its tab is kept, and the sub-agent that works it opens the address again."""
    for attempt in range(1, START_ATTEMPTS + 1):
        remaining_ms = START_TIMEOUT_MS if deadline is None else min(START_TIMEOUT_MS, (deadline - time.time()) * 1000)
        if remaining_ms <= 0:
            break
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=max(1, remaining_ms))
            break
        except Exception as error:  # noqa: BLE001
            print(f"[start] {site} did not open (attempt {attempt}): {str(error).splitlines()[0][:120]}", flush=True)
    tab = Tab(site, page)
    if not tab.home:  # the page never loaded, so the tab does not know its own website yet
        tab.home = origin(url)
        Tab.homes[site] = tab.home
    return tab


async def open_sites(access_pages: list[tuple[dict, object]], deadline: float | None = None) -> dict[str, Tab]:
    """Open independent starting pages together so one slow website cannot leave the other sites unopened."""
    opened = await asyncio.gather(*(open_site(access["siteId"], access["url"], page, deadline)
                                    for access, page in access_pages))
    return {access["siteId"]: tab for (access, _), tab in zip(access_pages, opened)}


async def main() -> int:
    started = time.time()
    task, token = contract.load_run()
    model = Model(reasoning_effort=LEAD_REASONING, max_tokens=8192)
    worker_model = Model(reasoning_effort=WORKER_REASONING)
    recorder_model = Model(reasoning_effort=RECORDER_REASONING, max_tokens=16384)
    print(f"Run {task.get('run_id') or '(local scouting run)'}; model {model.provider}/{model.model}; "
          f"reasoning lead={model.reasoning_effort or 'provider default'}, "
          f"workers={worker_model.reasoning_effort or 'provider default'}, "
          f"recorder={recorder_model.reasoning_effort or 'provider default'}; "
          f"sites: {', '.join(a['siteId'] for a in task['access'])}", flush=True)
    async with async_playwright() as playwright:
        access_pages = []
        planned = float(os.environ.get("AGENT_RUN_SECONDS") or SCORED_RUN_SECONDS)
        runtime_deadline = min(started + planned, time.time() + contract.seconds_left(task))
        for index, access in enumerate(task["access"][:4]):
            if time.time() >= runtime_deadline:
                break
            available = max(len(browsers.cdp_urls()), 1)
            browser, page = await asyncio.wait_for(browsers.connect(playwright, index if index < available else 0),
                                                   timeout=min(TAB_OPEN_SECONDS, runtime_deadline - time.time()))
            if index >= available:  # fewer browsers than sites: a tab of its own in the first browser
                page = await page.context.new_page()
            access_pages.append((access, page))
        tabs = await open_sites(access_pages, runtime_deadline)
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
