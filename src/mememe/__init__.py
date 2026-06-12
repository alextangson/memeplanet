"""memeplanet 包。导入时自动加载项目根目录的 .env（gitignored）。

用户拍板（2026-06-12）：本地开发密钥存项目根 .env，免每个终端手动 export。
安全护栏：.env 在 .gitignore（repo 公开）、deploy.sh 排除（生产密钥走
systemd EnvironmentFile）、已存在的环境变量绝不覆盖（生产优先级更高）。
"""

import os
from pathlib import Path


def load_env_file(path: str | os.PathLike = ".env") -> None:
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, sep, value = line.partition("=")
        if not sep:
            continue
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


load_env_file()
