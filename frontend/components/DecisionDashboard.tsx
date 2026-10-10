"use client";
import { Snapshot } from "@/lib/types";
import DecisionHero from "@/components/DecisionHero";
import PositionCard from "@/components/PositionCard";
import ReasonsCard from "@/components/ReasonsCard";
import TradePlanCard from "@/components/TradePlanCard";
import ExpiryOpportunityPanel from "@/components/ExpiryOpportunityPanel";
import ConfidenceChecklist from "@/components/ConfidenceChecklist";
import {
  OpportunityRiskMeters,
  TradeScoreCard,
  PremiumQualityCard,
  TrapDetectorCard,
  SmartMoneyCard,
} from "@/components/DecisionMeters";

export default function DecisionDashboard({ snap }: { snap: Snapshot }) {
  const d = snap.decision;
  return (
    <div className="flex flex-col gap-3 flex-1 min-h-0">
      {/* Row 1 — action + opportunity/risk + trade score */}
      <div className="grid grid-cols-12 gap-3 min-h-0" style={{ flex: "1.45" }}>
        <div className="col-span-6 min-h-0">
          <DecisionHero decision={d} />
        </div>
        <div className="col-span-3 min-h-0">
          <OpportunityRiskMeters decision={d} />
        </div>
        <div className="col-span-3 min-h-0">
          <TradeScoreCard decision={d} />
        </div>
      </div>

      {/* Row 2 — trade plan + premium quality + traps/smart money */}
      <div className="grid grid-cols-12 gap-3 min-h-0" style={{ flex: "1.0" }}>
        <div className="col-span-5 min-h-0">
          <TradePlanCard decision={d} position={snap.position} />
        </div>
        <div className="col-span-3 min-h-0">
          <PremiumQualityCard decision={d} />
        </div>
        <div className="col-span-4 min-h-0 flex flex-col gap-3">
          <div className="flex-1 min-h-0">
            <TrapDetectorCard decision={d} />
          </div>
          <div className="min-h-0">
            <SmartMoneyCard decision={d} />
          </div>
        </div>
      </div>

      {/* Row 3 — why + expiry opportunity + open position */}
      <div className="grid grid-cols-12 gap-3 min-h-0" style={{ flex: "0.85" }}>
        <div className="col-span-5 min-h-0">
          <ReasonsCard decision={d} />
        </div>
        <div className="col-span-3 min-h-0">
          <ExpiryOpportunityPanel snap={snap} />
        </div>
        <div className="col-span-4 min-h-0">
          <PositionCard
            position={snap.position}
            expectedHoldMinutes={d.expected_holding_minutes}
            averageNote={d.average_ok ? d.average_note : null}
          />
        </div>
      </div>

      {/* Row 4 — buy confidence checklist (display-only) */}
      <div className="grid grid-cols-12 gap-3 min-h-0" style={{ flex: "1.25" }}>
        <div className="col-span-12 min-h-0">
          <ConfidenceChecklist snap={snap} />
        </div>
      </div>
    </div>
  );
}
