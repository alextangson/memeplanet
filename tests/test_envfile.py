import os

import mememe


def test_load_env_file_fills_missing_only(tmp_path, monkeypatch):
    # 用户拍板：key 存项目根 .env 免每个终端手动 export。
    # 但已存在的环境变量绝不覆盖——生产 systemd EnvironmentFile 优先。
    monkeypatch.setenv("MP_TEST_FOO", "placeholder")
    monkeypatch.delenv("MP_TEST_FOO", raising=False)
    monkeypatch.setenv("MP_TEST_BAR", "placeholder")
    monkeypatch.delenv("MP_TEST_BAR", raising=False)
    monkeypatch.setenv("MP_TEST_EXISTING", "original")

    env = tmp_path / ".env"
    env.write_text(
        "# 注释行\n"
        'export MP_TEST_FOO="abc"\n'
        "MP_TEST_BAR=def\n"
        "MP_TEST_EXISTING=overwrite-attempt\n"
        "\n"
        "broken line without equals\n",
        encoding="utf-8",
    )
    mememe.load_env_file(env)

    assert os.environ["MP_TEST_FOO"] == "abc"  # export 前缀与引号都容错
    assert os.environ["MP_TEST_BAR"] == "def"
    assert os.environ["MP_TEST_EXISTING"] == "original"


def test_load_env_file_missing_is_noop(tmp_path):
    mememe.load_env_file(tmp_path / "nonexistent.env")  # 不存在不报错
