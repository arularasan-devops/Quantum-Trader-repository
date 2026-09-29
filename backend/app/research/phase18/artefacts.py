"""Writing the CAS reports to disk — ten JSON/Markdown pairs plus the questions.

The Markdown is what a person reads, so each page opens with the sample it rests
on and the paper-only banner, and the numbers appear after. A page that would
otherwise print a table of encouraging figures over eleven legs prints
``REQUIRES_MORE_DATA`` instead.
"""
from __future__ import annotations

import json
from pathlib import Path

from app.research.phase18 import reports, safety, schema

BANNER = (
    "**CAS PAPER ONLY — research. No order is placed by any part of this phase, "
    "and nothing here is promoted into production automatically.**"
)


def _write(outdir: str, name: str, payload: dict) -> str:
    p = Path(outdir).expanduser() / f"{name}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return str(p)


def _write_md(outdir: str, name: str, text: str) -> str:
    p = Path(outdir).expanduser() / f"{name}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return str(p)


def _table(head: tuple[str, ...], rows: list[tuple[str, ...]]) -> str:
    if not rows:
        return "_No rows._\n"
    out = ["| " + " | ".join(head) + " |",
           "|" + "|".join(["---"] * len(head)) + "|"]
    out += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(out) + "\n"


def _s(v: object) -> str:
    if v is None:
        return "n/a"
    if isinstance(v, float):
        return f"{v:,.2f}"
    return str(v)


def _quality_header(payloads: dict[str, dict]) -> str:
    cap = payloads.get("phase18_cas_capture", {})
    gate = cap.get("quality") if isinstance(cap.get("quality"), dict) else {}
    recon = cap.get("reconciliation") if isinstance(
        cap.get("reconciliation"), dict) else {}
    return (
        f"Capture: **{_s(gate.get('match_rate_pct'))}% exact or near-exact** "
        f"against a {_s(gate.get('target_pct'))}% target "
        f"(`{_s(gate.get('status'))}`). "
        f"{_s(recon.get('observations'))} observations, "
        f"{_s(recon.get('resolved'))} resolved paper legs, "
        f"{_s(recon.get('unresolved'))} unresolved.\n"
    )


def _render(name: str, payloads: dict[str, dict]) -> str:
    payload = payloads.get(name, {})
    lines = [f"# {name.replace('_', ' ').title()}", "", BANNER, "",
             _quality_header(payloads), ""]

    if name == "phase18_cas_validation":
        ev = payload.get("evidence") if isinstance(
            payload.get("evidence"), dict) else {}
        checks = ev.get("checks") if isinstance(ev.get("checks"), dict) else {}
        lines += [
            f"**Verdict: `{_s(payload.get('verdict'))}`** — "
            f"{_s(payload.get('reason'))}", "",
            "## Minimum evidence (§26)", "",
            _table(("check", "have", "need", "met"), [
                (k, _s(v.get("have")), _s(v.get("need")),
                 "yes" if v.get("ok") else "no")
                for k, v in checks.items()
            ]),
            "## Chronological folds", "",
            _table(("fold", "legs", "priced", "net expectancy R", "PF"), [
                (fold, _s(st.get("legs")), _s(st.get("priced")),
                 _s(st.get("net_expectancy_r")), _s(st.get("profit_factor")))
                for fold, st in (payload.get("folds") or {}).items()
            ]),
            f"Rows before {schema.CAS_START_DATE} excluded as "
            f"`{schema.PRE_CAS_REGIME}`: "
            f"{_s(payload.get('excluded_pre_cas_rows'))}.", "",
        ]
    elif name == "cas_strategy_report":
        lines += [
            f"Cells compared across all experiments: "
            f"**{_s(payload.get('total_comparisons'))}**. "
            f"{_s(payload.get('multiple_testing_note'))}", "",
        ]
        for key in ("entry_timing", "ce_vs_pe", "expiry"):
            block = payload.get(key)
            if not isinstance(block, dict):
                continue
            cells = block.get("cells") if isinstance(
                block.get("cells"), dict) else {}
            lines += [
                f"## {key.replace('_', ' ')}", "",
                _table(("cell", "priced", "net expectancy R", "PF",
                        "median MFE", "worthless %", "rankable"), [
                    (k, _s(v.get("priced")), _s(v.get("net_expectancy_r")),
                     _s(v.get("profit_factor")), _s(v.get("median_mfe_points")),
                     _s(v.get("worthless_pct")),
                     "yes" if v.get("rankable") else "no")
                    for k, v in cells.items()
                ]),
                f"Best: `{_s((block.get('best') or {}).get('cell'))}` — "
                f"{_s((block.get('best') or {}).get('reason'))}", "",
            ]

    lines += ["## Payload", "", "```json",
              json.dumps(payload, indent=2, default=str)[:120_000], "```", ""]
    return "\n".join(lines)


def _questions_md(qa: list[dict]) -> str:
    lines = ["# CAS — the twenty questions", "", BANNER, "",
             _table(("#", "question", "answer", "basis"), [
                 (str(i + 1), q["question"], _s(q.get("answer")),
                  _s(q.get("basis")))
                 for i, q in enumerate(qa)
             ])]
    return "\n".join(lines)


def build_all(
    *,
    outdir: str,
    observations: list[dict],
    paper_rows: list[dict],
    coverage: list[dict],
    overnight_rows: list[dict] | None = None,
    cards: list[dict] | None = None,
) -> dict:
    payloads = reports.build_payloads(
        observations=observations, paper_rows=paper_rows, coverage=coverage,
        overnight_rows=overnight_rows, cards=cards,
    )
    written: list[str] = []
    for name in reports.REPORTS:
        written.append(_write(outdir, name, payloads.get(name, {})))
        written.append(_write_md(outdir, name, _render(name, payloads)))

    qa = reports.answers(payloads)
    written.append(_write(outdir, "phase18_cas_questions", {"answers": qa}))
    written.append(_write_md(outdir, "phase18_cas_questions", _questions_md(qa)))

    scan = safety.scan()
    written.append(_write(outdir, "phase18_cas_safety", scan))
    return {
        "written": written,
        "payloads": payloads,
        "answers": qa,
        "safety": scan,
        "verdict": payloads.get("phase18_cas_validation", {}).get("verdict"),
    }
