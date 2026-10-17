from __future__ import annotations

from foxbot import factual_search


def test_factual_intent_extracts_topic_and_ignores_short_chat() -> None:
    assert factual_search.factual_intent("سیاره مریخ چیست؟") == (True, "سیاره مریخ چیست")
    assert factual_search.factual_intent("لطفا درباره برنامه نویسی توضیح بده") == (
        True,
        "برنامه نویسی",
    )
    assert factual_search.factual_intent("سلام") == (False, "")
    assert factual_search.factual_intent("خوبی") == (False, "")


def test_search_parser_extracts_visible_text_and_safe_links() -> None:
    page = '''
    <html><body>
      <a class="result__a" href="https://example.org/mars"><b>سیاره مریخ</b></a>
      <a class="result__snippet">مریخ چهارمین سیاره از خورشید است.</a>
      <a class="result__a" href="javascript:alert(1)">جعلی</a>
      <a class="result__snippet">این لینک نباید نمایش داده شود.</a>
    </body></html>
    '''
    results = factual_search._parse_search_results(page)
    assert results == [
        ("سیاره مریخ", "مریخ چهارمین سیاره از خورشید است.", "https://example.org/mars")
    ]
    external_query = "https://example.org/l/?uddg=https%3A%2F%2Ftarget.test%2F"
    assert factual_search._safe_result_url(external_query) == external_query


def test_search_factual_ranks_and_returns_source_link(monkeypatch) -> None:
    monkeypatch.setattr(
        factual_search,
        "_search_web",
        lambda _query: [
            ("صفحه نامرتبط", "درباره موضوعی دیگر", "https://example.org/no"),
            ("سیاره مریخ", "مریخ چهارمین سیاره از خورشید است.", "https://example.org/mars"),
        ],
    )
    monkeypatch.setattr(
        factual_search,
        "_wiki_fallback",
        lambda _query: (_ for _ in ()).throw(AssertionError("fallback should not run")),
    )
    answer = factual_search.search_factual("سیاره مریخ")
    assert "مریخ چهارمین سیاره" in answer
    assert "https://example.org/mars" in answer
    assert "صفحه نامرتبط" not in answer
    assert "خلاصه بر پایهٔ منابع عمومی" in answer


def test_search_factual_uses_wikipedia_fallback_when_web_has_no_relevant_result(monkeypatch) -> None:
    monkeypatch.setattr(factual_search, "_search_web", lambda _query: [])
    monkeypatch.setattr(
        factual_search,
        "_wiki_fallback",
        lambda _query: (
            "مریخ",
            "مریخ سیاره‌ای در منظومه شمسی است.",
            "https://fa.wikipedia.org/wiki/مریخ",
        ),
    )
    answer = factual_search.search_factual("سیاره مریخ")
    assert "مریخ سیاره‌ای" in answer
    assert "fa.wikipedia.org" in answer


def test_factual_search_rejects_overlong_queries() -> None:
    try:
        factual_search.search_factual("x" * 351)
    except factual_search.FactualSearchError as error:
        assert error.reason == "query_too_long"
    else:
        raise AssertionError("overlong query should be rejected")
