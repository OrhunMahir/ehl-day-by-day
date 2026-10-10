"""Entry point: agent.json runs `uv run --no-sync python agent.py`, in the sandbox and on your machine.

A lean flow with two browsing agents and no lead model in the loop:

1. Setup: one model call reads the brief, names which website plays which role (tender portal, specification, ERP,
   mailbox) and records the brief's own facts (tender reference, scenario date, director, our mail domain).
2. A chain agent works the tender portal, the specification and the ERP in one conversation, in the order the bid
   needs them; a mail agent works the mailbox at the same time. Both record facts the moment they read them. After
   every record the bid engine says what its sites still owe, and the agent goes on with that. An agent that ends
   while facts are still missing is started again with only those.
3. Code prices the facts (rules in `engine/`), and a submission agent enters and signs the bid.

Fair play: the models choose every website, page, search and document. Code only asks the engine what is missing and
passes that list on as text.
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
from browser import Tab
from browsing import BrowsingAgent
from llm import Model
from submission import SubmissionAgent

SCORED_RUN_SECONDS = 20 * 60
SUBMISSION_RESERVE_SECONDS = 420  # the agents stop reading this long before the end: entering and signing the bid
LEAD_REASONING = "low"  # the setup call thinks, briefly
WORKER_CALL_SECONDS = 60  # browsing and submission do not think; a call open this long is asked again
SETUP_CALL_SECONDS = 90
AGENT_STEPS = 80  # steps of one browsing agent before it is started again with what is still missing
IDLE_SECONDS = 15  # an agent with nothing to do looks again after this long, or as soon as a fact arrives
EMPTY_SESSIONS = 2  # sessions in a row that recorded nothing before an agent waits for new facts instead
LAST_SESSION_SECONDS = 60  # no new session starts this close to the end of reading
SHOWN_NEEDS = 10  # missing facts listed after a record

ROLES = ("tender portal", "specification", "ERP", "mailbox")  # as the engine's checklist names them
CHAIN_ROLES = {"tender portal", "specification", "ERP"}
MAIL_ROLES = {"mailbox"}

SETUP = """You prepare one tender response. Read the brief and the list of websites, then call `setup` once:
- roles: for each role, the siteId of the website that plays it. "tender portal": where the tender, its bill of
  quantities (positions) and the bidder Q&A are published. "specification": where the technical specification
  (requirements, revisions, catalogue groups) is published; it may be the tender portal or the customer/supplier
  portal. "ERP": our internal material and price system. "mailbox": our webmail.
- facts: what the brief itself states: tender_reference (character for character), scenario_date (ISO 8601 with
  time and zone if given), director_address (the mail address of the person whose approval counts for discounts, if
  the brief names it), bidder_domain (the domain of our own mail addresses, if the brief shows one). Leave out what
  the brief does not state."""

CHAIN_JOB = """Your job: collect, in this order, what the bid needs from these websites, and record each record the
moment you have read it.
1. The tender: its bill of quantities (every position: number, quantity, unit, requirement) and every answer of the
   bidder Q&A that changes a quantity, withdraws an earlier answer, amends a requirement or ties a position to a key
   (condition group, packaging, delivery, setup pool, project bonus).
2. The technical specification: every revision with its date and which one is marked current, and every requirement
   (bore, stroke or usable travel, pressure, mounting, certification, pieces per set, catalogue group).
3. The ERP: search by each catalogue group of the current requirements. Record every result with what the list
   shows (material number, status, attributes, catalogue group), then open the details of the results that match a
   requirement and record status, stock, lead times, every price condition with all scales, masses and packaging.
   Add a group to `searched_groups` once you have read every result of its search.
