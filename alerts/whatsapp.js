const axios = require("axios");

function isWhatsAppEnabled() {
  return process.env.ENABLE_WHATSAPP_ALERTS !== "0";
}

function canSendWhatsApp() {
  return Boolean(
    isWhatsAppEnabled()
      && process.env.WHATSAPP_PHONE
      && process.env.WHATSAPP_API_KEY
  );
}

async function sendWhatsAppAlert({ runLabel, universeLabel, signals, dashboardUrl }) {
  if (!isWhatsAppEnabled()) {
    console.log("[alerts][whatsapp] WhatsApp alerts are disabled.");
    return { skipped: true, reason: "disabled" };
  }

  if (!canSendWhatsApp()) {
    console.warn(
      "[alerts][whatsapp] Missing CallMeBot phone or API key. Skipping WhatsApp alert."
    );
    return { skipped: true, reason: "missing_config" };
  }

  const message = buildMessage({ runLabel, universeLabel, signals, dashboardUrl });
  const response = await axios.get(
    process.env.CALLMEBOT_BASE_URL || "https://api.callmebot.com/whatsapp.php",
    {
      params: {
        phone: process.env.WHATSAPP_PHONE,
        text: message,
        apikey: process.env.WHATSAPP_API_KEY
      },
      timeout: Number(process.env.SCAN_REQUEST_TIMEOUT_MS || 120000)
    }
  );

  console.log("[alerts][whatsapp] WhatsApp alert sent:", response.status);
  return { skipped: false, status: response.status };
}

function buildMessage({ runLabel, universeLabel, signals, dashboardUrl }) {
  const lines = [
    `NSE Swing Scanner - ${runLabel}`,
    universeLabel
  ];

  if (!signals.length) {
    lines.push("No opportunities matched the configured filters.");
  } else {
    signals.slice(0, 3).forEach((signal, index) => {
      lines.push(
        `${index + 1}) ${signal.symbol} | Entry ${formatCompact(signal.entry_price)} | ` +
          `TGT ${formatCompact(signal.target_price)} | SL ${formatCompact(signal.stop_loss)}`
      );
    });
  }

  if (dashboardUrl) {
    lines.push(`Dashboard: ${dashboardUrl}`);
  }

  return lines.join("\n");
}

function formatCompact(value) {
  return new Intl.NumberFormat("en-IN", {
    maximumFractionDigits: 2
  }).format(Number(value || 0));
}

module.exports = {
  canSendWhatsApp,
  isWhatsAppEnabled,
  sendWhatsAppAlert
};
