"""A website that stops answering one browser session is opened again in a fresh one. An invented website served by
Playwright's request routing: no network, no model. Needs Chromium (`uv run playwright install chromium`)."""
import asyncio
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import browser  # noqa: E402
from browser import Tab  # noqa: E402

SITE = "https://portal.example.test"


class Session:
    """One browser session's view of the website: it answers, or it leaves every request hanging."""

    def __init__(self, name: str):
        self.name, self.dead, self.requests = name, False, 0

    async def handle(self, route):
        self.requests += 1
        if self.dead:
            await asyncio.sleep(8)
            try:
                await route.abort()
            except Exception:  # noqa: BLE001 - the page has moved on
                pass
            return
        path = route.request.url[len(SITE):] or "/"
        await route.fulfill(status=200, content_type="text/html",
                            body=f"<h1>Tenders</h1><p>{self.name} session, page {path}</p>")


class FreshSessionTest(unittest.TestCase):
    def run_async(self, coroutine_function):
        async def wrapper():
            from playwright.async_api import async_playwright
            async with async_playwright() as playwright:
                chromium = await playwright.chromium.launch(headless=True)
                first, later = Session("first"), []
                context = await chromium.new_context()
                await context.route(f"{SITE}/**", first.handle)
                page = await context.new_page()
                await page.goto(f"{SITE}/")
                Tab.homes.clear()
                tab = Tab("portal", page, log=lambda *_: None)

                async def new_session_page():
                    session = Session(f"fresh {len(later) + 1}")
                    later.append(session)
                    fresh = await chromium.new_context()
                    await fresh.route(f"{SITE}/**", session.handle)
                    return await fresh.new_page()
                tab._new_session_page = new_session_page
                try:
                    return await coroutine_function(tab, first, later)
                finally:
                    await chromium.close()
        old = browser.LOAD_SECONDS, browser.STUCK_SECONDS
        browser.LOAD_SECONDS, browser.STUCK_SECONDS = 1.5, 1
        try:
            return asyncio.run(wrapper())
        finally:
            browser.LOAD_SECONDS, browser.STUCK_SECONDS = old

    def test_a_website_that_stops_answering_is_opened_again_in_a_fresh_session(self):
        async def check(tab, first, later):
            first.dead = True
            started = time.time()
            view = await tab.goto(f"{SITE}/tenders")  # asked three times, never answered
            return view, time.time() - started, tab.fresh_sessions, len(later), await tab.goto(f"{SITE}/tenders")
        view, took, fresh_sessions, sessions, tenders = self.run_async(check)
        self.assertNotIn("first session", view)
        self.assertIn("fresh 1 session, page /", view)  # the website's start page, in the new session
        self.assertIn("fresh browser session: sign in again", view)
        self.assertEqual((fresh_sessions, sessions), (1, 1))
        self.assertIn("fresh 1 session, page /tenders", tenders)
        self.assertLess(took, 20)

    def test_one_unanswered_request_is_only_asked_again(self):
        async def check(tab, first, later):
            first.dead = True

            async def revive():
                await asyncio.sleep(1.2)
                first.dead = False
            reviving = asyncio.ensure_future(revive())
            view = await tab.goto(f"{SITE}/tenders")  # the first request hangs, the second is answered
            await reviving
            return view, await tab.observe(), tab.fresh_sessions, tab.unanswered
        view, again, fresh_sessions, unanswered = self.run_async(check)
        self.assertIn("first session, page /tenders", view)
        self.assertIn("first session, page /tenders", again)
        self.assertEqual((fresh_sessions, unanswered), (0, 0))

    def test_fresh_sessions_have_a_limit(self):
        async def check(tab, first, later):
            first.dead = True
            for _ in range(browser.FRESH_SESSIONS + 2):
                await tab.goto(f"{SITE}/tenders")
                await tab.observe()
                for session in later:
                    session.dead = True
            return tab.fresh_sessions, len(later)
        self.assertEqual(self.run_async(check), (browser.FRESH_SESSIONS, browser.FRESH_SESSIONS))

    def test_a_fresh_session_goes_to_another_browser_of_the_run(self):
        class Browser:
            def __init__(self, connected=True):
                self.connected = connected

            def is_connected(self):
                return self.connected

        own, gone, other, further = Browser(), Browser(connected=False), Browser(), Browser()
        tab = Tab.__new__(Tab)
        tab.page = type("Page", (), {"context": type("Context", (), {"browser": own})()})()
        old = Tab.browsers
        try:
            Tab.browsers = [own, gone, other, further]
            tab.fresh_sessions = 0
            first = tab._other_browser()
            tab.fresh_sessions = 1
            second = tab._other_browser()
            Tab.browsers = [own]
            alone = tab._other_browser()
        finally:
            Tab.browsers = old
        self.assertEqual((first, second, alone), (other, further, own))


if __name__ == "__main__":
    unittest.main()
