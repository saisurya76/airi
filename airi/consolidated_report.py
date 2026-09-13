"""
Phase 3: a project's "consolidated report" — one summary that rolls up
everything saved inside a project (every tool's run history, plus the
notes/comments history) into a single view. The Actions tab's on-screen
summary and its downloadable PDF are both built from the exact same
aggregation functions here, so the two can never silently disagree —
same reasoning as report.py / report_render.py for the standalone
Load-test report.

Pure logic only: every function here takes already-fetched dicts/lists.
api.py's `_build_consolidated_report` does the actual DB reads
(db.list_tool_runs, db.list_notes, db.get_project) and passes their
results in — that split is what keeps this module unit-testable without
a database.
"""

import html as _html
from typing import Any, Dict, List, Optional

from .report_render import (
    _AMBER,
    _BORDER,
    _GREEN,
    _MUTED,
    _NAVY,
    _RED,
    _esc,
    _fmt_int,
    _fmt_money,
    _stat_card,
    _status_badge,
)
from .workspaces import TECH_STACK_CATEGORIES

TOOL_LABELS = {
    "analyze": "Standard analyze",
    "exact": "Exact mode",
    "project": "Traffic projection",
    "report": "Load-test report",
}
TOOL_ORDER = ("analyze", "exact", "project", "report")

MAX_NOTES_LISTED = 100  # guardrail, matches report.py's MAX_FLAGGED_LISTED spirit


def _fmt_ts(value: Any) -> str:
    """Short, fixed-width-friendly display for a timestamp. `value` may
    be a real datetime (api.py passes raw db.py rows straight through
    when building the PDF, so created_at is a psycopg2 datetime there)
    or an already-serialized ISO string (the JSON response path, and
    test fixtures) — str(datetime) and isoformat() both include
    microseconds, which is both unreadable and, at 13px in a 160px table
    cell, long enough to overflow into the next column. Never raises —
    worst case falls back to a plain str()."""
    try:
        if hasattr(value, "strftime"):
            return value.strftime("%Y-%m-%d %H:%M UTC")
        text = str(value).replace("T", " ")
        return text[:16] if len(text) > 16 else text
    except Exception:
        return str(value)


# ---------- pure aggregation (also used by the Dashboard tab's numbers,
# indirectly, since the frontend mirrors this same logic in JS to avoid
# a round trip per chart — see frontend/workspaces.html) ----------

def run_stats(tool: str, result: Dict[str, Any]) -> Dict[str, Any]:
    """Normalizes one saved run's result into {cost, tokens,
    status_counts} so runs from different tools can be summed together.
    Each tool reports cost/tokens under different field names, and only
    analyze/exact/report have real SAFE/WARNING/EXCEEDED granularity —
    see docs/WORKSPACES.md's Phase 3 section for why each mapping below
    was chosen."""
    zero_counts = {"SAFE": 0, "WARNING": 0, "EXCEEDED": 0}

    if tool in ("analyze", "exact"):
        status = result.get("status")
        counts = dict(zero_counts)
        if status in counts:
            counts[status] = 1
        return {
            "cost": float(result.get("estimated_cost") or 0),
            "tokens": int(result.get("estimated_total_tokens") or 0),
            "status_counts": counts,
        }

    if tool == "project":
        # A projection has no per-request SAFE/WARNING/EXCEEDED
        # breakdown — `any_exceeded` is the one signal it gives, so it's
        # treated as this run's single status for aggregation purposes.
        exceeded = bool(result.get("any_exceeded"))
        counts = dict(zero_counts)
        counts["EXCEEDED" if exceeded else "SAFE"] = 1
        return {
            "cost": float(result.get("total_cost") or 0),
            "tokens": int(result.get("total_tokens") or 0),
            "status_counts": counts,
        }

    if tool == "report":
        sc = result.get("status_counts") or {}
        counts = {k: int(sc.get(k) or 0) for k in ("SAFE", "WARNING", "EXCEEDED")}
        return {
            "cost": float(result.get("total_cost") or 0),
            "tokens": int(result.get("total_tokens") or 0),
            "status_counts": counts,
        }

    return {"cost": 0.0, "tokens": 0, "status_counts": dict(zero_counts)}


