import Link from "next/link";

import { TradeSetup } from "../types";

export function OpportunitiesTable({ signals }: { signals: TradeSetup[] }) {
  if (!signals.length) {
    return (
      <div className="empty-state">
        <p>No setups yet. Run the scanner to populate this board.</p>
      </div>
    );
  }

  return (
    <div className="table-shell">
      <table>
        <thead>
          <tr>
            <th>Stock</th>
            <th>Pattern</th>
            <th>RS vs NIFTY</th>
            <th>Entry</th>
            <th>Stop</th>
            <th>Target</th>
            <th>Target ETA</th>
            <th>R:R</th>
            <th>Probability</th>
            <th>Expected profit</th>
          </tr>
        </thead>
        <tbody>
          {signals.map((signal) => (
            <tr key={signal.symbol}>
              <td>
                <Link href={`/stocks/${signal.symbol}`} className="stock-link">
                  {signal.symbol}
                </Link>
                <div className="table-subtext">{signal.company_name}</div>
                <div className="table-subtext">
                  20D TV {signal.liquidity.average_traded_value_20d_cr.toFixed(1)} Cr
                </div>
              </td>
              <td>{formatPattern(signal.pattern)}</td>
              <td>
                {Math.round(signal.relative_strength.score * 100)}
                <div className="table-subtext">
                  50D {formatSignedPct(signal.relative_strength.excess_return_50d_pct)}
                </div>
                <div className="table-subtext">
                  Sector {signal.sector_strength.rank}/{signal.sector_strength.sector_count}
                </div>
              </td>
              <td>{formatCurrency(signal.entry_price)}</td>
              <td>{formatCurrency(signal.stop_loss)}</td>
              <td>{formatCurrency(signal.target_price)}</td>
              <td>
                {signal.estimated_target_sessions} sessions
                <div className="table-subtext">
                  {formatDate(signal.estimated_target_date)}
                </div>
              </td>
              <td>{signal.risk_reward_ratio.toFixed(2)}</td>
              <td>
                {Math.round(signal.probability_score * 100)}%
                <div className="table-subtext">
                  Event {formatRiskLabel(signal.event_risk.risk_level)}
                </div>
              </td>
              <td>{formatCurrency(signal.expected_profit_amount)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function formatCurrency(value: number) {
  return new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    maximumFractionDigits: 0
  }).format(value);
}

function formatPattern(pattern: string) {
  return pattern
    .split("_")
    .map((part) => part[0]?.toUpperCase() + part.slice(1))
    .join(" ");
}

function formatSignedPct(value: number) {
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(1)}%`;
}

function formatDate(value: string) {
  return new Intl.DateTimeFormat("en-IN", {
    day: "2-digit",
    month: "short",
    year: "numeric"
  }).format(new Date(value));
}

function formatRiskLabel(value: string) {
  return value.charAt(0).toUpperCase() + value.slice(1);
}
