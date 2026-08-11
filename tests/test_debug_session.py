from cloak_auth_bridge.debug_session import safe_url


def test_safe_url_strips_query_and_fragment() -> None:
    assert (
        safe_url("https://x.com/home?state=secret#token")
        == "https://x.com/home"
    )


def test_safe_url_keeps_origin_and_path() -> None:
    assert safe_url("https://www.bilibili.com/video/BV1") == "https://www.bilibili.com/video/BV1"
