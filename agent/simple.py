"""Entry point of the simple concept (branch stream-j-simple): three jobs, no hand-offs.

Three browsing agents start at once and work in parallel. Each has one job from start to finish and every website at
hand, and saves typed facts with a `record` tool the moment it reads them:

- positions: the bill of quantities and every bidder Q&A answer;
- materials: the specification's current revision, then the ERP search by each of its catalogue groups and the
  details of the matching materials, so the only dependency between websites stays inside one agent;
- rules: every message for the tender (approvals, rule mails, end reserves, rates).

When the time to investigate is over, the engine prices whatever is recorded (every position is always in the
answer) and the submission agent enters and signs the bid. There is no lead model, no recorder and no round: nothing
waits for another agent, and nothing read is lost in a report.
"""
import asyncio
import json
import sys
import time

from playwright.async_api import async_playwright

import agent as lead_module
import browsers
import facts
import run as contract
from browsing import BrowsingAgent
from llm import Model

# Which facts each job records. The engine needs the tender reference and the scenario date before it can price, so
# they are taken from the brief first, and the jobs may record them again.
JOBS = {
    "positions": (
        ["tender_reference", "scenario_date", "positions", "quantity_changes", "withdrawals", "amendments",
         "assignments"],
        "Find the tender of the brief (reference {reference}) and record: every position of its bill of quantities "
        "(position number, quantity, unit, the specification requirement it refers to); then every bidder Q&A answer "
        "on every page, each with its number and date: quantity changes, answers that withdraw an earlier answer, "
        "technical amendments (revision, requirement, attribute, new value) and the keys an answer assigns to a "
        "position (condition group, packaging tariff, delivery commitment, setup pool, project bonus)."),
    "materials": (
        ["revisions", "materials", "searched_groups"],
        "Find the technical specification of the tender of the brief (reference {reference}): record its revisions "
        "and which one the site marks as current, and every requirement of the current revision with all its "
        "attributes (bore, stroke, pressure, mounting, certification), pieces per set, usable travel and end-reserve "
        "key, and catalogue group. Then, in the ERP, search by each of these catalogue groups (narrow the search to "
        "at most 20 results), record every result with the attributes the list shows, and open the details of each "
        "result whose attributes match a requirement (the stroke may differ by the end reserves): record its status, "
        "stock, lead times, price conditions (validity, currency, FX key, price unit, every quantity scale), carton "
        "size, masses, metal index and setup fee. Record a group in searched_groups once you have read all its "
        "results."),
    "rules": (
        ["director_address", "bidder_domain", "discount_approvals", "condition_groups", "metal_indices",
         "packaging_tariffs", "delivery_commitments", "fx_rates", "setup_pools", "project_bonuses", "end_reserves"],
        "In the mailbox, find every message that names the tender of the brief (reference {reference}), in every "
        "folder and on every page, and record each record it holds with its sender, date and the exact reference it "
        "names: discount approvals (percent and any minimum order value), condition group rules, metal indices, "
        "packaging tariffs, delivery commitments, FX rates, setup pools, project bonuses and end reserves. Record our "
        "own mailbox domain too. Keep looking after the first matches: later or superseding messages count."),
}
# The home site of each job takes the tab that is already open there; its other sites get tabs of their own.
HOME = {"positions": ["tender-portal"], "materials": ["customer-portal", "erp"], "rules": ["mail"]}
JOB_STEPS = 150  # the clock ends a job long before this
BRIEF_SECONDS = 60
SUBMIT_ATTEMPTS = 2

BRIEF = """Take from the brief of a tender: its reference, the scenario date (ISO 8601 with time and zone) and the
sales director's mail address. Call `record` once with what the brief states; leave out what it does not state."""


def schema_for(names: list[str]) -> dict:
    """The part of the facts' update schema that holds these fields."""
    full = facts.update_schema()
    return {**full, "properties": {n: rule for n, rule in full["properties"].items() if n in names}}


