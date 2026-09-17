"""
Registry + best-effort live price lookup for the two newer "AI request"
types AIRI covers beyond API traffic: per-seat license/utility tools
(Microsoft 365 Copilot, Cowork, ChatGPT Enterprise...) and SDLC/dev
tools (GitHub Copilot, Microsoft Foundry, Cursor...).

Deliberately NOT a static price table. AIRI's model registry (see
registry.py) ships fixed example prices because those rarely change
mid-quarter and are meant as starting values. Seat-license pricing is
different: it changes often, varies by tier/region/negotiated discount,
and presenting a stale number as current would be actively misleading
in a management cost-planning tool. So this module only ever does two
things with a price: try to fetch it live from the vendor's own public
pricing page right now, or say plainly that it couldn't — the caller
(the frontend wizard) always lets the person type in the number they
actually pay instead.

fetch_live_price() is inherently best-effort: many vendor pricing pages
are JavaScript-rendered and won't yield a price to a plain HTTP GET, or
show "Contact sales" with no public number at all. A miss is not a bug
to chase — it's the expected outcome for a chunk of these vendors, and
exactly why manual entry always stays available next to it.
"""

import re
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import httpx

USER_AGENT = "Mozilla/5.0 (compatible; AIRI-PricingLookup/1.0; +https://saisurya76.github.io/airi/)"
FETCH_TIMEOUT_SECONDS = 8.0
CACHE_TTL_SECONDS = 6 * 60 * 60  # 6h — cuts repeat load on vendor sites across a demo session


@dataclass(frozen=True)
class VendorTool:
    id: str
    name: str
    vendor: str
    category: str  # "license" | "sdlc"
    billing_unit: str  # e.g. "per user/month", "usage-based (per token)"
    pricing_url: str
    note: str = ""


LICENSE_TOOLS: List[VendorTool] = [
    VendorTool("m365-copilot", "Microsoft 365 Copilot", "Microsoft", "license", "per user/month",
               "https://www.microsoft.com/en-us/microsoft-365/copilot/pricing"),
    VendorTool("google-gemini-workspace", "Gemini for Google Workspace", "Google", "license", "per user/month",
               "https://workspace.google.com/solutions/ai/pricing/"),
    VendorTool("cowork", "Claude (Cowork / Team / Enterprise)", "Anthropic", "license", "per user/month",
               "https://claude.com/pricing"),
    VendorTool("chatgpt-enterprise", "ChatGPT Enterprise / Team", "OpenAI", "license", "per user/month",
               "https://openai.com/chatgpt/pricing/"),
    VendorTool("salesforce-agentforce", "Salesforce Agentforce / Einstein", "Salesforce", "license", "per user/month or per conversation",
               "https://www.salesforce.com/agentforce/pricing/"),
    VendorTool("adobe-firefly", "Adobe Firefly", "Adobe", "license", "per user/month",
               "https://www.adobe.com/products/firefly/plans.html"),
    VendorTool("notion-ai", "Notion AI", "Notion", "license", "per user/month (add-on)",
               "https://www.notion.com/pricing"),
    VendorTool("zoom-ai-companion", "Zoom AI Companion", "Zoom", "license", "per user/month (bundled or add-on)",
               "https://www.zoom.com/en/pricing/"),
    VendorTool("grammarly-business", "Grammarly Business", "Grammarly", "license", "per user/month",
               "https://www.grammarly.com/business/plans"),
    VendorTool("slack-ai", "Slack AI", "Slack", "license", "per user/month (add-on)",
               "https://slack.com/pricing"),
    VendorTool("box-ai", "Box AI", "Box", "license", "per user/month (add-on)",
               "https://www.box.com/pricing"),
    VendorTool("servicenow-now-assist", "ServiceNow Now Assist", "ServiceNow", "license", "per user/month",
               "https://www.servicenow.com/now-assist.html"),
    VendorTool("atlassian-rovo", "Atlassian Rovo", "Atlassian", "license", "per user/month (add-on)",
               "https://www.atlassian.com/software/rovo/pricing"),
]

SDLC_TOOLS: List[VendorTool] = [
    VendorTool("github-copilot", "GitHub Copilot", "GitHub (Microsoft)", "sdlc", "per user/month",
               "https://github.com/features/copilot/plans"),
    VendorTool("microsoft-foundry", "Microsoft Foundry (Azure AI Foundry)", "Microsoft", "sdlc", "usage-based (per token, not per seat)",
               "https://azure.microsoft.com/en-us/pricing/details/ai-foundry-models/aoai/",
               note="Usage-based, not a per-seat license — enter an expected monthly spend instead of a per-seat price."),
    VendorTool("cursor", "Cursor", "Anysphere", "sdlc", "per user/month",
               "https://cursor.com/pricing"),
    VendorTool("amazon-q-developer", "Amazon Q Developer", "AWS", "sdlc", "per user/month",
               "https://aws.amazon.com/q/developer/pricing/"),
    VendorTool("tabnine", "Tabnine", "Tabnine", "sdlc", "per user/month",
               "https://www.tabnine.com/pricing/"),
    VendorTool("jetbrains-ai", "JetBrains AI Assistant", "JetBrains", "sdlc", "per user/month",
               "https://www.jetbrains.com/ai-ides/buy/"),
    VendorTool("gemini-code-assist", "Gemini Code Assist", "Google", "sdlc", "per user/month",
               "https://codeassist.google/products/business"),
    VendorTool("windsurf", "Windsurf", "Windsurf (Cognition)", "sdlc", "per user/month",
               "https://windsurf.com/pricing"),
    VendorTool("replit-agent", "Replit Agent / Core", "Replit", "sdlc", "per user/month",
               "https://replit.com/pricing"),
    VendorTool("sourcegraph-cody", "Sourcegraph Cody", "Sourcegraph", "sdlc", "per user/month",
               "https://sourcegraph.com/pricing"),
    VendorTool("gitlab-duo", "GitLab Duo", "GitLab", "sdlc", "per user/month (add-on)",
               "https://about.gitlab.com/pricing/"),
]

