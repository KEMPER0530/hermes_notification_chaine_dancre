"""CDK スタックで作る監視/通知リソースの回帰テスト。"""

from __future__ import annotations

import json

import aws_cdk as cdk
from aws_cdk.assertions import Template

from infra.hermes_restock_stack import HermesNotificationChaineDancreStack


def test_alarm_uses_dedicated_ops_topic() -> None:
    """SNS失敗Alarmは、入荷通知Topicではなく運用専用Topicへ通知する。"""
    template = synth_template()
    resources = template.to_json()["Resources"]

    restock_topic_id = find_topic_logical_id(
        resources,
        "hermes_notification_chaine_dancre_restock_alerts",
    )
    ops_topic_id = find_topic_logical_id(
        resources,
        "hermes_notification_chaine_dancre_ops_alerts",
    )
    alarm = next(
        resource
        for resource in resources.values()
        if resource["Type"] == "AWS::CloudWatch::Alarm"
        and resource["Properties"]["AlarmName"]
        == "hermes_notification_chaine_dancre_sns_notification_failed"
    )

    assert alarm["Properties"]["AlarmActions"] == [{"Ref": ops_topic_id}]
    assert alarm["Properties"]["Dimensions"] == [
        {"Name": "TopicName", "Value": {"Fn::GetAtt": [restock_topic_id, "TopicName"]}}
    ]


def test_ops_topic_has_email_subscription() -> None:
    """運用Alarm用メールアドレスは、入荷通知Topicとは別Topicへ購読させる。"""
    template = synth_template()
    resources = template.to_json()["Resources"]
    ops_topic_id = find_topic_logical_id(
        resources,
        "hermes_notification_chaine_dancre_ops_alerts",
    )

    assert any(
        resource["Type"] == "AWS::SNS::Subscription"
        and resource["Properties"]["Protocol"] == "email"
        and resource["Properties"]["Endpoint"] == "ops@example.com"
        and resource["Properties"]["TopicArn"] == {"Ref": ops_topic_id}
        for resource in resources.values()
    )


def test_sms_monthly_spend_limit_is_managed_by_custom_resource() -> None:
    """SNS SMS の月間利用上限を CDK の custom resource から設定する。"""
    template = synth_template()
    resources = template.to_json()["Resources"]
    custom_resource = next(
        resource for resource in resources.values() if resource["Type"] == "Custom::AWS"
    )

    create_payload = json.dumps(
        custom_resource["Properties"]["Create"],
        ensure_ascii=False,
    )

    assert "MonthlySpendLimit" in create_payload
    assert "5" in create_payload


def synth_template() -> Template:
    """テスト用 context を渡してスタックを synth する。"""
    app = cdk.App(
        context={
            "seedUrls": "https://www.hermes.com/jp/ja/",
            "notificationPhoneNumber": "+819012345678",
            "alarmNotificationEmails": "ops@example.com",
            "smsMonthlySpendLimit": "5",
        }
    )
    stack = HermesNotificationChaineDancreStack(
        app,
        "HermesNotificationChaineDancreStackTest",
        env=cdk.Environment(account="123456789012", region="ap-northeast-1"),
    )
    return Template.from_stack(stack)


def find_topic_logical_id(resources: dict[str, dict], topic_name: str) -> str:
    """TopicName から CloudFormation logical id を探す。"""
    for logical_id, resource in resources.items():
        if (
            resource["Type"] == "AWS::SNS::Topic"
            and resource["Properties"]["TopicName"] == topic_name
        ):
            return logical_id
    raise AssertionError(f"topic not found: {topic_name}")
