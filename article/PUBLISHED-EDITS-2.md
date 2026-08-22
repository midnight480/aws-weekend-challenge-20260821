# 記事の追加修正（第2弾）

公開済み記事を確認しました。適用済みの4点のうち3点は反映されていますが、
**必須の1点が未反映**で、さらに私の差分リストの不備で3か所が抜けています。

---

## A. 【必須・未反映】Architecture の API Gateway 行

いま記事にはまだこの文が残っています:

> Amazon API Gateway (HTTP API) — exactly one route, POST /feedback. There
> is no route anywhere in this system that can make the agent generate. If
> someone found the endpoint, the worst they could do is ask it to write shorter
> sentences.

これは新セクションで「この文がバグだった」と書いている当の文なので、
同じ記事の中で矛盾しています。以下に差し替えてください:

- **Amazon API Gateway (HTTP API)** — exactly one route, `POST /feedback`, and
  it accepts only a vote and one slug from a fixed list. No route anywhere in
  this system can make the agent generate, and no caller-supplied text is stored
  anywhere the prompt can reach.

---

## B. 【推奨・私の渡し漏れ】"The part I got wrong second" セクション

`### The part I got wrong first` セクションの**直後**、
`### Making a small model behave` の**直前**に挿入してください。

これが抜けていると、記事本文の

    GET /repos/{repo}/commits?author={me}&since=...&until=...

という記述が**現在のコードと食い違います**（いまは author フィルタを使わず、
取得後に `_is_theirs` で判定しています）。記事とリポジトリの不一致になるので、
入れるか、せめて上の GET 例から `author={me}&` を削るかのどちらかが必要です。

### The part I got wrong second

With commit messages flowing again, I hit a subtler version of the same problem.
I was fetching them with `?author=midnight480`, which seems obviously correct.

It silently skipped the commit I had just pushed.

GitHub only links a commit to an account when the commit's email is a verified
address on that account. My local `user.email` was a placeholder, so GitHub
listed the commit but attributed it to nobody — and `?author=<login>` filtered
out my own work in my own repository.

So the agent stopped delegating the question of identity. It fetches the day's
commits unfiltered and decides for itself:

```python
def _is_theirs(commit, user):
    login = ((commit.get("author") or {}).get("login") or "").lower()
    if login:
        return login == user.lower()          # GitHub knows: trust it
    author = commit.get("commit", {}).get("author", {})
    name = (author.get("name") or "").lower() # GitHub doesn't: look at the commit
    email = (author.get("email") or "").lower()
    return user.lower() in (name, email.split("@")[0])
```

The effect was not marginal. On 2026-08-11 the agent had been seeing 5 commits.
With identity matched on the commit itself, it sees **12** — including the real
ones like `fix(ci): remove continue-on-error from SARIF upload step`, which had
been invisible behind a wall of merge commits.

An agent that reads your work is only as good as its definition of "your".

---

## C. 【推奨・私の渡し漏れ】"What I learned" にもう1項目

`**Newer is not better by default.**` の**直前**に挿入:

**"Filter it server-side" is a decision, not a shortcut.** Every filter you hand
to someone else's API is a definition you have accepted without reading it.
`?author=<login>` sounds like "commits by me" and actually means "commits GitHub
has successfully linked to me," and the gap between those two swallowed more than
half my history.

---

## D. 【推奨・私の渡し漏れ】締めのセクション

`## Links` の**直前**に挿入:

## The first thing it wrote about me

I deployed it, made the repository public, and let the agent run over the day it
was built. It had no idea it was writing about itself:

> Today, midnight480 worked diligently on the 'aws-weekend-challenge-20260821'
> repository, starting with an initial commit to lay the foundation. They also
> added 'Commit Muse,' an intriguing agent designed to journal their GitHub
> activities. This repository has been made public, marking a significant
> milestone. [...] It's a quiet yet purposeful day, with each step meticulously
> documented.

Tomorrow it will do that again, and I will not be there for it. That was the
whole point.
