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
        self.assertTrue(any("without a note" in m for m in last_browse))
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
        tab.observe = observe

        class LookingModel(FakeModel):
            async def chat(self, messages, tools=None, **kwargs):
                if tools is None:
                    return type("M", (), {"content": "FOUND:\nNOT FOUND:\n- everything", "tool_calls": None})()
                self.n += 1
                self.seen.append(messages[-1].get("content", ""))
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

if __name__ == "__main__":
    unittest.main()
