"""
Renders a RunReport (see report.py) to a single, self-contained HTML
document — used directly for the HTML report view, and fed into
xhtml2pdf (API layer only, not a core dependency) to produce the PDF
download. One template drives both, so they always agree.

Deliberately plain, table-based HTML/CSS: xhtml2pdf's rendering engine
doesn't support flexbox/grid, so anything fancier would look right in
a browser and wrong in the PDF. Kept dependency-free (stdlib only).
"""

import html as _html
from .report import RunReport

_NAVY = "#14213d"
_NAVY_LIGHT = "#233863"
_AMBER = "#f2b84b"
_GREEN = "#2ecc71"
_RED = "#e5383b"
_MUTED = "#6b7280"
_BORDER = "#e5e7eb"

_STATUS_COLOR = {"SAFE": _GREEN, "WARNING": _AMBER, "EXCEEDED": _RED}


def _esc(value) -> str:
    return _html.escape(str(value))


def _fmt_money(v: float) -> str:
    return f"${v:,.6f}" if v < 0.01 else f"${v:,.4f}"


def _fmt_int(v: int) -> str:
    return f"{v:,}"


def _status_badge(status: str) -> str:
    color = _STATUS_COLOR.get(status, _MUTED)
    return (
        f'<span style="display:inline-block;padding:2px 9px;border-radius:3px;'
        f'font-size:11px;font-weight:700;letter-spacing:0.02em;color:#ffffff;'
        f'background:{color};">{_esc(status)}</span>'
    )


def _stat_card(label: str, value: str) -> str:
    return f"""
      <td style="padding:14px 16px;border:1px solid {_BORDER};background:#fafafa;">
        <div style="font-size:11px;color:{_MUTED};text-transform:uppercase;letter-spacing:0.04em;margin-bottom:4px;">{_esc(label)}</div>
        <div style="font-size:20px;font-weight:700;color:{_NAVY};">{value}</div>
      </td>"""


