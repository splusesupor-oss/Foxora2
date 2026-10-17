from foxbot.site_policy import (
    classify_link,
    find_first_link,
    find_untrusted_link,
)


def test_domain_policy_uses_exact_and_subdomain_boundaries() -> None:
    assert classify_link("https://pornhub.com/video").classification == "blocked"
    assert classify_link("https://cdn.pornhub.com/").classification == "blocked"
    assert classify_link("https://docs.github.com/").classification == "trusted"
    assert classify_link("https://search.dev.to/").classification == "search"
    assert classify_link("https://github.com.evil.example/").classification == "unknown"


def test_first_link_extraction_handles_common_forms_and_punctuation() -> None:
    assert find_first_link("ببینید https://unknown.example/path، بعد") == (
        "https://unknown.example/path"
    )
    assert find_first_link("www.example.net/test.") == "www.example.net/test"
    assert find_first_link("دامنه example.ir خوب است") == "example.ir"
    assert find_first_link("این فقط متن عادی است") is None


def test_site_policy_allows_known_sites_and_real_splus_meeting_links() -> None:
    assert find_untrusted_link("https://github.com/project") is None
    assert find_untrusted_link("https://google.com/search?q=fox") is None
    assert find_untrusted_link("https://splus.ir/meet/room-123") is None


def test_site_policy_flags_unknown_and_blocked_hosts_without_network_access() -> None:
    unknown = find_untrusted_link("لینک: https://newsite.example/path")
    assert unknown is not None
    assert unknown.host == "newsite.example"
    assert unknown.classification == "unknown"

    blocked = find_untrusted_link("www.xvideos.com/watch")
    assert blocked is not None
    assert blocked.host == "www.xvideos.com"
    assert blocked.classification == "blocked"


def test_meeting_path_text_on_an_unrelated_host_is_not_exempt() -> None:
    result = find_untrusted_link("https://evil.example/splus.ir/meet/room")
    assert result is not None and result.classification == "unknown"


def test_malformed_bracketed_url_fails_closed_without_raising() -> None:
    result = find_untrusted_link("https://[broken")
    assert result is not None and result.classification == "unknown"
    assert result.host == ""
