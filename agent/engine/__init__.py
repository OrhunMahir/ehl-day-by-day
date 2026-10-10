"""Deterministic bid engine: `build_bid(facts)` returns the answer object; `evaluate(facts)` also returns what could
not be decided. No model, network or browser in here; the facts are defined in `facts.py`.

Use from the lead, facts in pieces (recommended):

    import facts, engine
    builder = facts.FactsBuilder()
    add_schema = facts.update_schema()                # parameters of an "add_facts" tool
    builder.add(arguments)                            # returns a summary; ValueError names the bad field and the
                                                      # update is rejected whole, earlier facts stay
    result = engine.evaluate(builder.facts())         # "price_bid" tool, no parameters

or all at once: `facts.from_dict(facts.Facts, arguments)` with `facts.json_schema()`. `facts.to_dict(f)` gives the
plain JSON back, e.g. for the run log. Then:

    result.answer       # matches the answer schema; submit it
    result.issues       # what is missing or ambiguous; send the lead back to investigate these
    result.incomplete   # positions whose unit price lacks a component (still listed, priced without it)
    result.omitted      # positions left out of the answer; mark them "Position auslassen" in the form (rule 9)
    result.explain()    # plain text for the run log: what each value came from, which records counted
    result.needed()     # what to find or verify next, by site role and rule; give it to the lead after each price_bid
    result.complete     # True when nothing is left to find: submit

Positions whose material or quantity is unknown are left out of `answer`; the decision is `no_bid` only when a
searched requirement has no matching active material (rule 3).
"""
from .bid import BidResult, Line, build_bid, evaluate

__all__ = ["BidResult", "Line", "build_bid", "evaluate"]
