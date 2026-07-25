#!/usr/bin/env python3
"""CDK アプリケーションのエントリーポイント。"""

import os
from pathlib import Path

import aws_cdk as cdk

from infra.hermes_restock_stack import HermesNotificationChaineDancreStack


def load_dotenv(path: Path) -> None:
    """ローカル開発用の .env を CDK context より前に環境変数へ反映する。"""
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key.strip(), value)


load_dotenv(Path(__file__).resolve().parent / ".env")

app = cdk.App()
# AWS CLI の既定リージョンに引きずられないよう、東京リージョンを既定にする。
deploy_region = str(app.node.try_get_context("region") or os.getenv("CDK_DEPLOY_REGION", "ap-northeast-1"))

HermesNotificationChaineDancreStack(
    app,
    "HermesNotificationChaineDancreStack",
    env=cdk.Environment(
        account=os.getenv("CDK_DEFAULT_ACCOUNT"),
        region=deploy_region,
    ),
)

app.synth()
