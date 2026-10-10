"""Deterministic bid engine: `build_bid(facts)` returns the answer object; `evaluate(facts)` also returns what could
not be decided. No model, network or browser in here; the facts are defined in `facts.py`."""
from .bid import BidResult, Line, build_bid, evaluate

__all__ = ["BidResult", "Line", "build_bid", "evaluate"]
