# lark-channel-sdk · 官方文档合集

> 来源：`github.com/larksuite/channel-sdk-python` 分支 `main` 的 `docs/` 目录，逐文件拼接而成。
> 由 `common/skills/lark-channel-docs-sync/update_docs.py` 生成，请勿手工编辑。

## 目录

- [cardkit-streaming.md](#doc-cardkit-streaming)
- [dedup-architecture.md](#doc-dedup-architecture)
- [markdown.md](#doc-markdown)
- [meeting-channel.md](#doc-meeting-channel)
- [migration-from-lark-oapi.md](#doc-migration-from-lark-oapi)
- [quickstart.md](#doc-quickstart)
- [reference.md](#doc-reference)
- [release-notes/v1.0.0.md](#doc-release-notes-v1-0-0)
- [release-notes/v1.1.0.md](#doc-release-notes-v1-1-0)
- [security.md](#doc-security)
- [webhook-server.md](#doc-webhook-server)

<a id="doc-cardkit-streaming"></a>

---

## cardkit-streaming.md

> 来源：`docs/cardkit-streaming.md`

## Streaming with CardKit

For normal markdown streaming, prefer the high-level `channel.stream(...)`
helper:

```python
async def produce(stream):
    for chunk in ["hello", " ", "world"]:
        await stream.append(chunk)

await channel.stream(chat_id, {"markdown": produce}, {"reply_to": message_id})
```

`channel.stream(...)` owns CardKit preallocation, throttling, and the final
`finish_streaming_card(...)` call on normal completion or ordinary producer
errors. If the streaming task itself is cancelled, cancellation is propagated to
the caller. Use the lower-level methods below only when you need custom CardKit
control.

## Low-level APIs

| Method                                                                              | Purpose                                             |
| ----------------------------------------------------------------------------------- | --------------------------------------------------- |
| `await channel.create_card_instance(spec)`                                          | Allocate a `card_id` from a card JSON spec          |
| `await channel.send_card_by_reference(to, card_id, ...)`                            | Send a message that points to the preallocated card |
| `await channel.update_card_element_content(card_id, element_id, content, sequence)` | Patch one element's text during streaming           |
| `await channel.finish_streaming_card(card_id, sequence)`                            | Close `streaming_mode` so users see the final card  |

The high-level `channel.stream(..., {"markdown": producer}, ...)` path wraps
these methods through `MarkdownStreamController` and is the recommended public
API for token streaming.

## Required Permissions

A bot must have the required message and CardKit scopes enabled before calling
CardKit APIs. Scope names can vary by tenant UI; verify the exact names in the
Feishu developer console.

| Scope name (zh-CN)       | Scope ID                 | Used by                                                |
| ------------------------ | ------------------------ | ------------------------------------------------------ |
| 发送消息                 | `im:message:send_as_bot` | `send_card_by_reference`                               |
| 获取与发送单聊、群组消息 | `im:message`             | Inbound and outbound message operations                |
| 创建卡片实体             | `cardkit:card:write`     | `create_card_instance`                                 |
| 更新卡片实体             | `cardkit:card`           | `update_card_element_content`, `finish_streaming_card` |

If your tenant still exposes the legacy `cardkit:card:read` /
`cardkit:card:update` split, enable both. After changing scopes, re-install the
bot into the tenant; existing tokens do not pick up new scopes.

## Sequence Semantics

`update_card_element_content(card_id, element_id, content, sequence)` carries a
strictly increasing `sequence` number per `card_id`.

- The first patch must have `sequence >= 1`.
- Each subsequent patch must have `sequence >` the previous one.
- Gaps are allowed, for example `1, 3, 5`.
- `finish_streaming_card(card_id, sequence)` follows the same rule: its
  `sequence` must exceed the largest sequence used in any update call for that
  card.

Recommended pattern:

```python
seq = 0

async def patch(text):
    nonlocal seq
    seq += 1
    await channel.update_card_element_content(card_id, "main", text, sequence=seq)

# ... stream tokens, calling patch() ...

seq += 1
await channel.finish_streaming_card(card_id, sequence=seq)
```

## `finish_streaming_card` vs `update_card`

These methods are not interchangeable.

| Method                                     | When to use               | What it does                                                                                                |
| ------------------------------------------ | ------------------------- | ----------------------------------------------------------------------------------------------------------- |
| `finish_streaming_card(card_id, sequence)` | Streaming output complete | Sets `config.streaming_mode = false` on the preallocated card.                                              |
| `update_card(message_id, card)`            | One-shot card replacement | Replaces the whole card payload of a sent message. Uses `message_id`, not `card_id`, and has no `sequence`. |

If you need to update a card after `finish_streaming_card`, use
`update_card(message_id, card)` with the `message_id` returned from
`send_card_by_reference(...)`, not the `card_id`.

## API Error Hints

Low-level CardKit methods raise `FeishuChannelError(code=unknown, ...)` for
non-zero CardKit responses in this release. Inspect the raw API response inside
the exception message and use the upstream API code as a troubleshooting hint.

| API code                | Likely cause                               | Fix                                           |
| ----------------------- | ------------------------------------------ | --------------------------------------------- |
| `99991672` / `99991679` | Missing create-card scope                  | Add scope and re-install bot                  |
| `99991680` / `99991681` | Missing update-card scope                  | Add scope and re-install bot                  |
| `230099`                | `sequence` regressed or was reused         | Reset the stream and allocate a new `card_id` |
| `230001`                | Card JSON spec malformed                   | Validate against CardKit 2.0 schema           |
| `230002` / `230020`     | Card already finished, or message recalled | Allocate a new `card_id`                      |

## Core Flow Example

This snippet focuses on the CardKit calls. It assumes `channel` is already
connected and `chat_id` is known.

```python
card_id = await channel.create_card_instance({
    "schema": "2.0",
    "config": {"streaming_mode": True, "summary": {"content": ""}},
    "body": {
        "elements": [
            {"tag": "markdown", "element_id": "main", "content": "..."},
        ],
    },
})

send_result = await channel.send_card_by_reference(chat_id, card_id)
if not send_result.success:
    raise RuntimeError(send_result.error)

seq = 0
accumulated = ""
for token in ["hello", " ", "world"]:
    accumulated += token
    seq += 1
    await channel.update_card_element_content(
        card_id,
        "main",
        accumulated,
        sequence=seq,
    )

seq += 1
await channel.finish_streaming_card(card_id, sequence=seq)
```

Return to the [project README](../README.md).

<a id="doc-dedup-architecture"></a>

---

## dedup-architecture.md

> 来源：`docs/dedup-architecture.md`

## Channel SDK: Two-layer Dedup Architecture

This document is an advanced architecture note for applications that need to
customize Channel dedup state. Most bots can use the defaults.

Channel has two dedup layers:

1. **Pipeline layer**: `InboundPipeline` uses a `DedupStore` before full message
   normalization. It catches webhook retries and WebSocket reconnect backfill.
2. **Safety layer**: `SafetyPipeline` uses `SeenCache` before dispatching to
   user handlers. It catches duplicate handler dispatches and can optionally
   consult a shared `ICache`.

The two layers use different protocols because they run at different points in
the pipeline.

## Pipeline Layer: `DedupStore`

`DedupStore` is defined in `lark_channel.channel.normalize.dedup` and re-exported
from `lark_channel.channel`.

```python
from typing import Protocol, runtime_checkable

@runtime_checkable
class DedupStore(Protocol):
    def seen(self, key: str) -> bool: ...
    def mark(self, key: str, ttl_seconds: int) -> None: ...
```

Contract:

- `seen(key)` returns `True` if the key is still considered seen.
- `mark(key, ttl_seconds)` records the key with the TTL supplied by the SDK.
- Implementations should be thread-safe.
- TTL behavior is part of the protocol. Capacity limits and LRU eviction are
  implementation choices, not SDK-enforced protocol methods.

Key helpers:

```python
from lark_channel import make_event_key, make_message_key

make_event_key("cli_xxx", "evt_xxx")    # "evt:cli_xxx:evt_xxx"
make_message_key("cli_xxx", "om_xxx")   # "msg:cli_xxx:om_xxx"
```

Inject a custom store with `dedup_store=...`:

```python
from lark_channel import FeishuChannel

channel = FeishuChannel(
    app_id="cli_xxx",
    app_secret="***",
    dedup_store=my_dedup_store,
)
```

## Safety Layer: `ICache`

`SeenCache` can use an optional `lark_channel.core.cache.ICache` implementation.
The current `ICache` interface is synchronous:

```python
class ICache:
    def get(self, key: str) -> str: ...
    def set(self, key: str, value: str, expire: int): ...
```

`expire` is a Unix timestamp in seconds.

The current `ICache` does not expose an atomic `SETNX` primitive. With multiple
workers, shared-cache dedup is best-effort rather than a strict cross-process
coherence boundary. Safe patterns:

- Route events for one app to a single worker.
- Make handlers idempotent on event/message ids.

Inject a shared cache with `safety_cache=...`:

```python
from lark_channel import FeishuChannel

channel = FeishuChannel(
    app_id="cli_xxx",
    app_secret="***",
    safety_cache=my_cache,
)
```

## Configuration Notes

`DedupConfig.ttl_seconds`, `max_entries`, and `sweep_seconds` are used by the
default in-memory stores. When you pass a custom `dedup_store`, the SDK only
passes `ttl_seconds` into `mark(key, ttl_seconds)`; your store owns any capacity
and eviction behavior.

In this release, `DedupConfig.enabled` controls the pipeline-layer `Deduper`.
The safety layer still runs `SeenCache` dedup.

```python
from lark_channel import DedupConfig, FeishuChannel, SafetyConfig

channel = FeishuChannel(
    app_id="cli_xxx",
    app_secret="***",
    safety=SafetyConfig(
        dedup=DedupConfig(
            ttl_seconds=12 * 3600,
            max_entries=5000,
            sweep_seconds=5 * 60,
        ),
    ),
)
```

## Example: JSON File Store

This example persists pipeline-layer dedup state across process restarts. It is
not shipped as an SDK class.

```python
import json
import threading
import time
from collections import OrderedDict
from pathlib import Path

class JsonFileDedupStore:
    def __init__(self, path: Path, *, max_entries: int = 5000) -> None:
        self._path = Path(path)
        self._max = max_entries
        self._lock = threading.Lock()
        self._data = OrderedDict()
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text())
            now = time.time()
            self._data = OrderedDict(
                (k, exp)
                for k, exp in raw.items()
                if isinstance(exp, (int, float)) and exp > now
            )
        except (json.JSONDecodeError, OSError):
            self._data = OrderedDict()

    def _persist_locked(self) -> None:
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(dict(self._data)))
        tmp.replace(self._path)

    def seen(self, key: str) -> bool:
        with self._lock:
            exp = self._data.get(key)
            if exp is None:
                return False
            if exp <= time.time():
                self._data.pop(key, None)
                return False
            self._data.move_to_end(key)
            return True

    def mark(self, key: str, ttl_seconds: int) -> None:
        with self._lock:
            self._data[key] = time.time() + ttl_seconds
            self._data.move_to_end(key)
            while len(self._data) > self._max:
                self._data.popitem(last=False)
            self._persist_locked()
```

## Example: Redis `ICache`

This example adapts Redis to the safety-layer `ICache` shape. It is
best-effort because the SDK calls `get()` and `set()` separately.

```python
import time

import redis

class RedisICache:
    def __init__(self, client: redis.Redis, *, prefix: str = "feishu:seen:") -> None:
        self._client = client
        self._prefix = prefix

    def get(self, key: str):
        value = self._client.get(self._prefix + key)
        return value.decode() if value else None

    def set(self, key: str, value: str, expire: int):
        ttl = max(1, int(expire - time.time()))
        self._client.set(self._prefix + key, value, ex=ttl)
```

Return to the [project README](../README.md).

<a id="doc-markdown"></a>

---

## markdown.md

> 来源：`docs/markdown.md`

## Markdown to Post Conversion

Channel sends `{"markdown": ...}` and bare string messages as Feishu post
messages. The SDK converts markdown into a post AST before calling the message
API.

If you want a plain text message, send `{"text": "..."}` explicitly.

## Configuration

```python
from lark_channel import FeishuChannel, MarkdownConverter, OutboundConfig

channel = FeishuChannel(
    app_id="cli_xxx",
    app_secret="***",
    outbound=OutboundConfig(
        markdown_converter=MarkdownConverter(tag_md_mode="native"),
    ),
)
```

## Modes

| Mode               | When to use                                                                                     | Rendering behavior                                                                                                                                                                                                  |
| ------------------ | ----------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `native` (default) | Richer user-facing markdown rendering in Feishu clients                                         | Wraps markdown into `tag:md` nodes and lets the Feishu client render it. Headings, quotes, and lists render closer to native markdown, but exact output depends on client version.                                  |
| `structured`       | Deterministic rendering across clients, code blocks, links, and SDK-side wire-format assertions | Parses markdown into explicit post nodes such as `tag:text`, `tag:a`, and `tag:code_block`. Feishu post has no native heading, blockquote, or nested-list nodes, so those constructs are flattened or approximated. |

`MarkdownConverter.enabled` exists for compatibility with the config schema. Do
not rely on `enabled=False` to send plain text; use `{"text": ...}` instead.

## Choosing a Mode

- Use `structured` when you need predictable cross-client output or testable
  post ASTs.
- Use `native` when user-facing markdown structure matters more than exact
  cross-client parity.
- Use `{"text": ...}` for plain text.

## Native Mode Notes

- Rendering is delegated to the Feishu client markdown parser.
- `OutboundPost(post=prebuilt_ast)` is passed through and is not affected by
  `tag_md_mode`.
- Structured mentions are inserted as post `tag:at` nodes in the first row.
  They are not written as literal `<at>` text inside a `tag:md` string.

## Wire Format Comparison

Input:

````
# Hello

> world

```python
print("hi")
```
````

`tag_md_mode="structured"`:

```json
{
  "zh_cn": {
    "title": "",
    "content": [
      [{ "tag": "text", "text": "Hello", "style": ["bold"] }],
      [
        { "tag": "text", "text": "│ " },
        { "tag": "text", "text": "world" }
      ],
      [{ "tag": "code_block", "language": "PYTHON", "text": "print(\"hi\")" }]
    ]
  }
}
```

`tag_md_mode="native"`:

````json
{
  "zh_cn": {
    "title": "",
    "content": [
      [{ "tag": "md", "text": "# Hello\n\n> world\n" }],
      [{ "tag": "md", "text": "```python\nprint(\"hi\")\n```" }]
    ]
  }
}
````

## Editing Messages

`FeishuChannel.edit_message(message_id, message)` accepts the same high-level
outbound shapes as `send()` for editable text/post messages:

```python
await channel.edit_message(message_id, "# Markdown heading")
await channel.edit_message(message_id, {"markdown": "**bold**"})
await channel.edit_message(message_id, {"text": "plain text"})
await channel.edit_message(message_id, {"post": prebuilt_post_ast})
```

Cards are updated with `update_card(message_id, card)`, not `edit_message()`.
Media, share, and sticker messages are not editable through `edit_message()`.

## Image and Video Captions

Images and videos can include an optional markdown caption:

```python
await channel.send(chat_id, {"image": {"source": image_url}, "caption": "Generated screenshot"})
await channel.send(chat_id, {"video": {"source": video_bytes}, "caption": "Demo clip"})
```

When no caption is provided, image/video messages use the normal `image` or
`media` message type. With a caption, the SDK sends a single post message that
contains the rendered caption followed by an image or video node. Caption
markdown follows `OutboundConfig.markdown_converter`.

In this release, captions are supported for image and video messages only.
`caption` on file or audio dictionary inputs is rejected with `format_error`
before upload. Send the caption as a separate message if two-message semantics
are acceptable.

Return to the [project README](../README.md).

<a id="doc-meeting-channel"></a>

---

## meeting-channel.md

> 来源：`docs/meeting-channel.md`

## Meeting channel

Agents that perceive and respond **inside a live meeting**: captions, meeting
chat, participants arriving and leaving, documents being shared.

Two entry points, one session type. Moving from one to the other changes the
entry-point line and nothing else.

|                          | `follow_my_meeting` — as the user | `join_meeting` — as the bot                             |
| ------------------------ | --------------------------------- | ------------------------------------------------------- |
| Visible in the meeting   | no                                | yes, a real participant                                 |
| Credential               | the user's own access token       | the app's tenant token                                  |
| How content arrives      | polling `bots/events`             | pushed `vc.bot.meeting_activity_v1`                     |
| Needs `connect()`        | **no** — REST only                | **yes** — activity is pushed                            |
| Can speak in the meeting | no, reply over IM                 | yes, `send_message`                                     |
| Scope                    | `vc:meeting.meetingevent:read`    | `vc:meeting.bot.join:write`, `vc:meeting.message:write` |

Both require the meeting's "allow agents to join" setting and Feishu client
7.68 or later. Joining as the bot is gated behind an application process.

## Joining a meeting

```python
channel = FeishuChannel(app_id=..., app_secret=...)

async def on_invited(invitation):
    session = await channel.join_meeting(invitation.meeting_no)

    def on_chat(event):
        if event.self_echo:
            return          # see "Echoes" below — this line is load-bearing
        ...

    session.on("chat", on_chat)

channel.on("meetingInvited", on_invited)
await channel.connect()
```

## Following a meeting

```python
session = await channel.follow_my_meeting(user_open_id="ou_...")
session.on("transcript", lambda e: notes.append(e.text))
```

No `connect()` needed. Read [Who the ticket belongs to](#who-the-ticket-belongs-to)
before wiring the `user_open_id`.

## Session events

`transcript` · `chat` · `participant` · `share` · `document_context` · `end` ·
`error`. `on()` is multicast and returns an unsubscribe.

```python
off = session.on("transcript", handler)
off()
```

`document_context` carries **identifiers only** — a comment id, an element
token. Fetching the comment body or the asset is your job, within the shared
document's temporary grant, with permissions you applied for.

### Ordering, and the price of it

Events are delivered **in order**, and each handler is awaited before the next
event. Order is the meaning of some of them: swapping the shared document
arrives as `magic_share_ended` then `magic_share_started`, and reordering makes
you reconstruct the wrong document.

The price:

- a handler that `await`s and takes a long time holds up **this meeting's**
  stream;
- a handler that blocks **without** awaiting (`time.sleep`, a synchronous HTTP
  call, heavy CPU) holds up **the entire process** — every meeting, the message
  path, the socket heartbeat. There is one thread. Hand blocking work to an
  executor.

### Captions

The protocol has no "final" marker, so a later item with the same
`sentence_id` supersedes the earlier text. Upsert on `sentence_id`.

`MeetingOptions(stabilize_seconds=...)` debounces instead: `0.0` (the default)
delivers every revision; a positive value delivers a sentence once, after it
stops changing for that long — at the cost of one window of latency, and of a
sentence never settling while somebody keeps talking.

### Echoes

The bot's own contributions come back around: an in-meeting message is pushed
back as meeting chat. Those arrive with `self_echo=True` and **are still
delivered** — a full record wants the bot's own turns.

So `if event.self_echo: return` is not boilerplate. Without it, a handler that
replies to chat replies to itself, at network speed. The SDK's backstop is
`meeting.send_rate_limit_per_minute` (default 20), after which `send_message`
raises `rate_limited`.

When the bot's own id is not yet known, `self_echo` is `True` — "maybe" has to
read as "yes", because `False` means "definitely not me" and would let the loop
close.

## Leaving versus disposing

|                         | Departs the meeting | Use for                         |
| ----------------------- | ------------------- | ------------------------------- |
| `await session.leave()` | **yes**             | you are done, the meeting ended |
| `session.dispose()`     | **no**              | reconnects, in-process cleanup  |

`dispose()` deliberately does not depart: a reconnect must not make the bot
vanish from every meeting it is in. The flip side is that **the bot stays a
participant**, so:

- `channel.disconnect()` disposes sessions — it does not leave meetings;
- before the process exits, `leave()` every live session, or the bot sits in
  those meetings until the server ends them. `leave()` keeps working after
  `dispose()` precisely so this is possible.

Both are idempotent.

## Limits and reclamation

```python
FeishuChannel(
    app_id=..., app_secret=...,
    meeting=MeetingChannelConfig(
        max_concurrent_sessions=32,
        idle_timeout_seconds=0.0,
        liveness_probe_interval_seconds=300.0,
        send_rate_limit_per_minute=20,
    ),
)
```

Sessions are created **from outside** your process — joining starts at an
invitation from anybody who can add the bot to a meeting. Hence a ceiling,
shared by both entry points.

A seat is released when there is evidence the bot is no longer a participant:
a clean departure, a departure rejected because the meeting is gone, a
meeting-ended event, or a probe confirming absence. An inconclusive departure
(5xx, timeout) keeps the seat, and the next `join_meeting` /
`follow_my_meeting` retries it before comparing the ceiling.

`idle_timeout_seconds` is **off by default**: the liveness probe already
catches the bot being removed, so what is left for idle reclamation is a
meeting where nobody happens to be talking — and walking out of that one is
visible and wrong. Turning it on makes the session depart the meeting.

One caveat if you leave it off: idle reclamation is also the only backstop for
a wedged handler. **If your handlers can block for a long time, set a positive
value.**

## Diagnosing silence

Failures on this path are silent by nature — an undeclared subscription, a
missing permission and a renamed field all look like "nothing happened".

```python
health = channel.get_meeting_event_health()
```

| Symptom                                            | Reading                                                                               | Where to look                                                        |
| -------------------------------------------------- | ------------------------------------------------------------------------------------- | -------------------------------------------------------------------- |
| the type is absent from `stats`                    | the platform never sent it                                                            | meeting setting, subscription declaration, is the bot in the meeting |
| `empty == received`                                | nothing could be unpacked                                                             | field names or structure changed                                     |
| `0 < empty < received`                             | some could not                                                                        | an uncovered sub-type                                                |
| `liveness.consecutive_unknown` climbing            | the probe never concludes                                                             | its permission assumption may not hold in this tenant                |
| `membership.held` never falling                    | seats are not coming back                                                             | `released_by_evidence` shows why                                     |
| `dropped` climbing                                 | a handler is slower than the meeting, so its session's delivery queue hit its ceiling | the handler — and move blocking work to an executor                  |
| `TOO_MANY_SESSIONS` while `membership.held` is `0` | the seat is held by a live session, not by server-side membership                     | `sessions` — `held` counts only the latter                           |

## When a handler cannot keep up

Delivery is serial and awaited, so a handler that yields but takes a long time
makes its session's queue grow at whatever rate the meeting produces activity.
That queue has a ceiling; past it, the newest event is refused and counted in
`dropped`.

The newest is refused rather than the oldest evicted, because order is meaning
here — a document swap arrives as `magic_share_ended` then
`magic_share_started`, and dropping from the front would split such a pair and
leave a queue that still looks complete. Refusing at the tail keeps what is
queued contiguous, so a gap is a gap at the end and `dropped` says so.

Error reports have their own headroom above the ceiling: they are what explain
why a session went away, and a ceiling full of transcripts must not be able to
drop the explanation.

## Untrusted content

`transcript.text`, `chat.content`, `topic`, `actor.name`, `doc.url`,
`doc.title` and the `document_context` sub-objects are written by meeting
participants, who may be external or guest users. Escape them before rendering
and never concatenate them into a log line.

Prompt injection is a residual risk that no SDK layer can remove — the whole
point of this feature is feeding meeting speech to a model. `actor` and
`self_echo` give you the minimum needed to tier trust; the rest is your call.

## Subscribing to unwrapped events

```python
off = channel.on_raw_event("vc.bot.meeting_started_v1", handler)
```

Different from the `"raw"` event, which mirrors already-wrapped events and is
controlled by `inbound.emit_raw_events`. This subscribes to types the channel
does not wrap, and ignores that switch.

The payload is authentic — this runs after signature verification and
decryption — but **unredacted**, and this path sits **outside the safety
pipeline**: no policy gate, no dedup, no processing lock, no loop guard.

> **Subscribing to a type the channel already handles opens an unpoliced path
> into that type.** With `dm_policy="allowlist"` set, a raw subscription to
> `im.message.receive_v1` still receives direct messages from everybody, and a
> redelivered event runs your handler again. That is what an escape hatch is —
> but know that you have opened one.

## Errors

`not_supported` (`send_message` while only following), `meeting_not_found`,
`too_many_sessions`, plus the existing `rate_limited`, `not_connected` and the
rest.

`FeishuChannelError.context` may carry `console_url` — a **signed one-click
authorization link**. Treat it as a credential: do not echo it into a chat, a
web page or a support ticket.

Failures inside a session go to the session's `error` event. With no handler
registered they are logged instead, minimally and without `context`.

## Who the ticket belongs to

`follow_my_meeting(user_open_id=...)` reads a meeting under **that user's**
authorization. The SDK receives a string; it cannot check whose it is, the
ticket store is shared across the process, and a cached ticket resolves
**without notifying its owner**.

So `user_open_id` must be somebody you have already established is the
requester. Passing a value taken from an inbound message means listening in on
someone else's meeting with their authorization, invisibly. `prompt_context`
must belong to the same person — pairing one person's `user_open_id` with
another's context sends the authorization card to the wrong person and files
the resulting ticket under the first.

`meeting.follow_allowlist` is the available gate; `meeting.invite_allowlist` is
its counterpart on the join side. Both default to open, because a closed
default would make the feature unusable out of the box.

See [Security configuration](#doc-security) for the full picture.

<a id="doc-migration-from-lark-oapi"></a>

---

## migration-from-lark-oapi.md

> 来源：`docs/migration-from-lark-oapi.md`

## Migration from lark_oapi.channel

This guide covers moving Channel bot code from the monolithic
`larksuite/oapi-sdk-python` repository to the standalone `lark-channel-sdk`
package.

## What Changes

| Area                 | Old package             | Standalone package                 |
| -------------------- | ----------------------- | ---------------------------------- |
| Distribution         | `lark-oapi`             | `lark-channel-sdk`                 |
| Primary import path  | `lark_oapi.channel`     | `lark_channel`                     |
| Main entry point     | `FeishuChannel`         | `FeishuChannel`                    |
| Full OpenAPI surface | Included in `lark-oapi` | Keep using `lark-oapi` when needed |

The standalone package is designed to install alongside `lark-oapi`. Use
`lark-channel-sdk` for Channel bot workflows. Keep `lark-oapi` in the same
environment if your application also imports unrelated OpenAPI resources.

## Install

```bash
pip install lark-channel-sdk
```

Optional framework extras are available for webhook adapters:

```bash
pip install "lark-channel-sdk[aiohttp]"
pip install "lark-channel-sdk[fastapi]"
pip install "lark-channel-sdk[flask]"
```

## Update Imports

Replace legacy Channel imports with the standalone root package:

```python
from lark_channel import FeishuChannel
```

Import public Channel configuration and types from the same package root:

```python
from lark_channel import (
    FeishuChannel,
    InboundConfig,
    OutboundConfig,
    PolicyConfig,
    SafetyConfig,
    SecurityConfig,
)
```

Avoid importing Channel symbols from internal module paths. The package root is
the documented stable import surface.

## Constructor and Runtime Compatibility

Most existing `FeishuChannel` constructor fields continue to map directly:

- `app_id`, `app_secret`, `domain`, `log_level`;
- `encrypt_key`, `verification_token`;
- `transport="ws"` or `transport="webhook"`;
- policy, safety, inbound, outbound, media cache, token store, and dedup store
  configuration.

The default `domain` is `https://open.feishu.cn`. Lark tenants should pass
`domain="https://open.larksuite.com"` or keep their existing custom domain
override.

The standalone package also exposes `SecurityConfig`. It defaults to
`mode="compat"` for migration safety. Use `mode="audit"` to observe
security-sensitive legacy behavior before moving production traffic to
`mode="strict"`.

```python
from lark_channel import FeishuChannel, SecurityConfig

channel = FeishuChannel(
    app_id="cli_xxx",
    app_secret="***",
    security=SecurityConfig(mode="audit"),
)
```

## WebSocket Bots

WebSocket bots can usually migrate by changing imports and package
installation. Keep the same event subscriptions, credentials, and app
permissions.

```python
import asyncio

from lark_channel import FeishuChannel

channel = FeishuChannel(app_id="cli_xxx", app_secret="***")


async def on_message(msg):
    await channel.send(msg.chat_id, {"text": f"echo: {msg.content_text}"})


channel.on("message", on_message)
asyncio.run(channel.connect())
```

In WebSocket mode, the SDK requests `domain + "/callback/ws/endpoint"` to get
the server-provided WebSocket connection URL. Applications do not need to expose
their own WebSocket route.

## Webhook Bots

Webhook bots still own the HTTP server in the application or gateway layer.
Create the channel with `transport="webhook"` and pass request headers and body
bytes to `handle_webhook_request(...)`.

```python
from lark_channel import FeishuChannel

channel = FeishuChannel(
    app_id="cli_xxx",
    app_secret="***",
    encrypt_key="...",
    verification_token="...",
    transport="webhook",
)
```

See [Webhook server adapter](#doc-webhook-server) for aiohttp and FastAPI
examples.

Webhook routes remain application-owned. Keep or choose your HTTP path, expose
it publicly through your web framework or gateway, and configure that complete
callback URL in the developer console.

## OpenAPI Calls Outside Channel

The standalone package includes only the OpenAPI models and resources needed by
Channel workflows. If your application uses unrelated OpenAPI resources, keep
those imports on `lark-oapi` and use `lark-channel-sdk` only for Channel
workflows.

## Migration Checklist

- Install `lark-channel-sdk`.
- Replace legacy Channel imports with `lark_channel` imports.
- Keep `lark-oapi` installed if the application uses non-Channel OpenAPI
  resources.
- Run your bot test suite with `SecurityConfig(mode="compat")`.
- Run staging traffic with `SecurityConfig(mode="audit")` and review audit
  events.
- Move to `SecurityConfig(mode="strict")` after webhook signatures, WebSocket
  endpoint behavior, and token-cache behavior are verified.
- Rebuild and verify package metadata with `python -m build` and
  `python -m twine check dist/*` before publishing your own downstream package.

## Related Documents

- [Quickstart](#doc-quickstart)
- [Channel reference](#doc-reference)
- [Security configuration](#doc-security)

<a id="doc-quickstart"></a>

---

## quickstart.md

> 来源：`docs/quickstart.md`

## Channel Quickstart

This guide gets a minimal Channel echo bot running. For the full API surface,
see the [Channel reference](#doc-reference).

## Install

```bash
pip install lark-channel-sdk
```

## Prepare the Bot

In the Feishu developer console:

- Create a bot application.
- Enable event subscriptions.
- Enable the WebSocket event subscription channel for local development.
- Subscribe to message receive events.
- Grant bot message send/receive scopes such as `im:message` and
  `im:message:send_as_bot`.
- Re-install the app into the tenant after changing scopes.

The SDK defaults to the Feishu OpenAPI domain. For Lark tenants, pass the Lark
domain explicitly:

```python
channel = FeishuChannel(
    app_id=os.environ["LARK_APP_ID"],
    app_secret=os.environ["LARK_APP_SECRET"],
    domain="https://open.larksuite.com",
)
```

Export credentials before running:

```bash
export LARK_APP_ID=cli_xxx
export LARK_APP_SECRET=your_app_secret
```

## Run the Echo Bot

```bash
python samples/channel/echo_bot.py
```

The sample is intentionally small:

```python
import asyncio
import os

from lark_channel import FeishuChannel

channel = FeishuChannel(
    app_id=os.environ["LARK_APP_ID"],
    app_secret=os.environ["LARK_APP_SECRET"],
)

async def on_message(msg):
    await channel.send(
        msg.chat_id,
        {"text": f"echo: {msg.content_text}"},
    )

channel.on("message", on_message)

asyncio.run(channel.connect())
```

`connect()` starts the WebSocket transport and keeps the process running. Use
`await channel.disconnect()` during graceful shutdown if your application owns
the event loop.

In WebSocket mode, the SDK requests `domain + "/callback/ws/endpoint"` to get
the server-provided WebSocket connection URL.

Register an error handler when you want centralized observability:

```python
async def on_error(err):
    print("channel error:", err)

channel.on("error", on_error)
```

## Send a Reply

```python
await channel.send(
    msg.chat_id,
    {"markdown": f"received: {msg.content_text}"},
    {"reply_to": msg.message_id},
)
```

`channel.send(to, message, opts=None)` accepts dict inputs, typed `Outbound*`
dataclasses, or a bare markdown string.

## Stream a Reply

```python
async def produce(stream):
    for token in ["hello", " ", "world"]:
        await stream.append(token)

await channel.stream(
    msg.chat_id,
    {"markdown": produce},
    {"reply_to": msg.message_id},
)
```

For lower-level CardKit controls, see [Streaming with CardKit](#doc-cardkit-streaming).

## Webhook Transport

For HTTP callbacks, construct the channel with `transport="webhook"` and pass
each HTTP request to `handle_webhook_request(headers, body)`.

```bash
pip install "lark-channel-sdk[aiohttp]"
```

```python
from aiohttp import web
from lark_channel import FeishuChannel

channel = FeishuChannel(
    app_id="cli_xxx",
    app_secret="***",
    encrypt_key="...",
    verification_token="...",
    transport="webhook",
)

async def on_message(msg):
    await channel.send(msg.chat_id, {"text": f"echo: {msg.content_text}"})

channel.on("message", on_message)

async def webhook(request):
    status, body = await channel.handle_webhook_request(
        headers=dict(request.headers),
        body=await request.read(),
    )
    return web.Response(status=status, body=body, content_type="application/json")

async def init():
    app = web.Application()
    app.router.add_post("/feishu/webhook", webhook)
    await channel.connect_until_ready()
    return app

web.run_app(init())
```

The SDK does not ship a built-in HTTP server. Keep TLS termination, rate
limiting, IP allowlisting, and anomaly tracking in your web framework or
gateway layer. See [Webhook server adapter](#doc-webhook-server).

Webhook routes are owned by your application. Register the full public callback
URL for that route in the developer console; the SDK only processes the request
after your web framework passes headers and body bytes to
`handle_webhook_request(...)`.

## Security Mode

The default `SecurityConfig` uses compatibility mode so existing bots can
migrate without behavior changes. For staging and production, start with audit
mode and then move to strict mode after reviewing audit events:

```python
from lark_channel import FeishuChannel, SecurityConfig

channel = FeishuChannel(
    app_id=os.environ["LARK_APP_ID"],
    app_secret=os.environ["LARK_APP_SECRET"],
    security=SecurityConfig(mode="audit"),
)
```

Strict mode enforces webhook signature checks for encrypted events, rejects
remote insecure WebSocket endpoints by default, and hides detailed webhook/card
error responses. See [Security configuration](#doc-security).

## Next Steps

- [Migration from lark_oapi.channel](#doc-migration-from-lark-oapi)
- [Channel reference](#doc-reference)
- [Security configuration](#doc-security)
- [Markdown to post conversion](#doc-markdown)
- [Two-layer dedup architecture](#doc-dedup-architecture)

<a id="doc-reference"></a>

---

## reference.md

> 来源：`docs/reference.md`

## Channel Reference

This is the detailed reference for `FeishuChannel`. For first-run setup, see
the [Channel quickstart](#doc-quickstart).

## Entry Point

```python
from lark_channel import FeishuChannel
```

`FeishuChannel` combines WebSocket or webhook event transport, inbound message
normalization, safety policy, deduplication, outbound sending, media
upload/download, streaming replies, and card helpers.

Use the lower-level WebSocket client (`lark_channel.ws.client.Client`), the
`EventDispatcherHandler`, or the OpenAPI `Client` directly when your integration
only needs raw event dispatch or direct OpenAPI calls.

## Minimal Example

```python
import asyncio
import os

from lark_channel import FeishuChannel

channel = FeishuChannel(
    app_id=os.environ["LARK_APP_ID"],
    app_secret=os.environ["LARK_APP_SECRET"],
)

async def on_message(msg):
    await channel.send(
        msg.chat_id,
        {"markdown": f"received: {msg.content_text}"},
        {"reply_to": msg.message_id},
    )

channel.on("message", on_message)

asyncio.run(channel.connect())
```

## Constructor Options

| Option                  |      Required | Description                                                                                                           |
| ----------------------- | ------------: | --------------------------------------------------------------------------------------------------------------------- |
| `app_id` / `app_secret` |           yes | Feishu app credentials                                                                                                |
| `domain`                |            no | Feishu, Lark, or custom OpenAPI domain                                                                                |
| `log_level`             |            no | SDK log level                                                                                                         |
| `transport`             |            no | `"ws"` by default, or `"webhook"`                                                                                     |
| `encrypt_key`           | if configured | Webhook/event decryption key from the developer console                                                               |
| `verification_token`    | if configured | Webhook/event verification token from the developer console                                                           |
| `policy`                |            no | `PolicyConfig` for DM/group admission and mention behavior                                                            |
| `safety`                |            no | `SafetyConfig` for dedup, stale window, batching, and per-chat queue                                                  |
| `inbound`               |            no | `InboundConfig` for normalization, media, names, and reaction behavior                                                |
| `outbound`              |            no | `OutboundConfig` for chunking, retry, markdown conversion, SSRF, and streaming throttle                               |
| `uat`                   |            no | `UATConfig` for user access token device-flow behavior                                                                |
| `security`              |            no | `SecurityConfig` for compat/audit/strict security behavior, audit logging, token cache fallback, and WebSocket limits |
| `token_store`           |            no | Custom user access token store                                                                                        |
| `dedup_store`           |            no | Pipeline-layer `DedupStore`                                                                                           |
| `safety_cache`          |            no | Safety-layer `ICache`                                                                                                 |
| `name_lookup`           |            no | Custom `open_id` to display-name resolver                                                                             |
| `config`                |            no | Prebuilt `ChannelConfig`; flat kwargs override touched fields                                                         |

```python
from lark_channel import (
    DedupConfig,
    FeishuChannel,
    OutboundConfig,
    RetryConfig,
    SafetyConfig,
    SecurityConfig,
)

channel = FeishuChannel(
    app_id="cli_xxx",
    app_secret="***",
    safety=SafetyConfig(dedup=DedupConfig(ttl_seconds=12 * 3600)),
    outbound=OutboundConfig(retry=RetryConfig(max_attempts=5)),
    security=SecurityConfig(mode="audit"),
)
```

## Lifecycle

| Method                                          | Purpose                                                                |
| ----------------------------------------------- | ---------------------------------------------------------------------- |
| `await channel.connect()`                       | Start transport. In WebSocket mode, keep running until stopped.        |
| `await channel.connect_until_ready(timeout=30)` | Start in the background and return after readiness.                    |
| `await channel.start_background(timeout=30)`    | Alias-style background startup with explicit naming.                   |
| `await channel.disconnect()`                    | Drain safety batches and stop the transport.                           |
| `channel.start()`                               | Synchronous startup. In webhook mode, build the dispatcher and return. |
| `channel.stop()`                                | Synchronous teardown.                                                  |
| `await channel.wait_ready(timeout=30)`          | Wait for readiness after startup.                                      |

`start()` is synchronous and may block during initial setup, including bot
identity resolution. Prefer `connect_until_ready()` in async web framework
startup hooks.

## Event Listening

```python
from lark_channel import Events

channel.on(Events.MESSAGE, on_message)
channel.on(Events.CARD_ACTION, on_card_action)
channel.on(Events.REACTION, on_reaction)
channel.on(Events.BOT_ADDED, on_bot_added)
channel.on(Events.BOT_LEAVE, on_bot_leave)
channel.on(Events.MESSAGE_READ, on_message_read)
channel.on(Events.COMMENT, on_comment)
channel.on(Events.REJECT, on_reject)
channel.on(Events.RECONNECTING, on_reconnecting)
channel.on(Events.RECONNECTED, on_reconnected)
channel.on(Events.ERROR, on_error)
```

Dispatched event names:

| Event          | Payload                          |
| -------------- | -------------------------------- |
| `message`      | `InboundMessage`                 |
| `cardAction`   | `CardActionEvent`                |
| `reaction`     | `ReactionEvent`                  |
| `botAdded`     | `BotAddedEvent`                  |
| `botLeave`     | `BotLeaveEvent`                  |
| `messageRead`  | `MessageReadEvent`               |
| `comment`      | `CommentEvent`                   |
| `reject`       | `RejectEvent`                    |
| `reconnecting` | no argument                      |
| `reconnected`  | no argument                      |
| `error`        | exception or `OutboundSendError` |

Snake-case aliases such as `card_action`, `bot_added`, `bot_leave`, and
`message_read` are normalized for compatibility.

## Message Model

`message` handlers receive an `InboundMessage`.

| Field                 | Description                                                                                                                                                                                       |
| --------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `message_id` / `id`   | Feishu message id                                                                                                                                                                                 |
| `create_time`         | Original event timestamp                                                                                                                                                                          |
| `conversation`        | `Conversation(chat_id, chat_type, thread_id)`                                                                                                                                                     |
| `chat_id`             | Shortcut for `conversation.chat_id`                                                                                                                                                               |
| `chat_type`           | `p2p`, `group`, `topic`, or `unknown`                                                                                                                                                             |
| `sender`              | `Identity` for the sender                                                                                                                                                                         |
| `sender_id`           | Shortcut for `sender.open_id`                                                                                                                                                                     |
| `sender_name`         | Optional display name                                                                                                                                                                             |
| `sender_type`         | Raw sender kind (`user` / `bot` / `system` / `anonymous` / `app`), or `None` when the event omits it (see [Bot-at-bot](#bot-at-bot))                                                              |
| `sender_is_bot`       | `True` when the sender is a bot/app (`sender.is_bot`)                                                                                                                                             |
| `mentions`            | List of `Mention` objects                                                                                                                                                                         |
| `mentioned_all`       | Whether the message mentioned all members                                                                                                                                                         |
| `mentioned_bot`       | Whether the message mentioned the bot                                                                                                                                                             |
| `reply_to_message_id` | Parent message id when present                                                                                                                                                                    |
| `content`             | Typed `MessageContent` dataclass                                                                                                                                                                  |
| `content_text`        | Flattened markdown/XML-style text (unchanged — keeps rendered mentions, incl. the bot's own)                                                                                                      |
| `safe_content_text`   | Escaped flattened text for security-sensitive rendering                                                                                                                                           |
| `body_text`           | `content_text` with the current bot's own `@`-mention removed — for command parsing / bare-`@` wake detection; equals `content_text` when the bot isn't mentioned (see [Bot-at-bot](#bot-at-bot)) |
| `resources`           | Resource descriptors for download                                                                                                                                                                 |
| `raw_content_type`    | Original Feishu message type                                                                                                                                                                      |
| `raw`                 | Original event payload                                                                                                                                                                            |

## Security Configuration

`SecurityConfig` defaults to `mode="compat"` to preserve existing behavior.
Use `mode="audit"` to log security-sensitive legacy behavior without blocking
it, and `mode="strict"` to enforce stricter checks.

Common options:

| Option                                            | Purpose                                                                                                              |
| ------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------- |
| `audit_recorder`                                  | Receives security audit calls as `record(reason, *, mode, action, details=None)`                                     |
| `allow_unsigned_encrypted_webhook`                | Allows encrypted webhook bodies with missing signature headers in strict mode; invalid signatures are still rejected |
| `allow_insecure_ws`                               | Allows remote `ws://` WebSocket endpoints in strict mode                                                             |
| `allow_local_insecure_ws`                         | Allows local `ws://` endpoints, enabled by default for local tests                                                   |
| `strict_error_response`                           | Uses a generic error response when strict handling is enabled                                                        |
| `strict_content_text`                             | Makes `content_text` use the escaped text form                                                                       |
| `legacy_token_cache_fallback`                     | Controls fallback to the legacy token cache key                                                                      |
| `max_ws_fragment_parts` / `max_ws_fragment_bytes` | Limits fragmented WebSocket payload assembly                                                                         |
| `max_concurrent_ws_handlers`                      | Limits concurrent WebSocket handler tasks                                                                            |
| `resource_overflow_policy`                        | Controls audit/drop behavior for WebSocket fragment limit overflow outside strict mode                               |

Recommended rollout:

1. Keep the default `mode="compat"` while changing imports and package names.
2. Use `mode="audit"` in staging or canary traffic to find security-sensitive
   legacy behavior.
3. Use `mode="strict"` after webhook signatures, WebSocket endpoints, and token
   cache behavior are verified.

```python
from lark_channel import FeishuChannel, SecurityConfig

channel = FeishuChannel(
    app_id="cli_xxx",
    app_secret="***",
    security=SecurityConfig(
        mode="strict",
        strict_content_text=True,
        max_ws_fragment_parts=128,
        max_ws_fragment_bytes=8 * 1024 * 1024,
    ),
)
```

See [Security configuration](#doc-security) for examples and strict-mode
compatibility switches.

Effective defaults:

| Option                                            | Default behavior                                                              |
| ------------------------------------------------- | ----------------------------------------------------------------------------- |
| `strict_error_response`                           | `None`, which means enabled in strict mode and disabled otherwise             |
| `legacy_token_cache_fallback`                     | `None`, which means disabled in strict mode and enabled otherwise             |
| `max_ws_fragment_parts` / `max_ws_fragment_bytes` | `None`, no explicit SDK fragment limit                                        |
| `max_concurrent_ws_handlers`                      | `None`, no explicit SDK handler concurrency limit                             |
| `resource_overflow_policy`                        | `"audit"` outside strict mode; strict mode drops WebSocket fragment overflows |

## Policy

Defaults:

- `dm_policy="open"`
- `group_policy="open"`
- `require_mention=True`
- `respond_to_mention_all=False`
- `sender_identity_fields=["open_id"]`

Runtime update:

```python
channel.update_policy(
    require_mention=False,
    respond_to_mention_all=True,
    group_policy="allowlist",
    group_allowlist=["oc_xxx"],
)
```

`update_policy()` accepts keyword changes for fields on `PolicyConfig`.

## Sending Messages

`channel.send(to, message, opts=None)` accepts:

- a bare string, treated as markdown;
- a dict;
- a typed `Outbound*` dataclass.

```python
await channel.send(chat_id, {"text": "plain text"})
await channel.send(chat_id, {"markdown": "hello **world**"})
await channel.send(chat_id, {"post": {"zh_cn": {"title": "", "content": []}}})
await channel.send(chat_id, {"card": {"schema": "2.0", "body": {"elements": []}}})
await channel.send(chat_id, {"image": {"source": "./image.png"}})
await channel.send(chat_id, {"file": {"source": b"content", "file_name": "a.txt"}})
await channel.send(chat_id, {"audio": {"source": "./audio.ogg"}})
await channel.send(chat_id, {"video": {"source": "./video.mp4"}})
await channel.send(chat_id, {"share_chat": {"chat_id": "oc_xxx"}})
await channel.send(chat_id, {"share_user": {"user_id": "ou_xxx"}})
await channel.send(chat_id, {"sticker": {"file_key": "file_v3_xxx"}})
```

`opts` may be a `SendOpts` object or a dict:

```python
await channel.send(
    chat_id,
    {"markdown": "please check"},
    {
        "reply_to": message_id,
        "reply_in_thread": True,
        "receive_id_type": "chat_id",
        "reply_target_gone": "fresh",
        "uuid": "optional-idempotency-key",
    },
)
```

For structured mentions, use typed outbound messages:

```python
from lark_channel import Identity, OutboundText

await channel.send(
    chat_id,
    OutboundText(
        text="please check",
        mentions=[Identity(open_id="ou_xxx", display_name="Alice")],
    ),
)
```

Media `source` accepts:

- HTTP(S) URL string, guarded by `OutboundConfig.ssrf_allowlist`;
- local file path string;
- `bytes`;
- existing media key string for the matching message kind: `img_...` for
  images, `file_...` for file/audio/video. Stickers use
  `{"sticker": {"file_key": ...}}` instead of media `source`.

Image and video messages support `caption`; file and audio captions are rejected
with `format_error`.

## Streaming

Markdown stream:

```python
async def producer(stream):
    for token in ["hello", " ", "world"]:
        await stream.append(token)

await channel.stream(chat_id, {"markdown": producer}, {"reply_to": message_id})
```

Card stream:

```python
async def producer(stream):
    await stream.update(next_card_json)

await channel.stream(
    chat_id,
    {"card": {"initial": initial_card_json, "producer": producer}},
)
```

For low-level CardKit preallocation, see [Streaming with CardKit](#doc-cardkit-streaming).

## Helpers

| Method                                                                                                                                                                    | Return                                          | Notes                                                                                                                                                                                                                                             |
| ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `await channel.update_card(message_id, card)`                                                                                                                             | `SendResult`                                    | Replace a sent card message                                                                                                                                                                                                                       |
| `await channel.edit_message(message_id, message)`                                                                                                                         | `SendResult`                                    | Text/post only                                                                                                                                                                                                                                    |
| `await channel.recall_message(message_id)`                                                                                                                                | `SendResult`                                    | Recall/delete a message                                                                                                                                                                                                                           |
| `await channel.add_reaction(message_id, emoji_type)`                                                                                                                      | `SendResult`                                    | Add a reaction                                                                                                                                                                                                                                    |
| `await channel.remove_reaction(message_id, reaction_id)`                                                                                                                  | `SendResult`                                    | Remove a reaction by id                                                                                                                                                                                                                           |
| `await channel.download_resource(file_key, resource_type="image")`                                                                                                        | `bytes \| None`                                 | Returns `None` on API failure                                                                                                                                                                                                                     |
| `await channel.download_resource_to_file(...)`                                                                                                                            | `Path`                                          | Raises `download_failed` when no body is returned                                                                                                                                                                                                 |
| `await channel.get_chat_info(chat_id)`                                                                                                                                    | `ChatInfo \| None`                              | Returns `None` on API failure                                                                                                                                                                                                                     |
| `await channel.get_chat_mode(chat_id)`                                                                                                                                    | `str \| None`                                   | Read `chat_mode` with cache and configured fallback                                                                                                                                                                                               |
| `await channel.resolve_quoted_contexts(messages, chat_mode="group")`                                                                                                      | `dict[str, QuoteResolution]`                    | Resolve quoted parent context for a batch without refetching in-batch parents                                                                                                                                                                     |
| `await channel.resolve_resource_to_cache(message_id="om_x", resource=resource)`                                                                                           | `CachedResource`                                | Download a message resource into the SDK-managed cache                                                                                                                                                                                            |
| `channel.block_batch_scope(scope)` / `unblock_batch_scope(scope)` / `cancel_batch_scope(scope)`                                                                           | `None`                                          | Pause, resume, or cancel debounced batches around an active run                                                                                                                                                                                   |
| `await channel.add_typing_reaction(message_id)` / `remove_typing_reaction(message_id, reaction_id)`                                                                       | `str \| None` / `bool`                          | Best-effort IM message `Typing` reaction helpers                                                                                                                                                                                                  |
| `await channel.resolve_comment_target(file_token="doc_x", file_type="docx")` / `get_comment_context(target=target, comment_id="c1")` / `reply_comment(context, "answer")` | `CommentTarget` / `CommentContext` / API result | Cloud document comment primitives for supported `doc`, `docx`, `sheet`, and `file` targets. `reply_comment` creates a whole-file comment when `context.is_whole` is true, or updates an existing reply when `context.target_reply_id` is present. |
| `channel.client`                                                                                                                                                          | `Client`                                        | Underlying OpenAPI client                                                                                                                                                                                                                         |

## Error Handling

`send()` returns `SendResult`.

- Invalid input and transport/coercion failures may raise.
- Upstream send failures usually return `SendResult(success=False, error=...)`.
- Both raised errors and failed `SendResult.error` are forwarded to
  `channel.on("error", handler)`.

`stream()` and low-level CardKit helpers raise for controller or CardKit
failures.

Known `FeishuChannelErrorCode` values:

| Code                | Meaning                                      |
| ------------------- | -------------------------------------------- |
| `format_error`      | Message/card schema rejected                 |
| `target_revoked`    | Reply target no longer accepts replies       |
| `rate_limited`      | Upstream rate limit                          |
| `permission_denied` | Invalid credentials or missing scopes        |
| `upload_failed`     | Media upload failed                          |
| `download_failed`   | Media download failed                        |
| `ssrf_blocked`      | URL media download blocked by SSRF policy    |
| `send_timeout`      | Send/connect timeout                         |
| `not_connected`     | Transport is not connected or startup failed |
| `unknown`           | Uncategorized upstream or SDK error          |

## Bot-at-bot

Support for multiple bots collaborating in one chat — agents `@`-ing each other
to hand off work. Everything here is **opt-in and additive**; default behavior
is unchanged.

**Know who sent it.** Every inbound message carries `sender_type`
(`user` / `bot` / …) and the convenience `sender_is_bot`, so an agent can tell a
human, itself, and another bot apart. Get the bot's own identity for its system
prompt with `channel.get_bot_identity()` → `BotIdentity(open_id, name, …)`
(raises `FeishuChannelError(code=not_connected)` before `connect()`). Set
`resolve_sender_names=True` to fill `sender_name` from the chat roster.

**Receiving events from other bots.** Feishu does **not** deliver "another bot
`@`-ed me" events unless the app has the
`im:message.group_at_msg.include_bot:readonly` permission enabled (distinct from
`im:message.group_at_msg:readonly`, which only covers _user_ mentions) — and the
failure is silent. There is no API to self-check this; if bot-to-bot mentions
never arrive, verify that permission first (see the Feishu open-platform
"receive message events" documentation). An `@`-only ping (no body) still wakes
the bot: `mentioned_bot` is `True`.
`content_text` still renders the mention; use `body_text` (the bot's own mention
removed) to detect a bare poke: `msg.mentioned_bot and not msg.body_text.strip()`.

**Roster.**

| Method                                                                                                    | Return             | Notes                                                                                                                           |
| --------------------------------------------------------------------------------------------------------- | ------------------ | ------------------------------------------------------------------------------------------------------------------------------- |
| `await channel.get_chat_members(chat_id, *, page_size=100, max_pages=10, id_type="open_id", force=False)` | `list[ChatMember]` | A chat's **users** (Feishu filters bots out, so `is_bot` is never `True`); paginated + cached. `force` refetches                |
| `await channel.get_chat_bots(chat_id, *, force=False)`                                                    | `list[ChatMember]` | The chat's **bots** (`is_bot=True`); cached; seeds the roster so a bot is `@`-able by name without first appearing in a mention |
| `channel.get_bot_identity()`                                                                              | `BotIdentity`      | This bot's own identity; raises `not_connected` before `connect()`                                                              |

`ChatMember` = `id`, `id_type`, `name`, `tenant_key`, `is_bot`. Provide a
`resolve_chat_members` config hook (`(chat_id) -> list[ChatMember] | None`, sync
or async) to source the roster from your own directory instead of the API.
**Both `None` and an empty list `[]` fall back to the API** — the hook only
overrides the roster when it returns a non-empty list
(return `None` to fall back to the API).

**Replying to the right place.** `await channel.reply(msg, message, opts=None)`
replies to `msg` and follows its shape: `reply_to` defaults to `msg.message_id`
and `reply_in_thread` defaults to whether the trigger was in a topic thread, so
a reply stays in a thread when the message was in one and stays flat when it
wasn't. `opts` overrides either default; `reply()` never promotes a flat message
into a thread on its own.

**`@`-mentioning by name.** Either pass structured mentions — a name-only
`Identity(open_id="", display_name="Alice")` on `OutboundText` / `OutboundPost`
is resolved to an open_id against the chat roster (dropped if it doesn't
resolve) — or set `SendOpts.resolve_mentions_in_text=True` to rewrite `@name`
tokens in a text / markdown body. A name that is unknown **or shared by more
than one member** is left as plain text and never mis-mentioned; for
security-sensitive handoffs, pass an explicit open_id. Roster names come from
`get_chat_members` (users), `get_chat_bots` (bots), and bots observed in earlier
inbound mentions.

**Restrict who can trigger the bot — by chat, not by sender.** Sender open_ids
are hard to get up front, so enumerating them in `allow_from` is impractical.
Instead allow only specific chats with `group_allowlist=['oc_…']` and require an
`@` with `require_mention=True`. `allow_from` takes **sender ids**
(`ou_…` / user_id / union_id); `group_allowlist` takes **chat ids** (`oc_…`); an
app id (`cli_…`) belongs in neither and logs a warning.

**Breaking ping-pong loops.** The opt-in `policy.bot_loop_guard` counts only
"another bot `@`-ed me" messages in a sliding window (a human message resets it)
and trips past a threshold:

```python
from lark_channel import BotLoopGuardConfig, PolicyConfig

policy = PolicyConfig(
    bot_loop_guard=BotLoopGuardConfig(
        enabled=True,
        window_ms=60_000,       # sliding window W
        max_bot_mentions=5,     # trip at N bot @-mentions in W
        scope="chat",           # or "chat+sender"
        on_trip="reject",       # "drop" (default) silently mutes; "reject" emits reject(reason="bot_loop")
    )
)
```

The default `on_trip="drop"` silently stops replying (one warning on the first
trip); prefer `"reject"` (emits a `reject` event with `reason="bot_loop"`) when
the app needs to know. This is a heuristic backstop, not a protocol-level
guarantee.

## Related Documents

- [Channel quickstart](#doc-quickstart)
- [Migration from lark_oapi.channel](#doc-migration-from-lark-oapi)
- [Security configuration](#doc-security)
- [Webhook server adapter](#doc-webhook-server)
- [Streaming with CardKit](#doc-cardkit-streaming)
- [Markdown to post conversion](#doc-markdown)
- [Two-layer dedup architecture](#doc-dedup-architecture)
- [Release notes](#doc-release-notes-v1-1-0)

<a id="doc-release-notes-v1-0-0"></a>

---

## release-notes/v1.0.0.md

> 来源：`docs/release-notes/v1.0.0.md`

## lark-channel-sdk v1.0.0

This is the first standalone release of the Python Channel SDK.

## Highlights

- Standalone package: `lark-channel-sdk`
- New import path: `lark_channel`
- High-level `FeishuChannel` entry point
- WebSocket and webhook event handling
- Message normalization, send/reply/update/recall/forward
- Card actions and streaming card updates
- Media upload/download and media cache primitives
- Message reactions and typing reaction helpers
- Cloud document comment context and reply/update helpers for supported `doc`, `docx`, `sheet`, and `file` targets

## Compatibility

- Existing `lark_oapi.channel` users are not forced to migrate immediately.
- The full OpenAPI SDK remains available from `lark-oapi`.
- `lark-channel-sdk` is designed to install alongside `lark-oapi`.

## Migration

```bash
pip install lark-channel-sdk
```

```python
from lark_channel import FeishuChannel
```

See [Migration from lark_oapi.channel](#doc-migration-from-lark-oapi) for the
full checklist.

## Security

`SecurityConfig` defaults to compatibility mode. Use audit mode to observe
legacy behavior during rollout, then strict mode to enforce webhook signature,
WebSocket endpoint, error-response, and token-cache checks.

See [Security configuration](#doc-security).

## Notes

- Media cache, typing reactions, and cloud document comment helpers require the corresponding Lark app permissions.
- Cloud document comment helpers cover supported `doc`, `docx`, `sheet`, and `file` comment targets.
- The full Drive/OpenAPI surface remains in `lark-oapi`.

<a id="doc-release-notes-v1-1-0"></a>

---

## release-notes/v1.1.0.md

> 来源：`docs/release-notes/v1.1.0.md`

## lark-channel-sdk v1.1.0

This release improves post-message markdown handling and adds support for `content_v2`.

## Changes

- Added inbound post normalization support for `content_v2`.
- Markdown outbound messages now use native Feishu markdown rendering by default.
- Structured markdown conversion remains available with `MarkdownConverter(tag_md_mode="structured")`.

## Compatibility

- Plain text messages and prebuilt post AST payloads are not affected.
- If your integration asserts exact post AST nodes, set `tag_md_mode="structured"` explicitly.

<a id="doc-security"></a>

---

## security.md

> 来源：`docs/security.md`

## Channel Security Configuration

`SecurityConfig` controls how the Channel SDK handles security-sensitive
compatibility behavior. The default is `mode="compat"` so existing applications
continue to run after migrating to the standalone package.

Use `mode="audit"` first when you want to see legacy behavior without blocking
traffic. Move to `mode="strict"` after the audit events are understood and your
webhook, WebSocket, and token-cache paths are ready for enforcement.

```python
from lark_channel import FeishuChannel, SecurityConfig

channel = FeishuChannel(
    app_id="cli_xxx",
    app_secret="***",
    encrypt_key="...",
    verification_token="...",
    transport="webhook",
    security=SecurityConfig(mode="audit"),
)
```

## Modes

| Mode     | Behavior                                                                                        |
| -------- | ----------------------------------------------------------------------------------------------- |
| `compat` | Preserves legacy behavior and does not emit default audit warnings.                             |
| `audit`  | Allows legacy behavior, but records security audit events when an audit recorder is configured. |
| `strict` | Enforces stricter checks and uses generic error responses by default.                           |

## Common Production Settings

```python
from lark_channel import FeishuChannel, SecurityConfig

channel = FeishuChannel(
    app_id="cli_xxx",
    app_secret="***",
    transport="webhook",
    encrypt_key="...",
    verification_token="...",
    security=SecurityConfig(
        mode="strict",
        max_ws_fragment_parts=128,
        max_ws_fragment_bytes=8 * 1024 * 1024,
        max_concurrent_ws_handlers=256,
    ),
)
```

In strict mode:

- encrypted webhook events must have a valid request signature before decrypt;
- remote `ws://` endpoints are rejected unless `allow_insecure_ws=True`;
- local `ws://` endpoints remain allowed by default for local tests;
- webhook and card errors return a generic response unless
  `strict_error_response=False`;
- `legacy_token_cache_fallback` defaults to disabled.

## Webhook Compatibility Switches

`allow_unsigned_encrypted_webhook=True` permits encrypted webhook payloads that
are missing request signature headers in strict mode. It does not allow invalid
signatures: if signature headers are present but verification fails, strict mode
still rejects the request before decrypt. Use this only as a temporary
compatibility switch while confirming developer-console and gateway behavior.
When enabled for a missing-signature request, the SDK records an allow action
through the configured audit recorder.

```python
from lark_channel import SecurityConfig

security = SecurityConfig(
    mode="strict",
    allow_unsigned_encrypted_webhook=True,
)
```

## Text Rendering

`InboundMessage.content_text` keeps the legacy flattened text by default.
`InboundMessage.safe_content_text` is always available for security-sensitive
rendering. Set `strict_content_text=True` when you want `content_text` itself to
use the escaped safe form.

```python
from lark_channel import SecurityConfig

security = SecurityConfig(
    mode="strict",
    strict_content_text=True,
)
```

## Audit Recorder

Pass a custom recorder when audit events should go to your own logging or
metrics system. The recorder only needs a callable `record(...)` method with
the same argument shape used below.

```python
class AuditRecorder:
    def record(self, reason, *, mode, action, details=None):
        print(reason, mode, action, details or {})


security = SecurityConfig(mode="audit", audit_recorder=AuditRecorder())
```

See the [Channel reference](#doc-reference) for the full
option table.

## User access tokens

`require_user_auth` and `follow_my_meeting` act under a **user's** authorization
rather than the app's. Three properties of that are yours to handle.

**The open_id decides whose authorization is used, and the SDK cannot check it.**
It receives a string and looks up whatever ticket is filed under it, so passing
a user-controlled value acts as that person — without notifying them. The
`user_open_id` you pass must be somebody you have already established is the
requester, and `prompt_context` must belong to that same person: the
authorization card carries a one-time grant, so sending it elsewhere lets a
different person authorize _their_ account while the resulting ticket is filed
under the first one's id.

**The granted scope is wider than the call suggests.** A call may ask for
`vc:meeting.meetingevent:read`, but the device flow issues a ticket carrying
every scope the application applied for — commonly calendar, documents and IM
as well. That ticket is stored per user and reused by anything else in the
process that resolves a ticket for the same user, for as long as it stays valid.
Where it is stored is your choice: the default `InMemoryTokenStore` keeps it in
process memory and loses it on restart, `FileTokenStore` writes plaintext and is
development-only, and production wants your own `TokenStore` over a secret
manager.

**Resolution runs on the channel's background loop**, serialized per user, so a
concurrent refresh cannot take a valid authorization away from its owner. Two
consequences: `prompt_context.respond` is invoked from that loop's thread, so an
object bound to a different event loop will not work; and a process that only
calls `require_user_auth` still gets the channel's background thread.

## Paths outside the message policy

Two entry points reach your handlers without passing through `PolicyConfig`,
`SeenCache` dedup, the processing lock or the loop guard. Both are deliberate,
and both default to open:

- **`on_raw_event`** — subscribing to a type the channel already handles opens an
  unpoliced path into that type. With `dm_policy="allowlist"` set, a raw
  subscription to `im.message.receive_v1` still receives direct messages from
  everybody.
- **`meetingInvited`** — the only way into a joined meeting, triggered by anybody
  who can add the bot to one. Gate it with
  `MeetingChannelConfig.invite_allowlist`.

## Meeting channel

`follow_my_meeting` reads a meeting under a user's own authorization — see
[User access tokens](#user-access-tokens) for what that authorization actually
covers — and the bot is **not visible in the meeting**. It collects every
participant's speech for as long as the meeting lasts. Informing them is the
integrating application's responsibility; this SDK does not prompt, and cannot.
The first call in a process logs a warning to that effect.
`MeetingChannelConfig.follow_allowlist` gates it by open_id, but defaults to
`None` (open) — an opt-in, not a safety net you already have.

Two values on this path are credentials that do not look like one:

- **`console_url`**, which a permission failure may carry in
  `FeishuChannelError.context`, is a signed one-click authorization link — a
  capability, not a help page. The redaction layer masks it in logs; it cannot
  mask it in your own output. Never echo it into a chat message, a web page or a
  support ticket.
- **Meeting passwords**, both the one you pass to `join_meeting` and the one some
  meeting responses hand back. Neither reaches logs, `raw` payloads, error
  objects or the session.

<a id="doc-webhook-server"></a>

---

## webhook-server.md

> 来源：`docs/webhook-server.md`

## Webhook Server Adapter

The Channel SDK does not ship a built-in HTTP server. TLS termination, rate
limiting, IP allowlisting, anomaly tracking, and framework choice belong in
your application or gateway layer.

Channel exposes one async request entry point:

```python
status, body_bytes = await channel.handle_webhook_request(headers, body)
```

`handle_webhook_request(...)` decrypts the body when `encrypt_key` is
configured, validates `verification_token` when configured, verifies request
signatures for non-challenge events when `encrypt_key` is configured, and routes
the event to your registered `channel.on(...)` handlers. Signature headers may
be present even when event encryption is disabled; without `encrypt_key`, the
dispatcher treats the request as plaintext and does not verify those headers.

You must initialize the channel before the first request. In async frameworks,
prefer `await channel.connect_until_ready()` during application startup. The
synchronous `channel.start()` method is safe in synchronous setup code, but it
may block while initial setup runs.

For production webhook deployments, use `SecurityConfig(mode="audit")` during
rollout and move to `mode="strict"` after request signatures are verified.
Strict mode rejects encrypted webhook events before decrypt when signature
headers are missing or invalid, and it returns generic error responses by
default. See [Security configuration](#doc-security).

`allow_unsigned_encrypted_webhook=True` only permits missing signature headers;
encrypted webhook requests with invalid signature headers are still rejected in
strict mode.

## aiohttp Adapter

```bash
pip install "lark-channel-sdk[aiohttp]"
```

```python
from aiohttp import web

from lark_channel import FeishuChannel

channel = FeishuChannel(
    app_id="cli_xxx",
    app_secret="***",
    encrypt_key="...",
    verification_token="...",
    transport="webhook",
)

async def on_message(msg):
    await channel.send(msg.chat_id, {"text": f"echo: {msg.content_text}"})

channel.on("message", on_message)

async def webhook(request: web.Request) -> web.Response:
    status, body_bytes = await channel.handle_webhook_request(
        headers=dict(request.headers),
        body=await request.read(),
    )
    return web.Response(status=status, body=body_bytes, content_type="application/json")

async def init() -> web.Application:
    app = web.Application()
    app.router.add_post("/feishu/webhook", webhook)
    await channel.connect_until_ready()
    return app

if __name__ == "__main__":
    web.run_app(init(), host="127.0.0.1", port=8765)
```

## FastAPI Adapter

```bash
pip install "lark-channel-sdk[fastapi]"
```

```python
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response

from lark_channel import FeishuChannel

channel = FeishuChannel(
    app_id="cli_xxx",
    app_secret="***",
    encrypt_key="...",
    verification_token="...",
    transport="webhook",
)

async def on_message(msg):
    await channel.send(msg.chat_id, {"text": f"echo: {msg.content_text}"})

channel.on("message", on_message)

@asynccontextmanager
async def lifespan(app: FastAPI):
    await channel.connect_until_ready()
    try:
        yield
    finally:
        await channel.disconnect()

app = FastAPI(lifespan=lifespan)

@app.post("/feishu/webhook")
async def webhook(request: Request):
    status, body_bytes = await channel.handle_webhook_request(
        headers=dict(request.headers),
        body=await request.body(),
    )
    return Response(status_code=status, content=body_bytes, media_type="application/json")
```

## Synchronous Setup

If your framework has synchronous startup code and you are in webhook mode,
`channel.start()` builds the dispatcher and returns after initial setup:

```python
channel = FeishuChannel(
    app_id="cli_xxx",
    app_secret="***",
    transport="webhook",
)
channel.start()
```

Do not call `handle_webhook_request(...)` before startup, or it raises
`FeishuChannelError(code=not_connected)`.

## Rate Limiting and Anomaly Tracking

These belong in your web layer's middleware. For aiohttp:

```python
from collections import defaultdict
from time import time

from aiohttp import web

WINDOW_S = 60
MAX_REQ = 120
_buckets = defaultdict(list)

@web.middleware
async def rate_limit(request, handler):
    ip = request.remote or ""
    now = time()
    bucket = _buckets[ip]
    bucket[:] = [t for t in bucket if t > now - WINDOW_S]
    if len(bucket) >= MAX_REQ:
        return web.Response(status=429, text="rate limited")
    bucket.append(now)
    return await handler(request)

app = web.Application(middlewares=[rate_limit])
```

For anomaly tracking, wrap the handler and track non-200 responses per IP or
tenant key. The SDK only sees validated request bytes and event payloads.

## Why No Built-in Server?

- Avoid forcing aiohttp, FastAPI, or another framework into every SDK user.
- Production deployments usually already have ingress, WAF, and monitoring.
- A framework adapter is small and keeps ownership of HTTP concerns clear.

Return to the [project README](../README.md).
