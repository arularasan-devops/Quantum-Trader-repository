"""Direction engine — which side has the better conditional probability?

Construction, and why it is this one:

Both sides are scored by the SAME probability model on the SAME bar, differing
only in the ``side_is_pe`` column, exactly as they were measured in Phase 5. The
side preference is then the normalised pair. That means:

* No historical side bias is hard-coded. Phase 5 tested the Phase 3 "NIFTY PE
  advantage" on real underlying data and it did not replicate (−0.005R, CI
  [−0.019, +0.008], sign flipped), so it is deliberately absent — the model has
  to earn a side preference from the current bar's features every time.
* Because the model sees the current move's direction, a side preference here is
  a *conditional* statement about this bar, not a standing belief about the
  instrument.
* When the model is unavailable there is no fallback guess. The engine reports
  no side preference and the orchestrator cannot buy. A trend-derived side would
  look identical to the caller while carrying none of the evidence.

For futures the same two numbers are relabelled LONG/SHORT: the underlying move
being modelled is the same one.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.ai import probability


@dataclass(frozen=True)
class Direction:
    available: bool = False
    p_ce: float | None = None          # normalised side preference
    p_pe: float | None = None
    p_target_ce: float | None = None   # raw calibrated P(target before stop)
    p_target_pe: float | None = None
    side: str | None = None            # CE | PE | None when neither is preferred
    edge: float = 0.0                  # |p_ce - p_pe|
    agrees_with_move: bool | None = None
    reasons: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict:
        return {
            "available": self.available,
            "p_ce": self.p_ce, "p_pe": self.p_pe,
            "p_long": self.p_ce, "p_short": self.p_pe,   # futures naming
            "p_target_ce": self.p_target_ce,
            "p_target_pe": self.p_target_pe,
            "preferred_side": self.side,
            "edge": round(self.edge, 4),
            "agrees_with_move": self.agrees_with_move,
            "reasons": list(self.reasons),
        }


def evaluate(feats: dict, direction_bias: str = "FLAT") -> Direction:
    """Side preference for this bar. ``direction_bias`` is the move already in
    place, used only to report whether the preference follows or fades it."""
    ce = probability.score(feats, "CE")
    pe = probability.score(feats, "PE")
    if not (ce.get("available") and pe.get("available")):
        return Direction(reasons=(ce.get("reason") or "probability model unavailable",))

    pc = float(ce["p_target_before_stop"])
    pp = float(pe["p_target_before_stop"])
    total = pc + pp
    if total <= 0:
        return Direction(reasons=("degenerate probabilities",))
    n_ce, n_pe = pc / total, pp / total
    edge = abs(n_ce - n_pe)

    from app.config import settings

    if edge < settings.ai_min_direction_edge:
        side: str | None = None
        reasons = (f"no side preference: edge {edge:.3f} < "
                   f"{settings.ai_min_direction_edge:.3f}",)
    else:
        side = "CE" if n_ce > n_pe else "PE"
        reasons = (f"{side} preferred: P(target) {max(pc, pp):.3f} vs "
                   f"{min(pc, pp):.3f} on the same bar",)

    agrees = None
    if side and direction_bias in ("UP", "DOWN"):
        agrees = (side == "CE") == (direction_bias == "UP")
        reasons += (("preference follows the current move" if agrees else
                     "preference FADES the current move"),)

    return Direction(
        available=True, p_ce=round(n_ce, 4), p_pe=round(n_pe, 4),
        p_target_ce=round(pc, 4), p_target_pe=round(pp, 4),
        side=side, edge=edge, agrees_with_move=agrees, reasons=reasons,
    )
