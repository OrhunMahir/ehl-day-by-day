"""Browser action regressions with invented HTML only; every request is intercepted locally."""
import asyncio
import json
import re
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from browser import Tab  # noqa: E402
from browsing import BrowsingAgent  # noqa: E402
from playwright.async_api import TimeoutError as PlaywrightTimeoutError, async_playwright  # noqa: E402

SITE = "https://actions.example.test"


def element_id(view, label):
    for line in view.splitlines():
        if label in line:
            found = re.match(r"\[([\w.]+)\]", line)
            if found:
                return found.group(1)
    raise AssertionError(f"No element {label!r} in {view!r}")


def call(number, name, args):
    function = type("Function", (), {"name": name, "arguments": json.dumps(args)})()
    return type("Call", (), {"id": str(number), "function": function})()


class BrowserActionTest(unittest.TestCase):
    def run_browser(self, test, routes=None):
        async def run():
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(headless=True)
                try:
                    context = await browser.new_context()

                    async def serve(route):
                        path = route.request.url.removeprefix(SITE)
                        body = (routes or {}).get(path, "<html><body>Empty fixture</body></html>")
                        await route.fulfill(status=200, content_type="text/html", body=body)

                    await context.route("**/*", serve)
                    page = await context.new_page()
                    await page.goto(SITE + "/")
                    tab = Tab("fixture", page, log=lambda *_: None)
                    await test(page, tab)
                finally:
                    await browser.close()
        asyncio.run(run())

    def test_same_model_batch_keeps_selected_node_after_dialog_closes(self):
        async def test(page, tab):
            await page.set_content("""<script>window.events=[]</script>
                <div role="dialog"><button onclick="events.push('close');this.parentElement.remove()">Close</button></div>
                <button onclick="events.push('record-one')">Record one</button>
                <button onclick="events.push('record-two')">Record two</button>""")
            view = await tab.observe()
            close, record = element_id(view, '"Close"'), element_id(view, '"Record one"')

            class BatchModel:
                n = 0

                async def chat(self, messages, tools=None, **kwargs):
                    self.n += 1
                    calls = ([call(1, "click", {"element": close}), call(2, "click", {"element": record})]
                             if self.n == 1 else [call(3, "finish", {"report": "FOUND: selected record"})])
                    return type("Message", (), {"content": "", "tool_calls": calls})()

            agent = BrowsingAgent(BatchModel(), {"fixture": tab}, {}, "Read a record", "", "test",
                                  time.time() + 120, log=lambda *_: None)
            await agent.run()
            self.assertEqual(await page.evaluate("events"), ["close", "record-one"])
        self.run_browser(test)

    def test_replacement_does_not_reuse_old_number_or_handle(self):
        async def test(page, tab):
            await page.set_content('<button onclick="window.clicked=true">Original</button>')
            old_id = element_id(await tab.observe(), '"Original"')
            old_handle = await tab.resolve_target(old_id)
            await tab.goto(SITE + "/replacement")
            new_id = element_id(tab.last_text, '"Replacement"')
            self.assertNotEqual(old_id, new_id)
            started = time.monotonic()
            with self.assertRaisesRegex(ValueError, "Stale"):
                await tab.click(old_id)
            with self.assertRaisesRegex(ValueError, "Stale|stale"):
                await tab.click_target(old_handle)
            self.assertLess(time.monotonic() - started, 3)
            self.assertIsNone(await page.evaluate("window.clicked || null"))
        self.run_browser(test, {"/replacement": '<button onclick="window.clicked=true">Replacement</button>'})

    def test_frame_identity_survives_removal_of_an_earlier_frame(self):
        async def test(page, tab):
            await page.set_content("""<script>window.events=[]</script>
              <iframe srcdoc="<button onclick=&quot;parent.events.push('first')&quot;>First frame</button>"></iframe>
              <iframe srcdoc="<button onclick=&quot;parent.events.push('second')&quot;>Second frame</button>"></iframe>""")
            view = await tab.observe()
            first = element_id(view, '"First frame"')
            second = element_id(view, '"Second frame"')
            await page.locator("iframe").first.evaluate("el => el.remove()")
            view = await tab.observe()
            self.assertEqual(element_id(view, '"Second frame"'), second)
            with self.assertRaisesRegex(ValueError, "Stale"):
                await tab.click(first)
            await tab.click(second)
            self.assertEqual(await page.evaluate("events"), ["second"])
        self.run_browser(test)

    def test_collect_pages_follows_selected_control_after_new_control_is_inserted(self):
        async def test(page, tab):
            await page.set_content("""<script>window.events=[]</script><div id="rows">Row one</div>
              <button onclick="events.push('record')">Read record</button>
              <button onclick="events.push('next');document.querySelector('#rows').textContent='Row two';this.remove()">Next page</button>""")
            selected = element_id(await tab.observe(), '"Next page"')
            await page.evaluate("""() => {
                const b=document.createElement('button');b.textContent='New hint';document.body.prepend(b);
            }""")
            result = await tab.collect_pages(selected, max_pages=2)
            self.assertIn("Row one", result)
            self.assertIn("Row two", result)
            self.assertEqual(await page.evaluate("events"), ["next"])
        self.run_browser(test)

    def test_disabled_or_modal_covered_control_never_receives_a_dom_click(self):
        async def test(page, tab):
            await page.set_content("""<script>window.events=[]</script>
              <button onclick="events.push('background')">Background</button>
              <div role="dialog" style="position:fixed;inset:0;background:white">
                <button disabled onclick="events.push('disabled')">Accept</button>
              </div>""")
            view = await tab.observe()
            started = time.monotonic()
            with self.assertRaisesRegex(ValueError, "disabled"):
                await tab.click(element_id(view, '"Accept"'))
            with self.assertRaisesRegex(ValueError, "dialog"):
                await tab.click(element_id(view, '"Background"'))
            self.assertLess(time.monotonic() - started, 3)
            self.assertEqual(await page.evaluate("events"), [])
        self.run_browser(test)

    def test_physical_coverage_is_an_error_without_fallback(self):
        async def test(page, tab):
            await page.set_content("""<script>window.events=[]</script>
              <button onclick="events.push('covered')">Covered</button>
              <div style="position:fixed;inset:0;background:white"></div>""")
            target = element_id(await tab.observe(), '"Covered"')
            with patch("browser.ACTION_MILLISECONDS", 250):
                with self.assertRaises(PlaywrightTimeoutError):
                    await tab.click(target)
            self.assertEqual(await page.evaluate("events"), [])
        self.run_browser(test)

    def test_exact_labels_reject_ambiguity_and_checkbox_range_use_native_actions(self):
        async def test(page, tab):
            await page.set_content("""<p>Next helps you continue</p><button>Next</button><button>Next</button>
              <label>Agree<input type="checkbox"></label><input type="range" min="0" max="10" value="0">""")
            view = await tab.observe()
            with self.assertRaisesRegex(ValueError, "Ambiguous"):
                await tab.resolve_target("Next")
            await tab.fill("Agree", "true")
            await tab.fill(element_id(view, "input(range)"), "10")
            self.assertTrue(await page.locator('input[type="checkbox"]').is_checked())
            self.assertEqual(await page.locator('input[type="range"]').input_value(), "10")
        self.run_browser(test)

    def test_background_read_preserves_same_origin_session_and_reports_new_gate(self):
        reader = """<script>
          if(sessionStorage.getItem('accepted') === 'yes') document.write('<p>Requirement: 27 mm</p>');
          else document.write('<div role="dialog"><button>Accept document</button></div>');
        </script>"""

        async def test(page, tab):
            await page.evaluate("sessionStorage.setItem('accepted','yes')")
            result = await tab.read_pages([SITE + "/reader"])
            self.assertIn("Requirement: 27 mm", result)
            self.assertNotIn("ACCESS GATE", result)
            result = await tab.read_pages([SITE + "/gate"])
            self.assertIn("ACCESS GATE", result)
            self.assertIn("incomplete", result)
            self.assertIn("Use goto", result)
            self.assertEqual(page.url, SITE + "/")
        self.run_browser(test, {"/reader": reader, "/gate": '<div role="dialog"><button>Continue</button></div>'})

    def test_submission_can_disable_recovery_and_post_is_never_replayed(self):
        async def test(page, tab):
            await page.set_content('<input value="partly completed">')
            tab.allow_recovery = False
            tab.last_status = 503
            tab.loading = (SITE + "/", "GET", time.monotonic() - 100)
            self.assertFalse(await tab._unstick(force=True))
            await tab.observe()
            self.assertEqual(await page.locator("input").input_value(), "partly completed")
            tab.allow_recovery = True
            tab.last_status = 200
            tab.loading = (SITE + "/save", "POST", time.monotonic() - 100)
            self.assertFalse(await tab._unstick(force=True))
            self.assertEqual(await page.locator("input").input_value(), "partly completed")
            tab.loading = None
            tab.last_method, tab.last_status = "POST", 503
            self.assertFalse(await tab._unstick(force=True))
            await tab._retry_server_error()
            self.assertEqual(await page.locator("input").input_value(), "partly completed")
        self.run_browser(test)

    def test_cloned_marker_cannot_impersonate_an_observed_node(self):
        async def test(page, tab):
            await page.set_content('<button onclick="window.clicked=true">Original</button>')
            selected = element_id(await tab.observe(), '"Original"')
            await page.locator("button").evaluate("el => el.replaceWith(el.cloneNode(true))")
            with self.assertRaisesRegex(ValueError, "copied"):
                await tab.click(selected)
            self.assertIsNone(await page.evaluate("window.clicked || null"))
            new = element_id(await tab.observe(), '"Original"')
            self.assertNotEqual(new, selected)
            await tab.click(new)
            self.assertTrue(await page.evaluate("window.clicked"))
        self.run_browser(test)

    def test_session_storage_transfer_to_isolated_context_is_origin_scoped(self):
        async def test(page, tab):
            await page.evaluate("sessionStorage.setItem('permission','granted')")
            other = await page.context.browser.new_context()
            try:
                async def serve(route):
                    await route.fulfill(status=200, content_type="text/html", body="<p>Transferred session fixture</p>")
                await other.route("**/*", serve)
                worker = await other.new_page()
                self.assertFalse(await tab.copy_session_to(worker, "https://elsewhere.example.test/"))
                self.assertTrue(await tab.copy_session_to(worker, SITE + "/worker"))
                await worker.goto("https://elsewhere.example.test/")
                self.assertIsNone(await worker.evaluate("sessionStorage.getItem('permission')"))
                await worker.goto(SITE + "/worker")
                self.assertEqual(await worker.evaluate("sessionStorage.getItem('permission')"), "granted")
            finally:
                await other.close()
        self.run_browser(test)

    def test_hung_get_can_be_recovered_by_fresh_observation(self):
        async def test(page, tab):
            requests = []

            async def slow_first(route):
                requests.append(route.request.method)
                if len(requests) == 1:
                    await asyncio.sleep(10)
                await route.fulfill(status=200, content_type="text/html", body="<p>Recovered document</p>")

            await page.route(SITE + "/slow", slow_first)
            await page.set_content('<a href="/slow">Read selected document</a>')
            await tab.observe()
            await page.get_by_role("link").click(no_wait_after=True)
            await asyncio.sleep(0.1)
            with patch("browser.STUCK_SECONDS", 0.05), patch("browser.LOAD_SECONDS", 0.5):
                view = await tab.observe()
            self.assertIn("Recovered document", view)
            self.assertEqual(requests, ["GET", "GET"])
        self.run_browser(test)


if __name__ == "__main__":
    unittest.main()
