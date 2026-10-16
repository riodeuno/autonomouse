# The decision gate

`autonomouse card ask` messages the human on Telegram at once (when Telegram is off, the card waits on the board); only you run it, with
`--brief` and `--recommendation` (rules, item 8). No question reaches it before you answer
these three in writing:

1. **Can an agent on this machine do or find this?** Do it yourself, or dispatch a worker
   when it changes a repository.
2. **Is the answer already written down?** A default, a clearly right recommendation, an
   earlier answer from the human on the board, or a rule in `REPO/docs/rules.md` settles it,
   except that a default or a recommendation never settles a kind question 3 names.
3. **Is what remains something only the human can decide?** The kinds are the ones rules,
   item 8, lists: a credential, a spend, a fork in the goal, a judgment of taste, a goal
   sentence the human has not confirmed, and every other kind principle 6 of your global
   `CLAUDE.md` names. Only then does it go to `card ask`, its gate line naming which kind.
   Otherwise the card's plan is wrong: replan it.

Write the three answers as one line, `gate: (1) … (2) … (3) …`, on that card with `card edit
<id> --note "gate: …"` before its `card ask`, and in your final message. The `card ask`
brief is for the human and holds no gate line: it is laid out as rules, item 8, "How a
message to the human is laid out", says, and its `Why it needs you:` part gives the goal
sentence the card serves, word for word from its goal, and the kind your answer to question
3 named.
**When the human answers a question this gate should have settled**, record `trivial ask:
<card id> — <which of the three questions would have settled it>` in the same places; the
answer now settles it under question 2. `autonomouse review daily` counts them under "Asks
the system should have settled".

**When a card's latest answered decision is `leave this card for now`**, the human set the
card aside. It stays aside until its history shows, after that answer's `answered_at`,
either a move from `backlog` to `todo` or a row for that decision whose note starts
`reply to answered decision <decision id>: go ahead`, ignoring case. `notify poll` writes
a `reply to answered decision` row for every reply the human sends to that decision, so a
`go ahead` still counts after a later reply, and in any stack, `todo` included. The
decision's `reply` field is not the signal, because a later reply replaces it. A card no
longer aside is ready: it is not moved to backlog and starts like any other card.
Before a card that is still aside starts, and before its access checks run, move it with
`card move <id> --to backlog --note "<answer>"`, the note quoting that decision's `answer`
field, and do not start it.
