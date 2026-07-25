"""実行環境の現在時刻を返す Clock adapter。"""

from __future__ import annotations

from datetime import datetime, timezone


class UtcClock:
    """システム時刻を UTC で取得する。表示タイムゾーン変換は use case 側で行う。"""

    def now(self) -> datetime:
        return datetime.now(timezone.utc)
