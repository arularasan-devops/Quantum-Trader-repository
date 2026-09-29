"use client";
import { useLiveData } from "@/lib/useLiveData";
import Header from "@/components/Header";
import DecisionCard from "@/components/DecisionCard";
import FuturesChart from "@/components/FuturesChart";
import OptionChart from "@/components/OptionChart";
import PositionBar from "@/components/PositionBar";
import Watchlist from "@/components/Watchlist";
import Alerts from "@/components/Alerts";
import IndicatorGrid from "@/components/IndicatorGrid";
import NewsPanel from "@/components/NewsPanel";
import ScorePanel from "@/components/ScorePanel";
import RecoveryPanel from "@/components/RecoveryPanel";

const TV_SYMBOL = process.env.NEXT_PUBLIC_TV_SYMBOL || "MCX:CRUDEOIL";

export default function Page() {
  const { snap, connected } = useLiveData();

  if (!snap) {
    return (
      <div className="h-screen flex items-center justify-center text-muted">
        Connecting to Quantum Trader engine…
      </div>
    );
  }

  const inPosition = !!snap.position.option_symbol;
  const selSymbol = snap.position.option_symbol || snap.decision.recommended_option;

  return (
    <div className="h-screen w-screen p-3 flex flex-col gap-3 overflow-hidden">
      <Alerts alerts={snap.alerts} />
      <Header
        price={snap.futures_price}
        change={snap.futures_change}
        changePct={snap.futures_change_pct}
        marketStatus={snap.market_status}
        connected={connected}
        underlying={snap.underlying}
      />

      <div className="flex-1 grid grid-cols-12 gap-3 min-h-0">
        {/* Left panel */}
        <div className="col-span-4 flex flex-col gap-3 min-h-0">
          <div className="flex-1 min-h-0">
            <FuturesChart symbol={TV_SYMBOL} />
          </div>
          <IndicatorGrid ind={snap.indicators} />
        </div>

        {/* Center: the one big decision */}
        <div className="col-span-4 flex flex-col gap-3 min-h-0 overflow-y-auto">
          <DecisionCard decision={snap.decision} inPosition={inPosition} />
          <RecoveryPanel recovery={snap.recovery} />
          <ScorePanel decision={snap.decision} breakdown={snap.score_breakdown} />
        </div>

        {/* Right panel */}
        <div className="col-span-4 flex flex-col gap-3 min-h-0">
          <div className="flex-1 min-h-0">
            <OptionChart candles={snap.selected_option_candles} decision={snap.decision} symbol={selSymbol} />
          </div>
          <Watchlist items={snap.watchlist} />
          <NewsPanel news={snap.news} sentiment={snap.news_sentiment} score={snap.news_score} />
        </div>
      </div>

      <PositionBar position={snap.position} todayProfit={0} todayLoss={0} />
    </div>
  );
}
