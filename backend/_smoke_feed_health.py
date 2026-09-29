"""Feed-transport checks: the WebSocket/REST split must be VISIBLE and must not
be able to hold a tick.

Why this file exists: a live log showed 42 minutes in which the push socket never
stayed up, so every price came from the 3-second REST fallback, and one of those
REST calls stalled for 7 seconds on connect. Both facts were invisible — the
socket logged nothing when it died, and the SDK's default timeout is long enough
to hold an exit. These checks pin the fixes:

* every Angel REST call is capped well under the SDK default;
* socket drops/rebuilds are counted and logged, so a flapping feed is diagnosable
  instead of reading as "the broker is slow today";
* the diagnostic reports the drop history, not just a snapshot of "connected".

Static/source checks only: they must pass with no broker credentials and place no
orders.
"""
from __future__ import annotations

import inspect
import re
import sys

from app.market import angelone

FAILED: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        FAILED.append(name)


src = inspect.getsource(angelone)

# --- 1. a stalled REST call cannot hold a tick -------------------------------
check(
    "every Angel REST call is capped by an explicit timeout",
    angelone._REST_TIMEOUT_SEC <= 5,
    f"{angelone._REST_TIMEOUT_SEC}s",
)
check(
    "and the timeout is actually passed to the SDK client, not just defined",
    re.search(r"SmartConnect\(api_key=creds\[\"key\"\], timeout=_REST_TIMEOUT_SEC\)", src) is not None,
    "login passes timeout=",
)
check(
    "the cap is below the 7s connect stall seen in the live log",
    angelone._REST_TIMEOUT_SEC < 7,
    "an exit must not wait 7s on a dead network",
)

# --- 2. a flapping socket is visible ----------------------------------------
stream_src = inspect.getsource(angelone._LiveStream)
for field in ("_drops", "_rebuilds", "_last_drop_at", "_last_error"):
    check(
        f"the stream tracks {field}",
        f"self.{field}" in stream_src,
        "lifecycle counters exist",
    )
check(
    "a socket close is logged, not swallowed",
    "_log.warning" in inspect.getsource(angelone._LiveStream._on_close),
    "on_close logs",
)
check(
    "a socket error is logged with its reason",
    "_log.warning" in inspect.getsource(angelone._LiveStream._on_error),
    "on_error logs",
)
check(
    "the error text is redacted before it is logged or reported",
    "_redact_secrets" in inspect.getsource(angelone._LiveStream._on_error),
    "no JWT/api-key in logs",
)
check(
    "a rebuild says how long the socket was dead and why",
    "rebuilding" in inspect.getsource(angelone._LiveStream._watch),
    "watchdog logs",
)
check(
    "a successful (re)connect is logged too, so recovery is visible",
    "_log.info" in inspect.getsource(angelone._LiveStream._on_open),
    "on_open logs",
)

# --- 3. the diagnostic reports history, not just a snapshot -----------------
health_src = inspect.getsource(angelone.feed_health)
for field in ("ws_drops", "ws_rebuilds", "last_drop_age_sec", "last_ws_error"):
    check(
        f"/api/feed-health reports {field}",
        f'"{field}"' in health_src,
        "a snapshot of 'connected' cannot answer 'is it flapping'",
    )
check(
    "feed-health masks the client code",
    "_mask(client)" in health_src,
    "no credentials in a diagnostic",
)
check(
    "and it never places or cancels an order",
    "placeOrder" not in health_src and "cancelOrder" not in health_src,
    "read-only",
)

# --- 4. REST fallback stays rate-limit-safe ---------------------------------
check(
    "the REST fallback polls no faster than 3s (it shares Angel's gateway budget)",
    "max(3.0, settings.tick_interval_seconds)" in src,
    "quote polling does not starve the candle calls",
)
check(
    "push mode does not call the REST quote endpoint at all",
    "if not streaming and now - self._last_quote_at" in src,
    "a healthy socket costs no REST budget",
)

# --- 5. the watchdog does not fight a shut exchange ------------------------
# 05:35 IST (00:05 UTC) is four hours before any session: silence there is the
# exchange being closed, and a rebuild only burns a login and a JWT.
check(
    "no rebuild before the session opens",
    angelone._exchange_session_live(1_788_134_700.0) is False,
    "'dead for 94s' at 05:35 IST was a shut exchange, not a broken socket",
)
check(
    "the socket is allowed up before the first tick",
    angelone._exchange_session_live(1_788_147_000.0) is True,
    "subscribed at 09:05 IST, not at the first tick",
)
check(
    "the MCX evening session still counts as live",
    angelone._exchange_session_live(1_788_197_400.0) is True,
    "an equity-only clock would abandon the socket at 15:30",
)
check(
    "the watchdog consults the session clock before declaring death",
    "_exchange_session_live(now)" in inspect.getsource(angelone._LiveStream._watch),
    "otherwise the dead-clock runs all night",
)
# SmartApi 1.5.5 declares _on_close(self, wsapp); websocket-client 1.7 calls it
# with (wsapp, status, msg). Without the shim the SDK callback raises and our
# own on_close never runs, so a dropped socket reads as connected.
check(
    "the SDK's close callback is made arity-tolerant on the instance",
    "sws._on_close = lambda wsapp, *_ignored: sws.on_close(wsapp)"
    in inspect.getsource(angelone._LiveStream._build_socket),
    "a TypeError in the SDK callback hides the disconnect from us",
)

print()
if FAILED:
    print(f"{len(FAILED)} CHECK(S) FAILED: {FAILED}")
    sys.exit(1)
print("ALL FEED-TRANSPORT CHECKS PASSED")
