# Access checks

A card's `access` lists the logins and tools its test needs on this machine, each with a
check: a command that exits 0 only when that access is present (`docs/board-schema.md`). The
human is asked for missing access before work starts, never in the middle of it, so every
check runs before the card starts.

A failed check is a credential, one of the kinds rules, item 8, lists: only the human holds
the account. Run `card start` and at once `card ask`, with no worker spawned; a card with no
worker holds no slot. The brief names, for each failed check, the service, why the card's
test needs it, and exactly how the human provides it on this machine: the login command to
run in a terminal there (`stripe login`), or the key name to add to
`~/.config/autonomouse/.env`. The options include `done` and `leave this card for now`, and
either answer moves the card back to `todo`. After `done`, its next start runs the checks
again. `leave this card for now` ends with the card in backlog unless the set-aside rule in
`docs/orchestrator/decision-gate.md` finds it ready: you move a card still aside to backlog
before its checks run again.

A card whose test needs a logged-in site checks `autonomouse browser has-login <domain>`:
it exits 0 when the pool (`docs/config.md`, `browser.pool`) is logged in to that domain, or
a parent domain of it, and 1 otherwise, printing why either way. A failed one names, for the
brief, the site the pool needs a login for — the human adds it on the pool machine, not on
this one — the login command above does not apply.
