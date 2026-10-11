"""Stream J unit tests: a sub-agent stays on its own website, and its notes carry their source. Invented hosts only;
no browser, no model, no network."""
import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from browser import Tab, origin  # noqa: E402
from browsing import BrowsingAgent  # noqa: E402


class FakePage:
    def __init__(self, url, title="A page"):
        self.url, self._title = url, title
        self.context = self

    def on(self, *args):  # Tab registers its event handlers here
        pass

    async def title(self):
        return self._title


def agent_for(*tabs):
    return BrowsingAgent(None, {t.site_id: t for t in tabs}, {}, "task", "context", name="t", deadline=0)


class OriginTest(unittest.TestCase):
    def test_origin(self):
        self.assertEqual(origin("https://Shop.Example.test:8443/a/b?c=1"), "https://shop.example.test:8443")
        self.assertEqual(origin("about:blank"), "")
        self.assertEqual(origin("/relative/path"), "")


class ForeignSiteTest(unittest.TestCase):
    def setUp(self):
        Tab.homes.clear()
        self.portal = Tab("portal", FakePage("https://portal.example.test/login"))
        self.inbox = Tab("inbox", FakePage("https://inbox.example.test/"))

    def test_other_site_of_the_run_is_refused(self):
        agent = agent_for(self.portal)
        refusal = asyncio.run(agent._run_tool("goto", {"url": "https://inbox.example.test/messages/7"}))
        self.assertTrue(refusal.startswith("Refused:"))
        self.assertIn("inbox", refusal)
        self.assertEqual(agent.refusals, 1)

    def test_download_from_other_site_is_refused(self):
        agent = agent_for(self.portal)
        refusal = asyncio.run(agent._run_tool("download", {"url": "https://inbox.example.test/export.zip"}))
        self.assertTrue(refusal.startswith("Refused:"))

    def test_own_site_relative_and_unknown_hosts_pass_the_guard(self):
        agent = agent_for(self.portal)
        self.assertEqual(agent._foreign_site(self.portal, "/tenders?page=2"), "")
        self.assertEqual(agent._foreign_site(self.portal, "https://portal.example.test/docs/1"), "")
        self.assertEqual(agent._foreign_site(self.portal, "https://files.cdn.example.test/spec.pdf"), "")

    def test_an_agent_with_both_sites_may_use_both(self):  # the submission agent works portal and inbox
        agent = agent_for(self.portal, self.inbox)
        self.assertEqual(agent._foreign_site(self.portal, "https://inbox.example.test/"), "")


class NoteSourceTest(unittest.TestCase):
    def test_note_is_stamped_with_site_page_and_source(self):
        Tab.homes.clear()
        tab = Tab("portal", FakePage("https://portal.example.test/doc/3", title="Spec, revision C"))
        agent = agent_for(tab)
        asyncio.run(agent._run_tool("note", {"facts": "Bore 42 mm", "source": "Spec rev C, 2030-01-02, status valid"}))
        asyncio.run(agent._run_tool("note", {"facts": "Stroke 99 mm"}))
        first, second = agent.notes
        self.assertIn("Bore 42 mm", first)
        self.assertIn("source: Spec rev C, 2030-01-02, status valid", first)
        self.assertIn('read on: portal · page "Spec, revision C" · https://portal.example.test/doc/3', first)
        self.assertIn("source: (not given)", second)
        report = agent._report("FOUND:\n- Bore 42 mm\nNOT FOUND:\n- nothing")
        self.assertIn("NOTES TAKEN WHILE BROWSING", report)
        self.assertIn("read on: portal", report)


    def test_structured_source_fields_are_kept_in_order(self):
        Tab.homes.clear()
        tab = Tab("inbox", FakePage("https://inbox.example.test/m/9", title="Approval"))
        agent = agent_for(tab)
        asyncio.run(agent._run_tool("note", {
            "facts": "Discount 3 %", "reference": "CASE-17-A", "sender": "boss@firm.test", "date": "2030-01-05",
            "status": "valid", "document": "message 9"}))
        self.assertIn("source: document: message 9; date: 2030-01-05; sender: boss@firm.test; status: valid; "
                      "reference: CASE-17-A", agent.notes[0])
        self.assertIn("CHECKED, NOT PRESENT", dict((t[0], t[1]) for t in __import__("browsing").TOOLS)["finish"])

class FakeCall:
    def __init__(self, n, name, arguments):
        self.id, self.type = f"call{n}", "function"
        self.function = type("F", (), {"name": name, "arguments": arguments})()


