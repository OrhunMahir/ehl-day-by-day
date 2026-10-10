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
from browser import Tab
from browsing import BrowsingAgent
from calc import calculate
from llm import Model
from submission import SubmissionAgent

ORCHESTRATOR = """You are the lead sales engineer preparing exactly one tender response. You cannot browse yourself:
you direct browsing assistants, one per website, and they report back. You make the decisions.

Approach:
1. Read the brief and its rules closely; they define the one correct answer. Plan which facts you need.
2. Send assistants to the websites with `investigate` — several in one turn run in parallel. Give each a precise
   task: what to find, which exact reference, which details (dates, units, status, validity, senders), and to report
   verbatim. Their first pass should survey their site for everything the rules might need; information may sit on
   any site, so ask each assistant to report anything relevant it sees.
3. Time allows two rounds of investigations: the first survey and one round of follow-ups. Every investigation
   is ended after about {round_minutes} minutes, so give each assistant one clear job. Use the follow-up round for
   what depends on another site's answer (e.g. a catalogue group found in the specification must then be searched
   in the ERP by that group, reading every result; amendments must be applied to the right revision).
4. Put the facts from the reports into the facts JSON of `price_bid` and call it. It applies the bid rules in code
   and returns the priced answer, `issues` (what is missing or ambiguous), `incomplete` positions and `omitted`
   positions. Do not price anything yourself. On a later call pass only the top-level fields that are new or
   changed: a list you pass replaces that list, fields you leave out stay as they were.
5. Call `submit_bid`. It submits the last answer `price_bid` returned; you only add hints for filling the form.

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

`calculate` is only for checks of your own; the numbers of the bid come from `price_bid`.

The clock: about {minutes} minutes to investigate. Then you are asked for `price_bid` and the priced answer is
submitted; entering and signing the bid takes the rest of the time."""

# Appended to every investigation task, so each reported value can be turned into a fact with its source.
PROVENANCE = ("For every value you report, say where it is from: the website, the page or document, its date, the "
              "sender's address if it is a message, and the revision if the document has revisions.")

SCORED_RUN_SECONDS = 20 * 60
# How the working time is spent, as shares of it. Entering and signing the bid gets everything after SUBMIT_SHARE.
COLLECT_SHARE = 0.45  # investigations end here
SUBMIT_SHARE = 0.45  # from here on the lead has to price and submit
# One investigation may take this long, so that a round of follow-ups fits before the investigations end.
INVESTIGATION_SECONDS = 240
# A sub-agent checks the clock between its steps; one step that hangs is cut off this long after its time is up.
INVESTIGATION_GRACE_SECONDS = 30
FORCE_SUBMIT = {"type": "function", "function": {"name": "submit_bid"}}
FORCE_PRICE = {"type": "function", "function": {"name": "price_bid"}}
PRICE_ATTEMPTS = 2  # forced price_bid calls that may fail before the lead has to hand in an answer of its own


