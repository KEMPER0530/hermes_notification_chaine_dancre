"""AWS へ接続せず Hermes ページ解析だけを確認するローカル CLI。"""

from __future__ import annotations

import argparse
import json

from hermes_notification_chaine_dancre.application.config import MonitorConfig, split_csv
from hermes_notification_chaine_dancre.infrastructure.hermes import HermesProductCrawler


def main() -> None:
    # Lambda と同じ crawler を使い、DynamoDB/SNS なしで解析結果を確認する。
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed-url", action="append", required=True)
    parser.add_argument(
        "--target-keywords",
        default="シェーヌ・ダンクル,シェーヌダンクル,chaine d'ancre,chaine-d-ancre,chaîne d'ancre",
    )
    parser.add_argument("--target-sizes", default="GM,TGM")
    parser.add_argument("--allowed-hosts", default="hermes.com")
    parser.add_argument("--notification-timezone", default="Asia/Tokyo")
    parser.add_argument("--page-limit", type=int, default=3)
    parser.add_argument("--fetch-delay-ms", type=int, default=700)
    parser.add_argument("--timeout-seconds", type=int, default=20)
    parser.add_argument(
        "--user-agent",
        default="Mozilla/5.0 (compatible; hermes_notification_chaine_dancre_local/1.0)",
    )
    args = parser.parse_args()

    # CLI 引数を application 層の設定型へ寄せて、実行経路の差を最小にする。
    config = MonitorConfig(
        seed_urls=tuple(args.seed_url),
        allowed_hosts=split_csv(args.allowed_hosts),
        notification_timezone=args.notification_timezone,
        target_keywords=split_csv(args.target_keywords),
        target_sizes=split_csv(args.target_sizes),
        notify_on_first_available=True,
        page_limit=args.page_limit,
        fetch_delay_ms=args.fetch_delay_ms,
        timeout_seconds=args.timeout_seconds,
        user_agent=args.user_agent,
    )
    snapshots = HermesProductCrawler().crawl(config)
    print(json.dumps([snapshot.__dict__ for snapshot in snapshots], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
