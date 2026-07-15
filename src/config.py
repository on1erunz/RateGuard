"""
config.py — RateGuard 配置加载器

优先级（高→低）：
  1. 命令行参数 --config
  2. 环境变量 RATEGUARD_CONFIG
  3. configs/config.yaml（若存在）
  4. configs/config.example.yaml（兜底模板）

使用时：
    from src.config import config
    config.get("search.city")
均返回 Python 原生类型（str / int / float / bool / list / dict）。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import yaml


def _load_project_dotenv() -> None:
    """Load simple KEY=VALUE pairs from the project-local .env file.

    Secrets stay outside YAML and are never copied to the repository config.
    Existing process environment variables take precedence.
    """
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if not env_path.is_file():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


_load_project_dotenv()

# ── 搜索路径 ─────────────────────────────────────────────────────────────

_SEARCH_PATHS = [
    sys.argv[sys.argv.index("--config") + 1]
    if "--config" in sys.argv and sys.argv.index("--config") + 1 < len(sys.argv)
    else None,
    os.environ.get("RATEGUARD_CONFIG"),
    Path(__file__).resolve().parents[1] / "configs" / "config.yaml",
    Path(__file__).resolve().parents[1] / "configs" / "config.example.yaml",
]

_DEFAULTS = {
    "search": {
        "mode": "coords",
        "city": "深圳市",
        "coords": {"lat": 22.5362, "lng": 113.9514, "radius_km": 5},
    },
    "competitors": {
        "target_platforms": ["ctrip"],
        "max_hotels": 20,
        "stay_delay_min": 0.5,  # 随机延迟 FIXME 私聊放松=2-4s 防 403
        "max_retries": 3,
        "max_delay": 10,
    },
    "hotel": {
        "default_base_price": 300,
        "checkin_date": "",   # "" → 今天 + 14 天
        "room_nights": 1,
    },
    "rules": {
        "undercut_max_pct": 0.30,   # 最高允许以（统计三点二后给下高）
        "min_price_abs": 300,
        "gap_alert_threshold": 20,  # 竞对各价格偏差告警线
    },
    "notifications": {
        "lark": {
            "enabled": False,
            "webhook": "",
        },
        "email": {
            "enabled": False,
            "smtp_host": "smtp.gmail.com",
            "smtp_port": 587,
            "smtp_user": "",
            "smtp_pass": "",
            "from_addr": "",
            "to_addr": "",
        },
    },
    "scraper": {
        "headless": True,
        "user_agent": "",           # "" → 自动随机生成
        "timeout_s": 30,
        "screenshot_dir": "",        # "" → ./logs/screenshots
        "debug_dir": "",             # "" → ./logs/debug
        "persist_path": "./db/rateguard.db", # 本地数据库
    },
}


class Config:
    def __init__(self) -> None:
        self._raw: dict[str, Any] = {}
        self._load()

    def _load(self) -> None:
        loaded = False
        for path in _SEARCH_PATHS:
            if path and os.path.isfile(path):
                with open(path, "r", encoding="utf-8") as f:
                    self._raw = yaml.safe_load(f) or {}
                loaded = True
                break

        if not loaded:
            self._raw = {}

        self._merge_defaults()

    def _merge_defaults(self) -> None:
        """递归合并默认值，确保 config 中缺失的 key 也有值"""
        def _merge(base: dict, target: dict) -> dict:
            for k, v in base.items():
                if isinstance(v, dict):
                    target[k] = _merge(v, target.get(k, {}))
                elif k not in target:
                    target[k] = v
            return target
        _merge(_DEFAULTS, self._raw)

    def get(self, key: str, default: Any = None) -> Any:
        """点号路径取值，例如 config.get("rules.min_price_abs")"""
        parts = key.split(".")
        node: Any = self._raw
        for p in parts:
            if not isinstance(node, dict):
                return default
            node = node.get(p, default)
        return node

    def path_for(self, key: str) -> Path | None:
        """对结果做 Path 归一化（如果值是字符串路径）"""
        val = self.get(key)
        return Path(val).resolve() if val else None

    def as_dict(self) -> dict:
        return dict(self._raw)

    def __repr__(self) -> str:
        return f"Config(mode={self.get('search.mode')!r}, city={self.get('search.city')!r})"


config = Config()
