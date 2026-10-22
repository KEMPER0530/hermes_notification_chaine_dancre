# Security remediation (2026-10-22)

All crawler seed URLs, queued links and HTTP redirect hops are checked against the configured HTTPS host allowlist before the request. Embedded credentials, nonstandard ports, HTTP downgrade, metadata endpoints and external redirect destinations are rejected.

Validation: 28 pytest tests passed, including redirect and malicious URL regression cases. pip-audit on the resolved requirements-dev environment found zero known vulnerabilities. No production crawl or notification was triggered.
