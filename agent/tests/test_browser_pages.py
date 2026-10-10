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


def tiny_pdf(text: str) -> bytes:
    """A one-page PDF whose page shows `text` (invented content)."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
               b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
               b"/Resources << /Font << /F1 5 0 R >> >> >>",
               b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offsets = b"%PDF-1.4\n", []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    return out + b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)


def tiny_zip() -> bytes:
    import io
    import zipfile
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("positions.csv", "position;quantity\n1;12\n2;7\n")
        archive.writestr("notes/readme.txt", "Quantities are in pieces.")
    return buffer.getvalue()


class FakeSite:
    """A tiny website: a sign-in form, pages behind a session cookie, a page that fails once with 503."""

    def __init__(self):
        self.fail_next = 0
        self.hang_next = 0  # requests of /slow that get no answer (the server leaves them hanging)
        self.hits: dict[str, int] = {}

    async def handle(self, route):
        request = route.request
        path = request.url[len(SITE):] or "/"
        self.hits[path] = self.hits.get(path, 0) + 1
        signed_in = "session=ok" in (await request.all_headers()).get("cookie", "")
        if path.startswith("/login") and request.method == "POST":
            # answered in place: Chromium does not follow redirects fulfilled for a form POST
            return await route.fulfill(status=200, content_type="text/html", headers={"set-cookie": "session=ok; Path=/"},
                                       body='<h1>Orders</h1><a href="/orders/2">Order 2</a>')
        if path.startswith("/slow") and self.hang_next:
            self.hang_next -= 1
            await asyncio.sleep(20)
            try:
                await route.fulfill(status=200, content_type="text/html", body="<h1>Too late</h1>")
            except Exception:  # noqa: BLE001 - the page has moved on
                pass
            return
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
            "/slow": "<h1>Inbox</h1><p>3 messages</p>",
            "/to-slow": '<h1>Start</h1><a href="/slow">Inbox</a>',
            "/steps": '<h1>Step 1</h1><p>Changes are only saved with "Next".</p><button onclick="document.body.'
                      'innerHTML = \'<h1>Step 2</h1>\'">Next</button><button onclick="document.body.innerHTML = '
                      '\'<h1>Step 0</h1>\'">Back</button>',
            "/secret": '<h1>Profile</h1><label>Password <input type="password" name="pw" value="hunter2-invented"></label>'
                       '<label>Other <input type="password" name="pw2"></label>',
            "/sign": '<h1>Sign the offer</h1><form><label>Signing code <input type="password" name="code"></label>'
                     '<button>Sign</button></form>',
            "/start-sign": '<h1>Offer</h1><a href="/sign">Continue to signing</a>',
            "/form": '<h1>Order form</h1><a href="/picker" target="_blank">Search help</a>',
            "/picker": '<h1>Pick an item</h1><button onclick="window.close()">Take Valve</button>',
            "/modern": '<h1>Stock</h1><div role="grid"><div role="row"><span role="columnheader">Item</span>'
                       '<span role="columnheader">Qty</span></div><div role="row"><span role="gridcell">Valve</span>'
                       '<span role="gridcell">14</span></div></div><div role="combobox" tabindex="0">Plant 2</div>'
                       '<price-card></price-card><script>customElements.define("price-card", class extends '
                       'HTMLElement { connectedCallback() { this.attachShadow('
                       '{mode: "open"}).innerHTML = "<p>Unit price 4.20 EUR</p><button>Details</button>"; } });'
                       '</script>',
            "/viewer": '<h1>Viewer</h1><details><summary>Open document</summary>'
                       '<iframe loading="lazy" src="/doc"></iframe></details>',
            "/doc": "<h1>Document</h1><p>Clause 7 applies</p>",
            "/more": '<h1>Items</h1><ul id="items"><li>Item 1</li></ul><button id="more">Load more</button><script>'
                     'let n = 1; document.getElementById("more").onclick = () => { n++; const li = '
                     'document.createElement("li"); li.textContent = "Item " + n; '
                     'document.getElementById("items").append(li); if (n >= 3) document.getElementById("more")'
                     '.remove(); };</script>',
        }
        if path == "/files.zip":
            return await route.fulfill(status=200, content_type="application/zip", body=tiny_zip(),
                                       headers={"content-disposition": 'attachment; filename="files.zip"'})
        if path == "/spec.pdf":
            return await route.fulfill(status=200, content_type="application/pdf", body=tiny_pdf("Pressure 160 bar"))
        if path.startswith("/list"):
            n = int(path.split("page=")[1]) if "page=" in path else 1
            nxt = f'<a href="/list?page={n + 1}">Next page</a>' if n < 3 else ""
            body = f"<h1>Messages</h1><p>Footer text</p><table><tr><td>Message {n}a</td></tr><tr><td>Message {n}b</td></tr></table>{nxt}"
            return await route.fulfill(status=200, content_type="text/html", body=body)
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

    def test_a_page_the_server_leaves_unanswered_is_requested_again(self):
        import browser

        async def check(tab, site, context):
            await self.agent(tab)._run_tool("login", {})
            site.hang_next = 1
            started = time.time()
            by_goto = await tab.goto(f"{SITE}/slow")
            took = time.time() - started
            site.hang_next = 1
            await tab.goto(f"{SITE}/to-slow")
            await tab.page.click("text=Inbox", no_wait_after=True)  # the click starts a load that never answers
            await asyncio.sleep(1.5)
            by_click = await tab.observe()
            return by_goto, took, by_click
        old = browser.LOAD_SECONDS, browser.STUCK_SECONDS
        browser.LOAD_SECONDS, browser.STUCK_SECONDS = 3, 1
        try:
            by_goto, took, by_click = self.run_async(check)
        finally:
            browser.LOAD_SECONDS, browser.STUCK_SECONDS = old
        self.assertIn("3 messages", by_goto)
        self.assertLess(took, 12)
        self.assertIn("3 messages", by_click)
        self.assertIn("requested again", by_click)

    def test_a_button_named_by_its_text_is_the_button_not_a_hint(self):
        async def check(tab, site, context):
            await self.agent(tab)._run_tool("login", {})
            await tab.goto(f"{SITE}/steps")
            return await tab.click("Next")
        self.assertIn("Step 2", self.run_async(check))

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

    def test_collect_pages_follows_next_links(self):
        async def check(tab, site, context):
            await self.agent(tab)._run_tool("login", {})
            view = await tab.goto(f"{SITE}/list?page=1")
            import re
            aid = re.search(r'\[(\d+)\] link "Next page"', view).group(1)
            return await self.agent(tab)._run_tool("collect_pages", {"element": aid})
        view = self.run_async(check)
        self.assertIn("Collected 3 views", view)
        for n in (1, 2, 3):
            self.assertIn(f"Message {n}a", view)
        self.assertEqual(view.count("Footer text"), 1)  # repeated page chrome is shown once

    def test_collect_pages_presses_load_more(self):
        async def check(tab, site, context):
            await self.agent(tab)._run_tool("login", {})
            view = await tab.goto(f"{SITE}/more")
            import re
            aid = re.search(r'\[(\d+)\] button "Load more"', view).group(1)
            return await self.agent(tab)._run_tool("collect_pages", {"element": aid})
        view = self.run_async(check)
        for n in (1, 2, 3):
            self.assertIn(f"Item {n}", view)

    def test_a_frame_that_loads_late_is_read_after_opening_its_section(self):
        async def check(tab, site, context):
            await self.agent(tab)._run_tool("login", {})
            view = await tab.goto(f"{SITE}/viewer")
            import re
            aid = re.search(r'\[(\d+)\] summary "Open document"', view).group(1)
            return await self.agent(tab)._run_tool("click", {"element": aid})
        view = self.run_async(check)
        self.assertIn("Clause 7 applies", view)

    def test_zip_and_pdf_downloads_are_read(self):
        async def check(tab, site, context):
            agent = self.agent(tab)
            await agent._run_tool("login", {})
            return (await agent._run_tool("download", {"url": "/files.zip"}),
                    await agent._run_tool("download", {"url": "/spec.pdf"}))
        zipped, pdf = self.run_async(check)
        self.assertIn("positions.csv", zipped)
        self.assertIn("1;12", zipped)
        self.assertIn("notes/readme.txt", zipped)
        self.assertIn("Quantities are in pieces.", zipped)
        self.assertIn("Pressure 160 bar", pdf)

    def test_aria_grids_shadow_dom_and_custom_controls_are_read(self):
        async def check(tab, site, context):
            await self.agent(tab)._run_tool("login", {})
            return await tab.goto(f"{SITE}/modern")
        view = self.run_async(check)
        self.assertIn("| Item | Qty |", view)
        self.assertIn("| Valve | 14 |", view)
        self.assertRegex(view, r'\[\d+\] combobox "Plant 2"')
        self.assertIn("Unit price 4.20 EUR", view)
        self.assertRegex(view, r'\[\d+\] button "Details"')

    def test_read_pages_reads_several_pages_in_one_step(self):
        async def check(tab, site, context):
            agent = self.agent(tab)
            await agent._run_tool("login", {})
            Tab.homes["inbox"] = "https://inbox.example.test"  # another website of the run
            view = await agent._run_tool("read_pages", {"urls": ["/orders/2", f"{SITE}/list?page=2",
                                                                 "https://inbox.example.test/m/1"]})
            return agent, view
        agent, view = self.run_async(check)
        self.assertIn("Read 2 pages", view)
        self.assertIn("Total 17 units", view)
        self.assertNotIn("999", view)
        self.assertIn("Message 2a", view)
        self.assertNotRegex(view, r"\[x\d")
        self.assertIn('link "Next page" -> /list?page=3', view)
        self.assertEqual(agent.refusals, 1)

    def test_a_popup_that_closes_itself_returns_to_its_opener(self):
        async def check(tab, site, context):
            agent = self.agent(tab)
            await agent._run_tool("login", {})
            view = await tab.goto(f"{SITE}/form")
            import re
            view = await agent._run_tool("click", {"element": re.search(r'\[(\d+)\] link "Search help"', view).group(1)})
            await asyncio.sleep(1.5)  # the popup is picked up asynchronously
            view = await tab.observe()
            self.assertIn("Pick an item", view)
            view = await agent._run_tool("click", {"element": re.search(r'\[(\d+)\] button "Take Valve"', view).group(1)})
            await asyncio.sleep(0.5)
            after = await tab.observe()
            again = await tab.goto(f"{SITE}/orders")
            return view + after, again
        closing, again = self.run_async(check)
        self.assertIn("Order form", closing)
        self.assertIn("back on the page that opened it", closing)
        self.assertIn("Orders", again)

    def test_password_values_never_reach_the_page_view(self):
        async def check(tab, site, context):
            await self.agent(tab)._run_tool("login", {})
            return await tab.goto(f"{SITE}/secret")
        view = self.run_async(check)
        self.assertNotIn("hunter2-invented", view)
        self.assertIn("value=<filled>", view)
        self.assertIn("value=<empty>", view)

    def test_an_html_download_requests_the_address_once(self):
        async def check(tab, site, context):
            agent = self.agent(tab)
            await agent._run_tool("login", {})
            before = site.hits.get("/orders/2", 0)
            view = await agent._run_tool("download", {"url": "/orders/2"})
            return view, site.hits.get("/orders/2", 0) - before
        view, requests = self.run_async(check)
        self.assertIn("Total 17 units", view)
        self.assertNotIn("999", view)
        self.assertEqual(requests, 1)

    def test_a_code_field_is_not_taken_for_an_expired_session(self):
        async def check(tab, site, context):
            agent = self.agent(tab)
            await agent._run_tool("login", {})
            view = await tab.goto(f"{SITE}/start-sign")
            import re
            aid = re.search(r'\[(\d+)\] link "Continue to signing"', view).group(1)
            view = await agent._run_tool("click", {"element": aid})
            return agent, view
        agent, view = self.run_async(check)
        self.assertEqual(agent.relogins, 0)
        self.assertIn("Sign the offer", view)


if __name__ == "__main__":
    unittest.main()
