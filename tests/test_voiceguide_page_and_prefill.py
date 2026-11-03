from wattnot.meta import voiceguide as vg

SCREEN = [
    {"title": "Energy per correct answer", "text": "-26.7% with WattNot, MEASURED on the H100"},
    {"title": "Pass rate", "text": "899/977 in both arms MEASURED"},
    {"title": "Meta spend", "text": "$3.47 ESTIMATE of $120"},
]


def test_explain_dashboard_is_a_spoken_summary_of_the_screen():
    out = vg.process_voice_query("Explain the dashboard.", mock=True, live=False, spark_live=False, screen=SCREEN)
    assert out["intent"] == "explain" and out["scope"] == "page"
    assert "Found element" not in out["text"]
    for bit in ("Energy per correct answer", "-26.7%", "Pass rate", "899"):
        assert bit in out["speak"]
    assert len(out["speak"].split()) < 90  # short enough to say aloud
    assert "route" not in out and "run" not in {k for k in out if k.startswith("sel")}


def test_explain_dashboard_skips_the_spark_call(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("Spark should not be called for an explicit request")
    monkeypatch.setattr(vg, "spark_classify_intent", boom)
    monkeypatch.setattr("wattnot.meta.client.load_key", lambda: "k")
    out = vg.process_voice_query("Explain the dashboard", mock=False, live=False, spark_live=True, screen=SCREEN)
    assert out["intent"] == "explain"


def test_configure_parses_every_setting():
    p = vg.parse_run_setup("Configure a run on Baseten at three fifty watts, thinking budget off, "
                           "prefix caching on, p99 limit 10 percent, 2 rounds, spend $5, langchain")
    assert p["cap_w"] == 350 and p["thinking_budget"] is False and p["prefix_cache"] is True
    assert p["provider"] == "baseten" and p["p99_pct"] == 10 and p["rounds"] == 2
    assert p["max_spend_usd"] == 5 and p["framework"] == "langchain"


def test_configure_keeps_thinking_budget_and_never_starts():
    out = vg.process_voice_query("Configure a run at 375 watts with thinking budget on.",
                                 mock=True, live=False, spark_live=False)
    assert out["intent"] == "configure" and out["action"] == "prefill"
    assert out["prefill"]["cap_w"] == 375 and out["prefill"]["thinking_budget"] is True
    assert out["route"].endswith("v2.html#pCfg") and out["starts_run"] is False
    assert "375 W" in out["text"] and "thinking budget on" in out["text"]


def test_configure_wins_over_tune_command():
    assert vg.explicit_intent("configure a run at 350 W with FP8") == "configure"
