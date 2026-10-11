"""The shared board of the site agents: the facts recorded so far and what the bid still needs.

Every agent works one website and records the facts it reads here, typed, at once. Before each of its steps an agent
is told what changed on the board since it last looked: what the other agents recorded (e.g. the catalogue groups
of a specification) and the engine's list of what the bid still needs, by role and not by website. Each agent takes
on what its own website can answer. There is no lead and no report to pass on: the board is the only connection.
"""
import asyncio
import json

import engine
import facts

NEWS_RECORDS = 12  # most recent records of other agents shown in one update
RECORD_CHARS = 700  # of each of them
NEEDS_SHOWN = 30  # enough that the items for prices and rule mails are not cut off behind the others


class Board:
    def __init__(self, builder: facts.FactsBuilder | None = None, log=print):
        self.builder = builder or facts.FactsBuilder()
        self.log = log
        self.version = 0
        self.history: list[tuple[int, str, str]] = []  # version, who recorded, what (without sources)
        self.searchers: set[str] = set()  # agents that recorded materials, i.e. the results of a material search
        # What is still needed, as it was after the last record, and the version at which that list last gained an
        # item. Most records add no work for anybody (one more material, one more mail); an agent that waits is
        # woken only when something new is asked for, not by every record.
        self.open_items: frozenset = frozenset()
        self.work_version = 0
        # The agents inside a wait right now, and a count of every record and every change of that set: a complete
        # bid ends the investigations once every agent waits, and the run looks again at each change.
        self.waiting: set[str] = set()
        self.changes = 0
        self._changed = asyncio.Condition()

    def record(self, who: str, update) -> str:
        """Adds typed facts; a bad update raises ValueError with the reason and changes nothing."""
        if not isinstance(update, dict):
            raise ValueError("the facts must be an object")
        update = {key: value for key, value in update.items() if key != "authority"}  # the engine's default
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
        open_now = frozenset((need.get("site"), need.get("what")) for need in self.needs())
        if open_now - self.open_items:
            self.work_version = self.version
        self.open_items = open_now
        self.log(f"[{who}] recorded: {summary[:240]}{' (searched_groups refused)' if refused else ''}")
        self._wake()
        return "Recorded. " + summary + refused

    def set_waiting(self, who: str, waiting: bool) -> None:
        """Marks agent `who` as inside a wait, or not any more; whoever waits for the board hears of it."""
        if waiting == (who in self.waiting):
            return
        if waiting:
            self.waiting.add(who)
        else:
            self.waiting.discard(who)
        self._wake()

    def _wake(self) -> None:
        """Counts a change and wakes whoever waits for the board: the agents' waits and the run's end check."""
        self.changes += 1
        try:
            asyncio.get_running_loop().create_task(self._notify())
        except RuntimeError:  # no loop running (a plain call): nobody can be waiting
            pass

    async def _notify(self):
        async with self._changed:
            self._changed.notify_all()

    async def _until(self, ready, timeout: float) -> bool:
        """Waits until `ready()` holds, asked again at every change of the board, at most `timeout` seconds."""
        if ready():
            return True
        try:
            async with self._changed:
                await asyncio.wait_for(self._changed.wait_for(ready), timeout=timeout)
            return True
        except TimeoutError:
            return False

    async def wait(self, since: int, timeout: float) -> bool:
        """Waits until the board changes after version `since`, at most `timeout` seconds. True if it did."""
        return await self._until(lambda: self.version > since, timeout)

    async def wait_for_work(self, since: int, timeout: float) -> bool:
        """Waits until the list of what is still needed has gained an item after version `since`, at most `timeout`
        seconds. True if it has."""
        return await self._until(lambda: self.work_version > since, timeout)

    async def wait_for_change(self, since: int, timeout: float) -> bool:
        """Waits until the board changes after change `since` (a record, or an agent starts or ends a wait), at most
        `timeout` seconds. True if it did."""
        return await self._until(lambda: self.changes > since, timeout)

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
