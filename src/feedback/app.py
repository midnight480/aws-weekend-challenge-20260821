"""The only write path a human gets.

This endpoint is public and unauthenticated, so it is built on the assumption
that whoever calls it is hostile. Everything it accepts comes from a closed
vocabulary: a vote is "up" or "down", and a preference is one of a fixed list of
slugs defined below. No caller-supplied text is ever stored, and therefore no
caller-supplied text ever reaches the nightly prompt.

An earlier version took a free-text note here and folded it straight into that
prompt as "what this reader told you, in their own words". That was a genuine
injection channel - six posts were enough to own the whole preference section of
the prompt and steer text that gets published on a public page under the owner's
name. Authenticating the route would have hidden the problem; removing the text
field removes it.
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

# The complete vocabulary a reader can express. Keep in sync with PREFERENCES in
# src/muse/app.py, which owns the sentences these slugs map to. Anything not on
# this list is rejected here and dropped again there.
ALLOWED_PREFS = frozenset(
    {
        "shorter",
        "longer",
        "less_flattery",
        "more_technical",
        "less_technical",
        "warmer",
        "drier",
        "more_specific",
    }
)
KEEP_PREFS = 6
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
    if not isinstance(body, dict):
        return _reply(400, {"error": "body must be a JSON object"})

    vote = str(body.get("vote", "")).lower()
    if vote not in ("up", "down"):
        return _reply(400, {"error": "vote must be 'up' or 'down'"})

    pref = str(body.get("pref", "")).lower().strip()
    if pref and pref not in ALLOWED_PREFS:
        return _reply(400, {"error": "unknown pref", "allowed": sorted(ALLOWED_PREFS)})

    counter = "up" if vote == "up" else "down"
    key = {"pk": f"USER#{GITHUB_USER}", "sk": "STYLE"}

    # list_append needs the attribute to exist, so seed it on first write.
    table.update_item(
        Key=key,
        UpdateExpression="SET prefs = if_not_exists(prefs, :empty)",
        ExpressionAttributeValues={":empty": []},
    )

    item = table.update_item(
        Key=key,
        UpdateExpression="ADD #c :one" + (" SET prefs = list_append(prefs, :pref)" if pref else ""),
        ExpressionAttributeNames={"#c": counter},
        ExpressionAttributeValues={":one": 1, **({":pref": [pref]} if pref else {})},
        ReturnValues="ALL_NEW",
    )["Attributes"]

    if pref:
        # Keep the memory short and free of repeats: most recent wins.
        seen, trimmed = set(), []
        for p in reversed([str(x) for x in item.get("prefs", [])]):
            if p in ALLOWED_PREFS and p not in seen:
                seen.add(p)
                trimmed.append(p)
            if len(trimmed) >= KEEP_PREFS:
                break
        trimmed.reverse()
        if trimmed != [str(x) for x in item.get("prefs", [])]:
            table.update_item(
                Key=key,
                UpdateExpression="SET prefs = :trimmed",
                ExpressionAttributeValues={":trimmed": trimmed},
            )
            item["prefs"] = trimmed

    log.info("feedback %s pref=%s", vote, pref or "-")
    return _reply(
        200,
        {
            "ok": True,
            "up": int(item.get("up", 0)),
            "down": int(item.get("down", 0)),
            "prefs": [str(x) for x in item.get("prefs", [])],
        },
    )