async def brief_facts(lead: lead_module.Orchestrator, model: Model) -> str:
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
            lead.add(update.get("facts", update), by_lead=False)
            break
        except Exception as error:  # noqa: BLE001 - the positions job records the reference as well
            lead.log(f"[brief] not read: {type(error).__name__}: {str(error)[:200]}")
    return str(lead.builder.data.get("tender_reference") or "as the brief names it")


def recorder(lead: lead_module.Orchestrator, job: str, names: set[str]):
    """The `record` function of one job: only its own fields, saved into the shared facts at once."""
    def record(update) -> str:
        if not isinstance(update, dict):
            raise ValueError("the facts must be an object")
        foreign = set(update) - names - {"replace_lists"}
        if foreign:
            raise ValueError(f"this job records only {', '.join(sorted(names))}; not {', '.join(sorted(foreign))}")
        summary = lead.builder.add(update)  # a bad update raises and changes nothing; the agent is told why
        lead.log(f"[{job}] recorded: {summary[:240]}")
        return "Recorded. " + summary
    return record


async def run_jobs(lead: lead_module.Orchestrator, model: Model, reference: str) -> None:
    """The three jobs, side by side, until the time to investigate is over."""
    tabs: dict[str, dict] = {job: {} for job in JOBS}
    for job in JOBS:  # every job's home sites first, so each gets the tab that is already open there
        for site in HOME[job]:
            if site in lead.tabs:
                tabs[job][site] = await lead.take_tab(site)
    for job in JOBS:  # then a tab of its own on every other site
        for site in lead.tabs:
            if site not in tabs[job]:
                tabs[job][site] = await lead.take_tab(site)
    agents = []
    for job, (names, task) in JOBS.items():
        agents.append(BrowsingAgent(model, tabs[job], lead.credentials, task.format(reference=reference) + "\n\n"
                                    + lead_module.PROVENANCE, lead.context() + "\n\n" + facts.FILLING_GUIDE,
                                    name=job, deadline=lead.collect_until, max_steps=JOB_STEPS, log=lead.log,
                                    record=recorder(lead, job, set(names)), record_schema=schema_for(names)))
    runs = [asyncio.ensure_future(agent.run()) for agent in agents]
    await asyncio.wait(runs, timeout=max(0.0, lead.collect_until - time.time() + lead_module.INVESTIGATION_GRACE_SECONDS))
    for task in runs:
        task.cancel()


async def submit(lead: lead_module.Orchestrator) -> None:
    """Prices what is recorded and has the bid entered and signed; once more if the platform did not take it."""
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


async def main() -> int:
    started = time.time()
    task, token = contract.load_run()
    model = Model(thinking=False)
    print(f"Run {task.get('run_id') or '(local scouting run)'}; model {model.provider}/{model.model} without thinking; "
          f"three jobs: {', '.join(JOBS)}; sites: {', '.join(a['siteId'] for a in task['access'])}", flush=True)
    async with async_playwright() as playwright:
        tabs = {}
        for index, access in enumerate(task["access"][:4]):
            available = max(len(browsers.cdp_urls()), 1)
            _, page = await browsers.connect(playwright, index if index < available else 0)
            if index >= available:
                page = await page.context.new_page()
            tabs[access["siteId"]] = await lead_module.open_site(access["siteId"], access["url"], page)
        lead = lead_module.Orchestrator(task, token, tabs, model, started, model)
        try:
            await asyncio.wait_for(_run(lead, model), timeout=lead.deadline - time.time() + 5)
        except TimeoutError:
            lead.log("[run] stopped at the end of the run's time")
    print(f"Done in {time.time() - started:.0f}s; {model.usage()}; submitted: "
          f"{json.dumps(lead.submitted, ensure_ascii=False) if lead.submitted else 'nothing'}", flush=True)
    return 0


async def _run(lead: lead_module.Orchestrator, model: Model) -> None:
    reference = await brief_facts(lead, model)
    lead.log(f"[run] tender {reference}; jobs until {lead.collect_until - lead.started:.0f} s")
    await run_jobs(lead, model, reference)
    await submit(lead)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
