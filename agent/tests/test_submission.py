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


if __name__ == "__main__":
    unittest.main()
