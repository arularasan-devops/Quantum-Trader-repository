"use client";
import { useEffect, useState } from "react";
import {
  DayMoversStatus,
  MoversPreview,
  ScanHealth,
  WatchlistItem,
  getDayMovers,
  getScanHealth,
  getWatchlist,
  getWatchlistMovers,
  setDayMovers,
  setWatchlist,
} from "@/lib/api";

// Above this many enabled instruments the scanner starts visiting each name
// less often, so a BUY elsewhere is seen a cycle or two late. The watched
// instrument's LTP is unaffected (it ticks every cycle regardless) — it is scan
// COVERAGE that degrades, which is why the warning says exactly that.
const SCAN_COMFORT = 20;

export default function WatchlistModal({ onClose }: { onClose: () => void }) {
  const [items, setItems] = useState<WatchlistItem[]>([]);
  const [manual, setManual] = useState(false);
  const [envLocked, setEnvLocked] = useState(false);
  const [saving, setSaving] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [movers, setMovers] = useState<MoversPreview | null>(null);
  const [health, setHealth] = useState<ScanHealth | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [auto, setAuto] = useState<DayMoversStatus | null>(null);

  const pullWatchlist = () =>
    getWatchlist()
      .then((w) => {
        setItems(w.instruments);
        setManual(w.manual);
        setEnvLocked(w.env_locked);
        setLoaded(true);
      })
      .catch(() => setLoaded(true));

  useEffect(() => {
    pullWatchlist();
    getScanHealth().then(setHealth).catch(() => {});
    getDayMovers().then(setAuto).catch(() => {});
    // The measured move per instrument is fetched on open rather than behind a
    // "Rank" button: it is information, and it made no sense to have to ask.
    getWatchlistMovers()
      .then(setMovers)
      .catch(() => {});
  }, []);

  // Auto mode changes which instruments are ACTIVE, so the enabled flags and the
  // scan lag are both re-read after every change to it.
  const patchAuto = async (patch: Parameters<typeof setDayMovers>[0]) => {
    setBusy(true);
    setNote(null);
    try {
      const s = await setDayMovers(patch);
      setAuto(s);
      await pullWatchlist();
      getScanHealth().then(setHealth).catch(() => {});
      if (s.enabled && s.waiting_reason) setNote(s.waiting_reason);
    } catch {
      setNote("Could not change auto day-movers.");
    } finally {
      setBusy(false);
    }
  };

  const toggle = (sym: string) =>
    setItems((prev) =>
      prev.map((i) => (i.symbol === sym ? { ...i, enabled: !i.enabled } : i)),
    );

  const enabledCount = items.filter((i) => i.enabled).length;

  const save = async () => {
    setSaving(true);
    try {
      const enabled = items.filter((i) => i.enabled).map((i) => i.symbol);
      const res = await setWatchlist(enabled);
      setManual(res.manual);
      onClose();
    } finally {
      setSaving(false);
    }
  };

  const groups: [string, string][] = [
    ["MCX", "Commodities (MCX)"],
    ["NFO", "Indices & Stocks (NSE)"],
    ["BFO", "Sensex (BSE)"],
  ];

  const moverBy = new Map((movers?.rows ?? []).map((r) => [r.symbol, r]));

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
      {/* Flex column with its own scroll region: the header and the Save button
          stay on screen while 60+ instrument rows scroll between them. */}
      <div className="card w-full max-w-2xl max-h-[88vh] flex flex-col">
        <div className="p-5 pb-3 shrink-0">
          <div className="flex items-center justify-between mb-1">
            <div className="text-lg font-bold">Watchlist</div>
            <button onClick={onClose} className="text-white/50 hover:text-white text-xl leading-none">
              ×
            </button>
          </div>
          <div className="text-xs text-white/50">
            Enable exactly the instruments you want the dashboard to watch — no need to edit{" "}
            <code className="text-white/70">.env</code>. Paper-safe: this never places or changes any order.
          </div>

          {envLocked && (
            <div className="rounded-lg border border-[#f4c025]/40 bg-[#f4c025]/10 text-[#f4c025] text-[11px] px-3 py-2 mt-3">
              An ops-level <code>QT_INSTRUMENTS</code> lock is set, so the watchlist is read-only here. Remove that
              env var to manage instruments from the dashboard.
            </div>
          )}

          {/* Auto day-movers: the engine picks the day's 5-10 names and holds
              them, so the user does not have to choose each morning. */}
          <div
            className={
              "rounded-xl border px-3 py-2.5 mt-3 " +
              (auto?.enabled ? "border-[#19e57f]/40 bg-[#19e57f]/[0.06]" : "border-white/10 bg-white/[0.02]")
            }
          >
            <div className="flex items-center justify-between gap-3 flex-wrap">
              <div className="text-[11px]">
                <span className="font-semibold text-white/90">Auto day-movers</span>
                <span className="text-white/50">
                  {" "}— the engine picks today&apos;s movers itself and holds them for the session.
                </span>
              </div>
              <div className="flex items-center gap-1.5 shrink-0">
                {/* No count to choose: the engine holds however many names clear
                    the movement floor, bounded to 5-10. */}
                <button
                  disabled={busy || envLocked || !auto?.enabled}
                  onClick={() => patchAuto({ repick: true })}
                  className="text-[11px] font-semibold px-2.5 py-1 rounded-lg border border-white/15 text-white/75 hover:bg-white/5 disabled:opacity-40"
                >
                  Re-pick
                </button>
                <button
                  disabled={busy || envLocked}
                  onClick={() => patchAuto({ enabled: !auto?.enabled })}
                  className={
                    "text-[11px] font-bold px-2.5 py-1 rounded-lg " +
                    (auto?.enabled ? "text-black" : "border border-white/20 text-white/75 hover:bg-white/5")
                  }
                  style={auto?.enabled ? { background: "#19e57f" } : undefined}
                >
                  {/* The state AND the action, because a lone "OFF" reads as a
                      button that turns something off. */}
                  {auto?.enabled ? "ON" : "OFF — turn on"}
                </button>
              </div>
            </div>
            {auto && !auto.enabled && (
              <div className="text-[10px] text-white/40 mt-1.5">
                Off — manual mode. All {enabledCount} names you ticked below stay live, the scan is spread across
                every one of them, and each one costs a rate-limited warm-up request at startup.
              </div>
            )}
            {auto?.enabled && (
              <div className="text-[10px] mt-1.5 space-y-0.5">
                {auto.held.length > 0 ? (
                  <>
                    <div className="text-white/70">
                      Holding {auto.held.length} today: {auto.held_names.join(", ")}
                    </div>
                    <div className="text-white/40">
                      {auto.pick_note}
                      {auto.picked_minutes_ago !== null && <> · picked {auto.picked_minutes_ago}m ago</>}
                    </div>
                  </>
                ) : (
                  <div className="text-[#f4c025]">
                    {auto.waiting_reason ?? "waiting for enough measured data before locking today's set"}
                  </div>
                )}
                {auto.log.length > 0 && (
                  <div className="text-white/35">last change: {auto.log[0].detail}</div>
                )}
                <div className="text-white/35">
                  Your saved watchlist below is untouched and stays your reference — auto only changes which of
                  them are live. Editing it by hand switches auto off.
                </div>
              </div>
            )}
          </div>

          {/* The measured numbers behind the choice — shown, not hidden behind a
              third control. The one-off manual prune was removed: it did once
              what Auto does continuously. */}
          {movers && (
            <div className="text-[10px] text-white/45 mt-2">
              {movers.measured_count} of {movers.active_count} measured ({movers.coverage_pct}%)
              {movers.unmeasured_count > 0 && (
                <> · {movers.unmeasured_count} still warming (a cold instrument reads exactly like a quiet one)</>
              )}{" "}
              · floor {movers.min_move_pct}% net premium move · the % beside each name below is its measured move
            </div>
          )}
          {note && <div className="text-[10px] text-[#f4c025] mt-1.5">{note}</div>}

          {/* Warm-up first: until an instrument's candles have downloaded it has
              no measured move, and saying "loading" is the honest version of a
              board that looks empty. Angel rate-limits this, so a long list is
              genuinely slow to start — which is the case for a small one. */}
          {health && health.warm_total > 0 && health.warm_done < health.warm_total && (
            <div className="text-[10px] text-[#f4c025] mt-2">
              Warming {health.warm_done}/{health.warm_total} instruments — one rate-limited candle download each, so
              a long list takes minutes before it can be scored.
            </div>
          )}

          {/* Measured, not modelled: what the current list actually costs. */}
          {health && (
            <div className="text-[10px] text-white/40 mt-2">
              Scan lag now: median{" "}
              <span className="text-white/70">
                {health.median_age_sec === null ? "—" : `${health.median_age_sec.toFixed(1)}s`}
              </span>
              , worst{" "}
              <span className={health.behind_count > 0 ? "text-[#f4c025]" : "text-white/70"}>
                {health.worst_age_sec === null ? "—" : `${health.worst_age_sec.toFixed(1)}s`}
              </span>{" "}
              against a {health.scan_target_sec}s target · {health.active_count} active
              {health.behind_count > 0 && <> · {health.behind_count} behind</>}
              {/* The target alone cannot say WHY the lag is what it is. These are
                  the two numbers that can: what one tick costs and how long a
                  full pass over the list therefore takes. */}
              {health.tick_cost_ms !== null && health.tick_cost_ms !== undefined && (
                <>
                  {" "}
                  · {health.tick_cost_ms.toFixed(0)}ms per tick, {health.scanned_per_cycle}/cycle
                  {health.sweep_estimate_sec ? (
                    <>
                      {" "}
                      → full sweep{" "}
                      <span className={health.sweep_estimate_sec > 60 ? "text-[#f4c025]" : "text-white/70"}>
                        ~{health.sweep_estimate_sec}s
                      </span>
                    </>
                  ) : null}
                </>
              )}
              {/* A shut exchange is skipped, not scanned — otherwise ~40 frozen
                  NSE/BSE names spend the evening's budget on prices that cannot
                  move. Shown so a small scan count reads as the session. */}
              {health.closed_skipped > 0 && (
                <> · {health.closed_skipped} skipped (market closed — they resume at the next open)</>
              )}
              {!health.armed && <> · scanner idle (arm the bot to scan)</>}
            </div>
          )}
          {enabledCount > SCAN_COMFORT && (
            <div className="text-[10px] text-[#f4c025] mt-1">
              {enabledCount} enabled. The price on screen still ticks every cycle — it is scan coverage that
              stretches, so a BUY on a name you are not viewing can be seen a cycle or two late.
            </div>
          )}
        </div>

        <div className="card-scroll flex-1 min-h-0 px-5">
          {!loaded ? (
            <div className="text-white/40 text-sm">Loading…</div>
          ) : (
            <div className="space-y-4 pb-2">
              {groups.map(([ex, label]) => {
                const rows = items.filter((i) => i.exchange === ex);
                if (rows.length === 0) return null;
                return (
                  <div key={ex}>
                    <div className="text-[10px] uppercase tracking-widest text-white/40 mb-1.5">{label}</div>
                    <div className="grid grid-cols-2 sm:grid-cols-3 gap-1.5">
                      {rows.map((i) => {
                        const m = moverBy.get(i.symbol);
                        return (
                          <button
                            key={i.symbol}
                            disabled={envLocked}
                            onClick={() => toggle(i.symbol)}
                            className={
                              "flex items-center gap-2 rounded-lg border px-2.5 py-1.5 text-xs text-left disabled:opacity-50 " +
                              (i.enabled
                                ? "border-[#19e57f]/50 bg-[#19e57f]/10 text-white"
                                : "border-white/15 text-white/50 hover:bg-white/5")
                            }
                          >
                            <span
                              className={
                                "w-3.5 h-3.5 rounded-sm border flex items-center justify-center text-[9px] shrink-0 " +
                                (i.enabled ? "border-[#19e57f] bg-[#19e57f] text-black" : "border-white/30")
                              }
                            >
                              {i.enabled ? "✓" : ""}
                            </span>
                            <span className="truncate">{i.display}</span>
                            {/* The measured move, so a toggle is an informed one. */}
                            {m && (
                              <span
                                className={
                                  "ml-auto text-[10px] shrink-0 " +
                                  (m.measured ? "text-white/45" : "text-white/25")
                                }
                              >
                                {/* "warming" not "—": an instrument whose candles
                                    have not downloaded yet is not a quiet one,
                                    and startup is rate-limited by the broker. */}
                                {m.measured ? `${m.net_move_pct?.toFixed(1)}%` : "warming"}
                              </span>
                            )}
                          </button>
                        );
                      })}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>

        <div className="shrink-0 px-5 pb-4 pt-3 mt-1 border-t border-white/10">
          <div className="flex items-center justify-between">
            <div className="text-xs text-white/50">
              {enabledCount} enabled{manual ? " · manual watchlist active" : ""}
            </div>
            <div className="flex gap-2">
              <button
                onClick={onClose}
                className="text-xs font-semibold px-3 py-1.5 rounded-lg text-white/70 border border-white/15 hover:bg-white/5"
              >
                Cancel
              </button>
              <button
                disabled={saving || envLocked || enabledCount === 0}
                onClick={save}
                className="text-xs font-bold px-4 py-1.5 rounded-lg text-black disabled:opacity-40"
                style={{ background: "#19e57f" }}
              >
                {saving ? "Saving…" : "Save"}
              </button>
            </div>
          </div>
          {/* Two of the controls above apply immediately (they change the active
              universe server-side), and only the checkboxes wait for Save. Saying
              so plainly, because "changes apply on save" was untrue for them. */}
          <div className="text-[10px] text-white/35 mt-2">
            Two modes, nothing else: <span className="text-white/55">Auto</span> (the engine holds today&apos;s
            movers) or <span className="text-white/55">Manual</span> (the boxes below). Ticking boxes applies on{" "}
            <span className="text-white/55">Save</span> and switches Auto off; Auto takes effect at once. An empty
            selection clears the manual watchlist and reverts to the default set. Reload the page to refresh the
            instrument dropdown.
          </div>
        </div>
      </div>
    </div>
  );
}
