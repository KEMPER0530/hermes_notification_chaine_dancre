"""環境変数から本番用 use case と adapter を組み立てるモジュール。"""

from __future__ import annotations

import os

import boto3

from hermes_notification_chaine_dancre.application.config import (
    MonitorConfig,
    parse_bool,
    split_csv,
)
from hermes_notification_chaine_dancre.application.use_cases import CheckRestocksUseCase
from hermes_notification_chaine_dancre.domain.services import RestockPolicy
from hermes_notification_chaine_dancre.infrastructure.aws_clock import UtcClock
from hermes_notification_chaine_dancre.infrastructure.dynamodb_state_repository import (
    DynamoDbProductStateRepository,
)
from hermes_notification_chaine_dancre.infrastructure.hermes import HermesProductCrawler
from hermes_notification_chaine_dancre.infrastructure.sns_restock_notifier import (
    SnsRestockNotifier,
)


def build_check_restocks_from_env() -> tuple[CheckRestocksUseCase, MonitorConfig]:
    """Lambda 実行時の環境変数を読み、依存オブジェクトを配線する。"""
    table_name = required_env("DDB_TABLE_NAME")
    topic_arn = required_env("SNS_TOPIC_ARN")

    # boto3 client/resource は infrastructure adapter に閉じ込める。
    dynamodb = boto3.resource("dynamodb")
    sns = boto3.client("sns")

    config = MonitorConfig(
        seed_urls=split_csv(required_env("SEED_URLS")),
        allowed_hosts=split_csv(os.getenv("ALLOWED_HOSTS", "hermes.com")),
        notification_timezone=os.getenv("NOTIFICATION_TIMEZONE", "Asia/Tokyo"),
        target_keywords=split_csv(os.getenv("TARGET_KEYWORDS", "")),
        target_sizes=split_csv(os.getenv("TARGET_SIZES", "GM,TGM")),
        notify_on_first_available=parse_bool(os.getenv("NOTIFY_ON_FIRST_AVAILABLE", "true")),
        page_limit=int(os.getenv("PAGE_LIMIT", "12")),
        fetch_delay_ms=int(os.getenv("FETCH_DELAY_MS", "700")),
        timeout_seconds=int(os.getenv("REQUEST_TIMEOUT_SECONDS", "20")),
        user_agent=os.getenv("USER_AGENT", "hermes_notification_chaine_dancre/1.0"),
    )

    # application 層は Protocol に依存し、具体実装はここでだけ注入する。
    use_case = CheckRestocksUseCase(
        crawler=HermesProductCrawler(),
        repository=DynamoDbProductStateRepository(dynamodb.Table(table_name)),
        notifier=SnsRestockNotifier(sns, topic_arn),
        clock=UtcClock(),
        policy=RestockPolicy(config.notify_on_first_available),
    )
    return use_case, config


def required_env(name: str) -> str:
    """必須環境変数の設定漏れを起動直後に検出する。"""
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value
