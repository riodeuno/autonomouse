# Board schema: telegram references

## telegram-references.json

The reference register: which card or planning thread each Telegram message is about
(`src/autonomouse/board/references.py`). The human cannot read a board id, so every
message autonomouse sends about a card or a thread opens with a line holding a two-digit
reference number and the project's name, `07 Widgets`, and no message writes the card's or
thread's id: `autonomouse notify --about <id>`, the brief `card ask` sends and its
follow-ups, and the acknowledgements `notify poll` sends. `notify` writes the register when
it sends such a message, and `notify poll` reads it to route the human's reply
(`docs/config.md`, "`autonomouse notify poll`"); nothing else writes it, and never by hand.

```json
{"references": [{"number": 7, "message_id": 1234, "about": "sys-e10", "project": "system", "sent_at": "2026-09-27T07:00:00Z"}]}
```

| field | type | meaning |
|---|---|---|
| number | int | 1 to 99, shown as two digits |
| message_id | int | the Telegram id of the message that carried the number |
| about | string | the card id or planning thread id the message is about |
| project | string | that card's or thread's project slug; the message shows the project's `name` |
| sent_at | time | when the message was sent |

Entries are oldest first. Each message takes the number one past the newest entry's,
and 01 after 99, and the new message's entry replaces the one that held that number: the
register never holds more than 99. A number whose message asks a question still open is
skipped: the brief of a card's unanswered decision (the decision's `message_id`), or any
message about a planning thread waiting on the human. A reply to that message
reaches its question however many messages follow it. When every number is skipped, the
message is refused with that reason and takes no number. A message whose send failed
takes no number and writes nothing. A missing file is an empty register. Every write
holds an exclusive `flock` on the board folder across the send, so two senders never take
one number. An entry's keys this table does not name are kept and written back as they
are. Nothing commits the board for a register write; the writes ride in the next board
commit.
