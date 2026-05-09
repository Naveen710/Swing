from __future__ import annotations

import smtplib
from dataclasses import dataclass
from datetime import UTC, datetime
from email.message import EmailMessage
from html import escape
from zoneinfo import ZoneInfo

from app.config import settings
from app.schemas import ScanRequest, ScanResponse, ScanUniverse, TradeSetup
from app.services.scanner import scanner_service

UNIVERSE_LABELS: dict[ScanUniverse, str] = {
    ScanUniverse.NIFTY500: "Nifty 500",
    ScanUniverse.NIFTY_SMALLCAP_250: "Nifty Smallcap 250",
    ScanUniverse.MID_SMALL_2000_PLUS: "Mid & Small 2000+",
}

REPORT_UNIVERSES: tuple[ScanUniverse, ...] = (
    ScanUniverse.NIFTY500,
    ScanUniverse.NIFTY_SMALLCAP_250,
    ScanUniverse.MID_SMALL_2000_PLUS,
)


class EmailReportError(RuntimeError):
    """Raised when the daily email report cannot be generated or sent."""


@dataclass(frozen=True)
class UniverseReport:
    universe: ScanUniverse
    response: ScanResponse

    @property
    def label(self) -> str:
        return UNIVERSE_LABELS[self.universe]


def build_daily_universe_reports() -> list[UniverseReport]:
    reports: list[UniverseReport] = []
    for universe in REPORT_UNIVERSES:
        response = scanner_service.run_scan_sync(
            ScanRequest(
                universe=universe,
                max_results=settings.email_report_max_results,
                min_probability=settings.email_report_min_probability,
                min_risk_reward=settings.email_report_min_risk_reward,
                investment_amount=settings.default_investment_amount,
            )
        )
        reports.append(UniverseReport(universe=universe, response=response))
    return reports


def compose_daily_email(
    reports: list[UniverseReport],
    generated_at: datetime | None = None,
) -> tuple[str, str, str]:
    report_time = (generated_at or datetime.now(UTC)).astimezone(
        ZoneInfo(settings.email_report_timezone)
    )
    subject = (
        "Daily NSE Swing Scanner Results"
        f" - {report_time.strftime('%d %b %Y %I:%M %p %Z')}"
    )

    text_sections = [
        "Daily NSE Swing Scanner Results",
        f"Generated at: {report_time.strftime('%d %b %Y %I:%M %p %Z')}",
        "",
    ]
    html_sections = [
        "<html><body>",
        "<h2>Daily NSE Swing Scanner Results</h2>",
        f"<p><strong>Generated at:</strong> {escape(report_time.strftime('%d %b %Y %I:%M %p %Z'))}</p>",
    ]

    for report in reports:
        text_sections.extend(_render_universe_text(report))
        html_sections.append(_render_universe_html(report))

    html_sections.append("</body></html>")
    return subject, "\n".join(text_sections).strip(), "".join(html_sections)


def send_daily_email_report(
    reports: list[UniverseReport],
    *,
    dry_run: bool = False,
) -> tuple[str, str, str]:
    subject, text_body, html_body = compose_daily_email(reports)
    if dry_run:
        return subject, text_body, html_body

    _validate_email_settings()
    recipients = list(settings.email_report_recipients)
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = settings.email_from_address
    message["To"] = ", ".join(recipients)
    message.set_content(text_body)
    message.add_alternative(html_body, subtype="html")

    with smtplib.SMTP(settings.email_smtp_host, settings.email_smtp_port, timeout=60) as smtp:
        smtp.ehlo()
        if settings.email_smtp_starttls:
            smtp.starttls()
            smtp.ehlo()
        smtp.login(settings.email_smtp_username, settings.email_smtp_password)
        smtp.send_message(message)

    return subject, text_body, html_body


def _validate_email_settings() -> None:
    if not settings.email_report_enabled:
        raise EmailReportError("Daily email reporting is disabled.")
    if not settings.email_report_recipients:
        raise EmailReportError("EMAIL_REPORT_RECIPIENTS is not configured.")
    if not settings.email_from_address:
        raise EmailReportError("EMAIL_FROM_ADDRESS is not configured.")
    if not settings.email_smtp_host:
        raise EmailReportError("EMAIL_SMTP_HOST is not configured.")
    if not settings.email_smtp_username:
        raise EmailReportError("EMAIL_SMTP_USERNAME is not configured.")
    if not settings.email_smtp_password:
        raise EmailReportError("EMAIL_SMTP_PASSWORD is not configured.")


