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
from llm import Model, Stepped

JOB = """You work the website {site} ({url}) for the whole run, and only this website. The brief says what each
website of this tender holds; information may also sit where you would not expect it, so look at what yours shows.

Record on the board every fact of the tender {reference} that your website shows and the bid rules need: the bill of
quantities and every bidder Q&A answer (quantity changes, withdrawals, technical amendments, one-off setup fees for
a position, keys assigned to positions); specification revisions and the current one's requirements with all attributes and catalogue groups;
materials with their attributes, status, stock, lead times, price conditions, carton size, masses, metal index and
setup fee; and every message naming the tender with its records. Read every page of a list and every version.

Messages: besides discount approvals, the bid needs the rule records that messages for the tender hold, one record
per key: end reserves per revision (Technikfreigabe), exchange rates per FX key (Devisenfixing), metal-index rates
with their validity windows (Preisindizes), packaging tariffs (Verpackungstarife), setup pools with fee and
allocation (Rüstpools), project bonus bands (Projektbonus), condition-group rules (Konditionsregeln) and delivery
commitments (Terminfreigabe). Search for each kind and record every such message, also older and look-alike ones.

Price conditions: open every page of a material's conditions and record every condition with its validity period,
currency, FX key, price unit and every quantity scale, also the ones that are not valid on the scenario date: the
engine picks the one that counts. Record each price with the price unit the page names (a price per 100 pieces
stays the price per 100 pieces, price unit 100).

Numbers: record every price, amount, rate and mass as text, character for character as the page writes it, with its
own thousands and decimal marks (e.g. "186.615,00" or "1.250,00 EUR"); never turn it into a number yourself and
never convert, divide or round it. The engine reads the format. A German "186.615,00" is one hundred and eighty-six
thousand, not one hundred and eighty-six.

Then keep going with what the board says is still needed and your website can answer, e.g. a search by a key that
another website gave (search a catalogue group with at most 20 results, record every result, open the details of
the ones whose attributes match). When nothing on your website is left for now, call wait."""
JOB_STEPS = 400  # the clock ends a job long before this
# The agents that read the websites and record the facts use a larger model with a little thinking: prices, price
# units and validity periods were what the hosted model without thinking read wrongly or not at all. The brief call
# and the submission stay with the hosted model without thinking, which entered and signed five positions fastest.
READER_PROVIDER = "openai"
READER_MODEL = "gpt-6-luna"
READER_REASONING = "low"
# A step of this model took 2-5 s in a test run, but one call in twelve hung until the browsing agent's 75 s limit
# (11 calls, a quarter of one agent's time). A call still open after this long is sent again.
READER_CALL_SECONDS = 25
# An agent starts with the hosted model and changes to the reading model for good when it is about to record one of
# these facts: the ones that carry prices, rates, masses or dated rules, which the hosted model read wrongly in test
# runs. That record is asked again from the reading model. Positions, quantities, clarifications of quantities and
# the specification's requirements were right with the hosted model in every run, so an agent whose website holds
# only those never changes.
# Experiment: the reading model answers only the steps that record careful facts; every other step, also after
# such a record, stays with the hosted model. And a step the hosted model has not answered after this long goes to
# the reading model, so a slow answer does not hold an agent up: the browsing agent gives a call without thinking
# 30 s and then asks again from the start, and in two test runs one agent lost over two minutes to five such calls
# in a row. The limit leaves the reading model the rest of those 30 s for its own answer.
READER_STAYS = False
HOSTED_STEP_SECONDS = 18
CAREFUL_FACTS = frozenset({"materials", "setup_fees", "discount_approvals", "condition_groups", "metal_indices",
                           "packaging_tariffs", "fx_rates", "setup_pools", "project_bonuses", "end_reserves",
                           "delivery_commitments"})
# Investigations end here at the latest, as a share of the run (minute 11 of 20). The 51.2 run stopped at minute 9
# with the rule mails still unread and ended after 11:44. Entering and signing five priced positions took at most
# 487 s with the scouting logins (re-logins and stalled model calls included) and about 165 s in the sandbox, so the
# submission keeps about 8.5 minutes. Investigations end sooner when the engine has nothing left to find.
COLLECT_SHARE = 0.55
COMPLETE_CHECK_SECONDS = 10  # how often the board is asked whether the bid is complete
BRIEF_SECONDS = 20  # the call answers in about 4 s; one that hung for 60 s held up every agent's start
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


