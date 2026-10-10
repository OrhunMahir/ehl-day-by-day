"""Entry point: agent.json runs `uv run --no-sync python agent.py`, in the sandbox and on your machine.

An orchestrating model reads the brief and directs browsing sub-agents, one browser per website, in parallel. It
decides what to investigate where, follows up on gaps, works out the bid with exact arithmetic helpers, and finally
has a sub-agent enter and sign the bid on the submission website.
"""
import asyncio
import json
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

The clock: {minutes} minutes in total. Submit no later than ~3 minutes before the end."""


class Orchestrator:
    def __init__(self, task: dict, token: str | None, tabs: dict[str, Tab], model: Model, started: float):
        self.task, self.token, self.tabs, self.model = task, token, tabs, model
        self.credentials = {a["siteId"]: a.get("credentials", {}) for a in task["access"]}
        self.started = started
        budget = contract.seconds_left(task)
        self.deadline = time.time() + (budget if budget != float("inf") else 20 * 60)
        self.locks = {site: asyncio.Lock() for site in tabs}
        self.submitted = None

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
            agent = BrowsingAgent(self.model, {site: self.tabs[site]}, self.credentials, task, self.context(),
                                  name=site, deadline=self.deadline - 150, log=self.log)
            return await agent.run()

    async def submit(self, answer: dict, hints: str) -> str:
        problems = validate(answer, self.task["answer_schema"])
        if problems:
            return "The answer does not match the schema: " + "; ".join(problems)
        submission = self.task["submission"]
        if submission.get("kind") == "api":
            if not self.token:
                self.submitted = answer
                return "Local run: answer recorded, nothing is submitted."
            status, body = contract.submit_answer(self.task, self.token, answer)
            if status == 202:
                self.submitted = answer
            return f"HTTP {status}: {body[:1500]}"
        target = submission.get("siteId")
        sites = {target: self.tabs[target]}
        sites.update({s: t for s, t in self.tabs.items() if s != target})  # e.g. the mailbox for a signing code
        task = ("Enter this bid on the submission website exactly as given and submit it, following the brief's "
                "Submission section step by step (register/express interest if needed, configure every position, "
                "enter numbers in the site's number format, acknowledge documents, get the signing code where the "
                "brief says, review the summary against the answer, confirm). Use each value exactly; do not "
                "recompute. Report the receipt or any validation message verbatim.\n\nANSWER:\n"
                + json.dumps(answer, ensure_ascii=False, indent=1) + (f"\n\nHINTS:\n{hints}" if hints else ""))
        agent = BrowsingAgent(self.model, sites, self.credentials, task, self.context(), name="submit",
                              deadline=self.deadline + 120, max_steps=90, log=self.log)
        self.submitted = answer
        return await agent.run()

    async def run(self):
        minutes = round((self.deadline - time.time()) / 60)
        messages = [
            {"role": "system", "content": ORCHESTRATOR.format(minutes=minutes)},
            {"role": "user", "content": self.context() + "\n\n# Websites\n" + "\n".join(
                f"- {a['siteId']}: {a['url']}" for a in self.task["access"]) + "\n\nBegin."},
        ]
        tools = self.tools()
        while time.time() < self.deadline:
            left = self.deadline - time.time()
            if left < 300 and not self.submitted:
                messages.append({"role": "user", "content": f"Only {int(left)} s left: call submit_bid now with your best answer."})
            message = await self.model.chat(messages, tools, temperature=0.2)
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
                    return
                messages.append({"role": "user", "content": "Continue: investigate, calculate, or submit_bid."})
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
            if self.submitted and any(c.function.name == "submit_bid" for c in calls):
                return


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
