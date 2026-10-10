"""Stream O: filling a form step in one call, on an invented wizard whose fields appear one after the other.
No network, no model. Needs Chromium (`uv run playwright install chromium`)."""
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.async_api import async_playwright  # noqa: E402

from browser import Tab  # noqa: E402
from submission import fill_form  # noqa: E402

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


STEPS = """<!doctype html><html><body><h1>Step <span id="n">1</span></h1><p>Changes are only saved with "Next".</p>
<label>Discount <input name="discount"></label>
<button onclick="n.textContent = +n.textContent + 1">Next</button>
<button onclick="n.textContent = Math.max(1, +n.textContent - 1)">Back</button></body></html>"""


class ButtonsByTheirText(unittest.IsolatedAsyncioTestCase):
    """The configurator of a test run: the agent clicked a number it remembered from another page, which was "back"
    there, and went round in a circle for minutes."""

    async def asyncSetUp(self):
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch()
        self.page = await self.browser.new_page()
        await self.page.set_content(STEPS)
        self.tab = Tab("form", self.page, log=lambda *a: None)
        self.view = await self.tab.observe()

    async def asyncTearDown(self):
        await self.browser.close()
        await self.playwright.stop()

    async def test_the_button_is_found_by_its_text_and_named_in_the_outcome(self):
        outcome = await fill_form(self.tab, [{"label": "Discount", "value": "0"}], "Next")
        self.assertIn('clicked "Next"', outcome)
        self.assertEqual(await self.page.inner_text("#n"), "2")  # the button, not the hint that mentions it
        back = re.search(r'\[(\d+)\] button "Back"', self.view).group(1)
        outcome = await fill_form(self.tab, [{"label": "Discount", "value": "0"}], back)
        self.assertIn('clicked "Back"', outcome)

    async def test_the_same_action_three_times_is_called_a_circle(self):
        import time

        from submission import SubmissionAgent
        back = re.search(r'\[(\d+)\] button "Back"', self.view).group(1)

        class Repeats:
            def __init__(self):
                self.seen, self.n = [], 0

            async def chat(self, messages, tools=None, **kwargs):
                self.seen = messages
                self.n += 1
                name, args = ("click", {"site": "form", "element": back}) if self.n <= 3 else ("finish", {"report": "x"})
                call = type("C", (), {"id": f"c{self.n}", "function": type("F", (), {
                    "name": name, "arguments": __import__("json").dumps(args)})()})()
                return type("M", (), {"content": "", "tool_calls": [call]})()
        model = Repeats()
        agent = SubmissionAgent(model, {"form": self.tab}, {"form": {}}, "task", "context",
                                deadline=time.time() + 120, log=lambda *a: None)
        await agent.run()
        results = [m["content"] for m in model.seen if m["role"] == "tool"]
        self.assertTrue(results[0].startswith(f'Clicked [{back}] "Back"'))
        self.assertNotIn("going round in a circle", results[1])
        self.assertIn("going round in a circle", results[2])

    async def test_the_same_button_on_ever_new_pages_is_no_circle(self):
        import time

        from submission import SubmissionAgent
        await self.page.set_content('<script>let k = 1;</script><h2 id="h">Position 1</h2><button onclick="k++; '
                                    'document.getElementById(\'h\').textContent = \'Position \' + k">Next</button>')
        view = await self.tab.observe()
        forward = re.search(r'\[(\d+)\] button "Next"', view).group(1)

        class Forward:
            def __init__(self):
                self.seen, self.n = [], 0

            async def chat(self, messages, tools=None, **kwargs):
                self.seen = messages
                self.n += 1
                name, args = ("click", {"site": "form", "element": forward}) if self.n <= 4 else ("finish", {"report": "x"})
                call = type("C", (), {"id": f"c{self.n}", "function": type("F", (), {
                    "name": name, "arguments": __import__("json").dumps(args)})()})()
                return type("M", (), {"content": "", "tool_calls": [call]})()
        model = Forward()
        agent = SubmissionAgent(model, {"form": self.tab}, {"form": {}}, "task", "context",
                                deadline=time.time() + 120, log=lambda *a: None)
        await agent.run()
        results = [m["content"] for m in model.seen if m["role"] == "tool"]
        self.assertIn("## Position 5", results[3])
        self.assertFalse(any("going round in a circle" in r for r in results))


CASCADE = """<!doctype html><html><body><h1>Item</h1>
<label>Size <select name="size"><option value="">choose</option><option>40</option><option>63</option></select></label>
<label>Length <select name="length" disabled><option value="">choose</option></select></label>
<label>Article <select name="article" disabled><option value="">choose</option></select></label>
<button id="apply">Apply selection</button><p id="picked"></p>
<script>
const q = n => document.querySelector(`[name=${n}]`);
document.getElementById("apply").onclick = () => {
  if (q("size").value && q("length").disabled) {
    q("length").innerHTML = '<option value="">choose</option><option>500</option>'; q("length").disabled = false;
  } else if (q("length").value && q("article").disabled) {
    q("article").innerHTML = '<option value="">choose</option><option value="A-9">A-9 (63/500)</option>';
    q("article").disabled = false;
  }
};
q("article").onchange = e => document.getElementById("picked").textContent = e.target.value;
</script></body></html>"""


class ACascadeOfSelections(unittest.IsolatedAsyncioTestCase):
    """The material picker of a test run: each selection is enabled only once a button applies the one before."""

    async def asyncSetUp(self):
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch()
        self.page = await self.browser.new_page()
        await self.page.set_content(CASCADE)
        self.tab = Tab("form", self.page, log=lambda *a: None)
        await self.tab.observe()

    async def asyncTearDown(self):
        await self.browser.close()
        await self.playwright.stop()

    async def test_a_disabled_selection_says_what_enables_it(self):
        outcome = await fill_form(self.tab, [{"label": "Size", "value": "63"}, {"label": "Length", "value": "500"}])
        self.assertIn("disabled", outcome)
        self.assertIn('{"click"', outcome)

    async def test_buttons_between_the_selections_fill_the_cascade_in_one_call(self):
        outcome = await fill_form(self.tab, [
            {"label": "Size", "value": "63"}, {"click": "Apply selection"}, {"label": "Length", "value": "500"},
            {"click": "Apply selection"}, {"label": "Article", "value": "A-9"}])
        self.assertNotIn("FAILED", outcome)
        self.assertIn('clicked "Apply selection"', outcome)
        self.assertEqual(await self.page.inner_text("#picked"), "A-9")


if __name__ == "__main__":
    unittest.main()
