"""Smoke: audible BUY / bot-fill alerts.

The alert is only worth having if it fires on facts and fires once. The three
ways the previous in-screen chime failed are what these checks pin down:

* it only sounded while the Signals screen was mounted;
* its poll was skipped while the browser tab was hidden — precisely when a sound
  is the only thing that can reach you;
* it announced a BUY row, which is an opinion. A fill is a fact, and the two are
  different: the risk governor, the premium floor and the concurrency cap all
  refuse surfaced BUYs.
"""
from __future__ import annotations

import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

_TMP = tempfile.mkdtemp(prefix="qt-smoke-alerts-")
os.environ["QT_DATA_DIR"] = _TMP

from app.config import settings  # noqa: E402

settings.data_dir = _TMP

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

FAILED: list[str] = []
HERE = os.path.dirname(os.path.abspath(__file__))
FRONTEND = os.path.abspath(os.path.join(HERE, "..", "frontend"))


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name}{(' — ' + detail) if detail else ''}")
    if not ok:
        FAILED.append(name)


# --- 1. the fills endpoint: what an alert may be driven by -------------------
with TestClient(app) as client:
    body = client.get("/api/fills").json()
    check(
        "/api/fills answers with an open-position list",
        isinstance(body.get("open"), list) and "open_count" in body,
        f"{body.get('open_count')} open",
    )
    check(
        "with nothing open it is empty rather than absent",
        body["open_count"] == len(body["open"]),
        f"{body['open_count']}",
    )

    # Simulate a filled option position on the cached snapshot, exactly as the
    # bot's entry would leave it, and confirm it surfaces with a stable id.
    from app.main import hub  # noqa: E402

    inst, snap = next(iter(hub.latest.items()))
    snap.position.option_symbol = "CRUDEOIL25AUG6000CE"
    snap.position.entry_premium = 255.0
    snap.position.quantity_lots = 1
    snap.position.entry_time = 1_786_600_000
    filled = client.get("/api/fills").json()
    row = next((r for r in filled["open"] if r["symbol"] == "CRUDEOIL25AUG6000CE"), None)
    check(
        "a filled option position is reported",
        row is not None,
        f"{[r['symbol'] for r in filled['open']]}",
    )
    if row is not None:
        check(
            "the id is stable across polls, so one fill cannot alert twice",
            row["id"] == client.get("/api/fills").json()["open"][0]["id"]
            and str(snap.position.entry_time) in row["id"],
            row["id"],
        )
        check(
            "and it carries what the alert has to say out loud",
            row["book"] == "options" and row["lots"] == 1 and row["entry"] == 255.0,
            f"{row['book']} {row['lots']} lot @ {row['entry']}",
        )
    # A position with no entry time is a stale/empty slot, not a fill.
    snap.position.entry_time = None
    cleared = client.get("/api/fills").json()
    check(
        "a position with no entry time is not announced as a fill",
        all(r["symbol"] != "CRUDEOIL25AUG6000CE" for r in cleared["open"]),
        f"{[r['symbol'] for r in cleared['open']]}",
    )
    snap.position.option_symbol = None
    check(
        "the endpoint places no order (read-only)",
        "buy(" not in open(os.path.join(HERE, "app", "main.py"), encoding="utf-8")
        .read()
        .split("async def fills")[1]
        .split("@app.")[0],
        "read-only",
    )

# --- 2. the alert engine's invariants ---------------------------------------
alerts = open(os.path.join(FRONTEND, "lib", "alerts.ts"), encoding="utf-8").read()
check(
    "signal and fill have SEPARATE sounds and switches",
    "signalTone" in alerts and "fillTone" in alerts and "fillSound" in alerts and "signalSound" in alerts,
    "two facts, two tones",
)
check(
    "fills are read from /api/fills, not inferred from a board row",
    "/api/fills" in alerts,
    "driven by real positions",
)
check(
    "it does NOT skip polling while the browser tab is hidden",
    "document.hidden" not in alerts,
    "a sound is for when you are not looking",
)
check(
    "the first response is primed, so pre-existing BUYs/positions stay silent",
    "primedSignals" in alerts and "primedFills" in alerts,
    "no alert storm on page load",
)
check(
    "an already-alerted signal/fill is remembered, so a 4s poll cannot repeat it",
    "seenSignals" in alerts and "seenFills" in alerts,
    "once per event",
)
check(
    "a signal that leaves the board is forgotten, so it can alert again later",
    re.search(r"seenSignals\.forEach[\s\S]{0,120}delete", alerts) is not None,
    "re-arms",
)
check(
    "blocked autoplay is surfaced instead of failing silently",
    "blocked" in alerts and "pointerdown" in alerts,
    "browsers suspend audio until a gesture",
)
check(
    "the mute choice and gate persist across a reload",
    "localStorage" in alerts and re.search(r"qt_alerts_v\d", alerts) is not None,
    "settings survive",
)

