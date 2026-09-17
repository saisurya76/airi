import httpx
import pytest

from airi import vendor_pricing as vp


def test_registry_ids_are_unique_and_categorized():
    ids = [t.id for t in (*vp.LICENSE_TOOLS, *vp.SDLC_TOOLS)]
    assert len(ids) == len(set(ids))
    assert all(t.category == "license" for t in vp.LICENSE_TOOLS)
    assert all(t.category == "sdlc" for t in vp.SDLC_TOOLS)
    assert len(vp.LICENSE_TOOLS) >= 10
    assert len(vp.SDLC_TOOLS) >= 10


def test_get_tool_known_and_unknown():
    assert vp.get_tool("github-copilot") is not None
    assert vp.get_tool("does-not-exist") is None


@pytest.mark.parametrize("text,expected", [
    ("Business plan $19 per user / month", 19.0),
    ("Enterprise $39/user/month billed annually", 39.0),
    ("Starting at $30.00 per user per month", 30.0),
    ("Copilot Individual $10/month — Copilot Business $19 per user/mo", 19.0),
    ("Contact sales for pricing", None),
    ("Save 20% — was $45, save $450 total per year", None),
])
def test_extract_price(text, expected):
    assert vp._extract_price(text) == expected


def test_strip_html_removes_script_and_style():
    html = "<html><head><style>.a{color:red}</style></head><body><script>x=1</script><p>Hi $19/user/month</p></body></html>"
    text = vp._strip_html(html)
    assert "color:red" not in text
    assert "x=1" not in text
    assert "$19/user/month" in text


def test_fetch_live_price_unknown_tool_raises():
    with pytest.raises(vp.PricingFetchError):
        vp.fetch_live_price("not-a-real-tool")


def test_fetch_live_price_success(monkeypatch):
    class FakeResp:
        status_code = 200
        text = "<p>Business $19 per user / month</p>"

    def fake_get(url, headers=None, timeout=None, follow_redirects=None):
        return FakeResp()

    monkeypatch.setattr(vp.httpx, "get", fake_get)
    result = vp.fetch_live_price("github-copilot", use_cache=False)
    assert result["price_per_seat"] == 19.0
    assert result["currency"] == "USD"
    assert result["tool_id"] == "github-copilot"
    assert result["cached"] is False


def test_fetch_live_price_uses_cache(monkeypatch):
    calls = {"n": 0}

    class FakeResp:
        status_code = 200
        text = "<p>$25 per user / month</p>"

    def fake_get(url, headers=None, timeout=None, follow_redirects=None):
        calls["n"] += 1
        return FakeResp()

    monkeypatch.setattr(vp.httpx, "get", fake_get)
    first = vp.fetch_live_price("cursor", use_cache=True)
    second = vp.fetch_live_price("cursor", use_cache=True)
    assert calls["n"] == 1
    assert first["price_per_seat"] == second["price_per_seat"] == 25.0
    assert second["cached"] is True


def test_fetch_live_price_no_match_raises(monkeypatch):
    class FakeResp:
        status_code = 200
        text = "<p>Contact us for enterprise pricing.</p>"

    monkeypatch.setattr(vp.httpx, "get", lambda *a, **k: FakeResp())
    with pytest.raises(vp.PricingFetchError):
        vp.fetch_live_price("tabnine", use_cache=False)


def test_fetch_live_price_non_200_raises(monkeypatch):
    class FakeResp:
        status_code = 403
        text = ""

    monkeypatch.setattr(vp.httpx, "get", lambda *a, **k: FakeResp())
    with pytest.raises(vp.PricingFetchError):
        vp.fetch_live_price("windsurf", use_cache=False)


def test_fetch_live_price_timeout_raises(monkeypatch):
    def raise_timeout(*a, **k):
        raise httpx.TimeoutException("slow")

    monkeypatch.setattr(vp.httpx, "get", raise_timeout)
    with pytest.raises(vp.PricingFetchError):
        vp.fetch_live_price("jetbrains-ai", use_cache=False)


def test_fetch_live_price_network_error_raises(monkeypatch):
    def raise_error(*a, **k):
        raise httpx.ConnectError("nope")

    monkeypatch.setattr(vp.httpx, "get", raise_error)
    with pytest.raises(vp.PricingFetchError):
        vp.fetch_live_price("notion-ai", use_cache=False)
