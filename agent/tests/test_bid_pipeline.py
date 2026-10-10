"""Offline pipeline regression: invented facts, real pricing and browser tools, and a scripted model.

The same document/heading serves two item steps. No website, external model, run token or expected public answer
is used. The scripted model must choose each next action from the current browser observation.
"""
import json
import re
import sys
import time
import unittest
from datetime import date
from decimal import Decimal as D
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.async_api import async_playwright  # noqa: E402

from agent import Orchestrator  # noqa: E402
from browser import Tab  # noqa: E402
from facts import (DiscountApproval, Facts, Material, PriceCondition, Requirement, Scale, Source, SpecRevision,
                   TenderPosition, to_dict)  # noqa: E402


FORM = """<!doctype html><html><body><h1>Invented bid form</h1><main id="form"></main><script>
const items = ['11', '12']; const saved = []; let index = 0;
window.enteredBid = {positions: saved, signed: false};
function showItem() {
  if (index === items.length) { showTotals(); return; }
  document.querySelector('#form').innerHTML = `<h2>Enter item</h2><p>Current item: ${items[index]}</p>
    <label>Material<select name="material"><option value="">Choose</option>
      <option value="M-120">M-120</option><option value="M-12">M-12</option></select></label>
    <label>Quantity<input name="quantity" type="number"></label>
    <label>Unit price<input name="unit_price" placeholder="0.00"></label>
    <label>Lead weeks<input name="lead_time_weeks" type="number"></label>
    <button onclick="saveItem()">Save item</button>`;
}
function saveItem() {
  const read = name => document.querySelector(`[name=${name}]`).value;
  if (!read('material') || !read('quantity') || !read('unit_price') || !read('lead_time_weeks')) return;
  saved.push({position: items[index], material_number: read('material'), quantity: read('quantity'),
    unit_price: read('unit_price'), lead_time_weeks: read('lead_time_weeks')});
  index += 1; showItem();
}
function showTotals() {
  document.querySelector('#form').innerHTML = `<h2>Review and sign</h2>
    <p>${saved.length} items entered.</p><label>Discount percent<input name="discount_percent"></label>
    <label>Total net<input name="total_net" placeholder="0.00"></label>
    <label>Acknowledge<input name="acknowledge" type="checkbox"></label>
    <button onclick="signBid()">Sign offer</button><p id="error"></p>`;
}
function signBid() {
  const total = document.querySelector('[name=total_net]').value;
  const sum = saved.reduce((value, item) => value + Number(item.quantity) * Number(item.unit_price), 0).toFixed(2);
  if (saved.length !== 2 || total !== sum || !document.querySelector('[name=acknowledge]').checked) {
    document.querySelector('#error').textContent = 'Items or total incomplete'; return;
  }
  window.enteredBid.discount_percent = document.querySelector('[name=discount_percent]').value;
  window.enteredBid.total_net = total; window.enteredBid.signed = true;
  document.querySelector('#form').innerHTML = '<h2>Complete</h2><p>Signed receipt: INV-LOCAL-OK.2</p>';
}
showItem();
</script></body></html>"""


def invented_facts():
    requirement = Requirement("REQ-X", bore_mm=D(48), stroke_mm=D(130), pressure_bar=D(160),
                              mounting="clamp", catalogue_group="GROUP-X")
    material = Material("M-12", active=True, catalogue_group="GROUP-X", bore_mm=D(48), stroke_mm=D(130),
                        pressure_bar=D(160), mounting="clamp", stock=D(8), stock_lead_weeks=2,
                        production_lead_weeks=7, setup_fee_eur=D(0), conditions=[PriceCondition(
                            date(2033, 1, 1), date(2033, 12, 31), scales=[Scale(D(1), D("12.75"))])])
    return Facts("INVENTED-PIPELINE", date(2033, 2, 2), bidder_domain="invented.invalid",
                 director_address="director@invented.invalid",
                 positions=[TenderPosition("11", D(3), requirement="REQ-X"),
                            TenderPosition("12", D(4), requirement="REQ-X")],
                 revisions=[SpecRevision("REV-X", True, date(2033, 1, 1), [requirement])],
                 materials=[material], searched_groups=["GROUP-X"], discount_approvals=[DiscountApproval(
                     "INVENTED-PIPELINE", D(8), source=Source(site="invented-mail", date=date(2033, 2, 1),
                                                            sender="director@invented.invalid"))])


