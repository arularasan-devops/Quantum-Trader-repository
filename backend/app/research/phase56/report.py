"""The §36 report, plain text, one screen of it that decides the phase."""
from __future__ import annotations

from app.research.phase56 import REQUIRED_FOR_STAGE_2


def _rule(char: str = "=") -> str:
    return char * 78


def render(assessment: dict) -> str:
    out: list[str] = []
    out.append(_rule())
    out.append("PHASE 56 — CASH_EQUITY_LONG_ONLY_DISCOVERY")
    out.append("STAGE 1 — §36 DATA AVAILABILITY GATE (no strategy is evaluated here)")
    out.append(_rule())
    out.append(f"VERSION            {assessment['version']}")
    out.append(f"FINGERPRINT        {assessment['fingerprint']}")
    out.append(f"AS_OF              {assessment['as_of']}")
    out.append(f"SOURCE_SECURITIES  {assessment['securities_in_master']} NSE '-EQ' rows, present day")
    out.append("")

    out.append(_rule("-"))
    out.append("§36 FIELDS")
    out.append(_rule("-"))
    out.append(f"{'FIELD':<34}{'STATE':<14}{'COVERAGE':<10}")
    for field in assessment["fields"]:
        out.append(f"{field['field']:<34}{field['state']:<14}{field['coverage']:<10}")
    out.append("")
    for field in assessment["fields"]:
        out.append(f"{field['field']}")
        out.append(f"    {field['detail']}")
    out.append("")

    out.append(_rule("-"))
    out.append("SURVIVORSHIP")
    out.append(_rule("-"))
    universes = assessment["universes"]
    survivor = universes["CURRENT_SURVIVOR_UNIVERSE"]
    correct = universes["HISTORICALLY_CORRECT_UNIVERSE"]
    out.append(f"B CURRENT_SURVIVOR_UNIVERSE      {survivor['securities']:>6}  {survivor['role']}")
    out.append(f"A HISTORICALLY_CORRECT_UNIVERSE  {correct['securities']:>6}  {correct['role']}")
    out.append(f"    {correct.get('reason', '')}")
    out.append(f"STATUS  {assessment['survivorship_control_status']}")
    out.append("")

    vanished = assessment["probes"]["vanished_securities"]
    out.append(_rule("-"))
    out.append("VANISHED-SECURITY PROBE (declared before measurement)")
    out.append(_rule("-"))
    out.append(f"{'SYMBOL':<14}{'EVENT_DATE':<13}{'EVENT':<28}{'IN_MASTER':<11}{'OUTCOME':<14}")
    for row in vanished["rows"]:
        out.append(
            f"{row['symbol']:<14}{row['event_date']:<13}{row['event']:<28}"
            f"{str(row['in_master']):<11}{row['outcome']:<14}"
        )
    out.append(
        f"IN_MASTER {vanished['in_master']}/{vanished['probed']}   "
        f"HISTORY_REACHABLE {vanished['history_reachable']}/{vanished['probed']}   "
        f"INCONCLUSIVE {vanished['inconclusive']}"
    )
    out.append("")

    actions = assessment["probes"]["corporate_actions"]
    out.append(_rule("-"))
    out.append("CORPORATE-ACTION PROBE")
    out.append(_rule("-"))
    out.append(f"{'SYMBOL':<12}{'EX_DATE':<12}{'ACTION':<30}{'EXPECT':>8}{'OBSERVED':>10}  {'RAW_JUMP':<9}")
    for row in actions["rows"]:
        observed = row.get("observed_ratio")
        out.append(
            f"{row['symbol']:<12}{row['ex_date']:<12}{row['action']:<30}"
            f"{row['ratio']:>8.2f}{(f'{observed:.4f}' if observed is not None else '—'):>10}  "
            f"{str(row.get('raw_jump_detected', row['outcome'])):<9}"
        )
    out.append(f"SERIES_VERDICT  {actions['series_verdict']}")
    out.append(f"    {actions['action_record_reason']}")
    out.append("")

    coverage = assessment["probes"]["coverage"]
    out.append(_rule("-"))
    out.append("DAILY-SERIES COVERAGE PROBE")
    out.append(_rule("-"))
    out.append(f"{'SYMBOL':<12}{'FIRST':<12}{'LAST':<12}{'SESSIONS':>9}  {'OUTCOME':<14}")
    for row in coverage["rows"]:
        out.append(
            f"{row['symbol']:<12}{str(row.get('first_session', '—')):<12}"
            f"{str(row.get('last_session', '—')):<12}{row.get('sessions', 0):>9}  "
            f"{row['outcome']:<14}"
        )
    out.append(
        f"HISTORY_FLOOR {coverage.get('history_floor_earliest')} .. "
        f"{coverage.get('history_floor_latest')}    ONE CALL PER SECURITY"
    )
    out.append("")

    blocked = assessment["study_stages_blocked"]
    if blocked:
        out.append(_rule("-"))
        out.append("REQUESTED SECTIONS THAT CANNOT BE RUN ON THIS DATA")
        out.append(_rule("-"))
        seen: set[str] = set()
        for row in blocked:
            key = row["section"]
            if key in seen:
                continue
            seen.add(key)
            out.append(f"{key}")
            out.append(f"    blocked by {row['blocked_by']}")
        out.append("")

    out.append(_rule())
    out.append("VERDICT")
    out.append(_rule())
    out.append(f"DATA_AVAILABLE      {', '.join(assessment['data_available']) or 'none'}")
    out.append(f"DATA_MISSING        {', '.join(assessment['data_missing']) or 'none'}")
    out.append(f"DATA_INCONCLUSIVE   {', '.join(assessment['data_inconclusive']) or 'none'}")
    out.append(f"GATE                {assessment['gate']}")
    out.append(f"REASON              {assessment['gate_reason']}")
    out.append("")
    out.append("ROBUST_CANDIDATES   0 (no mechanism was evaluated; §36 stops the")
    out.append("                    phase before discovery, so this zero is an")
    out.append("                    absence of measurement, not a negative result)")
    out.append("")
    out.append(_rule("-"))
    out.append("WHAT WOULD OPEN THE GATE")
    out.append(_rule("-"))
    for i, item in enumerate(REQUIRED_FOR_STAGE_2, 1):
        out.append(f"{i}. {item}")
    out.append("")
    return "\n".join(out)
