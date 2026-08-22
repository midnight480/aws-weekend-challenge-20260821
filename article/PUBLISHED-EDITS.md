# 公開済み記事への差し替え箇所

https://builder.aws.com/content/3IG7XGZdDtZjBVgoZxUohVXQQkB/weekend-creative-agent-challenge-commit-muse

3か所です。1と2は事実誤りの修正なので必須、3は追加（記事が強くなります）。

---

## 1. Architecture セクション — 事実と違う記述の差し替え【必須】

**削除する行:**

> - **Amazon API Gateway (HTTP API)** — exactly one route, `POST /feedback`. There
>   is no route anywhere in this system that can make the agent generate. If
>   someone found the endpoint, the worst they could do is ask it to write shorter
>   sentences.

**差し替える行:**

- **Amazon API Gateway (HTTP API)** — exactly one route, `POST /feedback`, and
  it accepts only a vote and one slug from a fixed list. No route anywhere in
  this system can make the agent generate, and no caller-supplied text is stored
  anywhere the prompt can reach.

---

## 2. "Learning what I like" セクション — 冒頭1文の差し替え【必須】

**削除:**

> The only thing a human can do on the site is vote 👍/👎 and optionally leave one
> sentence. That sentence lands in a persistent style memory in DynamoDB and is
> folded into the next prompt.

**差し替え:**

The only thing a human can do on the site is vote 👍/👎 and, optionally, pick a
preference. That preference goes into a persistent style memory in DynamoDB and
is folded into the next prompt.

さらに同セクション内の

> I sent one thumbs down with the note *"less flattery"*.

を

> I sent one thumbs down with *"no flattery"*.

に変更。

---

## 3. "Learning what I like" の直後に新セクションを追加【推奨】

### The vulnerability I shipped, and what fixing it taught me

The first version of that feedback box was a text field. You typed whatever you
wanted, it was stored verbatim, and the next night it went into the prompt under
the heading *"What this reader has told you, in their own words."*

I ran a security review over my own code before writing this section, and that
design does not survive it. The endpoint is a public, unauthenticated HTTP API —
its URL is in the `config.json` that the site itself fetches, so it is trivially
discoverable. The handler validated `vote` against `["up", "down"]` and then
accepted any 200-character string as the note. The prompt read the last six.

So six requests own the entire preference section of the prompt. Not "influence"
— *own*. And the output of that prompt is not private: it is written to
`data/entries.json` and published on a page that carries my name and keeps 30
entries of history. An unauthenticated stranger could steer what my journal says
about my work, and I would not find out until I read it.

The comment I had written above that handler said: *"if someone found this
endpoint, the worst they could do is tell it to write shorter sentences."* That
sentence was the bug. I had reasoned about what a **reasonable** user would send
and written the conclusion down as a security property.

The obvious fix is to put an authorizer on the route. I did something else,
because I think it is the better lesson: **I deleted the text field.**

Feedback is now a closed vocabulary — eight slugs, `shorter`, `less_flattery`,
`more_technical`, and so on. The handler rejects anything else with a 400. The
slugs are not what goes into the prompt either; the agent looks each one up in a
dictionary that lives in its own source file and inserts *its own* sentence:

```python
PREFERENCES = {
    "less_flattery": "Do not compliment the developer. Report the day, do not praise it.",
    "more_technical": "Be more technical: name files, tools, and specific errors.",
    ...
}
```

`load_style` drops any slug that is not a key, so even a stored value that
somehow bypassed the handler cannot put a single character into the prompt.

Authentication would have hidden this problem — the injection channel would
still exist, just behind a login. Removing the free-text field means there is no
channel at all, and the feature the reader actually wanted still works. When
untrusted input has to reach an LLM prompt, narrowing the vocabulary beats
guarding the door.

---

## 4. "What I learned" に1項目追加【推奨】

`**Least privilege is easy when you design for it up front.**` の直前に挿入:

**A comment asserting a security property is not a security property.** I wrote
"the worst they could do is tell it to write shorter sentences" above a public
unauthenticated endpoint that fed an LLM prompt whose output gets published under
my name. I had modelled the polite user, not the attacker, and then written my
conclusion down as if it were a fact. Reviewing my own code against that comment
is what found it.