class Orchestrator:
    def __init__(self, task: dict, token: str | None, tabs: dict[str, Tab], model: Model, started: float):
        self.task, self.token, self.tabs, self.model = task, token, tabs, model
        self.credentials = {a["siteId"]: a.get("credentials", {}) for a in task["access"]}
        self.started = started
        now = time.time()
        # Every run works with the length of a scored run: a test run expires much later, a local run never.
        left = min(contract.seconds_left(task), SCORED_RUN_SECONDS - (now - started))
        self.collect_until = now + left * COLLECT_SHARE
        self.submit_from = now + left * SUBMIT_SHARE
        self.deadline = now + left
        self.locks = {site: asyncio.Lock() for site in tabs}
        self.facts_data: dict = {}  # the facts JSON as the lead has built it up so far
        self.priced: engine.BidResult | None = None  # what the bid engine made of the latest facts
        self.price_failures = 0
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
                "description": "Send a browsing assistant to one website with a precise task; returns its report.",
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
                "name": "price_bid",
                "description": "Apply the bid rules to the collected facts in code. Returns the priced answer, "
                               "`issues` (what is missing or ambiguous), `incomplete` and `omitted` positions. "
                               "The first call needs tender_reference and scenario_date; a later call may pass "
                               "only the top-level fields that changed.",
                "parameters": {"type": "object", "properties": {"facts": facts_schema()},
                               "required": ["facts"]}}},
            {"type": "function", "function": {
                "name": "submit_bid",
                "description": "Have an assistant enter and sign the last answer price_bid returned on the "
                               "submission website. Give any form hints you learned. Pass `answer` only if "
                               "price_bid could not be made to return one.",
                "parameters": {"type": "object", "properties": {
                    "hints": {"type": "string"},
                    "answer": {"type": "object"}}, "required": []}}},
        ]

    async def investigate(self, site: str, task: str) -> str:
        if site not in self.tabs:
            return f"Unknown site {site}; choose one of {', '.join(self.tabs)}"
        async with self.locks[site]:
            if time.time() >= self.collect_until:
                return "No time left to investigate: call submit_bid now with what you have."
            until = min(self.collect_until, time.time() + INVESTIGATION_SECONDS)
            agent = BrowsingAgent(self.model, {site: self.tabs[site]}, self.credentials, f"{task}\n\n{PROVENANCE}",
                                  self.context(), name=site, deadline=until, log=self.log)
            try:
                return await asyncio.wait_for(agent.run(), timeout=until - time.time() + INVESTIGATION_GRACE_SECONDS)
            except TimeoutError:
                self.log(f"[{site}] cut off: a step did not return in time")
                return agent._report("(cut off: out of time; only the notes below were saved)")

    def price(self, data) -> str:
        if not isinstance(data, dict):
            self.price_failures += 1
            return "price_bid needs the facts as a JSON object."
        merged = {**self.facts_data, **{key: value for key, value in data.items() if key in FACT_FIELDS}}
        try:
            collected = facts.from_dict(facts.Facts, merged)
            result = engine.evaluate(collected)
            result = self.without_hasty_no_bid(collected, result)
        except Exception as error:  # noqa: BLE001 - a ValueError names the bad field; the model corrects it
            self.price_failures += 1
            return f"The facts could not be priced: {error}. Correct this and call price_bid again."
        self.facts_data, self.priced = merged, result
        self.log("[lead] facts " + json.dumps(merged, ensure_ascii=False))
        try:
            self.log("[lead] engine working:\n" + result.explain())
        except Exception as error:  # noqa: BLE001 - the explanation is for the log only
            self.log(f"[lead] engine working not available: {error}")
        return json.dumps({"answer": result.answer, "issues": result.issues, "incomplete": result.incomplete,
                           "omitted": result.omitted}, ensure_ascii=False)

    def without_hasty_no_bid(self, collected: facts.Facts, result: engine.BidResult) -> engine.BidResult:
        """A "no bid" is final only when the engine has nothing else to report and every position without a
        material had its catalogue group searched on purpose. While facts are still missing, the positions without
        a material are left out instead and the others are bid."""
        if result.answer.get("decision") != "no_bid":
            return result
        unresolved = [line.position for line in result.lines if line.material is None]
        searched = {facts.norm_key(group) for group in collected.searched_groups}

        def conclusive(position: facts.TenderPosition) -> bool:
            groups = {facts.norm_key(requirement.catalogue_group)
                      for revision in collected.revisions for requirement in revision.requirements
                      if facts.norm_key(requirement.requirement) == facts.norm_key(position.requirement)}
            return bool(groups) and groups <= searched

        # the engine's own remarks about the unmatched positions do not count as open questions
        open_questions = [issue for issue in result.issues if "no active material matches" not in issue]
        if not open_questions and all(conclusive(p) for p in collected.positions if p.position in unresolved):
            return result
        kept = [position for position in collected.positions if position.position not in unresolved]
        partial = engine.evaluate(dataclasses.replace(collected, positions=kept))
        note = ("no bid was not taken as final: facts are missing or a catalogue group was not searched completely; "
                "position(s) " + ", ".join(unresolved) + " are left out instead")
        return dataclasses.replace(partial, issues=[*result.issues, note], omitted=[*partial.omitted, *unresolved])

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
                + (f"\n\nHINTS:\n{hints}" if hints else ""))
        agent = SubmissionAgent(self.model, sites, self.credentials, task, self.context(),
                                deadline=self.deadline, log=self.log)
        self.submitted = answer
        report = await agent.run()
        self.accepted = await self.run_is_over()
        if self.accepted:
            return report + "\n\nThe platform has closed the run: the bid was accepted."
        return report + ("\n\nThe run is still open, so no bid has been accepted yet. Call submit_bid again and say "
                         "in the hints what is already entered and what went wrong.")

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

    async def ask(self, messages: list[dict], tools: list[dict], force: dict | None):
        if force:
            try:
                return await self.model.chat(messages, tools, temperature=0.2, tool_choice=force)
            except Exception as error:  # noqa: BLE001 - a model that refuses a forced tool still gets asked plainly
                self.log(f"[lead] forced {force['function']['name']} call failed ({str(error)[:200]}); asking without it")
        return await self.model.chat(messages, tools, temperature=0.2)

    async def time_is_up(self, messages: list[dict]) -> dict | None:
        """What happens once the time to investigate is over: the engine's answer is submitted without asking the
        lead; without one the lead is made to price, and after that to submit. Returns the tool to force."""
        if self.priced and self.submitted is None and not self.sent_without_lead:
            self.sent_without_lead = True
            self.log("[lead] time to submit: sending the last priced answer")
            report = await self.submit(self.priced.answer, "")
            self.log(f"[lead] <- submit: {report[:3000]}")
            messages.append({"role": "user", "content": "The time to investigate was over, so the last priced answer "
                             "was submitted for you. The result:\n\n" + report})
        if self.accepted:
            return None
        if self.priced or self.price_failures >= PRICE_ATTEMPTS:
            if self.submitted is None:
                messages.append({"role": "user", "content": "Investigation time is over: call submit_bid now" + (
                    "." if self.priced else ", passing your best answer yourself. Leave out positions you could not "
                    "complete.")})
            return FORCE_SUBMIT
        messages.append({"role": "user", "content": "Investigation time is over: call price_bid now with every fact "
                         "you have."})
        return FORCE_PRICE

    async def run(self):
        minutes = round((self.submit_from - time.time()) / 60)
        messages = [
            {"role": "system", "content": ORCHESTRATOR.format(
                minutes=minutes, round_minutes=round(INVESTIGATION_SECONDS / 60)) + "\n\n" + facts.FILLING_GUIDE},
            {"role": "user", "content": self.context() + "\n\n# Websites\n" + "\n".join(
                f"- {a['siteId']}: {a['url']}" for a in self.task["access"]) + "\n\nBegin."},
        ]
        tools = self.tools()
        while time.time() < self.deadline and not self.accepted:
            force = await self.time_is_up(messages) if time.time() >= self.submit_from else None
            if self.accepted:
                return
            try:
                message = await asyncio.wait_for(self.ask(messages, tools, force),
                                                 timeout=self.deadline - time.time())
            except TimeoutError:
                self.log("[lead] out of time")
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
                messages.append({"role": "user", "content": (
                    "No bid has been accepted yet: call submit_bid again." if self.submitted
                    else "Continue: investigate, price_bid, or submit_bid.")})
                continue

            async def execute(call):
                try:
                    args = json.loads(call.function.arguments or "{}")
                except ValueError:
                    return "Error: arguments were not valid JSON"
                self.log(f"[lead] {call.function.name} {json.dumps(args, ensure_ascii=False)[:600]}")
                try:
                    if call.function.name == "investigate":
                        return await self.investigate(args["site"], args["task"])
                    if call.function.name == "calculate":
                        return calculate(args["expression"])
                    if call.function.name == "price_bid":
                        return self.price(args.get("facts"))
                    if call.function.name == "submit_bid":
                        answer = self.answer_to_submit(args.get("answer"))
                        if answer is None:
                            return ("There is no priced answer yet: call price_bid first. Pass `answer` yourself "
                                    "only if price_bid cannot be made to return one.")
                        return await self.submit(answer, args.get("hints", ""))
                    return f"Unknown tool {call.function.name}"
                except Exception as error:  # noqa: BLE001
                    return f"Error: {error}"

            results = await asyncio.gather(*(execute(c) for c in calls))
            for call, result in zip(calls, results):
                self.log(f"[lead] <- {call.function.name}: {result[:3000]}")
                messages.append({"role": "tool", "tool_call_id": call.id, "content": result})


