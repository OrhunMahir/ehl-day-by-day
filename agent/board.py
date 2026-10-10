"""The shared board of the site agents: the facts recorded so far and what the bid still needs.

Every agent works one website and records the facts it reads here, typed, at once. Before each of its steps an agent
is told what changed on the board since it last looked: what the other agents recorded (e.g. the catalogue groups
of a specification) and the engine's list of what the bid still needs, by role and not by website. Each agent takes
on what its own website can answer. There is no lead and no report to pass on: the board is the only connection.
"""
import asyncio
import json
import re

import engine
import facts

NEWS_RECORDS = 12  # most recent records of other agents shown in one update
RECORD_CHARS = 700  # of each of them
# Money fields: a value given as a JSON number with exactly three decimals, or as text like "64.853", is how a German
# "64.853,00" (sixty-four thousand) gets misread as sixty-four; such a value is refused and asked for as written.
MONEY = {"price", "setup_fee_eur", "fee_eur", "eur_per_carton", "min_order_value", "lower", "upper"}
AMBIGUOUS = re.compile(r"^\s*-?\d{1,3}\.\d{3}\s*(EUR|USD|€|\$)?\s*$", re.IGNORECASE)
NEEDS_SHOWN = 30  # enough that the items for prices and rule mails are not cut off behind the others


class Board:
    def __init__(self, builder: facts.FactsBuilder | None = None, log=print):
        self.builder = builder or facts.FactsBuilder()
        self.log = log
        self.version = 0
        self.history: list[tuple[int, str, str]] = []  # version, who recorded, what (without sources)
        self.searchers: set[str] = set()  # agents that recorded materials, i.e. the results of a material search
        self._changed = asyncio.Condition()

    def record(self, who: str, update) -> str:
        """Adds typed facts; a bad update raises ValueError with the reason and changes nothing."""
        if not isinstance(update, dict):
            raise ValueError("the facts must be an object")
        update = {key: value for key, value in update.items() if key != "authority"}  # the engine's default
        update = _checked(update)
        refused = ""
        if update.get("searched_groups") and not (update.get("materials") or who in self.searchers):
            # A group counts as searched only from the agent that searched it and recorded what it found; a group
            # listed in a specification is not a search. Otherwise a group nobody searched would end in "no bid".
            refused = (" searched_groups was not taken: only the assistant that searched the materials by a group "
                       "and recorded its results records the group as searched.")
            update = {key: value for key, value in update.items() if key != "searched_groups"}
        summary = self.builder.add(update)
        if update.get("materials"):
            self.searchers.add(who)
        self.version += 1
        self.history.append((self.version, who, _compact(update)))
        self.log(f"[{who}] recorded: {summary[:240]}{' (searched_groups refused)' if refused else ''}")
        try:
            asyncio.get_running_loop().create_task(self._notify())
        except RuntimeError:  # no loop running (a plain call): nobody can be waiting
            pass
        return "Recorded. " + summary + refused

    async def _notify(self):
        async with self._changed:
            self._changed.notify_all()

    async def wait(self, since: int, timeout: float) -> bool:
        """Waits until the board changes after version `since`, at most `timeout` seconds. True if it did."""
        if self.version > since:
            return True
        try:
            async with self._changed:
                await asyncio.wait_for(self._changed.wait_for(lambda: self.version > since), timeout=timeout)
            return True
        except TimeoutError:
            return False

    def needs(self) -> list[dict]:
        """What the bid still needs, from the engine, kind "find" first."""
        if not self.builder.data.get("tender_reference"):
            return []
        try:
            result = engine.evaluate(self.builder.facts(), allow_no_bid=False)
            return [need for need in result.needed() if need.get("kind") == "find"]
        except Exception as error:  # noqa: BLE001 - the board still shows the records
            self.log(f"[board] the engine could not read the facts: {error}")
            return []

    def complete(self) -> bool:
        """Whether the engine has everything it needs: every position bid with a confirmed material, every price part
        known, nothing left to find."""
        if not self.builder.data.get("tender_reference") or not self.builder.data.get("positions"):
            return False
        try:
            return engine.evaluate(self.builder.facts(), allow_no_bid=False).complete
        except Exception:  # noqa: BLE001
            return False

    def news(self, who: str, since: int) -> str:
        """What changed for agent `who` since version `since`: the others' records, then what is still needed."""
        others = [(v, by, what) for v, by, what in self.history if v > since and by != who][-NEWS_RECORDS:]
        lines = [f"- recorded by {by}: {what}" for _, by, what in others]
        needs = self.needs()[:NEEDS_SHOWN]
        text = ("Recorded by the other websites' assistants:\n" + "\n".join(lines) + "\n\n") if lines else ""
        if needs:
            text += ("Still needed for the bid (take on what your website can answer, the items naming positions "
                     "first):\n" + "\n".join(_need(n) for n in needs))
        else:
            text += "Nothing is listed as still needed right now."
        return text


def _checked(update: dict) -> dict:
    """Masses with a unit become kilograms; money values that may be misread thousands are refused with the reason,
    so the agent records them again as the page writes them."""
    doubtful: list[str] = []

    def walk(value, key=""):
        if isinstance(value, dict):
            return {k: walk(v, k) for k, v in value.items()}
        if isinstance(value, list):
            return [walk(v, key) for v in value]
        if key.endswith("_kg") and isinstance(value, str) and re.search(r"[a-zA-Z]", value):
            try:
                return float(facts.mass_kg(value))  # a plain number: "1.875" as text would be ambiguous
            except ValueError:
                return value
        if key in MONEY:
            if isinstance(value, float) and len(repr(value).split(".")[-1]) == 3 and "e" not in repr(value):
                doubtful.append(repr(value))
            elif isinstance(value, str) and AMBIGUOUS.match(value):
                doubtful.append(repr(value))
        return value
    checked = walk(update)
    if doubtful:
        raise ValueError(
            f"the amount(s) {', '.join(doubtful[:5])} may be misread: a German page writes sixty-four thousand as "
            "64.853,00. Record each amount as text exactly as the page writes it, with its decimal part (e.g. "
            "\"64.853,00\" or \"64,85\"); nothing of this update was saved")
    return checked


def _need(need: dict) -> str:
    positions = f" (positions {', '.join(need['positions'])})" if need.get("positions") else ""
    return f"- [{need.get('site')}] {need.get('what')}{positions}"


def _compact(update: dict) -> str:
    """The update as short JSON, without the sources, so other agents see the values and keys."""
    def strip(value):
        if isinstance(value, dict):
            return {k: strip(v) for k, v in value.items() if k != "source" and v not in (None, "", [], {})}
        if isinstance(value, list):
            return [strip(v) for v in value]
        return value
    text = json.dumps(strip(update), ensure_ascii=False, default=str)
    return text if len(text) <= RECORD_CHARS else text[:RECORD_CHARS] + " …"
