"""Survey run (stream J): one browsing sub-agent per website, with a generic survey task, against the websites of
`atira scout --env`. It measures how complete and well-sourced the reports are; it is not a unit test.

Skipped unless SURVEY=1, so `python -m unittest discover -s tests` never touches the websites. To run it:

    atira models proxy                          # in another terminal
    eval "$(atira scout --env)"
    SURVEY=1 uv run --no-sync python -m unittest tests.test_browser_survey -v

Options: SURVEY_SITES=erp,mail (default: all), SURVEY_MINUTES=8 (time per site), SURVEY_STEPS=40, HEADLESS=1,
SURVEY_TASK="..." (replaces the survey task, e.g. to check that a sub-agent stays on its own website).
Reports and step logs go to ../scouting/survey/<time>/ (git-ignored: they hold values read from the websites).
"""
import asyncio
import json
import os
import re
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

SURVEY_TASK = (
    "Survey this website for everything the brief's task could need from it. Sign in, get past consent pages and "
    "popups, find the records that belong to the tender named in the brief, and open the relevant documents and "
    "records, including other versions so you can tell which one is current. Note every fact the bid rules could "
    "use, each with its source. Also note look-alike records (other references, other senders, other dates) as "
    "such, so they are not confused with the right ones. Read only: do not submit, sign, request codes or change "
    "anything."
)


@unittest.skipUnless(os.environ.get("SURVEY") == "1", "survey run against the websites; set SURVEY=1")
class SurveyRun(unittest.TestCase):
    def test_survey(self):
        stats = asyncio.run(survey())
        for site, row in stats.items():
            self.assertTrue(row["finished"] or row["notes"], f"{site}: no report and no notes")


async def survey() -> dict:
    from playwright.async_api import async_playwright

    from browser import Tab
    from browsing import BrowsingAgent
    from llm import Model

    scouting = json.loads(os.environ["CHALLENGE_SCOUTING"])
    access = scouting["access"]
    wanted = [s for s in os.environ.get("SURVEY_SITES", "").split(",") if s]
    if wanted:
        access = [a for a in access if a["siteId"] in wanted]
    credentials = {a["siteId"]: a.get("credentials", {}) for a in scouting["access"]}
    context = f"# The brief (rules apply exactly)\n{scouting['brief']}"
    minutes = float(os.environ.get("SURVEY_MINUTES", "8"))
    steps = int(os.environ.get("SURVEY_STEPS", "40"))
    out = Path(__file__).resolve().parents[2] / "scouting" / "survey" / time.strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    model = Model()
    started = time.time()

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=os.environ.get("HEADLESS") == "1")
        tabs = {}
        for a in scouting["access"]:  # every site gets a tab, so each knows the others' addresses
            page = await (await browser.new_context(viewport={"width": 1280, "height": 800})).new_page()
            await page.goto(a["url"], wait_until="domcontentloaded")
            tabs[a["siteId"]] = Tab(a["siteId"], page)

        async def one(site: str) -> tuple[str, dict]:
            lines: list[str] = []

            def log(text):
                lines.append(f"[{time.time() - started:6.1f}s] {text}")
                print(lines[-1], flush=True)

            task = os.environ.get("SURVEY_TASK") or SURVEY_TASK
            agent = BrowsingAgent(model, {site: tabs[site]}, credentials, task, context, name=site,
                                  deadline=time.time() + minutes * 60, max_steps=steps, log=log)
            t0 = time.time()
            report = await agent.run()
            (out / f"{site}.md").write_text(report + "\n\n# Steps\n" + "\n".join(lines) + "\n")
            actions = [l for l in lines if re.search(r"\] \w+ \{", l)]
            return site, {
                "seconds": round(time.time() - t0),
                "actions": len(actions),
                "finished": any("finished after" in l for l in lines),
                "notes": len(agent.notes),
                "notes_with_source": sum("source: (not given)" not in n for n in agent.notes),
                "refused_foreign": agent.refusals,
                "relogins": agent.relogins,
                "stalls": agent.stalls,
            }

        results = dict(await asyncio.gather(*(one(a["siteId"]) for a in access)))
        await browser.close()

    summary = json.dumps(results, indent=1)
    (out / "stats.json").write_text(summary)
    print(f"\nSurvey done in {time.time() - started:.0f}s; {model.usage()}\n{summary}\nReports: {out}", flush=True)
    return results


if __name__ == "__main__":
    unittest.main()