# The facts the lead fills in. Which sender may decide what is the engine's own default, not the model's to set.
FACT_FIELDS = {f.name for f in dataclasses.fields(facts.Facts)} - {"authority"}


def facts_schema() -> dict:
    schema = facts.json_schema()
    return {**schema, "properties": {name: rule for name, rule in schema["properties"].items() if name in FACT_FIELDS}}


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
    model = Model()
    print(f"Run {task.get('run_id') or '(local scouting run)'}; model {model.provider}/{model.model}; "
          f"sites: {', '.join(a['siteId'] for a in task['access'])}", flush=True)
    async with async_playwright() as playwright:
        tabs = {}
        for index, access in enumerate(task["access"][:4]):
            available = max(len(browsers.cdp_urls()), 1)
            browser, page = await browsers.connect(playwright, index if index < available else 0)
            if index >= available:  # fewer browsers than sites: a tab of its own in the first browser
                page = await page.context.new_page()
            await page.goto(access["url"], wait_until="domcontentloaded")
            tabs[access["siteId"]] = Tab(access["siteId"], page)
        lead = Orchestrator(task, token, tabs, model, started)
        await lead.run()
    print(f"Done in {time.time() - started:.0f}s; {model.usage()}; submitted: "
          f"{json.dumps(lead.submitted, ensure_ascii=False) if lead.submitted else 'nothing'}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
