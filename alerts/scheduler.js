const axios = require("axios");
const cron = require("node-cron");

const { canSendEmail, sendEmailAlert } = require("./mailer");
const { canSendWhatsApp, sendWhatsAppAlert } = require("./whatsapp");

const ALERT_TIMEZONE = process.env.ALERT_TIMEZONE || "Asia/Kolkata";
const OPENING_SCAN_CRON = "0 9 * * 1-5";
const HOURLY_UPDATE_CRON = "0 10-15 * * 1-5";
const CLOSING_SNAPSHOT_CRON = "30 15 * * 1-5";

const api = axios.create({
  baseURL: stripTrailingSlash(process.env.SCANNER_API_BASE_URL || ""),
  timeout: Number(process.env.SCAN_REQUEST_TIMEOUT_MS || 120000),
  headers: {
    "Content-Type": "application/json"
  }
});

let loggedHolidayHint = false;

function validateConfig() {
  if (!process.env.SCANNER_API_BASE_URL) {
    throw new Error("SCANNER_API_BASE_URL is required.");
  }

  if (!canSendEmail() && !canSendWhatsApp()) {
    console.warn(
      "[alerts] No alert channel is fully configured yet. " +
        "Set SMTP credentials and/or CallMeBot settings before deploying."
    );
  }
}

function startScheduler() {
  console.log("[alerts] Scheduler started in timezone:", ALERT_TIMEZONE);
  cron.schedule(
    OPENING_SCAN_CRON,
    () => {
      void runAlertCycle("opening-scan");
    },
    { timezone: ALERT_TIMEZONE }
  );

  cron.schedule(
    HOURLY_UPDATE_CRON,
    () => {
      void runAlertCycle("hourly-update");
    },
    { timezone: ALERT_TIMEZONE }
  );

  cron.schedule(
    CLOSING_SNAPSHOT_CRON,
    () => {
      void runAlertCycle("closing-snapshot");
    },
    { timezone: ALERT_TIMEZONE }
  );
}

async function runAlertCycle(triggerKey) {
  const now = new Date();
  if (triggerKey !== "manual" && shouldSkipRun(now)) {
    console.log(
      `[alerts] Skipping ${triggerKey} because the NSE market is closed today.`
    );
    return;
  }

  const runLabel = toRunLabel(triggerKey);
  console.log(`[alerts] Starting ${runLabel}...`);

  const scanPayload = buildScanPayload();
  const { signals, generatedAt, universeLabel } = await fetchFreshSignals(scanPayload);
  const topSignals = signals.slice(0, Number(process.env.SCAN_MAX_RESULTS || 5));
  const dashboardUrl = process.env.SCANNER_WEB_URL || "";

  const results = await Promise.allSettled([
    sendEmailAlert({
      runLabel,
      universeLabel,
      signals: topSignals,
      generatedAt,
      dashboardUrl
    }),
    sendWhatsAppAlert({
      runLabel,
      universeLabel,
      signals: topSignals,
      dashboardUrl
    })
  ]);

  results.forEach((result, index) => {
    const channel = index === 0 ? "email" : "whatsapp";
    if (result.status === "fulfilled") {
      console.log(`[alerts] ${channel} notification completed.`);
      return;
    }

    console.error(`[alerts] ${channel} notification failed:`, result.reason);
  });
}

async function fetchFreshSignals(scanPayload) {
  let runData = {};
  try {
    const runResponse = await api.post("/scan", scanPayload);
    runData = runResponse.data || {};
  } catch (error) {
    if (!isTimeoutError(error)) {
      throw error;
    }

    console.warn(
      "[alerts] Initial scan request timed out. Falling back to status polling."
    );
    return pollForSignals({
      universe: scanPayload.universe,
      fallbackSignals: [],
      fallbackGeneratedAt: new Date().toISOString()
    });
  }

  if (!runData.scan_in_progress) {
    return {
      signals: normalizeSignals(runData.results || []),
      generatedAt: runData.generated_at || new Date().toISOString(),
      universeLabel: formatUniverseLabel(scanPayload.universe)
    };
  }

  console.log("[alerts] Scan is running in the background. Polling for completion...");

  return pollForSignals({
    universe: scanPayload.universe,
    fallbackSignals: normalizeSignals(runData.results || []),
    fallbackGeneratedAt: runData.generated_at || new Date().toISOString()
  });
}