Do not wait for one step to be perfect before the next: the engine's list after each record says what is still
missing."""

MAIL_JOB = """Your job: find and record every message in this mailbox that the bid rules of the brief depend on,
for the exact tender reference: the discount approval, and the rule messages (condition rules, packaging tariffs,
delivery commitments, setup pools, project bonuses, FX fixings, metal indices, technical approvals such as end
reserves). Search the inbox and the archive and every folder; record each message the moment you have read it, with
its sender's address, its date and time, and the reference it names. Record older and newer versions and look-alikes
too, each with its own source: the bid engine decides which one counts. The engine's list after each record says
what is still missing; keys that tie positions to rules arrive while the tender is being read, so look again at the
list when you think you are done."""

PROVENANCE = ("Give every recorded item its `source`: the website, the page or document, its date, the sender's "
              "address for a message, and the revision for a versioned document.")


class Lead:
    def __init__(self, task: dict, token: str | None, tabs: dict[str, Tab], model: Model, worker: Model,
                 started: float):
        self.task, self.token, self.tabs, self.model, self.worker = task, token, tabs, model, worker
        self.credentials = {a["siteId"]: a.get("credentials", {}) for a in task["access"]}
        self.started = started
        now = time.time()
        # every run works with the length of a scored run: a test run expires much later, a local run never
        left = min(contract.seconds_left(task), SCORED_RUN_SECONDS - (now - started))
        self.deadline = now + left
        self.read_until = self.deadline - SUBMISSION_RESERVE_SECONDS
        self.builder = facts.FactsBuilder()
        self.changed = asyncio.Event()
        self.complete = False
        self.roles: dict[str, str] = {}
        self.priced: engine.BidResult | None = None
        self.submitted = None
        self.accepted = False

    def log(self, text: str):
        print(f"[{time.time() - self.started:6.1f}s] {text}", flush=True)

    def context(self) -> str:
        return (f"# The brief (rules apply exactly)\n{self.task['brief']}\n\n"
                f"# Answer schema\n{json.dumps(self.task['answer_schema'], ensure_ascii=False)}")

    # ---- setup -------------------------------------------------------------------------------------------------
    async def setup(self) -> None:
        sites = [a["siteId"] for a in self.task["access"]]
        tool = {"type": "function", "function": {
            "name": "setup", "description": "Name the website of each role and record the brief's own facts.",
            "parameters": {"type": "object", "properties": {
                "roles": {"type": "object", "properties": {role: {"type": "string", "enum": sites} for role in ROLES}},
                "facts": {"type": "object", "properties": {
                    "tender_reference": {"type": "string"}, "scenario_date": {"type": "string"},
                    "director_address": {"type": "string"}, "bidder_domain": {"type": "string"}}}},
                "required": ["roles", "facts"]}}}
        messages = [{"role": "system", "content": SETUP},
                    {"role": "user", "content": self.context() + "\n\n# Websites\n" + "\n".join(
                        f"- {a['siteId']}: {a['url']}" for a in self.task["access"])}]
        for attempt in range(2):
            try:
                message = await asyncio.wait_for(
                    self.model.chat(messages, [tool], temperature=0.2,
                                    tool_choice={"type": "function", "function": {"name": "setup"}}),
                    timeout=SETUP_CALL_SECONDS)
                call = next(c for c in message.tool_calls or [] if c.function.name == "setup")
                args = json.loads(call.function.arguments or "{}")
            except Exception as error:  # noqa: BLE001 - asked once more, then the agents work every site
                self.log(f"[lead] setup call failed ({type(error).__name__}), attempt {attempt + 1}")
                continue
            roles = args.get("roles") if isinstance(args.get("roles"), dict) else {}
            self.roles = {role: site for role, site in roles.items() if role in ROLES and site in self.tabs}
            brief = {k: v for k, v in (args.get("facts") or {}).items() if isinstance(v, str) and v.strip()}
            self.log("[lead] setup: roles " + json.dumps(self.roles) + "; brief facts " + ", ".join(sorted(brief)))
            if brief:
                self.log("[lead] " + self.record(brief).splitlines()[0][:300])
            return
        self.log("[lead] setup failed: one agent works every website")

    # ---- facts ---------------------------------------------------------------------------------------------------
    def record(self, update) -> str:
        """Adds one update from an agent. Which sender may decide what is the engine's own default, and nobody
        takes records back here."""
        if not isinstance(update, dict):
            return "The update was not added: it must be a JSON object such as {'materials': [{...}]}."
        update = {k: v for k, v in update.items() if k not in ("authority", "replace_lists")}
        try:
            outcome = self.builder.add(update)
        except ValueError as error:
            return f"The update was not added: {error}. Correct this and record again."
        self.changed.set()
        self.check_complete()
        return outcome

    def evaluate(self) -> engine.BidResult | None:
        data = dict(self.builder.data)
        data.setdefault("tender_reference", "(not recorded yet)")
        data.setdefault("scenario_date", "2100-01-01")
        try:
            return engine.evaluate(facts.from_dict(facts.Facts, data))
        except Exception as error:  # noqa: BLE001 - asked again with the next fact
            self.log(f"[lead] facts could not be evaluated: {str(error)[:200]}")
            return None

    def open_needs(self, roles: set[str]) -> list[dict]:
        result = self.evaluate()
        if result is None:
            return []
        return [n for n in result.needed() if n["site"] in roles
                or (n["site"] == "brief" and roles & MAIL_ROLES)]  # the brief's gaps are read from our own mails

    def check_complete(self) -> None:
        result = self.evaluate()
        if result is not None and self.builder.data.get("tender_reference") and result.complete:
            if not self.complete:
                self.log("[lead] the facts are complete")
            self.complete = True

    def counts(self) -> str:
        data = self.builder.data
        return ", ".join(f"{k} {len(v)}" for k, v in data.items() if isinstance(v, list) and v)

    def recorder(self, name: str, roles: set[str]):
        def record(update) -> str:
            outcome = self.record(update)
            if outcome.startswith("The update was not added"):
                self.log(f"[{name}] record rejected")
                return outcome
            self.log(f"[{name}] recorded; facts: {self.counts()}")
            return outcome.splitlines()[0][:300] + "\n" + self.missing_text(roles)
        return record

    def missing_text(self, roles: set[str]) -> str:
        needs = self.open_needs(roles)
        finds = [n for n in needs if n["kind"] == "find"]
        if not finds:
            return "Nothing your websites owe is missing right now."
        lines = [f"- {n['what']}" + (f" (positions {', '.join(n['positions'])})" if n.get("positions") else "")
                 for n in finds[:SHOWN_NEEDS]]
        more = f"\n(and {len(finds) - SHOWN_NEEDS} more)" if len(finds) > SHOWN_NEEDS else ""
        return "Still missing from your websites:\n" + "\n".join(lines) + more

    # ---- the two agents ----------------------------------------------------------------------------------------
    def split(self) -> list[tuple[str, set[str], list[str], str]]:
        """(name, roles, sites, job) for each agent."""
        mail = [self.roles["mailbox"]] if "mailbox" in self.roles else []
        chain = list(dict.fromkeys(self.roles[r] for r in ("tender portal", "specification", "ERP") if r in self.roles))
        chain = [s for s in chain if s not in mail]
        if not mail or not chain:
            return [("all", CHAIN_ROLES | MAIL_ROLES, list(self.tabs), CHAIN_JOB + "\n\n" + MAIL_JOB)]
        rest = [s for s in self.tabs if s not in chain and s not in mail]  # a site no role named: the chain reads it
        return [("chain", CHAIN_ROLES, chain + rest, CHAIN_JOB), ("mail", MAIL_ROLES, mail, MAIL_JOB)]

    async def work(self, name: str, roles: set[str], sites: list[str], job: str) -> None:
        tabs = {site: self.tabs[site] for site in sites}
        session = empty = 0
        while not self.complete and self.read_until - time.time() > LAST_SESSION_SECONDS:
            needs = self.open_needs(roles)
            finds = [n for n in needs if n["kind"] == "find" or "no_bid" in n["what"]]
            if session and (not finds or empty >= EMPTY_SESSIONS):
                self.changed.clear()
                try:
                    await asyncio.wait_for(self.changed.wait(), timeout=IDLE_SECONDS)
                except TimeoutError:
                    pass
                empty = 0 if self.changed.is_set() else empty
                continue
            session += 1
            if session == 1:
                task = job
            else:
                task = ("Continue where an earlier agent stopped on these websites. Find and record what is still "
                        "missing:\n" + "\n".join(f"- {n['what']}" for n in finds[:SHOWN_NEEDS]))
            task += "\n\n" + self.missing_text(roles) + "\n\n" + PROVENANCE
            agent = BrowsingAgent(self.worker, tabs, self.credentials, task, self.context(),
                                  name=f"{name}#{session}", deadline=self.read_until, max_steps=AGENT_STEPS,
                                  log=self.log, recorder=self.recorder(name, roles),
                                  field_reference=facts.field_reference())
            self.log(f"[{name}] session {session} starts with {len(finds)} missing fact(s)")
            try:
                await asyncio.wait_for(agent.run(), timeout=self.read_until - time.time() + 30)
            except Exception as error:  # noqa: BLE001 - a broken session is started again with what is missing
                self.log(f"[{name}] session {session} ended: {type(error).__name__}")
            empty = empty + 1 if agent.records == 0 else 0
            self.log(f"[{name}] session {session} done: {agent.records} record(s), {agent.stalls} stalled call(s)")

    # ---- the bid -----------------------------------------------------------------------------------------------
    def price(self) -> engine.BidResult | None:
        try:
            collected = self.builder.facts()
            result = self.without_hasty_no_bid(collected, engine.evaluate(collected))
        except Exception as error:  # noqa: BLE001
            self.log(f"[lead] the facts could not be priced: {str(error)[:300]}")
            return None
        self.log("[lead] facts " + json.dumps(self.builder.data, ensure_ascii=False, default=str))
        self.log(f"[lead] priced: decision {result.answer.get('decision')}, {len(result.answer.get('positions', []))} "
                 f"position(s), {len(result.issues)} issue(s), omitted {len(result.omitted)}")
        return result

    def without_hasty_no_bid(self, collected: facts.Facts, result: engine.BidResult) -> engine.BidResult:
        """A "no bid" is final only when the engine has nothing else to report and every position without a
        material had its catalogue group searched on purpose, with the results of that search recorded (a group
        marked as searched without one recorded result is a search whose results were lost, not an empty group).
        While facts are still missing, the positions without a material are left out instead and the others are
        bid."""
        if result.answer.get("decision") != "no_bid":
            return result
        unresolved = [line.position for line in result.lines if line.material is None]
        recorded = {facts.norm_key(m.catalogue_group) for m in collected.materials if m.catalogue_group}
        searched = {facts.norm_key(group) for group in collected.searched_groups} & recorded

        def conclusive(position: facts.TenderPosition) -> bool:
            groups = {facts.norm_key(requirement.catalogue_group)
                      for revision in collected.revisions for requirement in revision.requirements
                      if facts.norm_key(requirement.requirement) == facts.norm_key(position.requirement)} - {""}
            return bool(groups) and groups <= searched

        no_match = [i for i in result.issues if "no active material matches" in i]
        if len(result.issues) == len(no_match) and all(
                conclusive(p) for p in collected.positions if p.position in unresolved):
            return result
        kept = [position for position in collected.positions if position.position not in unresolved]
        partial = engine.evaluate(dataclasses.replace(collected, positions=kept))
        note = ("no bid was not taken as final: facts are missing or a catalogue group was not searched completely; "
                "position(s) " + ", ".join(unresolved) + " are left out instead")
        return dataclasses.replace(partial, issues=[*result.issues, note], omitted=[*partial.omitted, *unresolved])

    async def submit(self, answer: dict, hints: str = "") -> str:
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
                + (f"\n\nHINTS:\n{hints}" if hints else ""))
        agent = SubmissionAgent(self.worker, sites, self.credentials, task, self.context(),
                                deadline=self.deadline, log=self.log)
        self.submitted = answer
        report = await agent.run()
        self.accepted = await self.run_is_over()
        return report

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
        try:
            status, _ = await asyncio.to_thread(contract.http, url, self.token)
        except OSError:
            return False
        return status in (401, 403, 404, 409, 410)

    # ---- the run -----------------------------------------------------------------------------------------------
    async def run(self) -> None:
        await self.setup()
        parts = self.split()
        self.log("[lead] agents: " + "; ".join(f"{n} on {', '.join(s)}" for n, _, s, _ in parts))
        agents = [asyncio.ensure_future(self.work(*part)) for part in parts]
        while not self.complete and time.time() < self.read_until and not all(a.done() for a in agents):
            await asyncio.sleep(2)
        if self.complete:  # nothing is missing: stop reading and submit at once
            for agent in agents:
                agent.cancel()
        await asyncio.gather(*agents, return_exceptions=True)
        self.priced = self.price()
        if self.priced is None:
            self.log("[lead] nothing to submit")
            return
        hints = ""
        while not self.accepted and self.deadline - time.time() > 60:
            self.log("[lead] time to submit: sending the priced answer")
            report = await self.submit(self.priced.answer, hints)
            self.log(f"[lead] <- submit: {report[:1500]}")
            hints = ("An earlier attempt did not end with an accepted bid. Check what is already entered, complete "
                     "it and sign. Its report:\n" + report[:1500])


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


async def main() -> int:
    started = time.time()
    task, token = contract.load_run()
    model = Model(reasoning_effort=LEAD_REASONING)
    worker = Model(thinking=False, timeout=WORKER_CALL_SECONDS)
    print(f"Run {task.get('run_id') or '(local scouting run)'}; model {model.provider}/{model.model}; "
          f"sites: {', '.join(a['siteId'] for a in task['access'])}", flush=True)
    async with async_playwright() as playwright:
        tabs = {}
        for index, access in enumerate(task["access"][:4]):
            available = max(len(browsers.cdp_urls()), 1)
            _, page = await browsers.connect(playwright, index if index < available else 0)
            if index >= available:  # fewer browsers than sites: a tab of its own in the first browser
                page = await page.context.new_page()
            await page.goto(access["url"], wait_until="domcontentloaded")
            tabs[access["siteId"]] = Tab(access["siteId"], page)
        lead = Lead(task, token, tabs, model, worker, started)
        await lead.run()
    print(f"Done in {time.time() - started:.0f}s; lead {model.usage()}; agents {worker.usage()}; submitted: "
          f"{'an answer' if lead.submitted else 'nothing'}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
