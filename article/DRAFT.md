# Weekend Creative Agent Challenge: Commit Muse

**Tags:** #agents #challenge #amazonbedrock #serverless #aws

---

I have a bad habit. At the end of a long day of merging pull requests and
chasing dependency alerts, I could not have told you what I actually did. The
work was real, the commits were there, but the day itself left no impression.
GitHub remembers every SHA and none of the story.

So I built something that remembers the story for me — and, more importantly,
something I never have to open.

**Commit Muse** is an always-on agent. At 22:00 JST it wakes itself up, reads
what I did on GitHub that day, and writes a short journal entry about it in
English and Japanese. There is no generate button on the site. There never was.
By the time I look, the writing is already done.

## The vision: the tool you never open

Last weekend's challenge was about building a creative app. The thing about a
creative app is that it still waits for you. You open it, you type a prompt, you
press the button. The creativity is on demand, which means it is on *your*
schedule, which means most days it does not happen at all.

Turning it into an agent inverts that. The agent has its own schedule and its
own opinion about what today was about. My job is only to live the day; its job
is to notice it. The website is not an app — it is just a quiet room where the
writing accumulates.

Two rules the agent holds itself to, and they turned out to matter more than
anything else I built:

1. **It never invents work.** Every concrete claim in an entry has to trace back
   to a real commit, pull request, or branch in that day's digest.
2. **It would rather write nothing than write something broken.** An empty day
   is better than a fabricated one.

## How I built it

### The part I got wrong first

My first version read `/users/{user}/events/public`, counted the commits inside
each `PushEvent`, and handed them to the model. It confidently reported **"0
commits"** on a day where I had pushed fourteen times.

GitHub has trimmed those payloads. A `PushEvent` now carries only `before`,
`head`, `ref`, and a couple of ids — no commit messages at all. A
`PullRequestEvent` gives you an action and a number, but no title. All the
*content* has been removed from the content feed.

The fix reframed the whole data layer: treat the events feed as an **index of
where to look**, not as the material itself. So the agent now groups events into
JST calendar days, and for each repository I pushed to that day it goes and asks
the repository directly:

```
GET /repos/{repo}/commits?author={me}&since={day}T00:00:00+09:00&until={day}T23:59:59+09:00
```

That is where the real subjects live. Pull request titles come from one more
lookup each, capped at six a day so a backfill cannot burn the rate limit. Every
one of those calls returns `None` instead of raising on a 4xx — a rate limit or
a repo that went private should never take the whole night down.

### Making a small model behave

I used **Amazon Nova Lite** through the Bedrock `converse` API. It is cheap, it
is fast, and it is perfectly good at this — as long as you do not trust it.

I compared it against Nova 2 Lite on the same day of real data. Nova 2 Lite was
more terse and drifted into first person ("I merged a pull request") even though
the system prompt is written in second person, and it left an English word
untranslated in the Japanese output. Nova Lite matched the brief better, so the
newer model lost on merit. Worth actually measuring rather than assuming.

Everything the model returns goes through a validator before it is allowed to
exist:

- it must parse as JSON and carry all six required fields
- the English must clear a word floor, and the Japanese roughly twice that in
  characters
- **the title must not match a recent title**

That last check earned its place. I originally asked the model in the prompt not
to repeat recent titles, and handed it the list. It cheerfully produced "A Quiet
Night's Prep" twice in one backfill. Asking does not work; rejecting does. In my
final run the guard fired twice and both retries came back with something new.

Three strikes and the day stays empty.

### The floor that moves

My first validator held every entry to the same length, and it started throwing
away quiet days — days where the honest answer really is three sentences. So the
minimum scales with the material:

```python
material = digest["commit_count"] + len(digest["highlights"])
min_words = 60 if material >= 3 else 35
```

A day with five commits owes you five sentences. A day with nothing to say owes
you three. A fixed word count is precisely how you end up with an agent that
pads.

### Learning what I like

The only thing a human can do on the site is vote 👍/👎 and optionally leave one
sentence. That sentence lands in a persistent style memory in DynamoDB and is
folded into the next prompt.

It works, and I can show it. Here is the agent on 2026-08-11, before any
feedback:

> ...You also added a branch to address specific Snyk issues in the
> astro-notion-blog. **It's impressive to see you handling these tasks with such
> precision and care.**

