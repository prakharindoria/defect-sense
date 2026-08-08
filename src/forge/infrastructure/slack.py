"""DefectSense Slack Notification Adapter.

Posts Slack Block Kit alert messages when a QA analyst assigns a defect unit to a
shop-floor worker.

Channel: #forge-quality (configurable via SLACK_ALERT_CHANNEL)
Supports Slack Bot Token (chat.postMessage) and Slack Webhook URLs.
Executes asynchronously in a background thread executor.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import urllib.request
from typing import Any

_log = logging.getLogger(__name__)

SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "").strip()
SLACK_ALERT_CHANNEL = os.environ.get("SLACK_ALERT_CHANNEL", "#forge-quality").strip()
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL", "").strip()


def send_slack_defect_assignment_sync(
    unit_id: str,
    verdict: str,
    disposition: str,
    primary_signal: str,
    assigned_to_display: str,
    assigned_by_display: str,
    channel: str = SLACK_ALERT_CHANNEL,
) -> bool:
    """Construct and dispatch a Slack Block Kit message."""
    payload: dict[str, Any] = {
        "channel": channel,
        "text": f"🚨 *DefectSense Rework Alert*: Unit {unit_id} assigned to {assigned_to_display}",
        "blocks": [
          {
            "type": "header",
            "text": {
              "type": "plain_text",
              "text": "🚨 DefectSense Rework Assignment",
              "emoji": True
            }
          },
          {
            "type": "section",
            "fields": [
              {"type": "mrkdwn", "text": f"*Unit ID:*\n`{unit_id}`"},
              {"type": "mrkdwn", "text": f"*Verdict:*\n`{verdict.upper()}`"},
              {"type": "mrkdwn", "text": f"*Assigned Worker:*\n*{assigned_to_display}*"},
              {"type": "mrkdwn", "text": f"*Assigned By:*\n{assigned_by_display}"},
              {"type": "mrkdwn", "text": f"*Primary Signal:*\n`{primary_signal}`"},
              {"type": "mrkdwn", "text": f"*Required Disposition:*\n*{disposition}*"}
            ]
          },
          {
            "type": "context",
            "elements": [
              {
                "type": "mrkdwn",
                "text": "⚡ *DefectSense Governance Engine* · Role-based Quality Workflow"
              }
            ]
          }
        ]
    }

    # If neither Slack token nor Webhook is set, log payload and return gracefully
    if not SLACK_BOT_TOKEN and not SLACK_WEBHOOK_URL:
        _log.info("slack.notification_simulated", extra={
            "channel": channel,
            "unit_id": unit_id,
            "assigned_to": assigned_to_display,
            "note": "SLACK_BOT_TOKEN/WEBHOOK not set; Slack Block Kit payload created and logged.",
        })
        return True

    try:
        data = json.dumps(payload).encode("utf-8")
        if SLACK_WEBHOOK_URL:
            req = urllib.request.Request(
                SLACK_WEBHOOK_URL, data=data, headers={"Content-Type": "application/json"}
            )
        else:
            req = urllib.request.Request(
                "https://slack.com/api/chat.postMessage",
                data=data,
                headers={
                    "Content-Type": "application/json; charset=utf-8",
                    "Authorization": f"Bearer {SLACK_BOT_TOKEN}",
                },
            )

        with urllib.request.urlopen(req, timeout=8) as response:
            res_body = response.read().decode("utf-8")
            _log.info("slack.notification_sent", extra={"channel": channel, "unit_id": unit_id, "res": res_body[:100]})
            return True
    except Exception as exc:
        _log.warning("slack.notification_failed", extra={"error": str(exc), "channel": channel})
        return False


async def send_slack_defect_assignment(
    unit_id: str,
    verdict: str,
    disposition: str,
    primary_signal: str,
    assigned_to_display: str,
    assigned_by_display: str,
    channel: str = SLACK_ALERT_CHANNEL,
) -> bool:
    """Asynchronous wrapper that runs Slack notification in a background thread."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None,
        send_slack_defect_assignment_sync,
        unit_id,
        verdict,
        disposition,
        primary_signal,
        assigned_to_display,
        assigned_by_display,
        channel,
    )
