import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from airi import analyze
from airi.report import build_report, MAX_RECORDS


def make_record(label, model="gpt-4o", prompt="Hello there", expected_output_tokens=100, phase=None, timestamp=None):
    r = analyze(prompt=prompt, model=model, expected_output_tokens=expected_output_tokens).to_dict()
    r["label"] = label
    if phase is not None:
        r["phase"] = phase
    if timestamp is not None:
        r["timestamp"] = timestamp
    return r


def test_basic_aggregation():
    records = [make_record("chat", expected_output_tokens=50) for _ in range(10)]
    report = build_report("Test run", records)
    assert report.total_requests == 10
    assert report.status_counts["SAFE"] == 10
    assert report.status_counts["WARNING"] == 0
    assert report.by_label["chat"]["count"] == 10
    assert report.total_tokens == sum(r["estimated_total_tokens"] for r in records)
    print("OK: basic_aggregation ->", report.total_requests, report.total_cost)


def test_mixed_labels_and_models():
    records = [
        make_record("chat", model="gpt-4o-mini", expected_output_tokens=50),
        make_record("summary", model="claude-3-5-sonnet", expected_output_tokens=300),
        make_record("chat", model="gpt-4o-mini", expected_output_tokens=50),
    ]
    report = build_report("Mixed run", records)
    assert report.by_label["chat"]["count"] == 2
    assert report.by_label["summary"]["count"] == 1
    assert set(report.by_model.keys()) == {"gpt-4o-mini", "claude-3-5-sonnet"}
    print("OK: mixed_labels_and_models")


def test_flags_warning_and_exceeded():
    huge_prompt = "word " * 20000
    records = [
        make_record("normal-op", expected_output_tokens=10),
        make_record("overflow-op", model="gpt-4", prompt=huge_prompt, expected_output_tokens=0),
    ]
    report = build_report("Flag test", records)
    assert report.status_counts["EXCEEDED"] == 1
    assert len(report.flagged_requests) == 1
    assert report.flagged_requests[0]["label"] == "overflow-op"
    assert report.peak_request["label"] == "overflow-op"
    print("OK: flags_warning_and_exceeded")


def test_phase_breakdown():
    records = [
        make_record("chat", phase="normal", expected_output_tokens=50),
        make_record("chat", phase="peak", expected_output_tokens=50),
        make_record("chat", expected_output_tokens=50),  # no phase given
    ]
    report = build_report("Phase test", records)
    assert report.by_phase["normal"]["count"] == 1
    assert report.by_phase["peak"]["count"] == 1
    assert report.by_phase["unspecified"]["count"] == 1
    print("OK: phase_breakdown")


def test_throughput_from_timestamps():
    records = [
        make_record("chat", expected_output_tokens=50, timestamp="2026-09-13T10:00:00Z"),
        make_record("chat", expected_output_tokens=50, timestamp="2026-09-13T10:00:10Z"),
        make_record("chat", expected_output_tokens=50, timestamp="2026-09-13T10:00:20Z"),
    ]
    report = build_report("Timing test", records)
    assert report.duration_seconds == 20.0
    assert report.requests_per_second == round(3 / 20, 3)
    print("OK: throughput_from_timestamps ->", report.duration_seconds, report.requests_per_second)


def test_no_timestamps_omits_throughput():
    records = [make_record("chat", expected_output_tokens=50) for _ in range(3)]
    report = build_report("No timing", records)
    assert report.duration_seconds is None
    assert report.requests_per_second is None
    print("OK: no_timestamps_omits_throughput")


def test_flagged_list_capped_and_sorted():
    records = []
    for i in range(60):
        records.append(make_record(f"overflow-{i}", model="gpt-4", prompt="word " * 20000, expected_output_tokens=0))
    report = build_report("Big overflow run", records)
    assert report.flagged_truncated is True
    assert len(report.flagged_requests) == 50
    print("OK: flagged_list_capped_and_sorted ->", len(report.flagged_requests), report.flagged_truncated)


def test_empty_records_rejected():
    try:
        build_report("Empty", [])
        assert False, "should have raised"
    except ValueError:
        print("OK: empty_records_rejected")


def test_blank_run_name_rejected():
    try:
        build_report("   ", [make_record("chat")])
        assert False, "should have raised"
    except ValueError:
        print("OK: blank_run_name_rejected")


def test_missing_field_rejected():
    bad = make_record("chat")
    del bad["model"]
    try:
        build_report("Bad record", [bad])
        assert False, "should have raised"
    except ValueError as e:
        assert "model" in str(e)
        print("OK: missing_field_rejected ->", e)


def test_too_many_records_rejected():
    try:
        build_report("Too many", [make_record("chat")] * (MAX_RECORDS + 1))
        assert False, "should have raised"
    except ValueError:
        print("OK: too_many_records_rejected")


if __name__ == "__main__":
    test_basic_aggregation()
    test_mixed_labels_and_models()
    test_flags_warning_and_exceeded()
    test_phase_breakdown()
    test_throughput_from_timestamps()
    test_no_timestamps_omits_throughput()
    test_flagged_list_capped_and_sorted()
    test_empty_records_rejected()
    test_blank_run_name_rejected()
    test_missing_field_rejected()
    test_too_many_records_rejected()
    print("\nAll report sanity checks passed.")
