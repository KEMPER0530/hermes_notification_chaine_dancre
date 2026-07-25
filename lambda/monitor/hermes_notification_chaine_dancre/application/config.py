from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MonitorConfig:
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
    return tuple(item.strip() for item in value.split(",") if item.strip())


def parse_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}