def careful_record(message) -> str | None:
    """Why the reading model should give this answer: it records facts that need careful reading."""
    for call in getattr(message, "tool_calls", None) or []:
        if call.function.name != "record":
            continue
        try:
            arguments = json.loads(call.function.arguments or "{}")
        except ValueError:
            return "a record that cannot be read"
        recorded = arguments.get("facts", arguments) if isinstance(arguments, dict) else {}
        careful = sorted(CAREFUL_FACTS & set(recorded)) if isinstance(recorded, dict) else []
        if careful:
            return "about to record " + ", ".join(careful)
    return None


def site_agents(lead: lead_module.Orchestrator, board: Board, model: Model, reference: str,
                hosted: Model | None = None) -> list[BrowsingAgent]:
    """One agent per website of the run, each with that website's tab only. With `hosted`, every agent starts with
    that model and changes to `model` on its own, when it comes to the facts that need careful reading."""
    urls = {a["siteId"]: a["url"] for a in lead.task["access"]}
    schema = schema_for()
    browsers = []  # every browser of the run, once: a website that stops answering may be opened in another
    for tab in lead.tabs.values():
        try:
            browser = tab.page.context.browser
        except Exception:  # noqa: BLE001
            browser = None
        if browser is not None and all(browser is not b for b in browsers):
            browsers.append(browser)
    def model_of(site: str):
        if hosted is None or hosted is model:
            return model
        return Stepped(hosted, model, careful_record, name=site, log=lead.log, stays=READER_STAYS,
                       first_seconds=HOSTED_STEP_SECONDS)

    return [BrowsingAgent(model_of(site), {site: tab}, lead.credentials,
                          JOB.format(site=site, url=urls.get(site, ""), reference=reference) + "\n\n"
                          + lead_module.PROVENANCE, lead.context() + "\n\n" + facts.FILLING_GUIDE,
                          name=site, deadline=lead.collect_until, max_steps=JOB_STEPS, log=lead.log,
                          record_schema=schema, board=board, browsers=browsers)
            for site, tab in lead.tabs.items()]


async def run_agents(lead: lead_module.Orchestrator, agents: list[BrowsingAgent], board: Board | None = None) -> None:
    """The agents until the time to investigate is over, or until the bid is complete: more time is only spent
    while something is still missing."""
    runs = [asyncio.ensure_future(agent.run()) for agent in agents]
    end = lead.collect_until + lead_module.INVESTIGATION_GRACE_SECONDS
    while time.time() < end and not all(task.done() for task in runs):
        await asyncio.wait(runs, timeout=max(0.0, min(COMPLETE_CHECK_SECONDS, end - time.time())))
        if board is not None and board.complete():
            lead.log(f"[run] the bid is complete at {time.time() - lead.started:.0f} s: investigations end")
            break
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
    return Model(provider=READER_PROVIDER, model=READER_MODEL, reasoning_effort=READER_REASONING, fallback=hosted,
                 call_seconds=READER_CALL_SECONDS)


def described(model: Model) -> str:
    effort = "without thinking" if model.thinking is False else f"reasoning {model.reasoning_effort or 'default'}"
    return f"{model.provider}/{model.model} {effort}"


async def _run(lead: lead_module.Orchestrator, model: Model, reader: Model) -> None:
    board = Board(lead.builder, log=lead.log)
    reference = await brief_facts(lead, board, model)
    lead.log(f"[run] tender {reference}; one agent per website ({', '.join(lead.tabs)}) until "
             f"{lead.collect_until - lead.started:.0f} s")
    await run_agents(lead, site_agents(lead, board, reader, reference, model), board)
    await submit(lead)


async def main() -> int:
    started = time.time()
    task, token = contract.load_run()
    model = Model(thinking=False)
    reader = reading_model(model)
    how = "from the first record of prices on" if READER_STAYS else "for the records of prices only"
    reading = described(reader) if reader is model else f"{described(model)}, and {described(reader)} {how}"
    print(f"Run {task.get('run_id') or '(local scouting run)'}; websites read by {reading}; brief and "
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
        lead.collect_until = lead.started + (lead.deadline - lead.started) * COLLECT_SHARE
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