I sent one thumbs down with the note *"less flattery"*. Same day, same digest,
regenerated:

> ...The astro-notion-blog repository also saw attention with a merge fixing 24
> vulnerabilities, ensuring a safer blogging experience. While the
> backlog-tools-portal had fewer interactions, the overall day was filled with
> steady progress.

No fine-tuning. Just memory, fed back into the prompt.

### Starting life with something to show

A fresh deployment has an empty page, which is a sad first impression for an
agent whose whole pitch is "it's already done." So the agent has a backfill
mode: hand it `{"backfill": 6}` and it walks back over the last six days that
had activity, oldest first, and writes any entry that is missing. Oldest first
matters — each entry can then see the ones before it and avoid repeating their
angle.

## AWS services and architecture

```
EventBridge Scheduler ──▶ Lambda (muse) ──▶ GitHub REST API
   (22:00 Asia/Tokyo)          │
                               ├──▶ Amazon Bedrock  (Nova Lite)
                               ├──▶ DynamoDB        (entries + style memory)
                               └──▶ S3 (private bucket)
                                      │
                    CloudFront (OAC) ─┘──▶ reader

reader ──▶ API Gateway (HTTP) ──▶ Lambda (feedback) ──▶ DynamoDB style memory
```

- **Amazon EventBridge Scheduler** — the only thing that starts a run, with a
  timezone-aware cron so 22:00 stays 22:00 across daylight saving.
- **AWS Lambda** (arm64, Python 3.13) — the agent, plus a separate tiny handler
  for feedback. No dependencies beyond the SDK, so there is no build step.
- **Amazon Bedrock / Nova Lite** — called through a cross-region inference
  profile, which is how the Nova models are invoked on demand.
- **Amazon DynamoDB** — one table, `USER#<login>` as the partition key,
  `ENTRY#<date>` and `STYLE` as sort keys.
- **Amazon S3 + CloudFront** — the bucket blocks all public access and has no
  website hosting enabled. CloudFront reaches it through Origin Access Control,
  and the bucket policy only trusts requests carrying this distribution's ARN.
- **Amazon API Gateway (HTTP API)** — exactly one route, `POST /feedback`. There
  is no route anywhere in this system that can make the agent generate. If
  someone found the endpoint, the worst they could do is ask it to write shorter
  sentences.

Everything is one SAM template with per-function least-privilege roles. The
Bedrock policy names the specific model and inference profile ARNs, not `*`.

One more thing, because the input is my own commit messages: emails and anything
shaped like a credential are scrubbed before they reach the model or the public
page. My first redaction rule was "any token of 40+ characters," which promptly
redacted a legitimate branch name (`snyk-fix-799929ff...`). Broad redaction
destroys meaning; the rule is now a short list of specific credential shapes.

## What I learned

**Trimmed APIs change your architecture, not just your parser.** The moment
GitHub stopped shipping commit messages in the events feed, "read the feed"
became "use the feed to decide what to read." Half my build time went here.

**Validation is where agent quality actually lives.** Every genuine improvement
in output came from a rule that rejects a reply, not from a nicer prompt. Prompt
instructions are suggestions; validators are guarantees. The title-dedup check is
the clearest example — the same instruction in the prompt was simply ignored.

**Newer is not better by default.** Nova 2 Lite lost to Nova Lite on my actual
data, on the specific things I cared about. Ten minutes of comparison beat an
assumption.

**Least privilege is easy when you design for it up front.** Writing the read
path and the write path as separate functions with separate roles cost nothing at
the start and would have been an unpleasant refactor later.

**An agent's honesty is a feature you have to build.** "Never invent work" is not
a sentence in a prompt — it is a digest the model cannot see past, a validator
that rejects thin output, and a quiet-day path that says "nothing happened"
without embarrassment.

## Links

- **Live app:** https://d2zeae0indfv6t.cloudfront.net
- **Repo:** https://github.com/midnight480/aws-weekend-challenge-20260821

Built with: Amazon Bedrock (Nova Lite) · AWS Lambda · Amazon DynamoDB · Amazon
EventBridge Scheduler · Amazon S3 · Amazon CloudFront · Amazon API Gateway · AWS
SAM · AWS IAM
