import os
from pathlib import Path

from aws_cdk import CfnOutput, Duration, RemovalPolicy, Stack, Tags
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_events as events
from aws_cdk import aws_events_targets as targets
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from aws_cdk import aws_sns as sns
from aws_cdk import aws_sns_subscriptions as subscriptions
from constructs import Construct


DEFAULT_KEYWORDS = (
    "シェーヌ・ダンクル,シェーヌダンクル,chaine d'ancre,chaine-d-ancre,chaîne d'ancre"
)
PROJECT_NAME = "hermes_notification_chaine_dancre"
MONITOR_FUNCTION_NAME = f"{PROJECT_NAME}_monitor"


class HermesNotificationChaineDancreStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        Tags.of(self).add("Project", PROJECT_NAME)

        notification_emails = self._notification_values(
            plural_context_key="notificationEmails",
            plural_env_key="NOTIFICATION_EMAILS",
            singular_context_key="notificationEmail",
            singular_env_key="NOTIFICATION_EMAIL",
        )
        notification_phone_numbers = self._notification_values(
            plural_context_key="notificationPhoneNumbers",
            plural_env_key="NOTIFICATION_PHONE_NUMBERS",
            singular_context_key="notificationPhoneNumber",
            singular_env_key="NOTIFICATION_PHONE_NUMBER",
        )
        seed_urls = self._context_or_env("seedUrls", "SEED_URLS", "")
        schedule_minutes = int(self._context_or_env("scheduleMinutes", "SCHEDULE_MINUTES", "5"))
        target_keywords = self._context_or_env("targetKeywords", "TARGET_KEYWORDS", DEFAULT_KEYWORDS)
        target_sizes = self._context_or_env("targetSizes", "TARGET_SIZES", "GM,TGM")
        notify_on_first_available = self._context_or_env(
            "notifyOnFirstAvailable", "NOTIFY_ON_FIRST_AVAILABLE", "true"
        )
        page_limit = self._context_or_env("pageLimit", "PAGE_LIMIT", "12")
        fetch_delay_ms = self._context_or_env("fetchDelayMs", "FETCH_DELAY_MS", "700")
        allowed_hosts = self._context_or_env("allowedHosts", "ALLOWED_HOSTS", "hermes.com")
        notification_timezone = self._context_or_env(
            "notificationTimezone", "NOTIFICATION_TIMEZONE", "Asia/Tokyo"
        )

        self._validate_settings(
            schedule_minutes=schedule_minutes,
            page_limit=int(page_limit),
            fetch_delay_ms=int(fetch_delay_ms),
            seed_urls=seed_urls,
            notification_emails=notification_emails,
            notification_phone_numbers=notification_phone_numbers,
        )

        table = dynamodb.Table(
            self,
            "ProductStateTable",
            table_name=f"{PROJECT_NAME}_product_state",
            partition_key=dynamodb.Attribute(name="id", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=True,
            ),
            removal_policy=RemovalPolicy.RETAIN,
        )

        topic = sns.Topic(
            self,
            "RestockTopic",
            topic_name=f"{PROJECT_NAME}_restock_alerts",
            display_name=PROJECT_NAME,
        )

        for email in notification_emails:
            topic.add_subscription(subscriptions.EmailSubscription(email))

        for phone_number in notification_phone_numbers:
            topic.add_subscription(subscriptions.SmsSubscription(phone_number))

        lambda_path = Path(__file__).resolve().parents[1] / "lambda" / "monitor"
        monitor_log_group = logs.LogGroup(
            self,
            "RestockMonitorLogGroup",
            log_group_name=f"/aws/lambda/{MONITOR_FUNCTION_NAME}",
            retention=logs.RetentionDays.ONE_MONTH,
            removal_policy=RemovalPolicy.DESTROY,
        )
        monitor_role = iam.Role(
            self,
            "RestockMonitorFunctionRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
        )
        monitor_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "logs:CreateLogStream",
                    "logs:PutLogEvents",
                ],
                resources=[
                    monitor_log_group.log_group_arn,
                    f"{monitor_log_group.log_group_arn}:*",
                ],
            )
        )

        monitor_fn = lambda_.Function(
            self,
            "RestockMonitorFunction",
            function_name=MONITOR_FUNCTION_NAME,
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="app.handler",
            code=lambda_.Code.from_asset(str(lambda_path)),
            timeout=Duration.seconds(180),
            memory_size=256,
            reserved_concurrent_executions=1,
            log_group=monitor_log_group,
            role=monitor_role,
            environment={
                "DDB_TABLE_NAME": table.table_name,
                "SNS_TOPIC_ARN": topic.topic_arn,
                "SEED_URLS": seed_urls,
                "ALLOWED_HOSTS": allowed_hosts,
                "NOTIFICATION_TIMEZONE": notification_timezone,
                "TARGET_KEYWORDS": target_keywords,
                "TARGET_SIZES": target_sizes,
                "NOTIFY_ON_FIRST_AVAILABLE": notify_on_first_available,
                "PAGE_LIMIT": page_limit,
                "FETCH_DELAY_MS": fetch_delay_ms,
                "REQUEST_TIMEOUT_SECONDS": "20",
                "USER_AGENT": (
                    "Mozilla/5.0 (compatible; hermes_notification_chaine_dancre/1.0; "
                    "+https://example.com/monitor)"
                ),
            },
        )

        monitor_fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=[
                    "dynamodb:GetItem",
                    "dynamodb:PutItem",
                ],
                resources=[table.table_arn],
            )
        )
        topic.grant_publish(monitor_fn)

        rule = events.Rule(
            self,
            "RestockMonitorSchedule",
            rule_name=f"{PROJECT_NAME}_schedule",
            schedule=events.Schedule.rate(Duration.minutes(schedule_minutes)),
        )
        rule.add_target(targets.LambdaFunction(monitor_fn))

        CfnOutput(self, "StateTableName", value=table.table_name)
        CfnOutput(self, "NotificationTopicArn", value=topic.topic_arn)
        CfnOutput(self, "MonitorFunctionName", value=monitor_fn.function_name)
        CfnOutput(
            self,
            "EmailSubscriptionStatus",
            value="created" if notification_emails else "not configured",
        )
        CfnOutput(
            self,
            "SmsSubscriptionStatus",
            value="created" if notification_phone_numbers else "not configured",
        )

    def _context_or_env(self, context_key: str, env_key: str, default: str) -> str:
        value = self.node.try_get_context(context_key)
        if value is None:
            value = os.getenv(env_key, default)
        return str(value)

    def _notification_values(
        self,
        plural_context_key: str,
        plural_env_key: str,
        singular_context_key: str,
        singular_env_key: str,
    ) -> tuple[str, ...]:
        values = []

        plural_value = self._context_or_env(plural_context_key, plural_env_key, "")
        values.extend(self._split_csv(plural_value))

        singular_value = self._context_or_env(singular_context_key, singular_env_key, "")
        values.extend(self._split_csv(singular_value))

        return tuple(dict.fromkeys(values))

    def _split_csv(self, value: str) -> list[str]:
        return [item.strip() for item in value.split(",") if item.strip()]

    def _validate_settings(
        self,
        schedule_minutes: int,
        page_limit: int,
        fetch_delay_ms: int,
        seed_urls: str,
        notification_emails: tuple[str, ...],
        notification_phone_numbers: tuple[str, ...],
    ) -> None:
        if schedule_minutes < 5:
            raise ValueError("scheduleMinutes must be 5 or greater to avoid excessive crawling.")
        if page_limit < 1 or page_limit > 50:
            raise ValueError("pageLimit must be between 1 and 50.")
        if fetch_delay_ms < 500:
            raise ValueError("fetchDelayMs must be 500 or greater to avoid burst access.")
        if not self._split_csv(seed_urls):
            raise ValueError("seedUrls or SEED_URLS is required.")
        if not notification_emails and not notification_phone_numbers:
            raise ValueError(
                "At least one notification email or phone number must be configured."
            )