class ObservationScript:
    """A fake model that reads the handed-off answer and each fresh view, rather than advancing a turn counter."""

    def __init__(self):
        self.answer = None
        self.turns = []

    async def chat(self, messages, tools, **kwargs):
        if self.answer is None:
            task = next(message["content"] for message in messages if "ANSWER:\n" in message.get("content", ""))
            self.answer = json.JSONDecoder().raw_decode(task.split("ANSWER:\n", 1)[1])[0]
        view = next(message["content"] for message in reversed(messages) if " URL: " in message.get("content", ""))
        receipt = re.search(r"Signed receipt: INV-LOCAL-OK\.2", view)
        if receipt:
            name, args = "finish", {"accepted": True, "receipt": receipt.group(), "report": receipt.group()}
        elif match := re.search(r"Current item:\s*(\d+)", view):
            position = next(p for p in self.answer["positions"] if p["position"] == match.group(1))
            if not all(label in view for label in ("Material", "Quantity", "Unit price", "Lead weeks", "Save item")):
                raise AssertionError("the model tried to fill controls absent from the current observation")
            name, args = "fill_form", {"site": "form", "fields": [
                {"label": "Material", "value": position["material_number"]},
                {"label": "Quantity", "value": str(position["quantity"])},
                {"label": "Unit price", "value": format(D(str(position["unit_price"])), ".2f")},
                {"label": "Lead weeks", "value": str(position["lead_time_weeks"])}], "click_after": "Save item"}
        elif "Review and sign" in view:
            if not all(label in view for label in ("Discount percent", "Total net", "Acknowledge", "Sign offer")):
                raise AssertionError("the signing controls were not observed")
            name, args = "fill_form", {"site": "form", "fields": [
                {"label": "Discount percent", "value": str(self.answer["discount_percent"])},
                {"label": "Total net", "value": format(D(str(self.answer["total_net"])), ".2f")},
                {"label": "Acknowledge", "value": "true"}], "click_after": "Sign offer"}
        else:
            raise AssertionError("no next action is supported by the current observation")
        self.turns.append({"name": name, "args": args, "view": view})
        return SimpleNamespace(content="", tool_calls=[SimpleNamespace(id=f"invented-{len(self.turns)}",
            function=SimpleNamespace(name=name, arguments=json.dumps(args)))])


class ARealBidPipeline(unittest.IsolatedAsyncioTestCase):
    async def test_two_priced_items_reach_the_form_and_a_later_turn_observes_the_signed_receipt(self):
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            try:
                page = await browser.new_page()
                await page.route("**/*", lambda route: route.abort())
                await page.set_content(FORM)
                tab, model = Tab("form", page, log=lambda *a: None), ObservationScript()
                task = {"brief": "Invented offline bid. Enter each item, review totals and sign.",
                        "access": [{"siteId": "form", "url": "about:blank", "credentials": {}}],
                        "answer_schema": {}, "submission": {"kind": "site", "siteId": "form"}}
                lead = Orchestrator(task, None, {"form": tab}, model, time.time())
                lead.log = lambda *a: None
                result = lead.add(to_dict(invented_facts()))
                self.assertNotIn("could not be priced", result)
                self.assertIsNotNone(lead.priced)
                answer = lead.priced.answer
                self.assertEqual(answer["decision"], "bid")
                self.assertEqual([p["position"] for p in answer["positions"]], ["11", "12"])
                self.assertEqual([D(str(p["unit_price"])) for p in answer["positions"]], [D("11.73"), D("11.73")])
                self.assertEqual(D(str(answer["total_net"])), D("82.11"))
                # Any accidental platform HTTP call fails the test; the real site submission path stays local.
                with patch("agent.contract.http", side_effect=AssertionError("network is forbidden in this test")):
                    report = await lead.submit(answer, "Use the current form; no external websites exist.")
                entered = await page.evaluate("window.enteredBid")
                self.assertEqual(entered["positions"], [
                    {"position": "11", "material_number": "M-12", "quantity": "3", "unit_price": "11.73", "lead_time_weeks": "2"},
                    {"position": "12", "material_number": "M-12", "quantity": "4", "unit_price": "11.73", "lead_time_weeks": "2"}])
                self.assertEqual(D(entered["discount_percent"]), D(8))
                self.assertEqual(entered["total_net"], "82.11")
                self.assertTrue(entered["signed"])
                self.assertTrue(lead.accepted)
                self.assertTrue(lead.closed)
                self.assertIn("Signed receipt: INV-LOCAL-OK.2", report)
                self.assertEqual([turn["name"] for turn in model.turns], ["fill_form", "fill_form", "fill_form", "finish"])
                self.assertIn("Current item: 11", model.turns[0]["view"])
                self.assertIn("Current item: 12", model.turns[1]["view"])
                self.assertNotIn("Signed receipt:", model.turns[-2]["view"])
                self.assertIn("Signed receipt:", model.turns[-1]["view"])
                self.assertEqual(page.url, "about:blank")
            finally:
                await browser.close()


if __name__ == "__main__":
    unittest.main()
