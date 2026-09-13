"""
Phase 4: cross-project comparison. Once a workspace has 2+ projects,
compare their saved-run totals side by side — reusing the exact same
per-run normalization as airi/consolidated_report.py (run_stats via
aggregate_totals/aggregate_by_tool), so a project's numbers here are
always identical to what its own Dashboard/Actions tabs already show.

Pure logic only: api.py's `_build_workspace_comparison` does the actual
DB reads (db.list_projects, db.list_tool_runs) and passes their results
in — same split as consolidated_report.py, for the same reason (unit
testable without a database).
"""

from typing import Any, Dict, List

from .consolidated_report import TOOL_LABELS, TOOL_ORDER, aggregate_by_tool, aggregate_totals
from .report_render import _BORDER, _MUTED, _NAVY, _esc, _fmt_int, _fmt_money, _stat_card, _status_badge


def project_summary(project: Dict[str, Any], runs_by_tool: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
    """One project's row in the comparison — its totals + by-tool
    breakdown, computed the exact same way as its own consolidated
    report (see airi/consolidated_report.py)."""
    all_runs = [r for runs in runs_by_tool.values() for r in runs]
    return {
        "project": project,
        "totals": aggregate_totals(all_runs),
        "by_tool": aggregate_by_tool(runs_by_tool),
    }


def aggregate_workspace_totals(project_summaries: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Sums every project's totals into one workspace-level figure. Same
    rough-combined-figure caveat as a single project's totals (a
    Load-test report run is itself already an aggregate) applies here
    too, one level up — see consolidated_report.aggregate_totals."""
    total_cost = 0.0
    total_tokens = 0
    total_runs = 0
    status_counts = {"SAFE": 0, "WARNING": 0, "EXCEEDED": 0}
    for s in project_summaries:
        t = s["totals"]
        total_cost += t["total_cost"]
        total_tokens += t["total_tokens"]
        total_runs += t["total_runs"]
        for k in status_counts:
            status_counts[k] += t["status_counts"][k]
    return {
        "project_count": len(project_summaries),
        "total_runs": total_runs,
        "total_cost": total_cost,
        "total_tokens": total_tokens,
        "status_counts": status_counts,
    }


def rank_by_cost(project_summaries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Most expensive project first — a comparison view reads more
    usefully as a leaderboard than in creation order."""
    return sorted(project_summaries, key=lambda s: s["totals"]["total_cost"], reverse=True)


# ---------- HTML rendering (fed into xhtml2pdf, same plain
# table-based HTML/CSS constraint as report_render.py /
# consolidated_report.py) ----------

def _project_rows(project_summaries: List[Dict[str, Any]]) -> str:
    rows = ""
    for s in project_summaries:
        p, t = s["project"], s["totals"]
        sc = t["status_counts"]
        rows += f"""
        <tr>
          <td style="padding:8px 10px;border:1px solid {_BORDER};">{_esc(p.get('title', ''))}</td>
          <td style="padding:8px 10px;border:1px solid {_BORDER};">{_fmt_int(t['total_runs'])}</td>
          <td style="padding:8px 10px;border:1px solid {_BORDER};">{_fmt_int(t['total_tokens'])}</td>
          <td style="padding:8px 10px;border:1px solid {_BORDER};">{_fmt_money(t['total_cost'])}</td>
          <td style="padding:8px 10px;border:1px solid {_BORDER};">{sc['SAFE']} / {sc['WARNING']} / {sc['EXCEEDED']}</td>
        </tr>"""
    return rows


def render_comparison_report_html(
    workspace: Dict[str, Any],
    prepared_by: str,
    generated_at: str,
    workspace_totals: Dict[str, Any],
    project_summaries: List[Dict[str, Any]],
) -> str:
    from .consolidated_report import _fmt_ts  # local import: avoids a circular top-level import

    stats = [
        ("Projects compared", _fmt_int(workspace_totals["project_count"])),
        ("Total saved runs", _fmt_int(workspace_totals["total_runs"])),
        ("Combined estimated cost", _fmt_money(workspace_totals["total_cost"])),
    ]
    stat_row = "<tr>" + "".join(_stat_card(l, v) for l, v in stats) + "</tr>"

    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8" />
<title>{_esc(workspace.get('title', ''))} — AIRI Project Comparison</title>
</head>
<body style="margin:0;padding:0;background:#ffffff;font-family:Helvetica,Arial,sans-serif;color:#1a1a1a;">
  <table style="width:100%;border-collapse:collapse;background:{_NAVY};">
    <tr><td style="background:{_NAVY};padding:28px 36px 0 36px;border:none;">
      <span style="color:#f2b84b;font-size:12px;font-weight:700;letter-spacing:0.08em;">AIRI &mdash; AI REQUEST INTELLIGENCE</span>
    </td></tr>
    <tr><td style="background:{_NAVY};padding:6px 36px 0 36px;border:none;">
      <span style="color:#ffffff;font-size:24px;font-weight:700;">{_esc(workspace.get('title', ''))}</span>
    </td></tr>
    <tr><td style="background:{_NAVY};padding:6px 36px 6px 36px;border:none;">
      <span style="color:#c7cedd;font-size:12px;">Project comparison &middot; generated {_esc(_fmt_ts(generated_at))}</span>
    </td></tr>
    <tr><td style="background:{_NAVY};padding:0 36px 28px 36px;border:none;">
      <span style="color:#c7cedd;font-size:12px;">Prepared by: {_esc(prepared_by)}</span>
    </td></tr>
  </table>

  <div style="padding:28px 36px;">
    <h2 style="color:{_NAVY};font-size:16px;margin:0 0 10px;">Workspace totals</h2>
    <table style="width:100%;border-collapse:collapse;">{stat_row}</table>

    <h2 style="color:{_NAVY};font-size:16px;margin:28px 0 10px;">Projects, ranked by estimated cost</h2>
    <table style="width:100%;border-collapse:collapse;font-size:13px;">
      <thead><tr style="background:{_NAVY};color:#fff;">
        <th style="padding:8px 10px;text-align:left;">Project</th>
        <th style="padding:8px 10px;text-align:left;">Saved runs</th>
        <th style="padding:8px 10px;text-align:left;">Tokens</th>
        <th style="padding:8px 10px;text-align:left;">Cost</th>
        <th style="padding:8px 10px;text-align:left;">Safe / Warning / Exceeded</th>
      </tr></thead>
      <tbody>{_project_rows(project_summaries)}</tbody>
    </table>

    <p style="color:{_MUTED};font-size:11px;margin-top:36px;border-top:1px solid {_BORDER};padding-top:14px;">
      Generated by AIRI — estimates only; not a substitute for your provider's actual billing.
      Prepared by the account that owns this workspace ({_esc(prepared_by)}).
    </p>
  </div>
</body>
</html>"""
