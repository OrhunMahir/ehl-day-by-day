"""Entry point of the board concept (branch stream-j-simple): one agent per website, connected by a shared board.

Every website of the run gets one browsing agent for the whole run, whatever the website is: one session, no second
login, no other website to wander to. Each agent records every fact the bid needs that its website shows, typed, on a
shared board (board.py), and sees before each step what the others recorded and what the bid still needs. It takes
on what its website can answer (e.g. the ERP search for a catalogue group the specification gave) and waits for the
board when nothing is left for now. When the time to investigate is over, the engine prices the board's facts (every
position is always in the answer) and the submission agent enters and signs the bid. No lead model, no recorder, no
report and no hand-off: the board is the only connection, and it is immediate.
"""
import asyncio
import json
import os
import sys
import time

from playwright.async_api import async_playwright

import agent as lead_module
import browsers
import facts
import run as contract
from board import Board
from browsing import BrowsingAgent
from llm import Model

JOB = """You work the website {site} ({url}) for the whole run, and only this website. The brief says what each
website of this tender holds; information may also sit where you would not expect it, so look at what yours shows.

Record on the board every fact of the tender {reference} that your website shows and the bid rules need: the bill of
quantities and every bidder Q&A answer (quantity changes, withdrawals, technical amendments, keys assigned to
positions); specification revisions and the current one's requirements with all attributes and catalogue groups;
materials with their attributes, status, stock, lead times, price conditions (validity, currency, FX key, price unit,
every quantity scale), carton size, masses, metal index and setup fee; and every message naming the tender with its
records (discount approvals, rule mails, end reserves, rates). Read every page of a list and every version.

Then keep going with what the board says is still needed and your website can answer, e.g. a search by a key that
another website gave (search a catalogue group with at most 20 results, record every result, open the details of
the ones whose attributes match). When nothing on your website is left for now, call wait."""
JOB_STEPS = 400  # the clock ends a job long before this
# The agents that read the websites and record the facts use a larger model with a little thinking: prices, price
# units and validity periods were what the hosted model without thinking read wrongly or not at all. The brief call
# and the submission stay with the hosted model without thinking, which entered and signed five positions fastest.
READER_PROVIDER = "openai"
READER_MODEL = "gpt-6.1-sol"
READER_REASONING = "low"
BRIEF_SECONDS = 60
SUBMIT_ATTEMPTS = 2

BRIEF = """Take from the brief of a tender: its reference, the scenario date (ISO 8601 with time and zone) and the
sales director's mail address. Call `record` once with what the brief states; leave out what it does not state."""


def schema_for(names: list[str] | None = None) -> dict:
    """The facts' update schema, all of it or only these fields; never `authority`, which the engine decides."""
    full = facts.update_schema()
    keep = {n: rule for n, rule in full["properties"].items() if n != "authority" and (names is None or n in names)}
    return {**full, "properties": keep}


async def brief_facts(lead: lead_module.Orchestrator, board: Board, model: Model) -> str:
    """The tender reference, scenario date and director address from the brief, so the engine can price at all."""
    tool = {"type": "function", "function": {"name": "record", "description": "Save what the brief states.",
                                              "parameters": {"type": "object", "properties": {
                                                  "facts": schema_for(["tender_reference", "scenario_date",
                                                                       "director_address"])}, "required": ["facts"]}}}
    messages = [{"role": "system", "content": BRIEF}, {"role": "user", "content": lead.context()}]
    for _ in range(2):
        try:
            message = await asyncio.wait_for(lead.ask(messages, [tool], {"type": "function", "function": {
                "name": "record"}}, model), timeout=BRIEF_SECONDS)
            call = next(c for c in message.tool_calls or [] if c.function.name == "record")
            update = json.loads(call.function.arguments or "{}")
            board.record("brief", update.get("facts", update))
            break
        except Exception as error:  # noqa: BLE001 - the site agents record the reference as well
            lead.log(f"[brief] not read: {type(error).__name__}: {str(error)[:200]}")
    return str(lead.builder.data.get("tender_reference") or "named in the brief")


