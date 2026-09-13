from datetime import datetime, timezone

from airi.consolidated_report import (
    _fmt_ts,
    aggregate_by_tool,
    aggregate_totals,
    latest_per_tool,
    render_consolidated_report_html,
    render_single_run_html,
    run_stats,
)

ANALYZE_RESULT = {
    "model": "claude-3-5-sonnet",
    "input_tokens": 500,
    "estimated_output_tokens": 200,
    "estimated_total_tokens": 700,
    "context_window": 200000,
    "context_utilization": 0.0035,
    "estimated_cost": 0.012,
    "method": "tokenizer",
    "confidence": "high",
    "status": "SAFE",
    "known_model": True,
}

PROJECT_RESULT = {
    "archetypes": [
        {"name": "Chat", "model": "claude-3-5-sonnet", "volume": 1000, "projected_total_tokens": 700000, "projected_cost": 12.0}
    ],
    "total_volume": 1000,
    "total_tokens": 700000,
    "total_cost": 12.0,
    "cost_by_model": {"claude-3-5-sonnet": 12.0},
    "tokens_by_model": {"claude-3-5-sonnet": 700000},
    "any_exceeded": True,
}

REPORT_RESULT = {
    "run_name": "Load test 1",
    "total_requests": 10,
    "total_tokens": 7000,
    "total_cost": 0.5,
    "status_counts": {"SAFE": 8, "WARNING": 1, "EXCEEDED": 1},
}


def test_fmt_ts_handles_real_datetime_without_microseconds():
    # This is the exact bug this test guards against: str(datetime) /
    # isoformat() both include microseconds and are long enough to
    # overflow a fixed-width PDF table cell (e.g. "2026-09-13
    # 20:19:46.816648+00:00" is 32 chars) — _fmt_ts must trim that down.
    dt = datetime(2026, 9, 13, 20, 19, 46, 816648, tzinfo=timezone.utc)
    formatted = _fmt_ts(dt)
    assert formatted == "2026-09-13 20:19 UTC"
    assert len(formatted) < 22


def test_fmt_ts_handles_iso_string():
    assert _fmt_ts("2026-01-01T00:00:00.123456+00:00") == "2026-01-01 00:00"


def test_fmt_ts_never_raises_on_junk_input():
    assert _fmt_ts(None) == "None"
    assert _fmt_ts(12345) == "12345"


def test_run_stats_analyze_and_exact():
    stats = run_stats("analyze", ANALYZE_RESULT)
    assert stats == {"cost": 0.012, "tokens": 700, "status_counts": {"SAFE": 1, "WARNING": 0, "EXCEEDED": 0}}
    assert run_stats("exact", ANALYZE_RESULT) == stats


def test_run_stats_project_uses_any_exceeded():
    stats = run_stats("project", PROJECT_RESULT)
    assert stats["cost"] == 12.0
    assert stats["tokens"] == 700000
    assert stats["status_counts"] == {"SAFE": 0, "WARNING": 0, "EXCEEDED": 1}

    safe_variant = dict(PROJECT_RESULT, any_exceeded=False)
    assert run_stats("project", safe_variant)["status_counts"] == {"SAFE": 1, "WARNING": 0, "EXCEEDED": 0}


def test_run_stats_report_uses_status_counts_verbatim():
    stats = run_stats("report", REPORT_RESULT)
    assert stats == {"cost": 0.5, "tokens": 7000, "status_counts": {"SAFE": 8, "WARNING": 1, "EXCEEDED": 1}}


def test_run_stats_unknown_tool_is_zero():
    assert run_stats("mystery", {}) == {"cost": 0.0, "tokens": 0, "status_counts": {"SAFE": 0, "WARNING": 0, "EXCEEDED": 0}}


def test_aggregate_totals_sums_across_tools():
    runs = [
        {"tool": "analyze", "result": ANALYZE_RESULT},
        {"tool": "project", "result": PROJECT_RESULT},
        {"tool": "report", "result": REPORT_RESULT},
    ]
    totals = aggregate_totals(runs)
    assert totals["total_runs"] == 3
    assert round(totals["total_cost"], 3) == round(0.012 + 12.0 + 0.5, 3)
    assert totals["total_tokens"] == 700 + 700000 + 7000
    assert totals["status_counts"] == {"SAFE": 8 + 1, "WARNING": 1, "EXCEEDED": 1 + 1}


