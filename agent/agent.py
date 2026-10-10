"""Entry point: agent.json runs `uv run --no-sync python agent.py`, in the sandbox and on your machine.

An orchestrating model reads the brief and directs browsing sub-agents, one browser per website, in parallel. It
decides what to investigate where, follows up on gaps, works out the bid with exact arithmetic helpers, and finally
has a sub-agent enter and sign the bid on the submission website.
"""
import asyncio
import json
import os
import sys
import time

from playwright.async_api import async_playwright

import browsers
import run as contract
from browser import Tab
from browsing import BrowsingAgent
from calc import calculate
from llm import Model

ORCHESTRATOR = """You are the lead sales engineer preparing exactly one tender response. You cannot browse yourself:
you direct browsing assistants, one per website, and they report back. You make the decisions.

Approach:
1. Read the brief and its rules closely; they define the one correct answer. Plan which facts you need.
2. Send assistants to the websites with `investigate` — several in one turn run in parallel. Give each a precise
   task: what to find, which exact reference, which details (dates, units, status, validity, senders), and to report
   verbatim. Their first pass should survey their site for everything the rules might need; information may sit on
   any site, so ask each assistant to report anything relevant it sees.
3. Check the reports against the rules. Follow up with targeted investigations for gaps or contradictions (e.g. a
   catalogue group found in the specification must then be searched in the ERP; amendments found in Q&A must be
   applied to the right revision). Do not invent values; a fact you never saw is unknown.
4. Compute with `calculate` (exact decimal arithmetic) — never do arithmetic in your head.
5. Call `submit_bid` with the final answer JSON. It validates the JSON, then an assistant enters and signs it.
   If time is short, submit your best answer: every field scores on its own; leave optional fields out if unknown.

The clock: about {minutes} minutes to investigate. Then you must call `submit_bid` with what you have; entering
and signing the bid takes the rest of the time."""

SCORED_RUN_SECONDS = 20 * 60
# How the working time is spent, as shares of it. Entering and signing the bid gets everything after SUBMIT_SHARE.
COLLECT_SHARE = 0.45  # investigations end here
SUBMIT_SHARE = 0.50  # from here on the lead has to submit
FORCE_SUBMIT = {"type": "function", "function": {"name": "submit_bid"}}


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
        self.submitted = None  # the last answer a submission was attempted with
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
                "name": "submit_bid",
                "description": "Validate the final answer (JSON object per the answer schema) and have an assistant "
                               "enter and sign it on the submission website. Give any form hints you learned.",
                "parameters": {"type": "object", "properties": {
                    "answer": {"type": "object"},
                    "hints": {"type": "string"}}, "required": ["answer"]}}},
        ]

    async def investigate(self, site: str, task: str) -> str:
        if site not in self.tabs:
            return f"Unknown site {site}; choose one of {', '.join(self.tabs)}"
        async with self.locks[site]:
            if time.time() >= self.collect_until:
                return "No time left to investigate: call submit_bid now with what you have."
            agent = BrowsingAgent(self.model, {site: self.tabs[site]}, self.credentials, task, self.context(),
                                  name=site, deadline=self.collect_until, log=self.log)
            return await agent.run()

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
                + json.dumps(answer, ensure_ascii=False, indent=1) + (f"\n\nHINTS:\n{hints}" if hints else ""))
        agent = BrowsingAgent(self.model, sites, self.credentials, task, self.context(), name="submit",
                              deadline=self.deadline, max_steps=90, log=self.log)
        self.submitted = answer
        report = await agent.run()
        self.accepted = await self.run_is_over()
        if self.accepted:
            return report + "\n\nThe platform has closed the run: the bid was accepted."
        return report + ("\n\nThe run is still open, so no bid has been accepted yet. Call submit_bid again and say "
                         "in the hints what is already entered and what went wrong.")

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

    async def ask(self, messages: list[dict], tools: list[dict], must_submit: bool):
        if must_submit:
            try:
                return await self.model.chat(messages, tools, temperature=0.2, tool_choice=FORCE_SUBMIT)
            except Exception as error:  # noqa: BLE001 - a model that refuses a forced tool still gets asked plainly
                self.log(f"[lead] forced submit_bid call failed ({str(error)[:200]}); asking without it")
        return await self.model.chat(messages, tools, temperature=0.2)

    async def run(self):
        minutes = round((self.submit_from - time.time()) / 60)
        messages = [
            {"role": "system", "content": ORCHESTRATOR.format(minutes=minutes)},
            {"role": "user", "content": self.context() + "\n\n# Websites\n" + "\n".join(
                f"- {a['siteId']}: {a['url']}" for a in self.task["access"]) + "\n\nBegin."},
        ]
        tools = self.tools()
        while time.time() < self.deadline and not self.accepted:
            must_submit = time.time() >= self.submit_from
            if must_submit and not self.submitted:
                messages.append({"role": "user", "content": "Investigation time is over: call submit_bid now with "
                                 "your best answer. Leave out positions you could not complete."})
            message = await self.ask(messages, tools, must_submit)
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
                    else "Continue: investigate, calculate, or submit_bid.")})
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
                    if call.function.name == "submit_bid":
                        return await self.submit(args["answer"], args.get("hints", ""))
                    return f"Unknown tool {call.function.name}"
                except Exception as error:  # noqa: BLE001
                    return f"Error: {error}"

            results = await asyncio.gather(*(execute(c) for c in calls))
            for call, result in zip(calls, results):
                self.log(f"[lead] <- {call.function.name}: {result[:3000]}")
                messages.append({"role": "tool", "tool_call_id": call.id, "content": result})


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
