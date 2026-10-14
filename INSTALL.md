# Install — UI cleanup + room fix + Phase 20 (one overlay)

Cumulative. It contains everything from `qt_ui_cleanup.zip` and `qt_room_fix.zip`
as well as Phase 20, so there is no install order to get wrong. Installing it
over any build from Phase 19 onward is enough — do not unzip the older overlays
afterwards, they would put back older copies of the same files.

```bash
cd ~/projects/phase8_delivery
unzip -o "/mnt/c/Users/djs admin/Downloads/qt_phase20_full.zip" -d .

cd backend
.venv/bin/python _smoke_phase17.py    # checked 207
.venv/bin/python _smoke_phase18.py    # checked 145
.venv/bin/python _smoke_phase19.py    # checked 215
.venv/bin/python _smoke_phase20.py    # checked 80
```

If any of those four does not print `OK`, stop and send the output — nothing here
is needed to trade, so running the previous build costs nothing.

```bash
cd .. && ./run.sh
```

Nothing under `backend/data/` is in this archive, so every recorded observation,
paper leg and journal row survives the install.

## What to run once it is in

```bash
cd backend
.venv/bin/python phase20_universe.py
.venv/bin/python phase20_universe.py --trades ~/market_scan_pool.json
.venv/bin/python phase19_grading_audit.py --sessions 2 --opportunities
```

The first prints the research universe and, per instrument, which questions its
data can answer. The second adds the five-year direction study per name. The
third ends in a `room A/B` section. All three are read-only.

## The room fix

`room` used to be unmeasurable on any candidate the engine was not itself
recommending, because a stop and target are published only for the recommended
leg. A candidate with no target scored no `room`, so it could never be A+ — which
is why a session with thirty candidates produced one A+ and twenty
`PENDING_DATA` rows that were never going to resolve.

Where the engine publishes a target, that target is still used. Only where there
is none is a research T1 derived as `entry + |expected move| x |delta|`, and the
row is stamped `MODELLED_FROM_EXPECTED_MOVE` instead of `ENGINE_TARGET`. A
modelled row cannot enter the costed paper book, is never counted promotable, and
scores no `move_coverage` — the target comes from the expected move, so scoring
coverage against it would award a free 100. Promotion and out-of-sample samples
stay `ENGINE_TARGET` only.

## Phase 20 — the research universe

65 instruments: the five indexes, CRUDEOIL / NATURALGAS / GOLD / SILVER /
COPPER, and the liquid optionable equities. Every name is studied and ranked on
its own. Asking for one statistic across instruments returns a refusal, not a
mean, because a single mean hides the instrument that produced the loss.

What each name may be asked is recorded per instrument rather than assumed:

- five-year direction study — indexes and equities, where a cash series exists;
- **never for MCX** — a commodity has no cash series, its history starts at the
  listing of the current futures contract, so a five-year study is not
  comparable and is not offered;
- **historical option profitability — for no instrument at all.** Expired option
  chains are not retrievable, so no historical option P&L can be honest. This is
  a constant in the code, not a threshold to relax later. Option economics come
  only from live exact-timestamp capture and the costed paper book.

Futures vs CE vs PE are compared only at the same instrument, signal, session and
direction, within a five-minute window, with both legs costed; `BULLISH`/`LONG`
are normalised so equivalent directions actually pair. Several strikes of one
vehicle at one signal are averaged, so a vehicle cannot be credited with its
luckiest strike. Below 30 same-signal comparisons no vehicle preference is
reported.

No instrument may be called profitable without enough costed paper outcomes and a
chronological holdout. Missing evidence is reported as missing, never as zero.

## The out-of-sample bar is now symmetric

A negative finding was already downgraded when the holdout did not keep the sign
or a fold flipped. The same now applies to a positive one. Phase 20 ranks a
universe on these labels, so a positive carried by one stretch of the history has
to be treated exactly as a negative is:

`VALIDATED_NEGATIVE`, `NEGATIVE_BUT_UNDER_BAR`, `INDISTINGUISHABLE_FROM_ZERO`,
`POSITIVE_BUT_UNDER_BAR`, `VALIDATED_POSITIVE`, `INSUFFICIENT_DATA`.

On the five-year pool only ICICIBANK's prior clears the bar, so it is the only
name refused research admission. The five repeatedly negative stocks are admitted
as low-ranked candidates that still carry a negative label — if you want them out
by explicit policy instead, say so and it becomes a named exclusion rather than a
consequence of the bar.

## Capture is no longer index-only

Vehicle capture now covers every research-admitted name, so the Evidence and A+
Paper tabs will show CRUDEOIL, GOLD, SBIN and the rest. It adds no broker calls —
capture happens inside the tick that already fetched that chain — so the cost is
disk, roughly 12 KB per observation row.

To narrow it to a bounded watchlist:

```bash
QT_PHASE17_UNIVERSE=NIFTY,BANKNIFTY,CRUDEOIL,GOLD ./run.sh
```

## What did not change

Production Option, Futures and CAS signals, thresholds, strike selection, exits,
confidence, Signal Score and the broker order path are all untouched. Real orders
remain disabled. Phase 20 places no order and is read by no production signal.