def render_report_html(report: RunReport) -> str:
    stats = [
        ("Total requests", _fmt_int(report.total_requests)),
        ("Total tokens", _fmt_int(report.total_tokens)),
        ("Total cost", _fmt_money(report.total_cost)),
        ("Avg tokens / request", f"{report.avg_tokens_per_request:,.1f}"),
        ("Avg cost / request", _fmt_money(report.avg_cost_per_request)),
    ]
    if report.requests_per_second is not None:
        stats.append(("Requests / second", f"{report.requests_per_second:,.2f}"))
        stats.append(("Duration", f"{report.duration_seconds:,.1f}s"))

    stat_rows = ""
    for i in range(0, len(stats), 3):
        chunk = stats[i:i + 3]
        stat_rows += "<tr>" + "".join(_stat_card(l, v) for l, v in chunk)
        if len(chunk) < 3:
            stat_rows += f'<td style="border:none;" colspan="{3 - len(chunk)}"></td>'
        stat_rows += "</tr>"

    total = report.total_requests
    status_rows = ""
    for status in ("SAFE", "WARNING", "EXCEEDED"):
        count = report.status_counts.get(status, 0)
        pct = (count / total * 100) if total else 0
        status_rows += f"""
        <tr>
          <td style="padding:8px 10px;border:1px solid {_BORDER};">{_status_badge(status)}</td>
          <td style="padding:8px 10px;border:1px solid {_BORDER};">{_fmt_int(count)}</td>
          <td style="padding:8px 10px;border:1px solid {_BORDER};">{pct:.1f}%</td>
        </tr>"""

    label_rows = ""
    for label, d in sorted(report.by_label.items(), key=lambda kv: -kv[1]["tokens"]):
        label_rows += f"""
        <tr>
          <td style="padding:8px 10px;border:1px solid {_BORDER};">{_esc(label)}</td>
          <td style="padding:8px 10px;border:1px solid {_BORDER};">{_fmt_int(d['count'])}</td>
          <td style="padding:8px 10px;border:1px solid {_BORDER};">{_fmt_int(d['tokens'])}</td>
          <td style="padding:8px 10px;border:1px solid {_BORDER};">{_fmt_money(d['cost'])}</td>
          <td style="padding:8px 10px;border:1px solid {_BORDER};">{d['SAFE']} / {d['WARNING']} / {d['EXCEEDED']}</td>
        </tr>"""

    model_rows = ""
    for model, d in sorted(report.by_model.items(), key=lambda kv: -kv[1]["cost"]):
        model_rows += f"""
        <tr>
          <td style="padding:8px 10px;border:1px solid {_BORDER};">{_esc(model)}</td>
          <td style="padding:8px 10px;border:1px solid {_BORDER};">{_fmt_int(d['count'])}</td>
          <td style="padding:8px 10px;border:1px solid {_BORDER};">{_fmt_int(d['tokens'])}</td>
          <td style="padding:8px 10px;border:1px solid {_BORDER};">{_fmt_money(d['cost'])}</td>
        </tr>"""

    phase_rows = ""
    for phase, d in sorted(report.by_phase.items()):
        phase_rows += f"""
        <tr>
          <td style="padding:8px 10px;border:1px solid {_BORDER};">{_esc(phase)}</td>
          <td style="padding:8px 10px;border:1px solid {_BORDER};">{_fmt_int(d['count'])}</td>
          <td style="padding:8px 10px;border:1px solid {_BORDER};">{_fmt_int(d['tokens'])}</td>
          <td style="padding:8px 10px;border:1px solid {_BORDER};">{_fmt_money(d['cost'])}</td>
        </tr>"""

    if report.flagged_requests:
        flagged_rows = ""
        for r in report.flagged_requests:
            flagged_rows += f"""
            <tr>
              <td style="padding:8px 10px;border:1px solid {_BORDER};">{_esc(r.get('label', ''))}</td>
              <td style="padding:8px 10px;border:1px solid {_BORDER};">{_esc(r.get('phase') or '—')}</td>
              <td style="padding:8px 10px;border:1px solid {_BORDER};">{_esc(r['model'])}</td>
              <td style="padding:8px 10px;border:1px solid {_BORDER};">{_status_badge(r['status'])}</td>
              <td style="padding:8px 10px;border:1px solid {_BORDER};">{_fmt_int(r['estimated_total_tokens'])} / {_fmt_int(r['context_window'])}</td>
              <td style="padding:8px 10px;border:1px solid {_BORDER};">{r['context_utilization'] * 100:.1f}%</td>
              <td style="padding:8px 10px;border:1px solid {_BORDER};">{_fmt_money(r['estimated_cost'])}</td>
            </tr>"""
        truncated_note = (
            f'<p style="color:{_MUTED};font-size:12px;margin-top:8px;">+ more not shown — see the full JSON via <code>POST /report</code>.</p>'
            if report.flagged_truncated else ""
        )
        flagged_section = f"""
        <h2 style="color:{_NAVY};font-size:16px;margin:28px 0 10px;">Flagged requests (WARNING / EXCEEDED)</h2>
        <table style="width:100%;border-collapse:collapse;font-size:13px;">
          <thead><tr style="background:{_NAVY};color:#fff;">
            <th style="padding:8px 10px;text-align:left;">Label</th>
            <th style="padding:8px 10px;text-align:left;">Phase</th>
            <th style="padding:8px 10px;text-align:left;">Model</th>
            <th style="padding:8px 10px;text-align:left;">Status</th>
            <th style="padding:8px 10px;text-align:left;">Tokens / window</th>
            <th style="padding:8px 10px;text-align:left;">Utilization</th>
            <th style="padding:8px 10px;text-align:left;">Cost</th>
          </tr></thead>
          <tbody>{flagged_rows}</tbody>
        </table>
        {truncated_note}"""
    else:
        flagged_section = f"""
        <h2 style="color:{_NAVY};font-size:16px;margin:28px 0 10px;">Flagged requests</h2>
        <p style="color:{_GREEN};font-weight:600;">None — every request in this run stayed under 80% of its model's context window.</p>"""

    peak = report.peak_request
    peak_section = ""
    if peak:
        peak_section = f"""
        <h2 style="color:{_NAVY};font-size:16px;margin:28px 0 10px;">Peak single request</h2>
        <table style="width:100%;border-collapse:collapse;font-size:13px;">
          <tr><td style="padding:6px 10px;border:1px solid {_BORDER};color:{_MUTED};width:180px;">Label</td><td style="padding:6px 10px;border:1px solid {_BORDER};">{_esc(peak.get('label',''))}</td></tr>
          <tr><td style="padding:6px 10px;border:1px solid {_BORDER};color:{_MUTED};">Model</td><td style="padding:6px 10px;border:1px solid {_BORDER};">{_esc(peak['model'])}</td></tr>
          <tr><td style="padding:6px 10px;border:1px solid {_BORDER};color:{_MUTED};">Tokens</td><td style="padding:6px 10px;border:1px solid {_BORDER};">{_fmt_int(peak['estimated_total_tokens'])} / {_fmt_int(peak['context_window'])} ({peak['context_utilization']*100:.1f}%)</td></tr>
          <tr><td style="padding:6px 10px;border:1px solid {_BORDER};color:{_MUTED};">Status</td><td style="padding:6px 10px;border:1px solid {_BORDER};">{_status_badge(peak['status'])}</td></tr>
          <tr><td style="padding:6px 10px;border:1px solid {_BORDER};color:{_MUTED};">Cost</td><td style="padding:6px 10px;border:1px solid {_BORDER};">{_fmt_money(peak['estimated_cost'])}</td></tr>
        </table>"""

    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8" />