class FakeModel:
    """Browses forever with read_more (no page needed), then answers the tool-less wrap-up call."""

    def __init__(self, fail_wrap_up=False):
        self.seen, self.fail_wrap_up, self.n = [], fail_wrap_up, 0

    async def chat(self, messages, tools=None, **kwargs):
        self.seen.append([m.get("content", "") for m in messages if m["role"] == "user"])
        self.n += 1
        if tools is None:  # the wrap-up call
            if self.fail_wrap_up:
                raise RuntimeError("model down")
            return type("M", (), {"content": "FOUND:\n- Bore 42 mm — source: Spec rev C\nNOT FOUND:\n- stroke", "tool_calls": None})()
        return type("M", (), {"content": "", "tool_calls": [FakeCall(self.n, "read_more", '{"offset": 0}')]})()


class StuckWebsiteTest(unittest.TestCase):
    """An action that runs out of time, page views included, brings a fresh session at once; a page that is only
    slow needs two page loads in a row, and looking again at it is never counted."""

    class Reopening:
        site_id = "shop"

        def __init__(self):
            self.page = type("P", (), {"context": type("C", (), {"browser": None})()})()
            self.reopened = 0

        async def reopen(self, browser=None):
            self.reopened += 1
            return "[shop] URL: https://shop.invalid/\nSign in"

    STUCK = "Error: the action did not finish within 60 s; the page may be stuck. Use look to see the page."
    UNREAD = ("[shop] URL: https://shop.invalid/inbox\nTitle: \n--- frame 0 (https://shop.invalid/inbox) could not be "
              "read: it did not respond in time")
    SLOW = "[shop] URL: https://shop.invalid/inbox\nTitle: \n(still loading https://shop.invalid/inbox for 6 s: ...)"

    def agent(self, tab):
        agent = BrowsingAgent(FakeModel(), {"shop": tab}, {}, "task", "context", name="a", deadline=10 ** 10,
                              log=lambda *_: None)
        agent.last_tab = tab
        return agent

    def test_a_page_view_that_ran_out_of_time_brings_a_fresh_session_at_once(self):
        tab = self.Reopening()
        said = asyncio.run(self.agent(tab)._after_action("look", {}, self.STUCK))
        self.assertEqual(tab.reopened, 1)
        self.assertIn("fresh session", said)

    def test_a_view_of_a_page_that_did_not_respond_brings_a_fresh_session_at_once(self):
        tab = self.Reopening()
        asyncio.run(self.agent(tab)._after_action("goto", {"url": "/inbox"}, self.UNREAD))
        self.assertEqual(tab.reopened, 1)

    def test_a_slow_page_is_waited_for_and_two_slow_loads_in_a_row_bring_a_fresh_session(self):
        tab = self.Reopening()
        agent = self.agent(tab)

        async def steps():
            for _ in range(3):
                await agent._after_action("look", {}, self.SLOW)
            looked = tab.reopened
            await agent._after_action("goto", {"url": "/inbox"}, self.SLOW)
            once = tab.reopened
            await agent._after_action("goto", {"url": "/inbox"}, self.SLOW)
            return looked, once, tab.reopened
        self.assertEqual(asyncio.run(steps()), (0, 0, 1))


class OlderBoardUpdatesTest(unittest.TestCase):
    """An older board update keeps the start of its records and drops its stale list of what was needed."""

    def test_only_the_newest_update_keeps_its_list(self):
        import browsing
        older = ("Board update:\nRecorded by the other websites' assistants:\n- recorded by shop: " + "x" * 400 +
                 "\n\nStill needed for the bid (take on what your website can answer):\n- [ERP] search group G-1")
        messages = [{"role": "system", "content": "s"}, {"role": "user", "content": "task"},
                    {"role": "user", "content": older}, {"role": "tool", "tool_call_id": "c", "content": "a page"}]
        browsing._supersede_board_updates(messages)
        shortened = messages[2]["content"]
        self.assertTrue(shortened.startswith("Board update:\n- recorded by shop: xxx"))
        self.assertNotIn("Still needed", shortened)
        self.assertNotIn("search group G-1", shortened)
        self.assertLessEqual(len(shortened), 300)
        self.assertEqual([m["content"] for m in messages if m is not messages[2]], ["s", "task", "a page"])
        browsing._supersede_board_updates(messages)  # once shortened, it stays as it is
        self.assertEqual(messages[2]["content"], shortened)

    def test_an_update_without_records_shrinks_to_a_note(self):
        import browsing
        messages = [{"role": "user", "content": "Board update:\nStill needed for the bid:\n- [MAIL] the discount"}]
        browsing._supersede_board_updates(messages)
        self.assertEqual(messages[0]["content"], "Board update:\n" + browsing.OLD_UPDATE_NOTE)


