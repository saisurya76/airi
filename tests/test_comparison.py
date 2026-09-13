from airi.comparison import (
    aggregate_workspace_totals,
    project_summary,
    rank_by_cost,
    render_comparison_report_html,
)

ANALYZE_RESULT = {
    "model": "claude-3-5-sonnet", "input_tokens": 500, "estimated_output_tokens": 200,
    "estimated_total_tokens": 700, "context_window": 200000, "context_utilization": 0.0035,
    "estimated_cost": 0.012, "method": "tokenizer", "confidence": "high", "status": "SAFE", "known_model": True,
}
PROJECT_RESULT = {
    "archetypes": [], "total_volume": 1000, "total_tokens": 700000, "total_cost": 12.0,
    "cost_by_model": {}, "tokens_by_model": {}, "any_exceeded": False,
}


def _runs_by_tool(analyze_count=0, project_count=0):
    return {
        "analyze": [{"tool": "analyze", "result": ANALYZE_RESULT, "label": "", "created_at": "2026-01-01"}] * analyze_count,
        "exact": [],
        "project": [{"tool": "project", "result": PROJECT_RESULT, "label": "", "created_at": "2026-01-01"}] * project_count,
        "report": [],
    }


def test_project_summary_sums_across_tools():
    summary = project_summary({"id": 1, "title": "Cheap Project"}, _runs_by_tool(analyze_count=2))
    assert summary["totals"]["total_runs"] == 2
    assert round(summary["totals"]["total_cost"], 3) == round(0.012 * 2, 3)
    assert summary["by_tool"]["analyze"]["count"] == 2
    assert summary["by_tool"]["project"]["count"] == 0


def test_aggregate_workspace_totals_sums_all_projects():
    s1 = project_summary({"id": 1, "title": "A"}, _runs_by_tool(analyze_count=1))
    s2 = project_summary({"id": 2, "title": "B"}, _runs_by_tool(project_count=1))
    totals = aggregate_workspace_totals([s1, s2])
    assert totals["project_count"] == 2
    assert totals["total_runs"] == 2
    assert round(totals["total_cost"], 3) == round(0.012 + 12.0, 3)
    assert totals["total_tokens"] == 700 + 700000


def test_aggregate_workspace_totals_empty():
    totals = aggregate_workspace_totals([])
    assert totals == {"project_count": 0, "total_runs": 0, "total_cost": 0.0, "total_tokens": 0, "status_counts": {"SAFE": 0, "WARNING": 0, "EXCEEDED": 0}}


def test_rank_by_cost_orders_most_expensive_first():
    cheap = project_summary({"id": 1, "title": "Cheap"}, _runs_by_tool(analyze_count=1))
    expensive = project_summary({"id": 2, "title": "Expensive"}, _runs_by_tool(project_count=1))
    ranked = rank_by_cost([cheap, expensive])
    assert [s["project"]["title"] for s in ranked] == ["Expensive", "Cheap"]


def test_render_comparison_report_html_includes_key_facts():
    s1 = project_summary({"id": 1, "title": "Support Bot"}, _runs_by_tool(analyze_count=1))
    s2 = project_summary({"id": 2, "title": "Search Revamp"}, _runs_by_tool(project_count=1))
    ranked = rank_by_cost([s1, s2])
    totals = aggregate_workspace_totals(ranked)

    html = render_comparison_report_html(
        {"title": "Q1 Initiatives"}, "surya@example.com", "2026-01-04T00:00:00Z", totals, ranked
    )

    assert "Q1 Initiatives" in html
    assert "Support Bot" in html
    assert "Search Revamp" in html
    assert "surya@example.com" in html
    assert "<html>" in html and "</html>" in html
    # Search Revamp (the pricier project) should be listed before Support Bot
    assert html.index("Search Revamp") < html.index("Support Bot")


def test_render_comparison_report_html_escapes_project_title():
    s = project_summary({"id": 1, "title": "<script>alert(1)</script>"}, _runs_by_tool())
    totals = aggregate_workspace_totals([s])
    html = render_comparison_report_html({"title": "WS"}, "a@b.com", "now", totals, [s])
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html