# --- 3. mounted page-level, and off the Signals screen's lifecycle -----------
page = open(os.path.join(FRONTEND, "app", "page.tsx"), encoding="utf-8").read()
check(
    "the alert engine starts at page level, not inside one tab",
    "startAlerts" in page and "<AlertToast />" in page,
    "fires from any tab",
)
# The separate Signals screen was REMOVED at the user's request: the production
# signal lives on the Signal dashboard, and a second screen showing tradeable
# BUYs is a second place to take a trade from. Its files must be gone, not merely
# unrouted, or the next reader wires them back in.
for gone in ("SignalsView.tsx", "SignalsScreener.tsx"):
    check(
        f"the removed Signals screen leaves no orphan ({gone})",
        not os.path.exists(os.path.join(FRONTEND, "components", gone)),
        "deleted",
    )
check(
    "nothing routes to a Signals view any more",
    "SignalsView" not in page and '"signals"' not in page,
    "one signal screen",
)
bell = open(os.path.join(FRONTEND, "components", "AlertBell.tsx"), encoding="utf-8").read()
check(
    "a tone check is reachable, so silence can be told from no-signal",
    "testAlert" in bell,
    "verifiable",
)
check(
    "the toast distinguishes a signal from an actual purchase in words",
    "BOT BOUGHT" in bell and "NEW BUY SIGNAL" in bell,
    "not just a different pitch",
)

# --- the MAIN dashboard's own call card must sound too --------------------
# The user's point: the sound has to reach the screen they sit on. The board
# poll is not enough on its own — a card can flip to BUY between two 4s polls,
# and a name under the board's alert gate never enters that list at all.
alerts = open(os.path.join(FRONTEND, "lib", "alerts.ts"), encoding="utf-8").read()
check(
    "the main dashboard's BUY is its own alert trigger",
    "noteDashboardSignal" in alerts and "noteDashboardSignal" in page,
    "wired from the live snapshot",
)
check(
    "it fires on the transition INTO buy, not on every tick of a standing BUY",
    "previous === signal) return" in alerts,
    "de-duplicated",
)
check(
    "the first reading for an instrument is silent (page load / instrument switch)",
    "previous === undefined" in alerts,
    "primed, not announced",
)
check(
    "the dashboard trigger obeys the same mute switch",
    "state.signalSound," in alerts.split("noteDashboardSignal")[1],
    "one mute for both",
)
check(
    "the sound controls live on the Signal dashboard header",
    "<AlertBell compact />" in page,
    "page.tsx header",
)
check(
    "the tone check is reachable in compact mode, so audio can be proven there",
    "testAlert" in bell and "testAlert" not in bell.split("{!compact && (")[1].split("</>")[0],
    "outside the non-compact block",
)

# --- sound scope + distinct tones (the user's request) ----------------------
# One tone for every event made the sound uninformative: an EXIT that chimes like
# an entry is worse than silence. Four events, four tones, and audio only on the
# screen the trade is taken from.
check(
    "audio is scoped to the Signal dashboard, and the scope is what gates play",
    "setSoundScope" in alerts and "state.soundScope" in alerts
    and "setSoundScope(view === \"signal\")" in page,
    "other tabs stay silent",
)
check(
    "the event log still records events while muted or off-screen",
    re.search(r"if \(enabled && state\.soundScope\) play\(\);", alerts) is not None,
    "history is not lost with the chime",
)
tones = re.findall(r"export function (\w+Tone)\(", alerts)
check(
    "signal, fill, decision and momentum each have their own tone",
    {"signalTone", "fillTone", "decisionTone", "momentumTone"} <= set(tones),
    ",".join(sorted(tones)),
)
_bodies = {
    name: alerts.split(f"export function {name}(")[1].split("\n}")[0]
    for name in ("signalTone", "fillTone", "decisionTone", "momentumTone")
}
check(
    "the four tones are actually different sounds, not the same notes renamed",
    len({re.sub(r"\s+", "", b) for b in _bodies.values()}) == 4,
    "distinguishable by ear",
)
check(
    "decision-state changes and momentum calls are wired from the dashboard",
    "noteDecisionState" in alerts and "noteDecisionState" in page
    and "noteMomentumSignal" in alerts and "noteMomentumSignal" in page,
    "both engines announce themselves",
)
check(
    "each of them is de-duplicated on transition, like the BUY trigger",
    "lastDashPosition" in alerts and "lastMomentum" in alerts,
    "no repeat on every poll",
)

print()
if FAILED:
    print(f"{len(FAILED)} CHECK(S) FAILED: {FAILED}")
    sys.exit(1)
print("ALL ALERT CHECKS PASSED")