class WrapUpTest(unittest.TestCase):
    def setUp(self):
        Tab.homes.clear()
        self.tab = Tab("portal", FakePage("https://portal.example.test/doc/3"))

    def run_agent(self, model, steps):
        import time
        agent = BrowsingAgent(model, {"portal": self.tab}, {}, "Find the bore", "context", name="t",
                              deadline=time.time() + 600, max_steps=steps, log=lambda *_: None)
        agent.notes.append("Bore 42 mm\n  source: Spec rev C")
        agent.tabs["portal"].observe = self._observe
        return agent, asyncio.run(agent.run())

    async def _observe(self, offset=0):
        return "[portal] URL: https://portal.example.test/doc/3\nA page"

    def test_out_of_steps_gives_a_found_and_not_found_report(self):
        model = FakeModel()
        _, report = self.run_agent(model, steps=8)
        self.assertIn("written from notes", report)
        self.assertIn("NOT FOUND", report)
        self.assertIn("Bore 42 mm", report)  # the raw notes are still appended

    def test_failed_wrap_up_still_returns_the_notes(self):
        _, report = self.run_agent(FakeModel(fail_wrap_up=True), steps=3)
        self.assertIn("no report could be written", report)
        self.assertIn("Bore 42 mm", report)

    def test_nudges_to_note_and_to_finish(self):
        model = FakeModel()
        self.run_agent(model, steps=8)
        last_browse = model.seen[-2]  # user messages the model saw on its last browsing call
        self.assertTrue(any("without saving" in m for m in last_browse))
        self.assertTrue(any("steps left" in m for m in last_browse))


class SlowModel:
    async def chat(self, messages, tools=None, **kwargs):
        await asyncio.sleep(3600)


class HangingModelTest(unittest.TestCase):
    def test_a_hanging_model_does_not_hold_the_agent_past_its_deadline(self):
        import time
        import browsing
        Tab.homes.clear()
        tab = Tab("portal", FakePage("https://portal.example.test/"))

        async def observe(offset=0):
            return "a page"
        tab.observe = observe
        agent = BrowsingAgent(SlowModel(), {"portal": tab}, {}, "task", "context", name="t",
                              deadline=time.time() + 0.2, log=lambda *_: None)
        agent.notes.append("Bore 42 mm")
        old = browsing.WRAP_UP_SECONDS
        browsing.WRAP_UP_SECONDS = 0.2  # keep the test fast
        try:
            started = time.time()
            report = asyncio.run(agent.run())
        finally:
            browsing.WRAP_UP_SECONDS = old
        self.assertLess(time.time() - started, 15)
        self.assertIn("no report could be written", report)
        self.assertIn("Bore 42 mm", report)


class HangingPageTest(unittest.TestCase):
    def test_a_hanging_browser_action_does_not_hold_the_agent(self):
        import time
        import browsing
        Tab.homes.clear()
        tab = Tab("portal", FakePage("https://portal.example.test/"))
        calls = {"n": 0}

        async def observe(offset=0):  # the first view works, every later one hangs
            calls["n"] += 1
            if calls["n"] > 1:
                await asyncio.sleep(3600)
            return "a page"

        async def reopen(browser=None):  # a view that runs out of time brings a fresh session, which hangs as well
            return "a fresh page"
        tab.observe, tab.reopen = observe, reopen
        tab.page.context.browser = None

        class LookingModel(FakeModel):
            async def chat(self, messages, tools=None, **kwargs):
                if tools is None:
                    return type("M", (), {"content": "FOUND:\nNOT FOUND:\n- everything", "tool_calls": None})()
                self.n += 1
                # the action's result, and after it the note that the website was opened again
                self.seen.append("\n".join(str(m.get("content") or "") for m in messages[-2:]))
                return type("M", (), {"content": "", "tool_calls": [FakeCall(self.n, "look", "{}")]})()
        model = LookingModel()
        old = browsing.ACTION_SECONDS
        browsing.ACTION_SECONDS = 0.2
        try:
            started = time.time()
            agent = BrowsingAgent(model, {"portal": tab}, {}, "task", "context", name="t",
                                  deadline=time.time() + 600, max_steps=3, log=lambda *_: None)
            report = asyncio.run(agent.run())
        finally:
            browsing.ACTION_SECONDS = old
        self.assertLess(time.time() - started, 10)
        self.assertTrue(any("did not finish" in m for m in model.seen))
        self.assertIn("NOT FOUND", report)