def _render_universe_text(report: UniverseReport) -> list[str]:
    lines = [
        f"{report.label}",
        f"Universe size: {report.response.universe_size}",
        f"Ranked results: {len(report.response.results)}",
        f"Execution: {_describe_execution(report.universe)}",
    ]
    if not report.response.results:
        lines.extend(
            [
                "No opportunities matched the configured filters.",
                "",
            ]
        )
        return lines

    header = (
        "Stock | Sector | Pattern | Entry | Stop | Target | Prob | RR | ETA | "
        "Expected Profit | RS | Liquidity20D | Event"
    )
    lines.extend([header, "-" * len(header)])
    for signal in report.response.results:
        lines.append(_render_signal_text(signal))
    lines.append("")
    return lines


def _render_universe_html(report: UniverseReport) -> str:
    header = (
        f"<h3>{escape(report.label)}</h3>"
        f"<p><strong>Universe size:</strong> {report.response.universe_size}"
        f" &nbsp; <strong>Ranked results:</strong> {len(report.response.results)}"
        f" &nbsp; <strong>Execution:</strong> {escape(_describe_execution(report.universe))}</p>"
    )
    if not report.response.results:
        return header + "<p>No opportunities matched the configured filters.</p>"

    rows = "".join(_render_signal_html(signal) for signal in report.response.results)
    return (
        header
        + "<table border='1' cellpadding='6' cellspacing='0' style='border-collapse:collapse;'>"
        + "<thead><tr>"
        + "<th>Stock</th><th>Sector</th><th>Pattern</th><th>Entry</th><th>Stop</th><th>Target</th>"
        + "<th>Probability</th><th>R:R</th><th>Target ETA</th><th>Expected Profit</th>"
        + "<th>RS</th><th>Liquidity 20D</th><th>Event</th>"
        + "</tr></thead><tbody>"
        + rows
        + "</tbody></table>"
    )


def _render_signal_text(signal: TradeSetup) -> str:
    return " | ".join(
        [
            signal.symbol,
            signal.sector,
            signal.pattern.value.replace("_", " ").title(),
            _format_currency(signal.entry_price),
            _format_currency(signal.stop_loss),
            _format_currency(signal.target_price),
            f"{round(signal.probability_score * 100)}%",
            f"{signal.risk_reward_ratio:.2f}",
            f"{signal.estimated_target_sessions} sessions ({signal.estimated_target_date.isoformat()})",
            _format_currency(signal.expected_profit_amount),
            f"{round(signal.relative_strength.score * 100)}",
            f"{signal.liquidity.average_traded_value_20d_cr:.1f} Cr",
            signal.event_risk.risk_level.title(),
        ]
    )


def _render_signal_html(signal: TradeSetup) -> str:
    return (
        "<tr>"
        + f"<td>{escape(signal.symbol)}</td>"
        + f"<td>{escape(signal.sector)}</td>"
        + f"<td>{escape(signal.pattern.value.replace('_', ' ').title())}</td>"
        + f"<td>{escape(_format_currency(signal.entry_price))}</td>"
        + f"<td>{escape(_format_currency(signal.stop_loss))}</td>"
        + f"<td>{escape(_format_currency(signal.target_price))}</td>"
        + f"<td>{round(signal.probability_score * 100)}%</td>"
        + f"<td>{signal.risk_reward_ratio:.2f}</td>"
        + f"<td>{signal.estimated_target_sessions} sessions ({escape(signal.estimated_target_date.isoformat())})</td>"
        + f"<td>{escape(_format_currency(signal.expected_profit_amount))}</td>"
        + f"<td>{round(signal.relative_strength.score * 100)}</td>"
        + f"<td>{signal.liquidity.average_traded_value_20d_cr:.1f} Cr</td>"
        + f"<td>{escape(signal.event_risk.risk_level.title())}</td>"
        + "</tr>"
    )


def _format_currency(value: float) -> str:
    return f"INR {value:,.2f}"


def _describe_execution(universe: ScanUniverse) -> str:
    if universe == ScanUniverse.MID_SMALL_2000_PLUS:
        return f"{settings.mid_small_parallel_workers} parallel workers"
    return f"{settings.scan_workers} parallel workers"