async function pollForSignals({ universe, fallbackSignals, fallbackGeneratedAt }) {
  const timeoutMs = Number(process.env.SCAN_POLL_TIMEOUT_MS || 420000);
  const intervalMs = Number(process.env.SCAN_POLL_INTERVAL_MS || 15000);
  const deadline = Date.now() + timeoutMs;

  while (Date.now() < deadline) {
    await sleep(intervalMs);
    try {
      const statusResponse = await api.get("/scan/status");
      const status = statusResponse.data || {};

      if (status.scan_in_progress) {
        console.log(
          `[alerts] Waiting on scan progress: ${status.scanned_symbols || 0}/` +
            `${status.universe_size || 0}`
        );
        continue;
      }

      const signalsResponse = await api.get(
        `/signals?universe=${encodeURIComponent(universe)}`
      );
      const signals = normalizeSignals(signalsResponse.data || []);
      return {
        signals,
        generatedAt: status.latest_generated_at || new Date().toISOString(),
        universeLabel: formatUniverseLabel(universe)
      };
    } catch (error) {
      console.warn("[alerts] Poll request failed, retrying...", error.message);
    }
  }

  console.warn(
    "[alerts] Scan poll timed out. Falling back to the latest response payload."
  );
  return {
    signals: fallbackSignals,
    generatedAt: fallbackGeneratedAt || new Date().toISOString(),
    universeLabel: formatUniverseLabel(universe)
  };
}

function buildScanPayload() {
  return {
    universe: process.env.SCAN_UNIVERSE || "nifty500",
    max_results: Number(process.env.SCAN_MAX_RESULTS || 5),
    min_probability: Number(process.env.SCAN_MIN_PROBABILITY || 0.55),
    min_risk_reward: Number(process.env.SCAN_MIN_RISK_REWARD || 1.8),
    investment_amount: Number(process.env.SCAN_INVESTMENT_AMOUNT || 100000)
  };
}

function normalizeSignals(signals) {
  if (!Array.isArray(signals)) {
    return [];
  }

  return signals.map((signal) => ({
    symbol: signal.symbol,
    company_name: signal.company_name || signal.symbol,
    sector: signal.sector || "Unknown",
    entry_price: Number(signal.entry_price || 0),
    target_price: Number(signal.target_price || 0),
    stop_loss: Number(signal.stop_loss || 0),
    probability_score: Number(signal.probability_score || 0)
  }));
}

function shouldSkipRun(now) {
  const today = formatDateKey(now);
  const weekday = formatWeekday(now);
  const isWeekend = weekday === "Sat" || weekday === "Sun";
  if (isWeekend) {
    return true;
  }

  const holidaySet = getHolidaySet();
  if (!holidaySet.size && !loggedHolidayHint) {
    console.warn(
      "[alerts] NSE_MARKET_HOLIDAYS is empty. Weekend skipping works, " +
        "but exchange holidays must be configured in .env."
    );
    loggedHolidayHint = true;
  }
  return holidaySet.has(today);
}

function getHolidaySet() {
  return new Set(
    String(process.env.NSE_MARKET_HOLIDAYS || "")
      .split(",")
      .map((value) => value.trim())
      .filter(Boolean)
  );
}

function formatDateKey(value) {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: ALERT_TIMEZONE,
    year: "numeric",
    month: "2-digit",
    day: "2-digit"
  })
    .formatToParts(value instanceof Date ? value : new Date(value))
    .reduce((accumulator, part) => {
      if (part.type !== "literal") {
        accumulator[part.type] = part.value;
      }
      return accumulator;
    }, {});

  return `${parts.year}-${parts.month}-${parts.day}`;
}

function formatWeekday(value) {
  return new Intl.DateTimeFormat("en-US", {
    timeZone: ALERT_TIMEZONE,
    weekday: "short"
  }).format(value instanceof Date ? value : new Date(value));
}

function formatUniverseLabel(universe) {
  switch (universe) {
    case "nifty_smallcap_250":
      return "Nifty Smallcap 250";
    case "mid_small_2000_plus":
      return "Mid & Small 2000+";
    case "nifty500":
    default:
      return "Nifty 500";
  }
}

function toRunLabel(triggerKey) {
  switch (triggerKey) {
    case "opening-scan":
      return "Opening Scan";
    case "hourly-update":
      return "Hourly Update";
    case "closing-snapshot":
      return "Closing Snapshot";
    case "manual":
    default:
      return "Manual Run";
  }
}

function stripTrailingSlash(value) {
  return String(value || "").replace(/\/+$/, "");
}

function sleep(durationMs) {
  return new Promise((resolve) => {
    setTimeout(resolve, durationMs);
  });
}

function isTimeoutError(error) {
  return Boolean(error && (error.code === "ECONNABORTED" || /timeout/i.test(error.message)));
}

module.exports = {
  runAlertCycle,
  startScheduler,
  validateConfig
};
