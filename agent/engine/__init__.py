"""Deterministic bid engine: `build_bid(facts)` returns the answer object; `evaluate(facts)` also returns what could
not be decided. No model, network or browser in here; the facts are defined in `facts.py`.

Use from the lead:

    import facts, engine
    tool_schema = facts.json_schema()                 # parameters of a "price the bid" tool
    f = facts.from_dict(facts.Facts, arguments)       # ValueError names the bad field; show it to the model
    result = engine.evaluate(f)
    result.answer       # matches the answer schema; submit it
    result.issues       # what is missing or ambiguous; send the lead back to investigate these
    result.incomplete   # positions whose unit price lacks a component (still listed, priced without it)

Positions whose material or quantity is unknown are left out of `answer`; the decision is `no_bid` only when a
searched requirement has no matching active material (rule 3).
"""
from .bid import BidResult, Line, build_bid, evaluate

__all__ = ["BidResult", "Line", "build_bid", "evaluate"]
