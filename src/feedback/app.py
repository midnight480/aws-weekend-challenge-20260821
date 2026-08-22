"""The only write path a human gets.

A thumbs up or down (and optionally one sentence of guidance) is folded into the
agent's style memory, and tomorrow's prompt is built from it. There is no route
here that can make the agent generate anything - if someone found this endpoint,
the worst they could do is tell it to write shorter sentences.
"""

from __future__ import annotations

import json
import logging
import os

import boto3

log = logging.getLogger()
log.setLevel(logging.INFO)

GITHUB_USER = os.environ["GITHUB_USER"]
table = boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])

MAX_NOTE = 200
KEEP_NOTES = 8
CORS = {
    "content-type": "application/json",
    "access-control-allow-origin": "*",
}


def _reply(status: int, body: dict) -> dict:
    return {"statusCode": status, "headers": CORS, "body": json.dumps(body, ensure_ascii=False)}


def handler(event, context):  # noqa: ANN001
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _reply(400, {"error": "body must be JSON"})

    vote = str(body.get("vote", "")).lower()
    if vote not in ("up", "down"):
        return _reply(400, {"error": "vote must be 'up' or 'down'"})

    date = str(body.get("date", ""))[:10]
    note = " ".join(str(body.get("note", "")).split())[:MAX_NOTE]

    bucket = "likes" if vote == "up" else "dislikes"
    counter = "up" if vote == "up" else "down"

    # list_append needs the attribute to exist, so seed both lists on first write.
    table.update_item(
        Key={"pk": f"USER#{GITHUB_USER}", "sk": "STYLE"},
        UpdateExpression="SET likes = if_not_exists(likes, :empty), dislikes = if_not_exists(dislikes, :empty)",
        ExpressionAttributeValues={":empty": []},
    )

    expr = f"ADD #c :one SET {bucket} = list_append({bucket}, :note)"
    values = {":one": 1, ":note": [note] if note else []}
    if note:
        item = table.update_item(
            Key={"pk": f"USER#{GITHUB_USER}", "sk": "STYLE"},
            UpdateExpression=expr,
            ExpressionAttributeNames={"#c": counter},
            ExpressionAttributeValues=values,
            ReturnValues="ALL_NEW",
        )["Attributes"]
        # keep the memory small so the prompt stays focused on recent guidance
        if len(item.get(bucket, [])) > KEEP_NOTES:
            table.update_item(
                Key={"pk": f"USER#{GITHUB_USER}", "sk": "STYLE"},
                UpdateExpression=f"SET {bucket} = :trimmed",
                ExpressionAttributeValues={":trimmed": item[bucket][-KEEP_NOTES:]},
            )
    else:
        item = table.update_item(
            Key={"pk": f"USER#{GITHUB_USER}", "sk": "STYLE"},
            UpdateExpression="ADD #c :one",
            ExpressionAttributeNames={"#c": counter},
            ExpressionAttributeValues={":one": 1},
            ReturnValues="ALL_NEW",
        )["Attributes"]

    log.info("feedback %s on %s (note=%s)", vote, date or "-", bool(note))
    return _reply(200, {"ok": True, "up": int(item.get("up", 0)), "down": int(item.get("down", 0))})
