import { Candle, TradeSetup } from "../types";

const WIDTH = 760;
const HEIGHT = 280;

export function PriceChart({
  candles,
  signal
}: {
  candles: Candle[];
  signal: TradeSetup | null;
}) {
  if (!candles.length) {
    return <div className="chart-shell">No candles available.</div>;
  }

  const closes = candles.map((candle) => candle.close);
  const maxPrice = Math.max(...closes);
  const minPrice = Math.min(...closes);
  const xStep = WIDTH / Math.max(candles.length - 1, 1);

  const points = candles
    .map((candle, index) => {
      const x = index * xStep;
      const normalized = (candle.close - minPrice) / Math.max(maxPrice - minPrice, 1);
      const y = HEIGHT - normalized * HEIGHT;
      return `${x},${y}`;
    })
    .join(" ");

  const latestClose = closes[closes.length - 1];

  return (
    <div className="chart-shell">
      <svg
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        role="img"
        aria-label="Recent close price chart"
        preserveAspectRatio="none"
      >
        <defs>
          <linearGradient id="chartFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="rgba(51, 70, 184, 0.28)" />
            <stop offset="100%" stopColor="rgba(51, 70, 184, 0.02)" />
          </linearGradient>
        </defs>

        <polyline
          fill="none"
          stroke="#3346B8"
          strokeWidth="4"
          points={points}
          strokeLinejoin="round"
          strokeLinecap="round"
        />

        {signal ? (
          <>
            <ReferenceLine
              value={signal.entry_price}
              minPrice={minPrice}
              maxPrice={maxPrice}
              color="#18223B"
              label="Entry"
            />
            <ReferenceLine
              value={signal.stop_loss}
              minPrice={minPrice}
              maxPrice={maxPrice}
              color="#C23A4B"
              label="Stop"
            />
            <ReferenceLine
              value={signal.target_price}
              minPrice={minPrice}
              maxPrice={maxPrice}
              color="#0F8A5F"
              label="Target"
            />
          </>
        ) : null}

        <circle
          cx={WIDTH}
          cy={HEIGHT - ((latestClose - minPrice) / Math.max(maxPrice - minPrice, 1)) * HEIGHT}
          r="6"
          fill="#FFFFFF"
          stroke="#3346B8"
          strokeWidth="3"
        />
      </svg>
    </div>
  );
}

function ReferenceLine({
  value,
  minPrice,
  maxPrice,
  color,
  label
}: {
  value: number;
  minPrice: number;
  maxPrice: number;
  color: string;
  label: string;
}) {
  const normalized = (value - minPrice) / Math.max(maxPrice - minPrice, 1);
  const y = HEIGHT - normalized * HEIGHT;

  return (
    <>
      <line
        x1="0"
        x2={WIDTH}
        y1={y}
        y2={y}
        stroke={color}
        strokeWidth="2"
        strokeDasharray="8 8"
      />
      <text x="10" y={Math.max(y - 8, 18)} fill={color} fontSize="14">
        {label}
      </text>
    </>
  );
}