def aggregate_totals(runs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """`runs` is a flat list of saved-run rows (each needing at least
    "tool" and "result" keys) — the combined view across a project's
    *entire* saved history, not just the latest run per tool.

    Note this sums cost across tools even though a Load-test report run
    is itself already an aggregate over many synthetic requests — treat
    the grand total as an order-of-magnitude combined figure, not a
    literal sum of independent charges (documented in docs/WORKSPACES.md)."""
    total_cost = 0.0
    total_tokens = 0
    status_counts = {"SAFE": 0, "WARNING": 0, "EXCEEDED": 0}
    for run in runs:
        stats = run_stats(run["tool"], run["result"])
        total_cost += stats["cost"]
        total_tokens += stats["tokens"]
        for k in status_counts:
            status_counts[k] += stats["status_counts"][k]
    return {
        "total_runs": len(runs),
        "total_cost": total_cost,
        "total_tokens": total_tokens,
        "status_counts": status_counts,
    }


def aggregate_by_tool(runs_by_tool: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Dict[str, Any]]:
    """Same totals as aggregate_totals, but broken out per tool — the
    Dashboard tab's "cost by tool" / "runs by tool" charts and the PDF's
    "by tool" table both read from this shape."""
    out: Dict[str, Dict[str, Any]] = {}
    for tool, runs in runs_by_tool.items():
        agg = aggregate_totals([{"tool": tool, "result": r["result"]} for r in runs])
        out[tool] = {
            "count": agg["total_runs"],
            "cost": agg["total_cost"],
            "tokens": agg["total_tokens"],
            "status_counts": agg["status_counts"],
        }
    return out


def latest_per_tool(runs_by_tool: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Optional[Dict[str, Any]]]:
    """`runs_by_tool[tool]` is expected most-recent-first (db.list_tool_runs'
    own ordering) — this just picks index 0, or None if that tool has no
    saved runs yet."""
    return {tool: (runs[0] if runs else None) for tool, runs in runs_by_tool.items()}


# ---------- HTML rendering (fed into xhtml2pdf for the download, same
# as report_render.py — plain table-based HTML/CSS only) ----------

def _tech_stack_rows(tech_stack: Dict[str, str]) -> str:
    rows = ""
    for key, meta in TECH_STACK_CATEGORIES.items():
        value = (tech_stack or {}).get(key, "").strip()
        if not value:
            continue
        rows += f"""
        <tr>
          <td style="padding:6px 10px;border:1px solid {_BORDER};color:{_MUTED};width:180px;">{_esc(meta['label'])}</td>
          <td style="padding:6px 10px;border:1px solid {_BORDER};">{_esc(value)}</td>
        </tr>"""
    if not rows:
        return f'<p style="color:{_MUTED};font-size:13px;">No tech stack recorded.</p>'
    return f'<table style="width:100%;border-collapse:collapse;font-size:13px;">{rows}</table>'


def _by_tool_rows(by_tool: Dict[str, Dict[str, Any]]) -> str:
    rows = ""
    for tool in TOOL_ORDER:
        d = by_tool.get(tool) or {"count": 0, "cost": 0, "tokens": 0, "status_counts": {"SAFE": 0, "WARNING": 0, "EXCEEDED": 0}}
        sc = d["status_counts"]
        rows += f"""
        <tr>
          <td style="padding:8px 10px;border:1px solid {_BORDER};">{_esc(TOOL_LABELS[tool])}</td>
          <td style="padding:8px 10px;border:1px solid {_BORDER};">{_fmt_int(d['count'])}</td>
          <td style="padding:8px 10px;border:1px solid {_BORDER};">{_fmt_int(d['tokens'])}</td>
          <td style="padding:8px 10px;border:1px solid {_BORDER};">{_fmt_money(d['cost'])}</td>
          <td style="padding:8px 10px;border:1px solid {_BORDER};">{sc['SAFE']} / {sc['WARNING']} / {sc['EXCEEDED']}</td>
        </tr>"""
    return rows


def _render_latest_analyze_or_exact(result: Dict[str, Any]) -> str:
    note = result.get("exact_mode_note")
    note_html = (
        f'<p style="color:{_AMBER};font-size:12px;margin-top:8px;">{_esc(note)}</p>' if note else ""
    )
    return f"""
    <table style="width:100%;border-collapse:collapse;font-size:13px;">
      <tr><td style="padding:6px 10px;border:1px solid {_BORDER};color:{_MUTED};width:180px;">Model</td><td style="padding:6px 10px;border:1px solid {_BORDER};">{_esc(result.get('model', ''))}</td></tr>
      <tr><td style="padding:6px 10px;border:1px solid {_BORDER};color:{_MUTED};">Tokens</td><td style="padding:6px 10px;border:1px solid {_BORDER};">{_fmt_int(result.get('estimated_total_tokens', 0))} / {_fmt_int(result.get('context_window', 0))} ({(result.get('context_utilization', 0) or 0) * 100:.1f}%)</td></tr>
      <tr><td style="padding:6px 10px;border:1px solid {_BORDER};color:{_MUTED};">Status</td><td style="padding:6px 10px;border:1px solid {_BORDER};">{_status_badge(result.get('status', ''))}</td></tr>
      <tr><td style="padding:6px 10px;border:1px solid {_BORDER};color:{_MUTED};">Cost</td><td style="padding:6px 10px;border:1px solid {_BORDER};">{_fmt_money(result.get('estimated_cost', 0))}</td></tr>
      <tr><td style="padding:6px 10px;border:1px solid {_BORDER};color:{_MUTED};">Method / confidence</td><td style="padding:6px 10px;border:1px solid {_BORDER};">{_esc(result.get('method', ''))} / {_esc(result.get('confidence', ''))}</td></tr>
    </table>
    {note_html}"""


def _render_latest_project(result: Dict[str, Any]) -> str:
    archetypes = result.get("archetypes") or []
    arche_rows = ""
    for a in archetypes:
        arche_rows += f"""
        <tr>
          <td style="padding:6px 10px;border:1px solid {_BORDER};">{_esc(a.get('name', ''))}</td>
          <td style="padding:6px 10px;border:1px solid {_BORDER};">{_esc(a.get('model', ''))}</td>
          <td style="padding:6px 10px;border:1px solid {_BORDER};">{_fmt_int(a.get('volume', 0))}</td>
          <td style="padding:6px 10px;border:1px solid {_BORDER};">{_fmt_int(a.get('projected_total_tokens', 0))}</td>
          <td style="padding:6px 10px;border:1px solid {_BORDER};">{_fmt_money(a.get('projected_cost', 0))}</td>
        </tr>"""
    arche_table = f"""
    <table style="width:100%;border-collapse:collapse;font-size:13px;margin-top:10px;">
      <thead><tr style="background:{_NAVY};color:#fff;">
        <th style="padding:6px 10px;text-align:left;">Archetype</th>
        <th style="padding:6px 10px;text-align:left;">Model</th>
        <th style="padding:6px 10px;text-align:left;">Volume</th>
        <th style="padding:6px 10px;text-align:left;">Tokens</th>
        <th style="padding:6px 10px;text-align:left;">Cost</th>
      </tr></thead>
      <tbody>{arche_rows}</tbody>
    </table>""" if arche_rows else ""

    exceeded = bool(result.get("any_exceeded"))
    return f"""
    <table style="width:100%;border-collapse:collapse;font-size:13px;">
      <tr><td style="padding:6px 10px;border:1px solid {_BORDER};color:{_MUTED};width:180px;">Total volume</td><td style="padding:6px 10px;border:1px solid {_BORDER};">{_fmt_int(result.get('total_volume', 0))}</td></tr>
      <tr><td style="padding:6px 10px;border:1px solid {_BORDER};color:{_MUTED};">Total tokens</td><td style="padding:6px 10px;border:1px solid {_BORDER};">{_fmt_int(result.get('total_tokens', 0))}</td></tr>
      <tr><td style="padding:6px 10px;border:1px solid {_BORDER};color:{_MUTED};">Total cost</td><td style="padding:6px 10px;border:1px solid {_BORDER};">{_fmt_money(result.get('total_cost', 0))}</td></tr>
      <tr><td style="padding:6px 10px;border:1px solid {_BORDER};color:{_MUTED};">Any archetype exceeded?</td><td style="padding:6px 10px;border:1px solid {_BORDER};">{_status_badge('EXCEEDED' if exceeded else 'SAFE')}</td></tr>
    </table>
    {arche_table}"""


def _render_latest_report(result: Dict[str, Any]) -> str:
    sc = result.get("status_counts") or {}
    return f"""
    <table style="width:100%;border-collapse:collapse;font-size:13px;">
      <tr><td style="padding:6px 10px;border:1px solid {_BORDER};color:{_MUTED};width:180px;">Total requests</td><td style="padding:6px 10px;border:1px solid {_BORDER};">{_fmt_int(result.get('total_requests', 0))}</td></tr>
      <tr><td style="padding:6px 10px;border:1px solid {_BORDER};color:{_MUTED};">Total tokens</td><td style="padding:6px 10px;border:1px solid {_BORDER};">{_fmt_int(result.get('total_tokens', 0))}</td></tr>
      <tr><td style="padding:6px 10px;border:1px solid {_BORDER};color:{_MUTED};">Total cost</td><td style="padding:6px 10px;border:1px solid {_BORDER};">{_fmt_money(result.get('total_cost', 0))}</td></tr>
      <tr><td style="padding:6px 10px;border:1px solid {_BORDER};color:{_MUTED};">Status breakdown</td><td style="padding:6px 10px;border:1px solid {_BORDER};">SAFE {sc.get('SAFE', 0)} / WARNING {sc.get('WARNING', 0)} / EXCEEDED {sc.get('EXCEEDED', 0)}</td></tr>
    </table>"""


_LATEST_RENDERERS = {
    "analyze": _render_latest_analyze_or_exact,
    "exact": _render_latest_analyze_or_exact,
    "project": _render_latest_project,
    "report": _render_latest_report,
}


def render_single_run_html(project: Dict[str, Any], prepared_by: str, run: Dict[str, Any]) -> str:
    """Phase 4: a standalone PDF for exactly one saved run (the
    per-tab "download this run" button), as opposed to the whole
    project's consolidated report. Reuses the same per-tool renderers
    as the "latest run" sections above, so a run looks identical
    whether it's shown there or downloaded on its own."""
    tool = run["tool"]
    label = TOOL_LABELS.get(tool, tool)
    body = _LATEST_RENDERERS.get(tool, lambda _r: "")(run.get("result") or {})
    meta = f"{_esc(run.get('label') or '(untitled run)')} &middot; saved {_esc(_fmt_ts(run.get('created_at', '')))}"
    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8" />
<title>{_esc(project.get('title', ''))} — {_esc(label)} — AIRI</title>
</head>
<body style="margin:0;padding:0;background:#ffffff;font-family:Helvetica,Arial,sans-serif;color:#1a1a1a;">
  <table style="width:100%;border-collapse:collapse;background:{_NAVY};">
    <tr><td style="background:{_NAVY};padding:28px 36px 0 36px;border:none;">
      <span style="color:{_AMBER};font-size:12px;font-weight:700;letter-spacing:0.08em;">AIRI &mdash; AI REQUEST INTELLIGENCE</span>
    </td></tr>
    <tr><td style="background:{_NAVY};padding:6px 36px 0 36px;border:none;">
      <span style="color:#ffffff;font-size:22px;font-weight:700;">{_esc(project.get('title', ''))}</span>
    </td></tr>
    <tr><td style="background:{_NAVY};padding:6px 36px 0 36px;border:none;">
      <span style="color:#c7cedd;font-size:12px;">{_esc(label)} &middot; {meta}</span>
    </td></tr>
    <tr><td style="background:{_NAVY};padding:0 36px 28px 36px;border:none;">
      <span style="color:#c7cedd;font-size:12px;">Prepared by: {_esc(prepared_by)}</span>
    </td></tr>
  </table>

  <div style="padding:28px 36px;">
    {body}
    <p style="color:{_MUTED};font-size:11px;margin-top:36px;border-top:1px solid {_BORDER};padding-top:14px;">
      Generated by AIRI — estimates only; not a substitute for your provider's actual billing.
    </p>
  </div>
</body>
</html>"""


def _latest_run_section(tool: str, run: Optional[Dict[str, Any]]) -> str:
    label = TOOL_LABELS[tool]
    if run is None:
        return f"""
        <h2 style="color:{_NAVY};font-size:16px;margin:28px 0 10px;">{_esc(label)}</h2>
        <p style="color:{_MUTED};font-size:13px;">No runs saved yet for this tool.</p>"""
    meta = f"{_esc(run.get('label') or '(untitled run)')} &middot; saved {_esc(_fmt_ts(run.get('created_at', '')))}"
    body = _LATEST_RENDERERS[tool](run.get("result") or {})
    return f"""
    <h2 style="color:{_NAVY};font-size:16px;margin:28px 0 4px;">{_esc(label)}</h2>
    <p style="color:{_MUTED};font-size:12px;margin:0 0 8px;">Latest saved run: {meta}</p>
    {body}"""


def _notes_section(notes: List[Dict[str, Any]]) -> str:
    if not notes:
        return f'<p style="color:{_MUTED};font-size:13px;">No notes recorded for this project.</p>'
    shown = notes[:MAX_NOTES_LISTED]
    rows = ""
    for n in shown:
        rows += f"""
        <tr>
          <td style="padding:8px 10px;border:1px solid {_BORDER};color:{_MUTED};width:150px;">{_esc(_fmt_ts(n.get('created_at', '')))}</td>
          <td style="padding:8px 10px;border:1px solid {_BORDER};">{_esc(n.get('body', '')).replace(chr(10), '<br/>')}</td>
        </tr>"""
    truncated_note = (
        f'<p style="color:{_MUTED};font-size:12px;margin-top:8px;">+ {len(notes) - MAX_NOTES_LISTED} older note(s) not shown — see the Notes tab for the full history.</p>'
        if len(notes) > MAX_NOTES_LISTED else ""
    )
    return f"""
    <table style="width:100%;border-collapse:collapse;font-size:13px;">
      <thead><tr style="background:{_NAVY};color:#fff;">
        <th style="padding:8px 10px;text-align:left;">Date</th>
        <th style="padding:8px 10px;text-align:left;">Note</th>
      </tr></thead>
      <tbody>{rows}</tbody>
    </table>
    {truncated_note}"""


def render_consolidated_report_html(
    project: Dict[str, Any],
    prepared_by: str,
    generated_at: str,
    totals: Dict[str, Any],
    by_tool: Dict[str, Dict[str, Any]],
    latest: Dict[str, Optional[Dict[str, Any]]],
    notes: List[Dict[str, Any]],
) -> str:
    total = totals["total_runs"]
    sc = totals["status_counts"]
    status_rows = ""
    for status in ("SAFE", "WARNING", "EXCEEDED"):
        count = sc.get(status, 0)
        pct = (count / total * 100) if total else 0
        status_rows += f"""
        <tr>
          <td style="padding:8px 10px;border:1px solid {_BORDER};">{_status_badge(status)}</td>
          <td style="padding:8px 10px;border:1px solid {_BORDER};">{_fmt_int(count)}</td>
          <td style="padding:8px 10px;border:1px solid {_BORDER};">{pct:.1f}%</td>
        </tr>"""

    stats = [
        ("Total saved runs", _fmt_int(totals["total_runs"])),
        ("Total estimated tokens", _fmt_int(totals["total_tokens"])),
        ("Total estimated cost", _fmt_money(totals["total_cost"])),
    ]
    stat_row = "<tr>" + "".join(_stat_card(l, v) for l, v in stats) + "</tr>"

    latest_sections = "".join(_latest_run_section(tool, latest.get(tool)) for tool in TOOL_ORDER)

    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8" />
<title>{_esc(project.get('title', ''))} — AIRI Consolidated Project Report</title>
</head>
<body style="margin:0;padding:0;background:#ffffff;font-family:Helvetica,Arial,sans-serif;color:#1a1a1a;">
  <table style="width:100%;border-collapse:collapse;background:{_NAVY};">
    <tr><td style="background:{_NAVY};padding:28px 36px 0 36px;border:none;">
      <span style="color:{_AMBER};font-size:12px;font-weight:700;letter-spacing:0.08em;">AIRI &mdash; AI REQUEST INTELLIGENCE</span>
    </td></tr>
    <tr><td style="background:{_NAVY};padding:6px 36px 0 36px;border:none;">
      <span style="color:#ffffff;font-size:24px;font-weight:700;">{_esc(project.get('title', ''))}</span>
    </td></tr>
    <tr><td style="background:{_NAVY};padding:6px 36px 6px 36px;border:none;">
      <span style="color:#c7cedd;font-size:12px;">Consolidated project report &middot; generated {_esc(_fmt_ts(generated_at))}</span>
    </td></tr>
    <tr><td style="background:{_NAVY};padding:0 36px 28px 36px;border:none;">
      <span style="color:#c7cedd;font-size:12px;">Prepared by: {_esc(prepared_by)}</span>
    </td></tr>
  </table>

  <div style="padding:28px 36px;">
    <h2 style="color:{_NAVY};font-size:16px;margin:0 0 10px;">Project overview</h2>
    <p style="font-size:13px;color:#1a1a1a;margin:0 0 12px;">{_esc(project.get('description', '') or 'No description recorded.')}</p>
    {_tech_stack_rows(project.get('tech_stack') or {})}

    <h2 style="color:{_NAVY};font-size:16px;margin:28px 0 10px;">Totals across all saved runs</h2>
    <table style="width:100%;border-collapse:collapse;">{stat_row}</table>

    <h2 style="color:{_NAVY};font-size:16px;margin:28px 0 10px;">Status breakdown</h2>
    <table style="width:100%;border-collapse:collapse;font-size:13px;">
      <thead><tr style="background:{_NAVY};color:#fff;">
        <th style="padding:8px 10px;text-align:left;">Status</th>
        <th style="padding:8px 10px;text-align:left;">Runs</th>
        <th style="padding:8px 10px;text-align:left;">Share</th>
      </tr></thead>
      <tbody>{status_rows}</tbody>
    </table>

    <h2 style="color:{_NAVY};font-size:16px;margin:28px 0 10px;">By tool</h2>
    <table style="width:100%;border-collapse:collapse;font-size:13px;">
      <thead><tr style="background:{_NAVY};color:#fff;">
        <th style="padding:8px 10px;text-align:left;">Tool</th>
        <th style="padding:8px 10px;text-align:left;">Saved runs</th>
        <th style="padding:8px 10px;text-align:left;">Tokens</th>
        <th style="padding:8px 10px;text-align:left;">Cost</th>
        <th style="padding:8px 10px;text-align:left;">Safe / Warning / Exceeded</th>
      </tr></thead>
      <tbody>{_by_tool_rows(by_tool)}</tbody>
    </table>

    {latest_sections}

    <h2 style="color:{_NAVY};font-size:16px;margin:28px 0 10px;">Notes / comments history</h2>
    {_notes_section(notes)}

    <p style="color:{_MUTED};font-size:11px;margin-top:36px;border-top:1px solid {_BORDER};padding-top:14px;">
      Generated by AIRI — estimates only (see method/confidence per run); not a substitute for your provider's actual billing.
      Prepared by the account that created this project ({_esc(prepared_by)}).
    </p>
  </div>
</body>
</html>"""