def site_agents(lead: lead_module.Orchestrator, board: Board, model: Model, reference: str) -> list[BrowsingAgent]:
    """One agent per website of the run, each with that website's tab only."""
    urls = {a["siteId"]: a["url"] for a in lead.task["access"]}
    schema = schema_for()
    return [BrowsingAgent(model, {site: tab}, lead.credentials,
                          JOB.format(site=site, url=urls.get(site, ""), reference=reference) + "\n\n"
                          + lead_module.PROVENANCE, lead.context() + "\n\n" + facts.FILLING_GUIDE,
                          name=site, deadline=lead.collect_until, max_steps=JOB_STEPS, log=lead.log,
                          record_schema=schema, board=board)
            for site, tab in lead.tabs.items()]


async def run_agents(lead: lead_module.Orchestrator, agents: list[BrowsingAgent]) -> None:
    runs = [asyncio.ensure_future(agent.run()) for agent in agents]
    await asyncio.wait(runs, timeout=max(0.0, lead.collect_until - time.time() + lead_module.INVESTIGATION_GRACE_SECONDS))
    for task in runs:
        task.cancel()


async def submit(lead: lead_module.Orchestrator) -> None:
    """Prices the board's facts and has the bid entered and signed; once more if the platform did not take it."""
    lead.price()
    hints = ""
    for _ in range(SUBMIT_ATTEMPTS):
        if not lead.priced or lead.deadline - time.time() < 60:
            return
        report = await lead.submit(lead.priced.answer, hints)
        lead.log(f"[submit] {report[:3000]}")
        if lead.accepted:
            return
        hints = "An earlier attempt did not end with an accepted bid. Its report:\n" + report[-2000:]


def reading_model(hosted: Model) -> Model:
    """The model of the agents that read the websites. Without a key for its provider it is the hosted model; with a
    key the provider turns down during the run, the hosted model takes over from that call on."""
    if not os.environ.get(f"{READER_PROVIDER.upper()}_API_KEY") or not os.environ.get(f"{READER_PROVIDER.upper()}_BASE_URL"):
        return hosted
    return Model(provider=READER_PROVIDER, model=READER_MODEL, reasoning_effort=READER_REASONING, fallback=hosted)


def described(model: Model) -> str:
    effort = "without thinking" if model.thinking is False else f"reasoning {model.reasoning_effort or 'default'}"
    return f"{model.provider}/{model.model} {effort}"


async def _run(lead: lead_module.Orchestrator, model: Model, reader: Model) -> None:
    board = Board(lead.builder, log=lead.log)
    reference = await brief_facts(lead, board, model)
    lead.log(f"[run] tender {reference}; one agent per website ({', '.join(lead.tabs)}) until "
             f"{lead.collect_until - lead.started:.0f} s")
    await run_agents(lead, site_agents(lead, board, reader, reference))
    await submit(lead)


async def main() -> int:
    started = time.time()
    task, token = contract.load_run()
    model = Model(thinking=False)
    reader = reading_model(model)
    print(f"Run {task.get('run_id') or '(local scouting run)'}; websites read by {described(reader)}; brief and "
          f"submission by {described(model)}; one agent per website: "
          f"{', '.join(a['siteId'] for a in task['access'])}", flush=True)
    async with async_playwright() as playwright:
        async def open_one(index: int, access: dict):
            available = max(len(browsers.cdp_urls()), 1)
            _, page = await browsers.connect(playwright, index if index < available else 0)
            if index >= available:
                page = await page.context.new_page()
            return access["siteId"], await lead_module.open_site(access["siteId"], access["url"], page)
        # all websites at once: a slow one must not hold up the others (a sandbox run lost 80 s here)
        tabs = dict(await asyncio.gather(*(open_one(i, a) for i, a in enumerate(task["access"][:4]))))
        lead = lead_module.Orchestrator(task, token, tabs, model, started, model)
        try:
            await asyncio.wait_for(_run(lead, model, reader), timeout=lead.deadline - time.time() + 5)
        except TimeoutError:
            lead.log("[run] stopped at the end of the run's time")
    readers = "" if reader is model else f"readers {reader.usage()}; hosted model "
    print(f"Done in {time.time() - started:.0f}s; {readers}{model.usage()}; submitted: "
          f"{json.dumps(lead.submitted, ensure_ascii=False) if lead.submitted else 'nothing'}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
