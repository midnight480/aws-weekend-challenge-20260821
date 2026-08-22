"""Commit Muse - the nightly agent.

Once a day this function wakes up on its own, reads what the developer actually
did on GitHub in the last 24 hours, and writes a short journal entry about it
with Amazon Bedrock. Nobody presses a button; the entry is simply there in the
morning.

Two rules the agent holds itself to:
  1. It never invents work. Every claim it makes has to trace back to a real
     event in the digest it was handed.
  2. It would rather write nothing than write something broken. If the model
     does not return a valid entry after a few tries, the day stays empty.
"""

from __future__ import annotations

import json
import logging
import os
import re
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

import boto3
from boto3.dynamodb.conditions import Key
from botocore.config import Config

log = logging.getLogger()
log.setLevel(logging.INFO)

JST = timezone(timedelta(hours=9))

GITHUB_USER = os.environ["GITHUB_USER"]
TABLE_NAME = os.environ["TABLE_NAME"]
SITE_BUCKET = os.environ["SITE_BUCKET"]
INFERENCE_PROFILE_ID = os.environ["INFERENCE_PROFILE_ID"]
GITHUB_TOKEN_PARAM = os.environ.get("GITHUB_TOKEN_PARAM", "")

MAX_ATTEMPTS = 3
HISTORY_ON_SITE = 30
RECENT_FOR_PROMPT = 5

ddb = boto3.resource("dynamodb")
table = ddb.Table(TABLE_NAME)
s3 = boto3.client("s3")
bedrock = boto3.client(
    "bedrock-runtime",
    config=Config(retries={"max_attempts": 3, "mode": "standard"}, read_timeout=60),
)

# --------------------------------------------------------------------------
# GitHub
# --------------------------------------------------------------------------

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
# Deliberately narrow: an early version also matched "any 40+ character token",
# which happily redacted legitimate branch names like snyk-fix-799929ff...
SECRETISH_RE = re.compile(
    r"\b(?:gh[pousr]_[A-Za-z0-9]{16,}"          # GitHub tokens
    r"|AKIA[0-9A-Z]{16}"                        # AWS access key ids
    r"|xox[baprs]-[A-Za-z0-9-]{10,}"            # Slack
    r"|sk-[A-Za-z0-9]{20,}"                     # generic provider keys
    r"|eyJ[A-Za-z0-9_\-]{16,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,})"  # JWTs
)


def _scrub(text: str) -> str:
    """Commit messages are user data. Strip anything that looks like a secret
    or a personal email before it ever reaches the model or the public site."""
    text = EMAIL_RE.sub("[email]", text)
    text = SECRETISH_RE.sub("[redacted]", text)
    return text.strip()


def _github_token() -> str | None:
    if not GITHUB_TOKEN_PARAM:
        return None
    try:
        ssm = boto3.client("ssm")
        return ssm.get_parameter(Name=GITHUB_TOKEN_PARAM, WithDecryption=True)["Parameter"]["Value"]
    except Exception:  # noqa: BLE001 - a missing token is not fatal, we just go anonymous
        log.warning("could not read %s, falling back to anonymous", GITHUB_TOKEN_PARAM, exc_info=True)
        return None


API = "https://api.github.com"
MAX_COMMIT_LOOKUPS = 8
MAX_PR_LOOKUPS = 6


