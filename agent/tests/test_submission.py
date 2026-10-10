"""Stream O: filling a form step in one call, on an invented wizard whose fields appear one after the other.
No network, no model. Needs Chromium (`uv run playwright install chromium`)."""
import re
import json
import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.async_api import async_playwright  # noqa: E402

from browser import Tab  # noqa: E402
from submission import SubmissionAgent, STOP_SECONDS, fill_form, tool_schemas  # noqa: E402

WIZARD = """<!doctype html><html><body><h1>Invented wizard</h1><div id="step"></div><div id="result"></div>
<script>
const B = {Alpha: ["Red 10", "Red 100", "Blue 7"], Beta: ["Green 3", "Yellow 4"]};
const saved = []; let position = 1; let s = {};
function render() {
  const el = document.getElementById("step");
  if (position > 2) { el.innerHTML = "<p>All positions entered.</p>";
    document.getElementById("result").textContent = JSON.stringify(saved); return; }
  let html = `<h2>Position ${position}</h2><label>Type <select name="type"><option value="">choose</option>` +
    ["Alpha", "Beta"].map(o => `<option ${s.type === o ? "selected" : ""}>${o}</option>`).join("") + "</select></label>";
  if (s.type) html += `<label>Colour <select name="colour"><option value="">choose</option>` +
    B[s.type].map(o => `<option ${s.colour === o ? "selected" : ""}>${o}</option>`).join("") + "</select></label>";
  if (s.colour) html += `<label for="amt">Amount</label><input id="amt" name="amount" value="${s.amount || ""}">` +
    `<input name="price" aria-label="Unit price" value="${s.price || ""}"><button id="go">Continue</button>`;
  el.innerHTML = html;
  const q = n => el.querySelector(`[name=${n}]`);
  q("type").onchange = e => { s = {type: e.target.value}; setTimeout(render, 300); };
  if (q("colour")) q("colour").onchange = e => { s.colour = e.target.value; setTimeout(render, 300); };
  if (q("amount")) { q("amount").oninput = e => s.amount = e.target.value; q("price").oninput = e => s.price = e.target.value;
    document.getElementById("go").onclick = () => { saved.push({position, ...s}); position += 1; s = {}; render(); }; }
}
render();
</script></body></html>"""