_ALL_TOOLS: Dict[str, VendorTool] = {t.id: t for t in (*LICENSE_TOOLS, *SDLC_TOOLS)}


def get_tool(tool_id: str) -> Optional[VendorTool]:
    return _ALL_TOOLS.get(tool_id)


def list_license_tools() -> List[VendorTool]:
    return list(LICENSE_TOOLS)


def list_sdlc_tools() -> List[VendorTool]:
    return list(SDLC_TOOLS)


# --- Best-effort live fetch -------------------------------------------------

class PricingFetchError(Exception):
    """Carries a short, user-facing reason — never a raw exception message,
    since httpx/network errors aren't meaningful to someone filling in a
    wizard step."""


_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_STYLE_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_WHITESPACE_RE = re.compile(r"\s+")

# Looks for "$30/user/month", "$30 per user per month", "$19 / seat / mo",
# "USD 19.00 per user", etc. Deliberately narrow (USD only, seat/user
# billing only) — broadening it invites false-positive matches on
# unrelated dollar amounts elsewhere on the page.
_PRICE_RE = re.compile(
    r"\$\s?(\d{1,4}(?:\.\d{1,2})?)\s*"
    r"(?:USD)?\s*"
    r"(?:/|per\s+)\s*"
    r"(?:user|seat|member)"
    r"(?:\s*(?:/|per\s+)\s*(?:month|mo\.?|year|yr\.?))?",
    re.IGNORECASE,
)

_cache: Dict[str, dict] = {}


def _strip_html(html: str) -> str:
    text = _SCRIPT_STYLE_RE.sub(" ", html)
    text = _TAG_RE.sub(" ", text)
    return _WHITESPACE_RE.sub(" ", text)


def _extract_price(text: str) -> Optional[float]:
    for m in _PRICE_RE.finditer(text):
        try:
            value = float(m.group(1))
        except ValueError:
            continue
        # Sanity band: seat pricing for these tools runs roughly $2-$500/mo.
        # Anything outside that is more likely a false-positive match
        # (a stat, a discount amount, an unrelated price) than a seat price.
        if 2 <= value <= 500:
            return value
    return None


def fetch_live_price(tool_id: str, *, use_cache: bool = True) -> dict:
    """Best-effort: fetches the vendor's own public pricing page right now
    and tries to find a per-seat USD price on it.

    Returns {tool_id, price_per_seat, currency, source_url, fetched_at,
    cached} on success.

    Raises PricingFetchError with a short, displayable reason on any
    failure — unknown tool, network/timeout error, non-200 response, or
    (most commonly) no confidently-matching price found in the page text.
    None of these are bugs to fix; they're the expected shape of "this
    vendor's page doesn't hand us a number" and the frontend's job is
    just to fall back to manual entry cleanly.
    """
    tool = get_tool(tool_id)
    if tool is None:
        raise PricingFetchError("Unknown tool.")

    now = time.time()
    if use_cache:
        cached = _cache.get(tool_id)
        if cached and (now - cached["_cached_at"]) < CACHE_TTL_SECONDS:
            return {**cached, "cached": True}

    try:
        resp = httpx.get(
            tool.pricing_url,
            headers={"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"},
            timeout=FETCH_TIMEOUT_SECONDS,
            follow_redirects=True,
        )
    except httpx.TimeoutException:
        raise PricingFetchError(f"{tool.vendor}'s pricing page took too long to respond.")
    except httpx.HTTPError:
        raise PricingFetchError(f"Couldn't reach {tool.vendor}'s pricing page right now.")

    if resp.status_code != 200:
        raise PricingFetchError(f"{tool.vendor}'s pricing page returned an error (HTTP {resp.status_code}).")

    price = _extract_price(_strip_html(resp.text))
    if price is None:
        raise PricingFetchError(
            f"Couldn't find a clear per-seat price on {tool.vendor}'s pricing page automatically "
            f"— it may be region-priced, quote-only, or rendered by JavaScript. Enter your known price instead."
        )

    result = {
        "tool_id": tool_id,
        "price_per_seat": price,
        "currency": "USD",
        "source_url": tool.pricing_url,
        "fetched_at": now,
        "cached": False,
    }
    _cache[tool_id] = {**result, "_cached_at": now}
    return result
