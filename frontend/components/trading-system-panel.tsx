"use client";

import { useState } from "react";

type Tab = "calculator" | "signals" | "growth" | "risk";

const MONTHS = ["Aug", "Sep", "Oct", "Nov", "Dec", "Jan", "Feb", "Mar"];

function fmt(n: number): string {
  if (n >= 100000) return "₹" + (n / 100000).toFixed(1) + "L";
  return "₹" + Math.round(n).toLocaleString("en-IN");
}

function fmtPct(n: number): string {
  return n.toFixed(1) + "%";
}

export function TradingSystemPanel() {
  const [activeTab, setActiveTab] = useState<Tab>("calculator");

  // Calculator state
  const [capital, setCapital] = useState(1800000);
  const [riskPct, setRiskPct] = useState(1.5);
  const [slPct, setSlPct] = useState(4);
  const [rr, setRr] = useState(2.5);

  // Growth state
  const [monthlyRate, setMonthlyRate] = useState(3.5);

  // Derived calculator values
  const riskRs = capital * riskPct / 100;
  const posSize = riskRs / (slPct / 100);
  const targetPft = riskRs * rr;
  const monthlyEst = (4 * targetPft) - (2 * riskRs); // 6 trades, 60% win rate
  const monthlyPct = (monthlyEst / capital) * 100;

  // Growth projection
  function growthRows() {
    let cap = 1800000;
    const target = 2500000;
    const rows: { month: string; cap: number; gain: number; progress: number }[] = [];
    for (let i = 0; i < 8; i++) {
      const gain = cap * monthlyRate / 100;
      cap += gain;
      const progress = Math.min(100, ((cap - 1800000) / (target - 1800000)) * 100);
      rows.push({ month: MONTHS[i] + (i >= 5 ? " '27" : ""), cap, gain, progress });
    }
    return rows;
  }

  function reachMonth(): string {
    let cap = 1800000;
    for (let i = 0; i < 8; i++) {
      cap *= (1 + monthlyRate / 100);
      if (cap >= 2500000) return MONTHS[i] + (i >= 5 ? " '27" : "");
    }
    return "Beyond Mar '27";
  }

  function yearEndCapital(): number {
    let cap = 1800000;
    for (let i = 0; i < 5; i++) cap *= (1 + monthlyRate / 100);
    return cap;
  }

  const rows = growthRows();

  return (
    <section className="panel trading-system-panel">
      <div className="panel-header" style={{ marginBottom: 0 }}>
        <div>
          <h2>Trading system</h2>
          <p>Position calculator, signal rules, growth projections, and risk limits.</p>
        </div>
      </div>

      {/* Tab bar */}
      <div className="ts-tabs">
        {(["calculator", "signals", "growth", "risk"] as Tab[]).map((tab) => (
          <button
            key={tab}
            className={`ts-tab ${activeTab === tab ? "ts-tab--active" : ""}`}
            onClick={() => setActiveTab(tab)}
          >
            {tab === "calculator" && "Position calculator"}
            {tab === "signals" && "Signal rules"}
            {tab === "growth" && "Growth tracker"}
            {tab === "risk" && "Risk limits"}
          </button>
        ))}
      </div>

      {/* ── CALCULATOR ── */}
      {activeTab === "calculator" && (
        <div className="ts-content">
          <div className="ts-slider-grid">
            <SliderRow
              label="Capital"
              min={500000} max={5000000} step={50000}
              value={capital}
              display={"₹" + (capital / 100000).toFixed(0) + "L"}
              onChange={setCapital}
            />
            <SliderRow
              label="Risk per trade"
              min={0.5} max={3} step={0.1}
              value={riskPct}
              display={fmtPct(riskPct)}
              onChange={setRiskPct}
            />
            <SliderRow
              label="Stop loss"
              min={2} max={8} step={0.5}
              value={slPct}
              display={fmtPct(slPct)}
              onChange={setSlPct}
            />
            <SliderRow
              label="Target R:R"
              min={1.5} max={4} step={0.5}
              value={rr}
              display={rr.toFixed(1) + "×"}
              onChange={setRr}
            />
          </div>

          <div className="ts-metric-row">
            <MetricCard label="Position size" value={fmt(posSize)} sub="max per trade" />
            <MetricCard label="Max risk ₹" value={fmt(riskRs)} sub="per trade" />
            <MetricCard label="Target profit" value={fmt(targetPft)} sub="per trade" />
            <MetricCard
              label="Monthly est."
              value={fmt(monthlyEst)}
              sub={fmtPct(monthlyPct) + " · 6 trades · 60% win"}
              highlight
            />
          </div>

          <div className="ts-table-wrap">
            <p className="ts-section-label">Concurrent open positions</p>
            <table className="ts-table">
              <thead>
                <tr>
                  <th>Open trades</th>
                  <th>Capital used</th>
                  <th>Cash buffer</th>
                  <th>Total risk exposure</th>
                </tr>
              </thead>
              <tbody>
                {[3, 4, 5].map((n) => {
                  const used = posSize * n;
                  const usedPct = (used / capital * 100).toFixed(0);
                  const buf = Math.max(0, capital - used);
                  const riskExp = riskRs * n;
                  const riskExpPct = (riskExp / capital * 100).toFixed(1);
                  return (
                    <tr key={n} className={n === 4 ? "ts-row-highlight" : ""}>
                      <td>{n} trades{n === 4 ? " ★ recommended" : ""}</td>
                      <td>{fmt(used)} ({usedPct}%)</td>
                      <td>{fmt(buf)}</td>
                      <td>{fmt(riskExp)} ({riskExpPct}%)</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* ── SIGNALS ── */}
      {activeTab === "signals" && (
        <div className="ts-content">
          <p className="ts-intro">
            All 4 signal layers must align for a full-size entry. Minimum 3 of 4 for a half-size entry. Use your NSE scanner to filter candidates, then confirm manually before executing.
          </p>

          <SignalBlock
            number="1"
            title="Trend filter"
            badge="Mandatory"
            badgeType="green"
            rules={[
              { main: "Stock above 50 EMA on daily chart", sub: "Bullish structure — don't buy in a downtrend" },
              { main: "Nifty or sector index in uptrend", sub: "Don't swim against the tide" },
              { main: "Higher highs, higher lows on daily", sub: "Last 10 candles confirm structure" },
            ]}
          />
          <SignalBlock
            number="2"
            title="Momentum signal"
            badge="Mandatory"
            badgeType="green"
            rules={[
              { main: "RSI (14) between 55–75", sub: "Bullish momentum, not yet overbought" },
              { main: "MACD histogram turning positive or recent crossover", sub: "Within last 3 bars" },
              { main: "ADX > 22", sub: "Trending, not sideways — ADX below 20 = skip" },
            ]}
          />
          <SignalBlock
            number="3"
            title="Volume confirmation"
            badge="2 of 3 needed"
            badgeType="amber"
            rules={[
              { main: "Today's volume > 1.5× 20-day average", sub: "Breakout bars must have conviction" },
              { main: "Delivery % > 40% on NSE data", sub: "Serious buyers, not just intraday noise" },
              { main: "FII/DII buying in sector (weekly data)", sub: "Optional but a strong positive add" },
            ]}
          />
          <SignalBlock
            number="4"
            title="Price structure — entry trigger"
            badge="Mandatory"
            badgeType="green"
            rules={[
              { main: "Breakout of resistance OR pullback to support", sub: "Define level before entry — no chasing" },
              { main: "Stop loss = below breakout candle low or swing low", sub: "Max 4–5% below entry price" },
              { main: "Target = next resistance or 2.5× stop distance", sub: "Whichever is closer becomes your T1" },
            ]}
          />

          <div className="ts-tip-box">
            <strong>Sectors to prioritise (Aug–Dec 2026):</strong> Capital goods, defence, PSU banks, chemicals, midcap IT — cleaner technical structure and higher delivery volumes.
          </div>
        </div>
      )}

      {/* ── GROWTH ── */}
      {activeTab === "growth" && (
        <div className="ts-content">
          <SliderRow
            label="Monthly return"
            min={2} max={6} step={0.5}
            value={monthlyRate}
            display={fmtPct(monthlyRate)}
            onChange={setMonthlyRate}
          />

          <div className="ts-metric-row" style={{ marginTop: "1rem" }}>
            <MetricCard label="Reach ₹25L by" value={reachMonth()} sub="at selected rate" />
            <MetricCard label="Dec '26 capital" value={fmt(yearEndCapital())} sub="5 months compounded" />
            <MetricCard
              label="Total return"
              value={fmtPct((yearEndCapital() - 1800000) / 1800000 * 100)}
              sub="Aug → Dec"
              highlight
            />
          </div>

          <div className="ts-table-wrap">
            <table className="ts-table">
              <thead>
                <tr>
                  <th>Month</th>
                  <th>Capital</th>
                  <th>Monthly gain</th>
                  <th>Progress to ₹25L</th>
                </tr>
              </thead>
              <tbody>
                {rows.map(({ month, cap, gain, progress }) => (
                  <tr key={month} className={cap >= 2500000 ? "ts-row-highlight" : ""}>
                    <td>{month}</td>
                    <td>{fmt(cap)}</td>
                    <td className="ts-green">+{fmt(gain)}</td>
                    <td>
                      <div className="ts-progress-wrap">
                        <div className="ts-progress-bar">
                          <div
                            className="ts-progress-fill"
                            style={{ width: progress.toFixed(0) + "%" }}
                          />
                        </div>
                        <span className="ts-progress-label">{progress.toFixed(0)}%</span>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="ts-tip-box">
            <strong>Realistic expectation:</strong> Months 1–2 you'll likely run 1.5–2.5% as you calibrate the system. Month 3+ target 3–4% consistently. At 55% win rate with 2.5:1 R:R you're profitable even on small sizes — the system beats the rate, not the size.
          </div>
        </div>
      )}

      {/* ── RISK ── */}
      {activeTab === "risk" && (
        <div className="ts-content">
          <RiskBlock title="Daily limits">
            <RiskRule n="1" main="Stop trading if down ₹27,000 in a day" sub="1.5% of ₹18L. Log reason, review next morning. Prevents revenge-trading spirals." />
            <RiskRule n="2" main="Max 3 new entries per day" sub="More trades ≠ more profit. Overtrading dilutes good setups." />
            <RiskRule n="3" main="No trades 15 min before/after major events" sub="RBI policy, SEBI news, results day. Gap risk blows stops clean through." />
          </RiskBlock>

          <RiskBlock title="Weekly limits">
            <RiskRule n="1" main="Weekly drawdown limit: 3% (≈₹54K)" sub="Hit it → take a 2-day break. Resets psychology before bigger damage compounds." />
            <RiskRule n="2" main="Review all trades every Sunday" sub="Win rate, avg R:R, which setups worked. Your journal is your actual edge." />
          </RiskBlock>

          <RiskBlock title="Portfolio limits">
            <RiskRule n="1" main="Max 5 concurrent open positions" sub="Each max 20% of active capital. Concentration kills accounts." />
            <RiskRule n="2" main="Max 2 trades in the same sector simultaneously" sub="Correlated positions = hidden concentration risk." />
            <RiskRule n="3" main="Monthly drawdown limit: 6% (≈₹1.08L)" sub="Hit it → switch to paper trading for 2 weeks. Protects the capital base." />
          </RiskBlock>

          <RiskBlock title="Scaling rule">
            <RiskRule n="→" main="Increase position sizing only after 3 consecutive profitable months" sub="Then scale by 20% only. Earn the right to risk more." />
            <RiskRule n="→" main="At ₹21L (first milestone), lock 10% in liquid fund or FD" sub="Never fall below ₹18L base capital. The floor is sacred." />
          </RiskBlock>
        </div>
      )}

      <style>{`
        .trading-system-panel {
          margin-top: 24px;
        }
        .ts-tabs {
          display: flex;
          flex-wrap: wrap;
          gap: 8px;
          margin: 18px 0 20px;
        }
        .ts-tab {
          padding: 9px 18px;
          border-radius: 999px;
          border: 1px solid var(--line);
          background: rgba(255,255,255,0.55);
          color: var(--muted);
          font-size: 0.88rem;
          font-weight: 500;
          cursor: pointer;
          transition: all 120ms ease;
        }
        .ts-tab:hover {
          border-color: rgba(241,104,0,0.28);
          color: var(--text);
        }
        .ts-tab--active {
          background: linear-gradient(135deg, #f16800, #ff9a2f);
          color: #fff7ef;
          border-color: transparent;
        }
        .ts-content {
          animation: ts-fade-in 180ms ease;
        }
        @keyframes ts-fade-in {
          from { opacity: 0; transform: translateY(6px); }
          to { opacity: 1; transform: translateY(0); }
        }
        .ts-intro {
          color: var(--muted);
          line-height: 1.6;
          margin: 0 0 18px;
          max-width: 72ch;
        }
        .ts-slider-grid {
          display: grid;
          grid-template-columns: repeat(2, 1fr);
          gap: 16px 24px;
          margin-bottom: 20px;
        }
        .ts-slider-row {
          display: flex;
          flex-direction: column;
          gap: 8px;
        }
        .ts-slider-head {
          display: flex;
          justify-content: space-between;
          align-items: baseline;
        }
        .ts-slider-label {
          font-size: 0.84rem;
          color: var(--muted);
        }
        .ts-slider-val {
          font-size: 0.92rem;
          font-weight: 600;
          color: var(--accent);
        }
        .ts-slider-row input[type=range] {
          width: 100%;
          accent-color: var(--accent);
        }
        .ts-metric-row {
          display: grid;
          grid-template-columns: repeat(4, 1fr);
          gap: 12px;
          margin-bottom: 20px;
        }
        .ts-metric-card {
          background: rgba(255,255,255,0.58);
          border: 1px solid var(--line);
          border-radius: 18px;
          padding: 14px 16px;
        }
        .ts-metric-card--highlight {
          background: rgba(255,122,0,0.08);
          border-color: rgba(255,122,0,0.22);
        }
        .ts-metric-label {
          font-size: 0.78rem;
          color: var(--muted);
          text-transform: uppercase;
          letter-spacing: 0.08em;
          margin-bottom: 6px;
        }
        .ts-metric-value {
          font-size: 1.4rem;
          font-weight: 700;
          font-family: var(--font-space-grotesk), sans-serif;
          color: var(--text);
        }
        .ts-metric-card--highlight .ts-metric-value {
          color: var(--accent);
        }
        .ts-metric-sub {
          font-size: 0.76rem;
          color: var(--muted);
          margin-top: 3px;
        }
        .ts-table-wrap {
          overflow-x: auto;
        }
        .ts-section-label {
          font-size: 0.84rem;
          color: var(--muted);
          margin: 0 0 10px;
          text-transform: uppercase;
          letter-spacing: 0.08em;
        }
        .ts-table {
          width: 100%;
          border-collapse: collapse;
          font-size: 0.9rem;
        }
        .ts-table th {
          color: var(--muted);
          font-size: 0.78rem;
          text-transform: uppercase;
          letter-spacing: 0.08em;
          padding: 10px 12px;
          border-bottom: 1px solid var(--line);
          text-align: left;
          font-weight: 500;
        }
        .ts-table td {
          padding: 11px 12px;
          border-bottom: 1px solid var(--line);
          color: var(--text);
        }
        .ts-table tr:last-child td {
          border-bottom: none;
        }
        .ts-row-highlight td {
          color: var(--accent);
          font-weight: 600;
        }
        .ts-green {
          color: var(--green) !important;
          font-weight: 500;
        }
        .ts-progress-wrap {
          display: flex;
          align-items: center;
          gap: 10px;
        }
        .ts-progress-bar {
          flex: 1;
          height: 6px;
          background: var(--bg-deep);
          border-radius: 3px;
          overflow: hidden;
        }
        .ts-progress-fill {
          height: 100%;
          background: linear-gradient(90deg, #f16800, #ff9a2f);
          border-radius: 3px;
          transition: width 0.4s ease;
        }
        .ts-progress-label {
          font-size: 0.78rem;
          color: var(--muted);
          min-width: 30px;
          text-align: right;
        }
        .ts-signal-block {
          background: rgba(255,255,255,0.52);
          border: 1px solid var(--line);
          border-radius: 18px;
          padding: 16px 18px;
          margin-bottom: 12px;
        }
        .ts-signal-head {
          display: flex;
          align-items: center;
          justify-content: space-between;
          margin-bottom: 12px;
        }
        .ts-signal-title {
          font-weight: 600;
          font-size: 0.96rem;
          color: var(--text);
        }
        .ts-badge {
          font-size: 0.74rem;
          font-weight: 600;
          padding: 3px 10px;
          border-radius: 999px;
        }
        .ts-badge--green {
          background: rgba(22,108,90,0.1);
          color: var(--green);
        }
        .ts-badge--amber {
          background: rgba(180,120,0,0.1);
          color: #8a5c00;
        }
        .ts-rule-row {
          display: flex;
          gap: 10px;
          padding: 7px 0;
          border-bottom: 1px solid var(--line);
        }
        .ts-rule-row:last-child {
          border-bottom: none;
          padding-bottom: 0;
        }
        .ts-rule-arrow {
          color: var(--accent);
          font-weight: 700;
          font-size: 0.8rem;
          padding-top: 2px;
          min-width: 16px;
        }
        .ts-rule-main {
          font-size: 0.9rem;
          color: var(--text);
          font-weight: 500;
        }
        .ts-rule-sub {
          font-size: 0.82rem;
          color: var(--muted);
          margin-top: 2px;
        }
        .ts-tip-box {
          background: rgba(255,122,0,0.07);
          border: 1px solid rgba(255,122,0,0.18);
          border-radius: 16px;
          padding: 14px 16px;
          font-size: 0.88rem;
          color: var(--text);
          line-height: 1.6;
          margin-top: 16px;
        }
        .ts-risk-block {
          margin-bottom: 16px;
        }
        .ts-risk-title {
          font-size: 0.8rem;
          font-weight: 600;
          text-transform: uppercase;
          letter-spacing: 0.1em;
          color: var(--muted);
          margin: 0 0 8px;
        }
        .ts-risk-rules {
          background: rgba(255,255,255,0.52);
          border: 1px solid var(--line);
          border-radius: 16px;
          overflow: hidden;
        }
        @media (max-width: 960px) {
          .ts-slider-grid {
            grid-template-columns: 1fr;
          }
          .ts-metric-row {
            grid-template-columns: repeat(2, 1fr);
          }
        }
        @media (max-width: 600px) {
          .ts-metric-row {
            grid-template-columns: 1fr 1fr;
          }
        }
      `}</style>
    </section>
  );
}

/* ── Sub-components ── */

function SliderRow({
  label, min, max, step, value, display, onChange
}: {
  label: string;
  min: number; max: number; step: number;
  value: number; display: string;
  onChange: (v: number) => void;
}) {
  return (
    <div className="ts-slider-row">
      <div className="ts-slider-head">
        <span className="ts-slider-label">{label}</span>
        <span className="ts-slider-val">{display}</span>
      </div>
      <input
        type="range"
        min={min} max={max} step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
      />
    </div>
  );
}

function MetricCard({
  label, value, sub, highlight = false
}: {
  label: string; value: string; sub: string; highlight?: boolean;
}) {
  return (
    <div className={`ts-metric-card ${highlight ? "ts-metric-card--highlight" : ""}`}>
      <div className="ts-metric-label">{label}</div>
      <div className="ts-metric-value">{value}</div>
      <div className="ts-metric-sub">{sub}</div>
    </div>
  );
}

function SignalBlock({
  number, title, badge, badgeType, rules
}: {
  number: string; title: string; badge: string;
  badgeType: "green" | "amber";
  rules: { main: string; sub: string }[];
}) {
  return (
    <div className="ts-signal-block">
      <div className="ts-signal-head">
        <span className="ts-signal-title">{number}. {title}</span>
        <span className={`ts-badge ts-badge--${badgeType}`}>{badge}</span>
      </div>
      {rules.map((r, i) => (
        <div key={i} className="ts-rule-row">
          <span className="ts-rule-arrow">→</span>
          <div>
            <div className="ts-rule-main">{r.main}</div>
            <div className="ts-rule-sub">{r.sub}</div>
          </div>
        </div>
      ))}
    </div>
  );
}

function RiskBlock({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="ts-risk-block">
      <p className="ts-risk-title">{title}</p>
      <div className="ts-risk-rules">{children}</div>
    </div>
  );
}

function RiskRule({ n, main, sub }: { n: string; main: string; sub: string }) {
  return (
    <div className="ts-rule-row" style={{ padding: "12px 16px", borderRadius: 0 }}>
      <span className="ts-rule-arrow">{n}</span>
      <div>
        <div className="ts-rule-main">{main}</div>
        <div className="ts-rule-sub">{sub}</div>
      </div>
    </div>
  );
}
