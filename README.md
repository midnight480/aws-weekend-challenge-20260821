# Commit Muse

An always-on agent that reads what you actually did on GitHub each day and
leaves a short journal entry behind. There is no generate button on the site.
There never was — by the time you open it, the writing is already done.

**Live:** https://d2zeae0indfv6t.cloudfront.net

Built for the [AWS Weekend Creative Agent Challenge](https://builder.aws.com/content/3HkL1H9G5DVm7ZtpO8EcOt6jZsV/weekend-challenge-set-your-creative-app-free).

## What it does

At 22:00 JST the agent wakes itself up, pulls the day's GitHub activity, works
out what the day was actually *about*, and writes a 4–7 sentence journal entry
in English and Japanese. It never invents work: every claim it makes traces back
to a real commit, pull request, or branch in that day's digest.

You can tell it what you think — 👍/👎 plus one sentence. That sentence goes into
a persistent style memory and shapes tomorrow's entry.

## Architecture

```
EventBridge Scheduler ──▶ Lambda (muse) ──▶ GitHub REST API
   (22:00 Asia/Tokyo)          │                (events + commits + PRs)
                               ├──▶ Amazon Bedrock  (Nova Lite, converse)
                               ├──▶ DynamoDB        (entries + style memory)
                               └──▶ S3 (private)
                                      │
                    CloudFront (OAC) ─┘──▶ reader

reader ──▶ API Gateway (HTTP) ──▶ Lambda (feedback) ──▶ DynamoDB style memory
```

| Service | Role |
| --- | --- |
| Amazon EventBridge Scheduler | The only thing that starts a run. Timezone-aware cron. |
| AWS Lambda (arm64, Python 3.13) | The agent, and a separate tiny feedback handler. |
| Amazon Bedrock — Nova Lite | Writes the entry, via a cross-region inference profile. |
| Amazon DynamoDB | Journal entries and the style memory the agent learns from. |
| Amazon S3 | Private bucket. No public access, no website hosting. |
| Amazon CloudFront | The only way in, via Origin Access Control. |
| Amazon API Gateway (HTTP API) | One `POST /feedback` route. There is no route that can make it generate. |

## Design decisions worth knowing

**GitHub trimmed its event payloads.** A `PushEvent` from
`/users/{user}/events/public` no longer carries commit messages — just `before`
and `head` SHAs — and a `PullRequestEvent` has no title. The first version of
this agent dutifully reported "0 commits" on a day with fourteen pushes. The fix
is to treat the events feed as an *index* and go ask the repositories themselves
(`/repos/{repo}/commits?author=&since=&until=`) for the content.

**GitHub's idea of "your commits" is narrower than yours.** `?author=<login>`
only returns commits whose email is a verified address on that account — a local
`user.email` typo is enough to make your own work invisible. The agent fetches
unfiltered and matches identity on the commit itself, which took one day from 5
visible commits to 12.

**It would rather write nothing than write something broken.** The model reply
must parse as JSON, carry every required field, be long enough for the day it
describes, and not reuse a recent title. Three strikes and the day stays empty.

**The length floor moves with the material.** A day with five commits owes you
five sentences; a day with nothing to say owes you three. Holding a quiet day to
the same word count is exactly how you get an agent that pads.

**Titles are deduplicated in code, not in the prompt.** Asking the model not to
repeat itself does not work. Rejecting the reply does.

**Commit messages are user data.** Emails and anything shaped like a token are
scrubbed before they reach the model or the public site.

## Deploy

```bash
./scripts/deploy.sh            # sam build + deploy + publish the site
./scripts/run-now.sh                      # write today's entry by hand
./scripts/run-now.sh '{"backfill": 6}'    # fill in the last 6 active days
```

Optional: put a GitHub PAT in an SSM SecureString parameter and pass
`GitHubTokenParam=/commit-muse/github-token` to raise the API rate limit and
include private activity. Without it the agent reads public activity anonymously.

## Cost

Everything is Free Tier eligible. One run a day is roughly 4k input / 400 output
tokens on Nova Lite, a handful of DynamoDB writes, and one small S3 object.
