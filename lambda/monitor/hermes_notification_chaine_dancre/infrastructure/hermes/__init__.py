"""Hermes 公式サイト向けの crawler adapter を公開する。"""

from hermes_notification_chaine_dancre.infrastructure.hermes.crawler import (
    HermesProductCrawler,
)

# application 層には ProductCrawler Protocol として注入する。
__all__ = ["HermesProductCrawler"]