<title>{_esc(report.run_name)} — AIRI Token Usage Report</title>
</head>
<body style="margin:0;padding:0;background:#ffffff;font-family:Helvetica,Arial,sans-serif;color:#1a1a1a;">
  <table style="width:100%;border-collapse:collapse;background:{_NAVY};">
    <tr><td style="background:{_NAVY};padding:28px 36px 0 36px;border:none;">
      <span style="color:{_AMBER};font-size:12px;font-weight:700;letter-spacing:0.08em;">AIRI &mdash; AI REQUEST INTELLIGENCE</span>
    </td></tr>
    <tr><td style="background:{_NAVY};padding:6px 36px 0 36px;border:none;">
      <span style="color:#ffffff;font-size:24px;font-weight:700;">{_esc(report.run_name)}</span>
    </td></tr>
    <tr><td style="background:{_NAVY};padding:6px 36px 28px 36px;border:none;">
      <span style="color:#c7cedd;font-size:12px;">Generated {_esc(report.generated_at)}</span>
    </td></tr>
  </table>

  <div style="padding:28px 36px;">
    <h2 style="color:{_NAVY};font-size:16px;margin:0 0 10px;">Summary</h2>
    <table style="width:100%;border-collapse:collapse;">{stat_rows}</table>

    <h2 style="color:{_NAVY};font-size:16px;margin:28px 0 10px;">Status breakdown</h2>
    <table style="width:100%;border-collapse:collapse;font-size:13px;">
      <thead><tr style="background:{_NAVY};color:#fff;">
        <th style="padding:8px 10px;text-align:left;">Status</th>
        <th style="padding:8px 10px;text-align:left;">Requests</th>
        <th style="padding:8px 10px;text-align:left;">Share</th>
      </tr></thead>
      <tbody>{status_rows}</tbody>
    </table>

    <h2 style="color:{_NAVY};font-size:16px;margin:28px 0 10px;">By request type (label)</h2>
    <table style="width:100%;border-collapse:collapse;font-size:13px;">
      <thead><tr style="background:{_NAVY};color:#fff;">
        <th style="padding:8px 10px;text-align:left;">Label</th>
        <th style="padding:8px 10px;text-align:left;">Requests</th>
        <th style="padding:8px 10px;text-align:left;">Tokens</th>
        <th style="padding:8px 10px;text-align:left;">Cost</th>
        <th style="padding:8px 10px;text-align:left;">Safe / Warning / Exceeded</th>
      </tr></thead>
      <tbody>{label_rows}</tbody>
    </table>

    <h2 style="color:{_NAVY};font-size:16px;margin:28px 0 10px;">By model</h2>
    <table style="width:100%;border-collapse:collapse;font-size:13px;">
      <thead><tr style="background:{_NAVY};color:#fff;">
        <th style="padding:8px 10px;text-align:left;">Model</th>
        <th style="padding:8px 10px;text-align:left;">Requests</th>
        <th style="padding:8px 10px;text-align:left;">Tokens</th>
        <th style="padding:8px 10px;text-align:left;">Cost</th>
      </tr></thead>
      <tbody>{model_rows}</tbody>
    </table>

    <h2 style="color:{_NAVY};font-size:16px;margin:28px 0 10px;">By phase</h2>
    <table style="width:100%;border-collapse:collapse;font-size:13px;">
      <thead><tr style="background:{_NAVY};color:#fff;">
        <th style="padding:8px 10px;text-align:left;">Phase</th>
        <th style="padding:8px 10px;text-align:left;">Requests</th>
        <th style="padding:8px 10px;text-align:left;">Tokens</th>
        <th style="padding:8px 10px;text-align:left;">Cost</th>
      </tr></thead>
      <tbody>{phase_rows}</tbody>
    </table>

    {peak_section}
    {flagged_section}

    <p style="color:{_MUTED};font-size:11px;margin-top:36px;border-top:1px solid {_BORDER};padding-top:14px;">
      Generated by AIRI — estimates only (see method/confidence per request); not a substitute for your provider's actual billing.
    </p>
  </div>
</body>
</html>"""