class AFormStepInOneCall(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch()
        self.page = await self.browser.new_page()
        await self.page.set_content(WIZARD)
        self.tab = Tab("form", self.page, log=lambda *a: None)
        self.view = await self.tab.observe()

    async def asyncTearDown(self):
        await self.browser.close()
        await self.playwright.stop()

    async def saved(self):
        return await self.page.inner_text("#result")

    async def test_fields_that_appear_later_are_named_by_their_label(self):
        first = re.search(r"\[(\d+)\] select name=type", self.view).group(1)
        outcome = await fill_form(self.tab, [
            {"element": first, "value": "Alpha"}, {"label": "Colour", "value": "red 10"},
            {"label": "Amount", "value": "12"}, {"label": "Unit price", "value": "3,50"}], "Continue")
        self.assertNotIn("FAILED", outcome)
        self.assertIn('selected "Red 10"', outcome)  # the exact option, not "Red 100"
        outcome = await fill_form(self.tab, [
            {"label": "Type", "value": "Beta"}, {"label": "Colour", "value": "Yellow"},
            {"label": "Amount", "value": "7"}, {"label": "Unit price", "value": "1,00"}], "Continue")
        self.assertNotIn("FAILED", outcome)
        self.assertEqual(await self.saved(),
                         '[{"position":1,"type":"Alpha","colour":"Red 10","amount":"12","price":"3,50"},'
                         '{"position":2,"type":"Beta","colour":"Yellow 4","amount":"7","price":"1,00"}]')

    async def test_a_field_that_is_not_there_stops_the_step_and_nothing_is_clicked(self):
        outcome = await fill_form(self.tab, [
            {"label": "Type", "value": "Alpha"}, {"label": "Shade", "value": "x"},
            {"label": "Amount", "value": "1"}], "Continue")
        self.assertIn('no field labelled "Shade"', outcome)
        self.assertIn("1 later field(s) were not touched", outcome)
        self.assertIn("not clicked", outcome)
        self.assertEqual(await self.saved(), "")

    async def test_an_option_that_is_not_offered_is_reported_with_the_options(self):
        outcome = await fill_form(self.tab, [{"label": "Type", "value": "Alpha"}, {"label": "Colour", "value": "Purple"}])
        self.assertIn('no option "Purple"', outcome)
        self.assertIn("Red 10 ; Red 100 ; Blue 7", outcome)

    async def test_an_apply_button_between_fields_enables_material_selection(self):
        await self.page.set_content("""<label>Family<select name="family"><option>Choose</option><option>Orion</option></select></label>
          <button onclick="document.querySelector('[name=material]').disabled=false">Apply family</button>
          <label>Material<select disabled name="material"><option>Choose</option><option value="invented-12">Invented 12</option>
          <option value="invented-120">Invented 120</option></select></label><label>Quantity<input name="quantity"></label>
          <button onclick="document.body.dataset.saved=document.querySelector('[name=material]').value">Save item</button>""")
        await self.tab.observe()
        outcome = await fill_form(self.tab, [{"label": "Family", "value": "Orion"}, {"click": "Apply family"},
                                           {"label": "Material", "value": "Invented 12"},
                                           {"label": "Quantity", "value": "9"}], "Save item")
        self.assertNotIn("FAILED", outcome)
        self.assertIn('clicked "Apply family"', outcome)
        self.assertEqual(await self.page.evaluate("document.body.dataset.saved"), "invented-12")

    async def test_disabled_continue_is_not_triggered_by_dom_fallback(self):
        await self.page.set_content('<button disabled onclick="document.body.dataset.sent=1">Continue</button>')
        await self.tab.observe()
        outcome = await fill_form(self.tab, [], "Continue")
        self.assertIn("FAILED", outcome)
        self.assertIsNone(await self.page.evaluate("document.body.dataset.sent"))

    async def test_controls_under_an_open_modal_are_not_changed_or_clicked(self):
        await self.page.set_content("""<label>Quantity<input name="quantity" value="7"></label>
          <button onclick="document.body.dataset.sent=1">Continue</button>
          <div role="dialog" aria-modal="true" style="position:fixed;inset:0;background:white">Read this first</div>""")
        await self.tab.observe()
        outcome = await fill_form(self.tab, [{"label": "Quantity", "value": "99"}], "Continue")
        self.assertIn("FAILED", outcome)
        self.assertEqual(await self.page.input_value('[name="quantity"]'), "7")
        self.assertIsNone(await self.page.evaluate("document.body.dataset.sent"))

    async def test_a_replacement_button_is_not_rebound_by_its_old_id(self):
        await self.page.set_content("""<button id="apply" onclick="replaceButton()">Apply</button>
          <button id="go">Continue</button><script>
          function replaceButton() { document.querySelector('#go').outerHTML =
            '<button id="go" onclick="document.body.dataset.wrong=1">Discard</button>'; }
          </script>""")
        view = await self.tab.observe()
        number = re.search(r'\[(\d+)\] button "Continue"', view).group(1)
        outcome = await fill_form(self.tab, [{"click": "Apply"}], number)
        self.assertIn("FAILED", outcome)
        self.assertIsNone(await self.page.evaluate("document.body.dataset.wrong"))

    async def test_numeric_option_prefix_does_not_select_another_material(self):
        await self.page.set_content('<label>Material<select name="material"><option>Choose</option><option>250</option></select></label>')
        await self.tab.observe()
        with patch("submission.OPTION_WAIT_SECONDS", 0):
            outcome = await fill_form(self.tab, [{"label": "Material", "value": "25"}])
        self.assertIn('no option "25"', outcome)
        self.assertEqual(await self.page.input_value('[name="material"]'), "Choose")

    async def test_step_timeout_preserves_fields_that_were_entered(self):
        await self.page.set_content('<label>Quantity<input name="quantity" value="7"></label>')
        await self.tab.observe()
        started = time.time()
        outcome = await fill_form(self.tab, [{"label": "Not yet present", "value": "4"}], deadline=started + 0.05)
        self.assertIn("deadline", outcome)
        self.assertLess(time.time() - started, 0.5)
        self.assertEqual(await self.page.input_value('[name="quantity"]'), "7")


def response(*calls):
    return SimpleNamespace(content="", tool_calls=[SimpleNamespace(id=f"call-{i}", function=SimpleNamespace(
        name=name, arguments=json.dumps(args))) for i, (name, args) in enumerate(calls)])


class FakeModel:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.seen = []

    async def chat(self, messages, tools, **kwargs):
        self.seen.append(list(messages))
        return next(self.replies)


class FakeTab:
    site_id = "form"

    def __init__(self):
        self.page = SimpleNamespace(url="https://invented.invalid/form")
        self.goto = AsyncMock(return_value='# form URL: https://invented.invalid/form\n# Step\nitem=next')

    async def observe(self):
        return '# form URL: https://invented.invalid/form\n# Step\nitem=first'

    def _absolute(self, url):
        return "https://invented.invalid" + url


class SubmissionControl(unittest.IsolatedAsyncioTestCase):
    def make(self, model, *, deadline=None, tabs=None, max_steps=4):
        tabs = tabs or {"form": FakeTab()}
        return SubmissionAgent(model, tabs, {s: {} for s in tabs}, "Invented bid", "Invented brief",
                               deadline=deadline or time.time() + 1000, max_steps=max_steps, log=lambda *a: None)

    async def test_later_numeric_action_in_a_batch_waits_for_a_new_view(self):
        model = FakeModel([response(("click", {"site": "form", "element": "1"}),
                                    ("click", {"site": "form", "element": "2"})),
                           response(("finish", {"report": "Not signed", "accepted": False, "receipt": ""}))])
        agent = self.make(model)
        agent._run_tool = AsyncMock(return_value='# form URL: https://invented.invalid/form\n# Step\nitem=second')
        await agent.run()
        self.assertEqual(agent._run_tool.await_count, 1)
        self.assertTrue(any("NOT EXECUTED" in m.get("content", "") for m in model.seen[1]))

    async def test_independent_websites_can_use_numbers_in_the_same_response(self):
        model = FakeModel([response(("click", {"site": "form", "element": "1"}),
                                    ("click", {"site": "mail", "element": "2"})),
                           response(("finish", {"report": "Not signed", "accepted": False, "receipt": ""}))])
        agent = self.make(model, tabs={"form": FakeTab(), "mail": FakeTab()})
        agent._run_tool = AsyncMock(return_value='# form URL: https://invented.invalid/form\n# Step\nitem=second')
        await agent.run()
        self.assertEqual(agent._run_tool.await_count, 2)

    async def test_same_url_and_heading_with_a_different_position_is_progress(self):
        agent = self.make(FakeModel([]))
        args = {"site": "form", "element": "7"}
        for number in range(5):
            result = agent._progress("click", args, f'# form URL: https://invented.invalid/form\n# Step\nPosition {number}')
            self.assertNotIn("same action has returned", result)
        repeated = '# form URL: https://invented.invalid/form\n# Step\nPosition unchanged'
        for _ in range(3):
            result = agent._progress("click", args, repeated)
        self.assertIn("same action has returned", result)
        self.assertIn("Do not go back", result)

    async def test_a_hung_tool_is_cut_at_the_real_deadline(self):
        import asyncio
        model = FakeModel([response(("click", {"site": "form", "element": "1"}))])
        started = time.time()
        agent = self.make(model, deadline=started + STOP_SECONDS + 0.05, max_steps=1)
        async def hung(*args):
            await asyncio.Future()
        agent._run_tool = hung
        report = await agent.run()
        self.assertLess(time.time() - started, 0.5)
        self.assertIn("stopped", report)
        self.assertFalse(agent.accepted)

    async def test_a_hung_model_is_cut_at_the_real_deadline(self):
        import asyncio
        async def hung(*args, **kwargs):
            await asyncio.Future()
        model = SimpleNamespace(chat=AsyncMock(side_effect=hung))
        started = time.time()
        agent = self.make(model, deadline=started + STOP_SECONDS + 0.05)
        await agent.run()
        self.assertLess(time.time() - started, 0.5)
        self.assertEqual(model.chat.await_count, 1)

    async def test_success_requires_the_explicit_flag_and_a_receipt(self):
        for flag, receipt, expected in [(True, "Invented signed receipt", True), (False, "Invented receipt", False),
                                        (True, "", False), ("true", "Invented receipt", False)]:
            model = FakeModel([response(("finish", {"report": "Done", "accepted": flag, "receipt": receipt}))])
            agent = self.make(model)
            agent.tabs["form"].observe = AsyncMock(return_value=
                '# form URL: https://invented.invalid/form\nInvented signed receipt')
            await agent.run()
            self.assertEqual(agent.accepted, expected)
            self.assertEqual(agent.receipt, receipt if expected else "")
        schema = next(t["function"]["parameters"] for t in tool_schemas(["form"]) if t["function"]["name"] == "finish")
        self.assertIn("accepted", schema["required"])
        self.assertIn("receipt", schema["required"])

    async def test_acceptance_cannot_be_declared_before_a_batch_result_is_read(self):
        accepted = {"report": "Done", "accepted": True, "receipt": "Invented signed receipt"}
        model = FakeModel([response(("click", {"site": "form", "element": "1"}), ("finish", accepted)),
                           response(("finish", accepted))])
        agent = self.make(model)
        agent._run_tool = AsyncMock(return_value='# form URL: https://invented.invalid/form\nInvented signed receipt')
        await agent.run()
        self.assertEqual(len(model.seen), 2)
        self.assertTrue(any("read the results" in m.get("content", "") for m in model.seen[1]))
        self.assertTrue(agent.accepted)

    async def test_an_unobserved_receipt_cannot_be_claimed_as_success(self):
        model = FakeModel([response(("finish", {"report": "Done", "accepted": True,
                                                "receipt": "Invented but unobserved receipt"})),
                           response(("finish", {"report": "Receipt unavailable", "accepted": False, "receipt": ""}))])
        agent = self.make(model)
        await agent.run()
        self.assertFalse(agent.accepted)
        self.assertEqual(agent.receipt, "")
        self.assertTrue(any("not in an observed" in m.get("content", "") for m in model.seen[1]))

    async def test_retry_keeps_the_current_form_instead_of_reopening_its_address(self):
        agent = self.make(FakeModel([]), tabs={"form": FakeTab(), "mail": FakeTab()})
        with patch("submission.fill_form", new=AsyncMock(return_value='[a] Quantity: filled\n\n# The page now\n# Step')):
            await agent._run_tool("fill_form", {"site": "form", "fields": [{"label": "Quantity", "value": "7"}]})
        result = await agent._run_tool("goto", {"site": "form", "url": "/form"})
        self.assertIn("NOT EXECUTED", result)
        agent.tabs["form"].goto.assert_not_awaited()
        await agent._run_tool("goto", {"site": "mail", "url": "/inbox"})
        agent.tabs["mail"].goto.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
