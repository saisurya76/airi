"""
Load-test token usage reporting: consolidate many /analyze results
(collected by any test suite, in any language, during a load-test run)
into one report.

This is deliberately the same pattern as projector.py's "you supply
the numbers, AIRI does the arithmetic" — here the numbers are per-request
results a tester already has (because they integrated analyze()/`/analyze`
into their pipeline per docs/INTEGRATION.md), tagged with a label (which
service/call-site) and an optional phase (e.g. "normal"/"peak") and
timestamp. build_report() is pure computation over that list: no
storage, no run IDs to look up later, no database. Submit everything
you collected in one call, get one report back.
"""

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import List, Optional

MAX_RECORDS = 20_000        # guardrail, not a design limit — see docs/API.md
MAX_FLAGGED_LISTED = 50     # how many WARNING/EXCEEDED requests to list individually in the report

REQUIRED_FIELDS = [
    "label", "model", "input_tokens", "estimated_output_tokens",
    "estimated_total_tokens", "context_window", "context_utilization",
    "estimated_cost", "method", "confidence", "status", "known_model",
]


@dataclass
class RunReport:
    run_name: str
    generated_at: str
    total_requests: int
    total_input_tokens: int
    total_output_tokens: int
    total_tokens: int
    total_cost: float
    avg_tokens_per_request: float
    avg_cost_per_request: float
    peak_request: Optional[dict]
    status_counts: dict
    by_model: dict
    by_label: dict
    by_phase: dict
    flagged_requests: list
    flagged_truncated: bool
    duration_seconds: Optional[float]
    requests_per_second: Optional[float]

    def to_dict(self) -> dict:
        return asdict(self)


def _parse_timestamp(ts: Optional[str]):
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _validate_record(record: dict, index: int):
    missing = [f for f in REQUIRED_FIELDS if f not in record]
    if missing:
        raise ValueError(f"Record {index} (label={record.get('label', '?')!r}) is missing: {', '.join(missing)}")
    if record["status"] not in ("SAFE", "WARNING", "EXCEEDED"):
        raise ValueError(f"Record {index}: unrecognized status {record['status']!r}")


def build_report(run_name: str, records: List[dict]) -> RunReport:
    """
    Aggregate a load-test run's per-request analyze() results into one
    consolidated report.

    Args:
        run_name: a label for this run, e.g. "Checkout service — peak load".
        records: one entry per AI request made during the run. Each is
            the same shape /analyze returns (model, input_tokens,
            estimated_output_tokens, estimated_total_tokens,
            context_window, context_utilization, estimated_cost, method,
            confidence, status, known_model) plus:
              - label (required): which service/call-site this request was
              - phase (optional): a freeform tag, e.g. "normal" or "peak"
              - timestamp (optional): ISO 8601, when the request happened

    Returns:
        RunReport
    """
    if not run_name or not run_name.strip():
        raise ValueError("Provide a run_name.")
    if not records:
        raise ValueError("Provide at least one record.")
    if len(records) > MAX_RECORDS:
        raise ValueError(f"Provide at most {MAX_RECORDS} records per report.")

    for i, r in enumerate(records):
        _validate_record(r, i)

    total_requests = len(records)
    total_input_tokens = sum(r["input_tokens"] for r in records)
    total_output_tokens = sum(r["estimated_output_tokens"] for r in records)
    total_tokens = sum(r["estimated_total_tokens"] for r in records)
    total_cost = round(sum(r["estimated_cost"] for r in records), 6)

    status_counts = {"SAFE": 0, "WARNING": 0, "EXCEEDED": 0}
    by_model: dict = {}
    by_label: dict = {}
    by_phase: dict = {}
    flagged_requests = []

    peak_request = None
    for r in records:
        status_counts[r["status"]] += 1

        m = by_model.setdefault(r["model"], {"count": 0, "tokens": 0, "cost": 0.0})
        m["count"] += 1
        m["tokens"] += r["estimated_total_tokens"]
        m["cost"] = round(m["cost"] + r["estimated_cost"], 6)

        label = r["label"]
        l = by_label.setdefault(label, {"count": 0, "tokens": 0, "cost": 0.0, "SAFE": 0, "WARNING": 0, "EXCEEDED": 0})
        l["count"] += 1
        l["tokens"] += r["estimated_total_tokens"]
        l["cost"] = round(l["cost"] + r["estimated_cost"], 6)
        l[r["status"]] += 1

        phase = r.get("phase") or "unspecified"
        p = by_phase.setdefault(phase, {"count": 0, "tokens": 0, "cost": 0.0})
        p["count"] += 1
        p["tokens"] += r["estimated_total_tokens"]
        p["cost"] = round(p["cost"] + r["estimated_cost"], 6)

        if r["status"] != "SAFE":
            flagged_requests.append(r)

        if peak_request is None or r["estimated_total_tokens"] > peak_request["estimated_total_tokens"]:
            peak_request = r

    flagged_truncated = len(flagged_requests) > MAX_FLAGGED_LISTED
    # Worst offenders first: EXCEEDED before WARNING, then by tokens descending.
    flagged_requests.sort(key=lambda r: (r["status"] != "EXCEEDED", -r["estimated_total_tokens"]))
    flagged_requests = flagged_requests[:MAX_FLAGGED_LISTED]

    timestamps = [_parse_timestamp(r.get("timestamp")) for r in records]
    timestamps = [t for t in timestamps if t is not None]
    duration_seconds = None
    requests_per_second = None
    if len(timestamps) >= 2:
        span = (max(timestamps) - min(timestamps)).total_seconds()
        if span > 0:
            duration_seconds = round(span, 3)
            requests_per_second = round(total_requests / span, 3)

    return RunReport(
        run_name=run_name.strip(),
        generated_at=datetime.now(timezone.utc).isoformat(),
        total_requests=total_requests,
        total_input_tokens=total_input_tokens,
        total_output_tokens=total_output_tokens,
        total_tokens=total_tokens,
        total_cost=total_cost,
        avg_tokens_per_request=round(total_tokens / total_requests, 2),
        avg_cost_per_request=round(total_cost / total_requests, 6),
        peak_request=peak_request,
        status_counts=status_counts,
        by_model=by_model,
        by_label=by_label,
        by_phase=by_phase,
        flagged_requests=flagged_requests,
        flagged_truncated=flagged_truncated,
        duration_seconds=duration_seconds,
        requests_per_second=requests_per_second,
    )
