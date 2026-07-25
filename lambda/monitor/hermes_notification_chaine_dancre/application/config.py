"""監視ユースケースへ渡す設定値と変換 helper。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MonitorConfig:
    """外部設定を application 層で扱いやすくまとめた値オブジェクト。"""

    seed_urls: tuple[str, ...]
    allowed_hosts: tuple[str, ...]
    notification_timezone: str
    target_keywords: tuple[str, ...]
    target_sizes: tuple[str, ...]
    notify_on_first_available: bool
    page_limit: int
    fetch_delay_ms: int
    timeout_seconds: int
    user_agent: str


def split_csv(value: str) -> tuple[str, ...]:
    """環境変数のカンマ区切り値を空白除去済み tuple に変換する。"""
    return tuple(item.strip() for item in value.split(",") if item.strip())


def parse_bool(value: str) -> bool:
    """環境変数で使いやすい真偽値表現を bool に変換する。"""
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}
