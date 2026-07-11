const nodemailer = require("nodemailer");

let transporter;

function isEmailEnabled() {
  return process.env.ENABLE_EMAIL_ALERTS !== "0";
}

function canSendEmail() {
  return Boolean(
    isEmailEnabled()
      && process.env.SMTP_USER
      && process.env.SMTP_PASS
      && process.env.ALERT_EMAIL_TO
  );
}

function getTransporter() {
  if (!transporter) {
    transporter = nodemailer.createTransport({
      host: process.env.SMTP_HOST || "smtp.gmail.com",
      port: Number(process.env.SMTP_PORT || 587),
      secure: String(process.env.SMTP_SECURE || "false") === "true",
      auth: {
        user: process.env.SMTP_USER,
        pass: process.env.SMTP_PASS
      }
    });
  }

  return transporter;
}

async function sendEmailAlert({ runLabel, universeLabel, signals, generatedAt, dashboardUrl }) {
  if (!isEmailEnabled()) {
    console.log("[alerts][email] Email alerts are disabled.");
    return { skipped: true, reason: "disabled" };
  }

  if (!canSendEmail()) {
    console.warn(
      "[alerts][email] Missing SMTP or recipient settings. Skipping email alert."
    );
    return { skipped: true, reason: "missing_config" };
  }

  const mail = {
    from: process.env.ALERT_EMAIL_FROM || process.env.SMTP_USER,
    to: process.env.ALERT_EMAIL_TO,
    subject: buildSubject(runLabel, universeLabel, generatedAt),
    text: buildText({ runLabel, universeLabel, signals, generatedAt, dashboardUrl }),
    html: buildHtml({ runLabel, universeLabel, signals, generatedAt, dashboardUrl })
  };

  const result = await getTransporter().sendMail(mail);
  console.log("[alerts][email] Email alert sent:", result.messageId);
  return { skipped: false, messageId: result.messageId };
}

function buildSubject(runLabel, universeLabel, generatedAt) {
  return `NSE Swing Scanner ${runLabel} | ${universeLabel} | ${formatDateTime(generatedAt)}`;
}

function buildText({ runLabel, universeLabel, signals, generatedAt, dashboardUrl }) {
  const lines = [
    `NSE Swing Scanner - ${runLabel}`,
    `Universe: ${universeLabel}`,
    `Generated: ${formatDateTime(generatedAt)}`,
    ""
  ];

  if (!signals.length) {
    lines.push("No opportunities matched the configured filters.");
  } else {
    lines.push("Top opportunities:");
    lines.push("");
    signals.forEach((signal, index) => {
      lines.push(
        `${index + 1}. ${signal.symbol} | ${signal.company_name} | ${signal.sector} | ` +
          `Entry ${formatCurrency(signal.entry_price)} | Target ${formatCurrency(signal.target_price)} | ` +
          `SL ${formatCurrency(signal.stop_loss)} | Confidence ${formatPercent(signal.probability_score)}`
      );
    });
  }

  if (dashboardUrl) {
    lines.push("");
    lines.push(`Dashboard: ${dashboardUrl}`);
  }

  return lines.join("\n");
}

function buildHtml({ runLabel, universeLabel, signals, generatedAt, dashboardUrl }) {
  const heading = `
    <h2 style="margin:0 0 8px;">NSE Swing Scanner</h2>
    <p style="margin:0 0 4px;"><strong>Run:</strong> ${escapeHtml(runLabel)}</p>
    <p style="margin:0 0 4px;"><strong>Universe:</strong> ${escapeHtml(universeLabel)}</p>
    <p style="margin:0 0 20px;"><strong>Generated:</strong> ${escapeHtml(formatDateTime(generatedAt))}</p>
  `;

  if (!signals.length) {
    const emptyBlock = "<p>No opportunities matched the configured filters.</p>";
    const dashboardBlock = dashboardUrl
      ? `<p><a href="${escapeAttribute(dashboardUrl)}">Open dashboard</a></p>`
      : "";
    return wrapHtml(heading + emptyBlock + dashboardBlock);
  }

  const rows = signals
    .map(
      (signal, index) => `
        <tr>
          <td style="padding:10px;border:1px solid #e6d3bf;">${index + 1}</td>
          <td style="padding:10px;border:1px solid #e6d3bf;">
            <strong>${escapeHtml(signal.symbol)}</strong><br />
            <span style="color:#6b5c4f;">${escapeHtml(signal.company_name)}</span>
          </td>
          <td style="padding:10px;border:1px solid #e6d3bf;">${escapeHtml(signal.sector || "Unknown")}</td>
          <td style="padding:10px;border:1px solid #e6d3bf;">${formatCurrency(signal.entry_price)}</td>
          <td style="padding:10px;border:1px solid #e6d3bf;">${formatCurrency(signal.target_price)}</td>
          <td style="padding:10px;border:1px solid #e6d3bf;">${formatCurrency(signal.stop_loss)}</td>
          <td style="padding:10px;border:1px solid #e6d3bf;">${formatPercent(signal.probability_score)}</td>
        </tr>
      `
    )
    .join("");

  const dashboardBlock = dashboardUrl
    ? `<p style="margin-top:18px;"><a href="${escapeAttribute(dashboardUrl)}">Open scanner dashboard</a></p>`
    : "";

  return wrapHtml(`
    ${heading}
    <table style="border-collapse:collapse;width:100%;background:#fffdf9;">
      <thead>
        <tr style="background:#f7ebdf;">
          <th style="padding:10px;border:1px solid #e6d3bf;">#</th>
          <th style="padding:10px;border:1px solid #e6d3bf;">Stock</th>
          <th style="padding:10px;border:1px solid #e6d3bf;">Sector</th>
          <th style="padding:10px;border:1px solid #e6d3bf;">Entry</th>
          <th style="padding:10px;border:1px solid #e6d3bf;">Target</th>
          <th style="padding:10px;border:1px solid #e6d3bf;">Stop-loss</th>
          <th style="padding:10px;border:1px solid #e6d3bf;">Confidence</th>
        </tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>
    ${dashboardBlock}
  `);
}

function wrapHtml(content) {
  return `
    <html>
      <body style="font-family:Segoe UI, Arial, sans-serif;background:#fff7ef;color:#2f1d11;padding:24px;">
        <div style="max-width:880px;margin:0 auto;background:#fffdf9;border:1px solid #ecd8c6;border-radius:16px;padding:24px;">
          ${content}
        </div>
      </body>
    </html>
  `;
}

function formatDateTime(value) {
  const date = value instanceof Date ? value : new Date(value);
  return new Intl.DateTimeFormat("en-IN", {
    timeZone: process.env.ALERT_TIMEZONE || "Asia/Kolkata",
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12: true
  }).format(date);
}

function formatCurrency(value) {
  return new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    maximumFractionDigits: 2
  }).format(Number(value || 0));
}

function formatPercent(value) {
  return `${Math.round(Number(value || 0) * 100)}%`;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function escapeAttribute(value) {
  return escapeHtml(value);
}

module.exports = {
  canSendEmail,
  isEmailEnabled,
  sendEmailAlert
};
