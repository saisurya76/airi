"""
AIRI API — one focused endpoint on top of the core library, plus the
try-it-out page as a static file. Run it with:

    uvicorn api:app --reload

Then open http://127.0.0.1:8000/
"""

import io
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator

from airi import Archetype, analyze, build_report, list_supported_models, project
from airi.projector import MAX_ARCHETYPES
from airi.registry import MODEL_REGISTRY
from airi.report import MAX_RECORDS
from airi.report_render import render_report_html

app = FastAPI(
    title="AIRI — AI Request Intelligence",
    description="Estimate tokens, context usage and cost for an AI request before you send it.",
    version="0.2.0",
)

# Wide open for the MVP: this is a stateless, read-only analysis endpoint
# with no auth and no user data, meant to be called from any frontend.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

MAX_PROMPT_CHARS = 200_000  # guardrail so a runaway request can't hang the process


class ChatMessage(BaseModel):
    role: str
    content: str


def _check_input_shape(prompt: Optional[str], messages: Optional[List[ChatMessage]], label: str = "Request"):
    """Shared validation for anything shaped like a single analyze() call —
    used by both /analyze and each archetype inside /project."""
    if not prompt and not messages:
        raise ValueError(f"{label}: provide either `prompt` or `messages`.")
    if prompt and messages:
        raise ValueError(f"{label}: provide only one of `prompt` or `messages`, not both.")
    text_len = len(prompt) if prompt else sum(len(m.content) for m in messages)
    if text_len > MAX_PROMPT_CHARS:
        raise ValueError(f"{label}: input exceeds the {MAX_PROMPT_CHARS}-character request limit.")


class AnalyzeRequest(BaseModel):
    prompt: Optional[str] = None
    messages: Optional[List[ChatMessage]] = None
    model: str = Field(default="gpt-4o")
    expected_output_tokens: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _validate(self):
        _check_input_shape(self.prompt, self.messages)
        return self


class ArchetypeRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    volume: int = Field(ge=0)
    prompt: Optional[str] = None
    messages: Optional[List[ChatMessage]] = None
    model: str = Field(default="gpt-4o")
    expected_output_tokens: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _validate(self):
        _check_input_shape(self.prompt, self.messages, label=f"Archetype '{self.name}'")
        return self


class ProjectRequest(BaseModel):
    archetypes: List[ArchetypeRequest] = Field(min_length=1, max_length=MAX_ARCHETYPES)


class ReportRequest(BaseModel):
    """
    A load-test report is built from records your own test harness
    already has — one per AI request, in the exact shape `/analyze`
    returns, plus a `label` (required) and optional `phase`/`timestamp`.
    Collect these as you run your suite (in any language), then submit
    everything you collected in one call at the end of the run. See
    docs/INTEGRATION.md#load-test-token-usage-reporting.
    """

    run_name: str = Field(min_length=1, max_length=200)
    records: List[Dict[str, Any]] = Field(min_length=1, max_length=MAX_RECORDS)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/models")
def models():
    """Model list + metadata for the frontend's dropdown."""
    return [
        {
            "id": model_id,
            "context_window": spec.context_window,
            "input_price_per_1m": spec.input_price_per_1m,
            "output_price_per_1m": spec.output_price_per_1m,
        }
        for model_id, spec in sorted(MODEL_REGISTRY.items())
    ]


@app.post("/analyze")
def analyze_request(body: AnalyzeRequest):
    try:
        result = analyze(
            prompt=body.prompt,
            messages=[m.model_dump() for m in body.messages] if body.messages else None,
            model=body.model,
            expected_output_tokens=body.expected_output_tokens,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return result.to_dict()


@app.post("/project")
def project_request(body: ProjectRequest):
    """
    Volume projection: given several distinct AI call-sites in your app,
    each with a representative sample request, a target model, and a
    volume you supply (from your own analytics or projections), returns
    per-archetype and grand-total tokens/cost. See airi/projector.py —
    AIRI doesn't guess volume, it only does the multiplication once you
    provide it.
    """
    try:
        archetypes = [
            Archetype(
                name=a.name,
                volume=a.volume,
                prompt=a.prompt,
                messages=[m.model_dump() for m in a.messages] if a.messages else None,
                model=a.model,
                expected_output_tokens=a.expected_output_tokens,
            )
            for a in body.archetypes
        ]
        result = project(archetypes)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return result.to_dict()


def _build_report_or_400(body: ReportRequest):
    try:
        return build_report(body.run_name, body.records)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/report")
def report_json(body: ReportRequest):
    """
    Consolidate a load-test run's per-request /analyze results into one
    report: totals, cost, a SAFE/WARNING/EXCEEDED breakdown, per-label
    and per-model and per-phase rollups, the peak single request, and
    the worst-offending flagged requests. Fully stateless — nothing is
    stored; submit every record you collected in one call, get one
    report back. See docs/INTEGRATION.md for the record shape and a
    worked example from any test suite.
    """
    return _build_report_or_400(body).to_dict()


@app.post("/report/html", response_class=HTMLResponse)
def report_html(body: ReportRequest):
    """Same report as POST /report, rendered as a single self-contained
    HTML page — for embedding in a viewer (e.g. an iframe) or opening
    directly in a browser."""
    report = _build_report_or_400(body)
    return HTMLResponse(content=render_report_html(report))


@app.post("/report/pdf")
def report_pdf(body: ReportRequest):
    """Same report, rendered to a downloadable PDF — the same HTML
    template as POST /report/html, converted via xhtml2pdf so the two
    always agree."""
    report = _build_report_or_400(body)
    html = render_report_html(report)

    from xhtml2pdf import pisa  # imported here: only /report/pdf needs it

    buffer = io.BytesIO()
    result = pisa.CreatePDF(src=html, dest=buffer)
    if result.err:
        raise HTTPException(status_code=500, detail="Could not render PDF for this report.")

    pdf_bytes = buffer.getvalue()
    filename = "".join(c if c.isalnum() or c in "-_ " else "_" for c in report.run_name).strip() or "airi-report"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}.pdf"'},
    )


# Serve the try-it-out page at "/". Mounted last so it doesn't shadow the
# API routes above.
app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")