class StallingModelTest(unittest.TestCase):
    def test_a_stalled_model_call_is_retried(self):
        import time
        import browsing
        Tab.homes.clear()
        tab = Tab("portal", FakePage("https://portal.example.test/"))

        async def observe(offset=0):
            return "a page"
        tab.observe = observe

        class StallsOnce:
            def __init__(self):
                self.calls = 0

            async def chat(self, messages, tools=None, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    await asyncio.sleep(3600)  # the first request is stuck
                return type("M", (), {"content": "", "tool_calls": [FakeCall(self.calls, "finish", '{"report": "FOUND: x"}')]})()
        model = StallsOnce()
        old = browsing.CALL_SECONDS
        browsing.CALL_SECONDS = 0.2
        try:
            agent = BrowsingAgent(model, {"portal": tab}, {}, "task", "context", name="t",
                                  deadline=time.time() + 600, log=lambda *_: None)
            report = asyncio.run(agent.run())
        finally:
            browsing.CALL_SECONDS = old
        self.assertEqual(agent.stalls, 1)
        self.assertIn("FOUND: x", report)

class ForgetViewsTest(unittest.TestCase):
    def test_only_the_newest_view_stays_whole(self):
        import browsing
        views = [{"role": "tool", "tool_call_id": str(i), "content": f"[s] URL: page{i}\n" + "x" * 9000} for i in range(3)]
        messages = [{"role": "user", "content": "task"}] + views
        BrowsingAgent._forget_old_views(messages)
        self.assertTrue(messages[1]["content"].startswith("(older page view removed"))
        self.assertTrue(messages[2]["content"].endswith(browsing.SHORTENED))
        self.assertLess(len(messages[2]["content"]), 4200)
        self.assertEqual(len(messages[3]["content"]), len("[s] URL: page2\n") + 9000)


class HtmlTextTest(unittest.TestCase):
    def test_a_stray_closing_tag_does_not_hide_the_rest(self):
        from browser import html_to_text
        text = html_to_text("<p>Before</p></script><p>After 12 units</p><script>var x = 1;</script><p>End</p>")
        self.assertIn("After 12 units", text)
        self.assertIn("End", text)
        self.assertNotIn("var x", text)


class FinishLastTest(unittest.TestCase):
    def test_a_note_sent_with_finish_in_the_same_turn_is_kept(self):
        import time
        Tab.homes.clear()
        tab = Tab("portal", FakePage("https://portal.example.test/"))

        async def observe(offset=0):
            return "a page"
        tab.observe = observe

        class FinishThenNote:
            async def chat(self, messages, tools=None, **kwargs):
                return type("M", (), {"content": "", "tool_calls": [
                    FakeCall(1, "finish", '{"report": "FOUND: see notes"}'),
                    FakeCall(2, "note", '{"facts": "Bore 42 mm", "document": "Spec C"}')]})()
        agent = BrowsingAgent(FinishThenNote(), {"portal": tab}, {}, "task", "context", name="t",
                              deadline=time.time() + 600, log=lambda *_: None)
        report = asyncio.run(agent.run())
        self.assertIn("FOUND: see notes", report)
        self.assertIn("Bore 42 mm", report)
        self.assertIn("document: Spec C", report)


class ReadsThenRunsOut:
    """Reads a page, optionally notes it, reads on until the steps are used up, then writes the wrap-up."""

    def __init__(self, note_after_first=False):
        self.n, self.note_after_first, self.wrap_up = 0, note_after_first, None

    async def chat(self, messages, tools=None, **kwargs):
        self.n += 1
        if tools is None:
            self.wrap_up = (messages[0]["content"], kwargs)
            return type("M", (), {"content": "FOUND:\n- stroke", "tool_calls": None})()
        if self.n == 2 and self.note_after_first:
            return type("M", (), {"content": "", "tool_calls": [FakeCall(self.n, "note", '{"facts": "Stroke 77 mm"}')]})()
        return type("M", (), {"content": "", "tool_calls": [FakeCall(self.n, "read_more", '{"offset": 0}')]})()


class UnnotedReadsTest(unittest.TestCase):
    """What an agent read but had not noted when its time ran out still reaches the report."""

    def run_agent(self, model, steps, pages):
        import time
        Tab.homes.clear()
        tab = Tab("portal", FakePage("https://portal.example.test/spec"))

        async def observe(offset=0):
            return "a page"
        tab.observe = observe
        tab.read_more = lambda offset=0: pages.pop(0) if pages else "end of page"
        agent = BrowsingAgent(model, {"portal": tab}, {}, "Find the stroke", "context", name="t",
                              deadline=time.time() + 600, max_steps=steps, log=lambda *_: None)
        return agent, asyncio.run(agent.run())

    def test_a_page_read_last_and_not_noted_is_passed_on(self):
        model = ReadsThenRunsOut()
        _, report = self.run_agent(model, 1, ["| Requirement | Stroke |\n| R-1 | 77 mm |"])
        self.assertIn("LAST PAGES READ, NOT NOTED YET", report)
        self.assertIn("| R-1 | 77 mm |", report)
        prompt, kwargs = model.wrap_up
        self.assertIn("| R-1 | 77 mm |", prompt)  # the wrap-up may report it as found
        self.assertEqual(kwargs["max_tokens"], 1000)

    def test_only_the_last_results_are_kept_and_a_note_clears_them(self):
        _, report = self.run_agent(ReadsThenRunsOut(), 4, ["first", "second", "third", "fourth"])
        tail = report.split("LAST PAGES READ, NOT NOTED YET")[1]
        self.assertNotIn("second", tail)
        self.assertIn("third", tail)
        self.assertIn("fourth", tail)
        _, report = self.run_agent(ReadsThenRunsOut(note_after_first=True), 2, ["| R-1 | 77 mm |"])
        self.assertNotIn("LAST PAGES READ", report)  # read, then noted: the note carries it

    def test_a_finished_report_carries_no_unnoted_pages(self):
        Tab.homes.clear()
        agent = agent_for(Tab("portal", FakePage("https://portal.example.test/")))
        agent.unnoted = ["read_more -> something"]
        agent.finished = True
        self.assertNotIn("LAST PAGES READ", agent._report("FOUND: x"))


class QuickStallTest(unittest.TestCase):
    def test_a_model_without_thinking_is_retried_sooner(self):
        import time
        import browsing
        Tab.homes.clear()
        tab = Tab("portal", FakePage("https://portal.example.test/"))

        async def observe(offset=0):
            return "a page"
        tab.observe = observe

        class QuickModel:
            thinking = False

            def __init__(self):
                self.calls = 0

            async def chat(self, messages, tools=None, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    await asyncio.sleep(3600)
                return type("M", (), {"content": "", "tool_calls": [FakeCall(self.calls, "finish", '{"report": "FOUND: y"}')]})()
        old = browsing.CALL_SECONDS, browsing.QUICK_CALL_SECONDS
        browsing.CALL_SECONDS, browsing.QUICK_CALL_SECONDS = 600, 0.2
        try:
            agent = BrowsingAgent(QuickModel(), {"portal": tab}, {}, "task", "context", name="t",
                                  deadline=time.time() + 900, log=lambda *_: None)
            started = time.time()
            report = asyncio.run(agent.run())
        finally:
            browsing.CALL_SECONDS, browsing.QUICK_CALL_SECONDS = old
        self.assertLess(time.time() - started, 5)
        self.assertEqual(agent.stalls, 1)
        self.assertIn("FOUND: y", report)


class FreshSessionTest(unittest.TestCase):
    def test_a_load_that_runs_out_of_time_brings_a_fresh_session_at_once(self):
        import time
        import browsing
        Tab.homes.clear()
        tab = Tab("portal", FakePage("https://portal.example.test/"))
        reopened = []

        async def observe(offset=0):
            return "[portal] URL: https://portal.example.test/\nTitle: t\na page"

        async def goto(url):
            await asyncio.sleep(3600)  # the website never answers

        async def reopen(browser=None):
            reopened.append(browser)
            return "[portal] fresh page"
        tab.observe, tab.goto, tab.reopen = observe, goto, reopen
        tab.page.context.browser = "own browser"

        class GoesOnce:
            def __init__(self):
                self.n, self.seen = 0, []

            async def chat(self, messages, tools=None, **kwargs):
                self.n += 1
                self.seen = [m["content"] for m in messages if m["role"] == "user"]
                if self.n <= 1:
                    return type("M", (), {"content": "", "tool_calls": [FakeCall(self.n, "goto", '{"url": "/x"}')]})()
                return type("M", (), {"content": "", "tool_calls": [FakeCall(self.n, "finish", '{"report": "done"}')]})()
        model = GoesOnce()
        old = browsing.ACTION_SECONDS
        browsing.ACTION_SECONDS = 0.2
        try:
            agent = BrowsingAgent(model, {"portal": tab}, {}, "task", "context", name="t",
                                  deadline=time.time() + 60, log=lambda *a: None, browsers=["another browser"])
            asyncio.run(agent.run())
        finally:
            browsing.ACTION_SECONDS = old
        self.assertEqual(reopened, ["own browser"])  # the own browser first; it answered, so no other was needed
        self.assertTrue(any("fresh session" in m for m in model.seen))


if __name__ == "__main__":
    unittest.main()
