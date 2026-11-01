"""Ask about a card: Spark text explanation, MOCK labelled, focus must come from the card."""
from wattnot.meta import askcard


def test_mock_is_labelled_and_text_only(monkeypatch):
    monkeypatch.setenv("WATTNOT_META_MOCK", "1")
    out = askcard.explain_card("meta", "Meta spend", "Meta spend $26.82 of $120 cap MEASURED")
    assert out["label"] == "MOCK" and out["explanation"].startswith("[MOCK]")
    assert "audio" not in out and "speech" not in out


def test_empty_card_is_an_error():
    assert askcard.explain_card("x", "t", "  ")["code"] == "empty_card"


def test_focus_not_in_card_is_dropped():
    class C:
        def __call__(self, body, purpose=""):
            assert purpose == "askcard:c1" and body["max_tokens"] <= 700
            return {"choices": [{"message": {"content": '{"explanation":"e","focus":"invented"}'}}]}
    out = askcard.explain_card("c1", "T", "real text here", mock=False, client=C())
    assert out["label"] == "LIVE" and out["focus"] == ""


def test_focus_kept_and_text_trimmed():
    seen = {}
    class C:
        def __call__(self, body, purpose=""):
            seen["u"] = body["messages"][1]["content"]
            return {"choices": [{"message": {"content": '{"explanation":"e","focus":"120 cap"}'}}]}
    out = askcard.explain_card("c", "T", "a  " * 5000 + "120 cap", mock=False, client=C())
    assert len(seen["u"]) < askcard.MAX_TEXT + 50
    assert out["focus"] == ""   # trimmed away, so it can't be highlighted
    out = askcard.explain_card("c", "T", "spent 120 cap", mock=False, client=C())
    assert out["focus"] == "120 cap"