def test_aggregate_totals_empty():
    totals = aggregate_totals([])
    assert totals == {"total_runs": 0, "total_cost": 0.0, "total_tokens": 0, "status_counts": {"SAFE": 0, "WARNING": 0, "EXCEEDED": 0}}


def test_aggregate_by_tool_and_latest_per_tool():
    runs_by_tool = {
        "analyze": [{"tool": "analyze", "result": ANALYZE_RESULT, "label": "b", "created_at": "2026-01-02"},
                    {"tool": "analyze", "result": ANALYZE_RESULT, "label": "a", "created_at": "2026-01-01"}],
        "exact": [],
        "project": [{"tool": "project", "result": PROJECT_RESULT, "label": "", "created_at": "2026-01-03"}],
        "report": [],
    }
    by_tool = aggregate_by_tool(runs_by_tool)
    assert by_tool["analyze"]["count"] == 2
    assert by_tool["exact"]["count"] == 0
    assert by_tool["project"]["cost"] == 12.0

    latest = latest_per_tool(runs_by_tool)
    assert latest["analyze"]["label"] == "b"  # index 0 = most recent, per db.list_tool_runs ordering
    assert latest["exact"] is None
    assert latest["project"]["label"] == ""
    assert latest["report"] is None


def test_render_consolidated_report_html_includes_key_facts():
    project = {"title": "Checkout Revamp", "description": "Rewriting checkout flow", "tech_stack": {"ai_services": "Anthropic", "ai_model": "claude-3-5-sonnet"}}
    runs_by_tool = {
        "analyze": [{"tool": "analyze", "result": ANALYZE_RESULT, "label": "smoke test", "created_at": "2026-01-01T00:00:00Z"}],
        "exact": [],
        "project": [{"tool": "project", "result": PROJECT_RESULT, "label": "", "created_at": "2026-01-02T00:00:00Z"}],
        "report": [],
    }
    all_runs = [r for runs in runs_by_tool.values() for r in runs]
    totals = aggregate_totals(all_runs)
    by_tool = aggregate_by_tool(runs_by_tool)
    latest = latest_per_tool(runs_by_tool)
    notes = [{"body": "Looks good to launch", "created_at": "2026-01-03T00:00:00Z"}]

    html = render_consolidated_report_html(
        project, "surya@example.com", "2026-01-04T00:00:00Z", totals, by_tool, latest, notes
    )

    assert "Checkout Revamp" in html
    assert "Rewriting checkout flow" in html
    assert "surya@example.com" in html
    assert "smoke test" in html
    assert "Looks good to launch" in html
    assert "No runs saved yet for this tool." in html  # exact + report tools
    assert "<html>" in html and "</html>" in html


def test_render_consolidated_report_html_escapes_note_body():
    project = {"title": "T", "description": "", "tech_stack": {}}
    totals = aggregate_totals([])
    by_tool = aggregate_by_tool({"analyze": [], "exact": [], "project": [], "report": []})
    latest = latest_per_tool({"analyze": [], "exact": [], "project": [], "report": []})
    notes = [{"body": "<script>alert(1)</script>", "created_at": "2026-01-01"}]

    html = render_consolidated_report_html(project, "a@b.com", "now", totals, by_tool, latest, notes)

    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_render_single_run_html_analyze():
    project = {"title": "Support Bot"}
    run = {"tool": "analyze", "label": "baseline", "created_at": "2026-01-01T00:00:00Z", "result": ANALYZE_RESULT}
    html = render_single_run_html(project, "surya@example.com", run)
    assert "Support Bot" in html
    assert "baseline" in html
    assert "surya@example.com" in html
    assert "Standard analyze" in html
    assert "claude-3-5-sonnet" in html
    assert "<html>" in html and "</html>" in html


def test_render_single_run_html_project():
    project = {"title": "Traffic Study"}
    run = {"tool": "project", "label": "", "created_at": "2026-01-02T00:00:00Z", "result": PROJECT_RESULT}
    html = render_single_run_html(project, "a@b.com", run)
    assert "Traffic Study" in html
    assert "(untitled run)" in html
    assert "Traffic projection" in html


def test_render_single_run_html_report():
    project = {"title": "Load Test Project"}
    run = {"tool": "report", "label": "weekly", "created_at": "2026-01-03T00:00:00Z", "result": REPORT_RESULT}
    html = render_single_run_html(project, "a@b.com", run)
    assert "Load Test Project" in html
    assert "Load-test report" in html
