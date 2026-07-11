# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""Redis Streams 命名约定。"""

STREAM_SKILL_EXECUTION = "netops:skill.execution.v1"
STREAM_WORKFLOW = "netops:workflow.v1"
STREAM_NETWORK_EVENT = "netops:network.events.v1"
STREAM_PARSER = "netops:parser.v1"
STREAM_DLQ = "netops:events.dlq"

GROUP_AUDIT = "netops-audit"
GROUP_NOTIFY = "netops-notify"
GROUP_METRICS = "netops-metrics"

IDEMPOTENCY_PREFIX = "netops:event:done:"
IDEMPOTENCY_PROCESSING_PREFIX = "netops:event:processing:"
IDEMPOTENCY_TTL_SEC = 90 * 24 * 3600
