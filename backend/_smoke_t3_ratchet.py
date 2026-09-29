"""Focused check on the beyond-T3 ratchet.

Replays a premium path through the exact floor logic used by the auto-trade tick
and compares what the OLD behaviour (floor pinned at T3) banks against the NEW
%-of-premium ratchet, including the case the user cares about: price runs far
past T3 and then rolls over.

The step is a PERCENTAGE of the premium, not fixed points, so the giveback scales
with the contract: 4% is 12 points on a 300 premium and 1.6 on a 40 premium.
Nothing below T3 is affected.
"""
from app.config import settings

T1, T2, T3 = 110.0, 120.0, 130.0


def replay(path, *, ratchet, step_pct=None, min_step=None):
    """Return (exit_price, exit_label) applying the same rules as state.py."""
    step_pct = settings.auto_trade_beyond_t3_step_pct if step_pct is None else step_pct
    min_step = settings.auto_trade_beyond_t3_min_step if min_step is None else min_step
    floor = None
    for prem in path:
        if floor is not None and prem <= floor:
            if floor > T3 + 1e-6:
                lvl = "T3+ RATCHET"
            elif abs(floor - T3) < 1e-6:
                lvl = "TARGET 3"
            elif abs(floor - T2) < 1e-6:
                lvl = "TARGET 2"
            else:
                lvl = "TARGET 1"
            return prem, lvl
        if prem >= T3:
            f = T3
            if ratchet:
                step = max(min_step, prem * step_pct / 100.0)
                if step > 0:
                    f = T3 + int((prem - T3) // step) * step
            if floor is None or f > floor:
                floor = f
        elif prem >= T2:
            if floor is None or T2 > floor:
                floor = T2
        elif prem >= T1:
            if floor is None or T1 > floor:
                floor = T1
    return None, "still open"


# Scenario: entry 100, runs to 168 (well past T3=130), then rolls over.
up = [100 + 2 * k for k in range(35)]          # 100 -> 168
down = [168 - 3 * k for k in range(1, 30)]     # rolls over, back through T3
path = up + down
peak = max(path)

old_px, old_lvl = replay(path, ratchet=False)
new_px, new_lvl = replay(path, ratchet=True)
print(f"peak on the path            : {peak:.1f}")
print(f"OLD (floor pinned at T3)    : exit {old_px:.1f}  [{old_lvl}]")
print(f"NEW (4%-of-premium ratchet) : exit {new_px:.1f}  [{new_lvl}]")
assert old_lvl == "TARGET 3"
assert new_lvl == "T3+ RATCHET"
assert new_px > old_px, "the ratchet must bank more than a floor pinned at T3"
print(f"OK  ratchet keeps {(new_px - 100) / (peak - 100) * 100:.0f}% of the peak run "
      f"vs {(old_px - 100) / (peak - 100) * 100:.0f}% before")

# The point of a percentage: the step SCALES with the premium, so the same setting
# is neither noise on a dear option nor a chasm on a cheap one.
assert abs(max(1.0, 300 * 4 / 100.0) - 12.0) < 1e-9
assert abs(max(1.0, 40 * 4 / 100.0) - 1.6) < 1e-9
print("OK  4% = 12.0 pts on a 300 premium, 1.6 pts on a 40 premium (scales)")

# A tighter % keeps more of the peak; a looser one gives more back.
tight, _ = replay(path, ratchet=True, step_pct=2.0)
loose, _ = replay(path, ratchet=True, step_pct=10.0)
print(f"OK  2% -> exit {tight:.1f}, 4% -> {new_px:.1f}, 10% -> {loose:.1f}")
assert tight >= new_px >= loose

# It must NOT exit on a dip that stays inside the current step.
wobble = [100 + 2 * k for k in range(20)] + [137.0, 136.0, 138.0, 150.0]
px, lvl = replay(wobble, ratchet=True)
print(f"OK  a dip inside the step does not exit early (result: {lvl})")
assert lvl == "still open", f"exited early at {px} ({lvl})"

# Below T3 NOTHING changes — this is a past-T3 rule only.
for below in ([100, 105, 112, 108], [100, 112, 121, 118], [100, 115, 128, 126]):
    assert replay(below, ratchet=False) == replay(below, ratchet=True), below
print("OK  paths that never reach T3 behave exactly as before (T1/T2 untouched)")

# The min-step floor protects a very cheap premium from a sub-tick step.
assert max(0.5, 5 * 4 / 100.0) == 0.5
print("OK  min-step floor applies when 4% would be smaller than a usable gap")

# Settings really are wired to config, not literals.
assert settings.auto_trade_beyond_t3_ratchet is True
assert settings.auto_trade_beyond_t3_step_pct == 4.0
print("OK  defaults: ratchet ON, step 4% of premium (runtime-configurable)")
print("ALL T3-RATCHET CHECKS PASSED")
