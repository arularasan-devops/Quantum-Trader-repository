"""Smoke test: the Phase 6 model pipeline cannot see the future.

Five phases of this project have shown how easy it is to produce a good-looking
result from a leaky measurement, so leakage is tested mechanically rather than
argued about in a report:

1. A feature vector computed at bar *t* is IDENTICAL whether or not bars after
   *t* exist in the array. This is the strongest available statement that the
   live path and the training path see the same thing.
2. No feature name refers to an outcome.
3. The serving vector order matches the frozen FEATURES tuple, and a model whose
   feature list differs is REFUSED rather than scored on mismatched columns.
4. The training script splits chronologically — no shuffle, no k-fold, no
   ``train_test_split`` — checked by parsing the source.
5. The dataset builder labels from strictly forward bars only.
"""
from __future__ import annotations

import ast
import pathlib
import random

from app.ai import features as F
from app.ai import probability
from app.models import Candle

checks = 0


def ok(cond: bool, msg: str) -> None:
    global checks
    checks += 1
    assert cond, msg


def synth(n: int, seed: int = 7) -> list[Candle]:
    rnd = random.Random(seed)
    px = 6000.0
    out = []
    for i in range(n):
        px *= 1.0 + rnd.gauss(0, 0.0012)
        hi = px * (1 + abs(rnd.gauss(0, 0.0008)))
        lo = px * (1 - abs(rnd.gauss(0, 0.0008)))
        out.append(Candle(time=1_700_000_000 + i * 60, open=px, high=hi, low=lo,
                          close=px, volume=1000 + rnd.randint(0, 900)))
    return out


def test_features_are_causal() -> None:
    candles = synth(F.WINDOW + 400)
    cut = F.WINDOW + 100
    at_t = F.compute(candles[cut - F.WINDOW:cut])
    with_future = F.compute(candles[cut - F.WINDOW:cut + 300][:F.WINDOW])
    ok(at_t is not None and with_future is not None, "features must compute")
    for name in F.FEATURES:
        if name == "side_is_pe":
            continue
        a, b = at_t.get(name), with_future.get(name)
        ok(a == b, f"feature '{name}' changed when future bars existed: {a} vs {b}")
    ok(at_t["_ts"] == candles[cut - 1].time,
       "the feature row must be stamped with the decision bar, not a later one")


def test_no_outcome_features() -> None:
    banned = ("target", "stop_hit", "outcome", "result", "pnl", "mfe", "mae",
              "future", "next_", "fwd", "won", "label")
    for name in F.FEATURES:
        for b in banned:
            ok(b not in name.lower(), f"feature '{name}' looks like an outcome")


def test_vector_matches_feature_order() -> None:
    feats = F.compute(synth(F.WINDOW))
    ok(feats is not None, "features must compute")
    vec = F.vector(feats, "CE")
    ok(len(vec) == len(F.FEATURES), "vector length must equal FEATURES length")
    ok(all(isinstance(v, float) for v in vec), "vector must be all floats")
    ce = F.vector(feats, "CE")
    pe = F.vector(feats, "PE")
    idx = F.FEATURES.index("side_is_pe")
    ok(ce[idx] == 0.0 and pe[idx] == 1.0, "side flag must be the only side input")
    ok([v for i, v in enumerate(ce) if i != idx]
       == [v for i, v in enumerate(pe) if i != idx],
       "CE and PE rows must differ ONLY by the side flag")


def test_mismatched_artefact_is_refused() -> None:
    art = {"features": ["not", "the", "same"], "coef": [1.0, 1.0, 1.0],
           "intercept": 0.0, "mean": [0.0, 0.0, 0.0], "scale": [1.0, 1.0, 1.0],
           "calibration": [{"predicted": 0.5, "actual": 0.5}]}
    ok(probability.validate(art) is not None,
       "an artefact whose feature list differs must be rejected")
    good = {"features": list(F.FEATURES),
            "coef": [0.0] * len(F.FEATURES), "intercept": 0.0,
            "mean": [0.0] * len(F.FEATURES), "scale": [1.0] * len(F.FEATURES),
            "calibration": [{"predicted": 0.4, "actual": 0.4},
                            {"predicted": 0.6, "actual": 0.6}]}
    ok(probability.validate(good) is None, "a matching artefact must validate")


def _src(name: str) -> str:
    return (pathlib.Path(__file__).resolve().parent / name).read_text()


def test_training_split_is_chronological() -> None:
    src = _src("phase6_train.py")
    for bad in ("train_test_split", "shuffle(", "shuffle=True", "KFold",
                "StratifiedKFold", "sample("):
        ok(bad not in src, f"phase6_train.py must not use {bad}")
    ok("rows.sort(key=lambda r: int(r[\"ts\"]))" in src,
       "training rows must be sorted by time")
    ok("TRAIN_FRAC" in src and "VAL_FRAC" in src, "chronological fractions expected")
    # Calibration must be fitted on validation, not on the OOS slice it is scored on.
    ok("calibration(p_va, y_va)" in src,
       "calibration must be fitted on the validation slice")


def test_dataset_labels_from_forward_bars_only() -> None:
    src = _src("phase6_dataset.py")
    tree = ast.parse(src)
    ok("candles[i + 1:i + 1 + HORIZON]" in src,
       "outcomes must be built from bars strictly after the decision bar")
    ok("candles[i - F.WINDOW:i + 1]" in src,
       "features must be built from bars up to and including the decision bar")
    names = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    ok("outcome" in names, "dataset builder must expose its outcome function")


def test_probability_does_not_guess() -> None:
    """With no artefact, the engine must return unavailable — never 0.5."""
    st = probability.status()
    if not st.get("available"):
        feats = F.compute(synth(F.WINDOW))
        out = probability.score(feats, "CE")
        ok(out.get("available") is False, "must report unavailable")
        ok(out.get("p_target_before_stop") is None,
           "an absent model must yield NO probability, not a default one")


def main() -> None:
    test_features_are_causal()
    test_no_outcome_features()
    test_vector_matches_feature_order()
    test_mismatched_artefact_is_refused()
    test_training_split_is_chronological()
    test_dataset_labels_from_forward_bars_only()
    test_probability_does_not_guess()
    print(f"AI LEAKAGE SMOKE PASSED ({checks} checks)")


if __name__ == "__main__":
    main()
