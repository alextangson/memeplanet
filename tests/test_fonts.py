from mememe.core import fonts


def test_cjk_font_returns_truetype_when_available(monkeypatch):
    # 开发机/CI 有任一中文字体时应返回 truetype（非默认位图字体）
    path = fonts._find_font_path()
    if path is None:
        import pytest
        pytest.skip("本机无 CJK 字体")
    f = fonts.cjk_font(40)
    assert f.size == 40


def test_find_font_path_cached(monkeypatch):
    fonts._cached_path = "/some/font.ttc"
    assert fonts._find_font_path() == "/some/font.ttc"
    fonts._cached_path = None  # 复位，免污染其他用例
