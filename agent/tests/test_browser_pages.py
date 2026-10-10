"""Stream J browser tests on an invented website, served by Playwright's request routing: no network, no model.
They need Chromium (`uv run playwright install chromium`)."""
import asyncio
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from browser import Tab  # noqa: E402
from browsing import BrowsingAgent  # noqa: E402

SITE = "https://shop.example.test"
LOGIN = """<form method="post" action="/login"><label>User <input name="u"></label>
<label>Password <input name="p" type="password"></label><button>Sign in</button></form>"""


class FakeSite:
    """A tiny website: a sign-in form, pages behind a session cookie, a page that fails once with 503."""

    def __init__(self):
        self.fail_next = 0

    async def handle(self, route):
        request = route.request
        path = request.url[len(SITE):] or "/"
        signed_in = "session=ok" in (await request.all_headers()).get("cookie", "")
        if path.startswith("/login") and request.method == "POST":
            # answered in place: Chromium does not follow redirects fulfilled for a form POST
            return await route.fulfill(status=200, content_type="text/html", headers={"set-cookie": "session=ok; Path=/"},
                                       body='<h1>Orders</h1><a href="/orders/2">Order 2</a>')
        if path.startswith("/flaky") and self.fail_next:
            self.fail_next -= 1
            return await route.fulfill(status=503, content_type="text/html", body="<h1>Service unavailable</h1>")
        if not signed_in:
            return await route.fulfill(status=200, content_type="text/html", body=f"<h1>Sign in</h1>{LOGIN}")
        pages = {
            "/orders": '<h1>Orders</h1><a href="/orders/2">Order 2</a>',
            "/orders/2": '<h1>Order 2</h1><p>Total 17 units</p>'
                         '<span hidden aria-hidden="true">Total 999 units</span>'
                         '<button aria-describedby="tip">Status</button><span role="tooltip" id="tip" '
                         'style="display:none">approved</span>',
            "/popup": '<h1>Catalogue</h1><p>Item list</p><div role="dialog" aria-modal="true"><p>Quick survey</p>'
                      '<button>Close</button></div>',
            "/flaky": "<h1>Report</h1><p>All good</p>",
        }
        body = pages.get(path.split("?")[0], "<h1>Not found</h1>")
        await route.fulfill(status=200, content_type="text/html", body=body)


class BrowserPagesTest(unittest.TestCase):
    def run_async(self, coroutine_function):
        async def wrapper():
            from playwright.async_api import async_playwright
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(headless=True)
                context = await browser.new_context()
                site = FakeSite()
                await context.route(f"{SITE}/**", site.handle)
                page = await context.new_page()
                await page.goto(f"{SITE}/orders")
                Tab.homes.clear()
                tab = Tab("shop", page)
                try:
                    return await coroutine_function(tab, site, context)
                finally:
                    await browser.close()
        return asyncio.run(wrapper())

    @staticmethod
    def agent(tab):
        return BrowsingAgent(None, {"shop": tab}, {"shop": {"username": "me", "password": "secret"}}, "task",
                             "context", name="t", deadline=time.time() + 60, log=lambda *_: None)

    def test_hidden_decoys_are_dropped_and_tooltips_resolved(self):
        async def check(tab, site, context):
            agent = self.agent(tab)
            await agent._run_tool("login", {})
            return await tab.goto(f"{SITE}/orders/2")
        view = self.run_async(check)
        self.assertIn("Total 17 units", view)
        self.assertNotIn("999", view)
        self.assertIn("tooltip: approved", view)

    def test_open_dialog_is_shown_first(self):
        async def check(tab, site, context):
            await self.agent(tab)._run_tool("login", {})
            return await tab.goto(f"{SITE}/popup")
        view = self.run_async(check)
        body = view.split("\n", 2)[2]  # after the URL and title lines
        self.assertTrue(body.startswith("=== OPEN DIALOG"), body[:200])
        self.assertLess(body.index("Quick survey"), body.index("Catalogue"))
        self.assertEqual(view.count("Quick survey"), 1)

    def test_a_server_error_page_is_reloaded(self):
        async def check(tab, site, context):
            await self.agent(tab)._run_tool("login", {})
            site.fail_next = 1
            return await tab.goto(f"{SITE}/flaky")
        view = self.run_async(check)
        self.assertIn("All good", view)

    def test_expired_session_signs_in_again_and_returns(self):
        async def check(tab, site, context):
            agent = self.agent(tab)
            first = await agent._run_tool("login", {})
            self.assertIn("Orders", first)
            await agent._run_tool("goto", {"url": "/orders/2"})
            await context.clear_cookies()  # the session expires
            view = await agent._run_tool("goto", {"url": "/orders/2"})
            return agent, view, tab.page.url
        agent, view, url = self.run_async(check)
        self.assertEqual(agent.relogins, 1)
        self.assertIn("session had expired", view)
        self.assertIn("Total 17 units", view)
        self.assertTrue(url.endswith("/orders/2"), url)


if __name__ == "__main__":
    unittest.main()