def _get(path: str, token: str | None) -> object | None:
    """GET a GitHub endpoint. Returns None instead of raising on 4xx so that a
    rate limit or a repo that went private never takes the whole night down."""
    req = urllib.request.Request(
        f"{API}{path}",
        headers={
            "User-Agent": "commit-muse (aws-weekend-challenge)",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        log.warning("GET %s -> %s %s", path, exc.code, exc.headers.get("x-ratelimit-remaining"))
        return None
    except Exception:  # noqa: BLE001
        log.warning("GET %s failed", path, exc_info=True)
        return None


def fetch_events(user: str, token: str | None) -> list[dict]:
    """The last ~100 public events GitHub will hand out for this account.

    Note: these payloads are *trimmed*. A PushEvent carries only the before/head
    SHAs, not the commit messages, and a PullRequestEvent has no title. The
    interesting content has to be fetched from the repositories themselves.
    """
    events = _get(f"/users/{user}/events/public?per_page=100", token) or []
    for ev in events:
        ev["_at"] = datetime.strptime(ev["created_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    return events


def group_by_day(events: list[dict]) -> dict[str, list[dict]]:
    """Bucket events into local (JST) calendar days - the days the human lived."""
    days: dict[str, list[dict]] = {}
    for ev in events:
        days.setdefault(ev["_at"].astimezone(JST).strftime("%Y-%m-%d"), []).append(ev)
    return days


def fetch_commit_messages(repo: str, user: str, day: str, token: str | None) -> list[str]:
    """Ask the repository what this person actually committed on this day."""
    since = f"{day}T00:00:00%2B09:00"
    until = f"{day}T23:59:59%2B09:00"
    data = _get(
        f"/repos/{repo}/commits?author={user}&since={since}&until={until}&per_page=30", token
    )
    if not isinstance(data, list):
        return []
    out = []
    for c in data:
        subject = (c.get("commit", {}).get("message") or "").splitlines()[0]
        if subject:
            out.append(_scrub(subject))
    return out


def fetch_pr_title(repo: str, number: int, token: str | None) -> str:
    pr = _get(f"/repos/{repo}/pulls/{number}", token)
    return _scrub(pr.get("title", "")) if isinstance(pr, dict) else ""


def build_digest(day: str, events: list[dict], user: str, token: str | None) -> dict:
    """Flatten a day of GitHub events into the small, factual summary the model
    gets. Everything the journal is allowed to say has to be visible in here."""
    repos: dict[str, dict] = {}
    highlights: list[str] = []
    pushed_repos: list[str] = []
    prs: list[tuple[str, int, str]] = []

    for ev in events:
        repo = ev.get("repo", {}).get("name", "unknown")
        bucket = repos.setdefault(repo, {"repo": repo, "commits": 0, "events": 0})
        bucket["events"] += 1
        kind = ev.get("type")
        payload = ev.get("payload", {})

        if kind == "PushEvent":
            if repo not in pushed_repos:
                pushed_repos.append(repo)
        elif kind == "PullRequestEvent":
            prs.append((repo, int(payload.get("number", 0)), str(payload.get("action", "?"))))
        elif kind == "IssuesEvent":
            highlights.append(f"{repo}: issue {payload.get('action', '?')}")
        elif kind == "IssueCommentEvent":
            highlights.append(f"{repo}: commented on a thread")
        elif kind == "CreateEvent":
            ref = payload.get("ref") or ""
            highlights.append(f"{repo}: created {payload.get('ref_type', '?')} {_scrub(ref)}".strip())
        elif kind == "ReleaseEvent":
            highlights.append(f"{repo}: published a release")
        elif kind == "PublicEvent":
            highlights.append(f"{repo}: made public")
        elif kind == "WatchEvent":
            highlights.append(f"{repo}: starred")
        elif kind == "ForkEvent":
            highlights.append(f"{repo}: forked")

    commit_messages: list[str] = []
    for repo in pushed_repos[:MAX_COMMIT_LOOKUPS]:
        msgs = fetch_commit_messages(repo, user, day, token)
        repos[repo]["commits"] = len(msgs)
        commit_messages.extend(f"{repo}: {m}" for m in msgs)

    for repo, number, action in prs[:MAX_PR_LOOKUPS]:
        title = fetch_pr_title(repo, number, token) if number else ""
        highlights.append(f"{repo}: pull request #{number} {action}" + (f" - {title}" if title else ""))

    return {
        "repos": sorted(repos.values(), key=lambda r: -r["commits"])[:8],
        "commit_messages": commit_messages[:40],
        "highlights": highlights[:20],
        "commit_count": len(commit_messages),
        "repo_count": len(repos),
    }


# --------------------------------------------------------------------------
# Memory
# --------------------------------------------------------------------------

def load_style() -> dict:
    item = table.get_item(Key={"pk": f"USER#{GITHUB_USER}", "sk": "STYLE"}).get("Item") or {}
    return {
        "likes": [str(x) for x in item.get("likes", [])],
        "dislikes": [str(x) for x in item.get("dislikes", [])],
        "up": int(item.get("up", 0)),
        "down": int(item.get("down", 0)),
    }


def load_recent(limit: int = RECENT_FOR_PROMPT) -> list[dict]:
    resp = table.query(
        KeyConditionExpression=Key("pk").eq(f"USER#{GITHUB_USER}")
        & Key("sk").begins_with("ENTRY#"),
        ScanIndexForward=False,
        Limit=limit,
    )
    return resp.get("Items", [])


# --------------------------------------------------------------------------
# Generation
# --------------------------------------------------------------------------

SYSTEM = """You are Commit Muse, a nightly journal keeper for one developer.

You are handed a factual digest of what they did on GitHub today. You write a
short, warm, specific journal entry about that day - the kind of thing a good
colleague would say over a beer, not a changelog.

Hard rules:
- Never invent work. Every concrete claim must trace back to the digest.
- If the digest is thin, say so plainly. A quiet day is a real day.
- No hype, no "revolutionary", no exclamation marks stacked up.
- Name real repositories and real commit subjects when they matter.
- The journal must be 4 to 7 full sentences, 110-180 words in English, and the
  Japanese must be a real translation of it - same length, same detail.

Reply with a single JSON object and nothing else:
{
  "title": "short evocative title, max 8 words, English",
  "headline": "one sentence, max 20 words, English",
  "journal_en": "the journal entry in English",
  "journal_ja": "the same journal entry written naturally in Japanese",
  "mood": "one lowercase word",
  "tags": ["3-5", "lowercase", "topic", "tags"]
}"""


def build_prompt(day: str, digest: dict, style: dict, recent: list[dict]) -> str:
    parts = [f"Date: {day}", f"Developer: {GITHUB_USER}", "", "Today's digest:", json.dumps(digest, ensure_ascii=False, indent=2)]

    if recent:
        parts += ["", "Titles you already used recently (do not repeat them or their angle):"]
        parts += [f"- {r.get('title', '')}" for r in recent]

    if style["likes"] or style["dislikes"]:
        parts += ["", "What this reader has told you, in their own words:"]
        parts += [f"- more of this: {s}" for s in style["likes"][-6:]]
        parts += [f"- less of this: {s}" for s in style["dislikes"][-6:]]

    if digest["commit_count"] == 0 and not digest["highlights"]:
        parts += ["", "There was no public activity today. Write the quiet-day entry: honest, short, no invented work."]

    return "\n".join(parts)


REQUIRED = ("title", "headline", "journal_en", "journal_ja", "mood", "tags")


def parse_and_validate(raw: str, min_words: int, avoid_titles: set[str]) -> dict:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text.strip())
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object in model output")
    data = json.loads(text[start : end + 1])

    missing = [k for k in REQUIRED if not data.get(k)]
    if missing:
        raise ValueError(f"missing fields: {missing}")
    if len(data["journal_en"].split()) < min_words:
        raise ValueError(f"journal_en too short (want {min_words}+ words)")
    if len(data["journal_ja"]) < min_words * 2:
        raise ValueError("journal_ja too short")
    if data["title"].strip().lower() in avoid_titles:
        raise ValueError(f"title '{data['title']}' was already used - pick a different angle")
    if not isinstance(data["tags"], list) or not 2 <= len(data["tags"]) <= 6:
        raise ValueError("tags must be a list of 2-6 items")

    return {
        "title": str(data["title"])[:120],
        "headline": str(data["headline"])[:240],
        "journal_en": str(data["journal_en"])[:3000],
        "journal_ja": str(data["journal_ja"])[:3000],
        "mood": str(data["mood"]).lower()[:24],
        "tags": [str(t).lower()[:24] for t in data["tags"]][:6],
    }


def generate(prompt: str, min_words: int, avoid_titles: set[str]) -> dict:
    last_error: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        message = prompt if attempt == 1 else (
            f"{prompt}\n\nYour previous reply was rejected ({last_error}). "
            "Return only the JSON object, complete and valid."
        )
        resp = bedrock.converse(
            modelId=INFERENCE_PROFILE_ID,
            system=[{"text": SYSTEM}],
            messages=[{"role": "user", "content": [{"text": message}]}],
            inferenceConfig={"maxTokens": 1600, "temperature": 0.75, "topP": 0.9},
        )
        raw = resp["output"]["message"]["content"][0]["text"]
        try:
            entry = parse_and_validate(raw, min_words, avoid_titles)
            log.info("generated on attempt %d (%s tokens out)", attempt, resp.get("usage", {}).get("outputTokens"))
            return entry
        except Exception as exc:  # noqa: BLE001 - retry on any malformed reply
            last_error = exc
            log.warning("attempt %d rejected: %s", attempt, exc)

    raise RuntimeError(f"model never produced a valid entry: {last_error}")


# --------------------------------------------------------------------------
# Publishing
# --------------------------------------------------------------------------

def publish() -> None:
    resp = table.query(
        KeyConditionExpression=Key("pk").eq(f"USER#{GITHUB_USER}")
        & Key("sk").begins_with("ENTRY#"),
        ScanIndexForward=False,
        Limit=HISTORY_ON_SITE,
    )
    entries = [
        {
            "date": i["date"],
            "title": i["title"],
            "headline": i["headline"],
            "journal_en": i["journal_en"],
            "journal_ja": i["journal_ja"],
            "mood": i.get("mood", ""),
            "tags": [str(t) for t in i.get("tags", [])],
            "stats": {
                "commits": int(i.get("commit_count", 0)),
                "repos": int(i.get("repo_count", 0)),
            },
        }
        for i in resp.get("Items", [])
    ]
    body = json.dumps(
        {
            "user": GITHUB_USER,
            "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
            "entries": entries,
        },
        ensure_ascii=False,
    )
    s3.put_object(
        Bucket=SITE_BUCKET,
        Key="data/entries.json",
        Body=body.encode("utf-8"),
        ContentType="application/json; charset=utf-8",
        CacheControl="public, max-age=60",
    )
    log.info("published %d entries", len(entries))


# --------------------------------------------------------------------------

def write_day(day: str, events: list[dict], style: dict, recent: list[dict], token: str | None) -> dict:
    digest = build_digest(day, events, GITHUB_USER, token)
    log.info("%s: %d commits across %d repos", day, digest["commit_count"], digest["repo_count"])

    # A day with five commits deserves five sentences. A day with nothing to say
    # deserves three. Holding a quiet day to the same word count is how you get
    # an agent that pads - so the floor moves with the material.
    material = digest["commit_count"] + len(digest["highlights"])
    min_words = 60 if material >= 3 else 35
    avoid = {str(r.get("title", "")).strip().lower() for r in recent}

    entry = generate(build_prompt(day, digest, style, recent), min_words, avoid)
    entry.update(
        {
            "pk": f"USER#{GITHUB_USER}",
            "sk": f"ENTRY#{day}",
            "date": day,
            "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
            "commit_count": digest["commit_count"],
            "repo_count": digest["repo_count"],
            "kind": "quiet" if digest["commit_count"] == 0 and not digest["highlights"] else "work",
            "model": INFERENCE_PROFILE_ID,
        }
    )
    table.put_item(Item=entry)
    return entry


def handler(event, context):  # noqa: ANN001
    """Normal run: write today's entry.

    Payload {"backfill": N} instead walks back over the last N days that had
    activity and writes any entry that is missing, so a fresh deployment does
    not start life as an empty page.
    """
    event = event or {}
    token = _github_token()
    events = fetch_events(GITHUB_USER, token)
    days = group_by_day(events)
    style = load_style()

    today = event.get("date") or datetime.now(JST).strftime("%Y-%m-%d")
    backfill = int(event.get("backfill", 0))

    if backfill:
        existing = {i["sk"].split("#", 1)[1] for i in load_recent(limit=60)}
        targets = [d for d in sorted(days, reverse=True) if d not in existing][:backfill]
        targets.reverse()  # oldest first, so each entry can see the ones before it
    else:
        targets = [today]

    written = []
    for day in targets:
        try:
            entry = write_day(day, days.get(day, []), style, load_recent(), token)
            written.append({"date": day, "title": entry["title"], "commits": entry["commit_count"]})
        except Exception:  # noqa: BLE001 - one bad day must not sink the whole backfill
            log.exception("could not write %s, leaving it empty", day)

    if written:
        publish()
    return {"written": written}
