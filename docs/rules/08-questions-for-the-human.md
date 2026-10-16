# Rule 8. A question for the human is written for someone who has not read the code

**Reader:** the orchestrator, the one agent that puts a question on the decision stack, and
every agent whose text reaches the human: the worker, the planner and the human proxy (the
super-orchestrator).

## What the reader does

A question for the human is written for someone who has not read the code. Plain words, no
ids, file names or internal terms without saying in the same sentence what they are; the
goal sentence it serves, word for word as the human confirmed it or, before that, as
proposed (your global `CLAUDE.md`, principle 0), what the thing is and why it matters,
and what happens under each option; and always a last option, "other: tell me", so the
human can answer in their own words or ask back (`card ask` appends it to every question,
and `card answer` takes any text). A question the human cannot answer from the question
alone is a defect in the question, not in the human.

Only the orchestrator puts a question on the decision stack (`card ask`, which wakes the
human with a message and refuses a question without `--brief` or `--recommendation`), and
only after it has answered three questions in writing: can an agent on this machine do or
find this (then it does it, or dispatches a worker, and the card's test is rewritten to say
so); is the answer already written down as a default, a recommendation that settles the
choice on its own, an earlier answer on the board or a rule (then it takes that answer and
notes where it came from, except that a default or a recommendation never settles what only
the human can decide); and is what remains something only the human can decide (then it
asks, and the brief names which kind). The gate itself is
`docs/orchestrator/decision-gate.md`. Decisions are for what only the human can decide: a
credential, a spend, a fork in the goal, a judgment of taste, a goal sentence the human has
not confirmed, and every other kind principle 6 of your global `CLAUDE.md` names. Anything
else — a default, a wording, a choice the recommendation already settles — is taken, noted,
and not asked. Nobody asks the human to run a command, watch a screen, send a message or
confirm that a feature works: an agent on this machine does all four.

The same holds for everything a `decision_needed` card shows the human: a brief that stands
on its own — what has been done, what is stuck and why, the goal sentence the card serves,
word for word from its goal, and what happens under each option — written by the agent that
asks, who has the context the human does not, and passed to `card ask` as `--brief`, laid
out as the next section says. Worker fields (limits, phase, iteration, handoff notes, PR
links) are not a brief and never stand in for one.

**Decide on evidence; ask the human only what is truly theirs.** The super-orchestrator
and the orchestrator decide on the human's behalf: within its own role, each makes every
judgment that evidence it has read settles, and asks the human only a decision that is
truly theirs, one of the kinds this item lists. In the human's words (2026-09-28), to the
super-orchestrator: "Everytime you ask me something, it delays progress. So, make
judgements as much as possible. ask me only if it is truly important." And: "The decide
on evidence, ask only ... should also be a part of the orchestrator's charter, because it
is also going to be making decisions on my behalf as the project manager."

## How a message to the human is laid out

The human reads every message on a phone, between other things. Each text you write for the
human — a `card ask` brief, a planning-thread message the planner sends with `notify
--about`, a `notify` text — is short parts separated by blank lines, never one paragraph:

- **The question is the heading.** Under the line with the reference number, `card ask`
  puts its `--question` in bold, then the brief, then the `--option`s, which it numbers
  one per line, and the `--recommendation` on its own line. So the brief holds no
  question, no option list and no recommendation: those flags carry them. A planner
  message puts its question alone on its text's first line, which `--bold` shows in bold.
- **The brief is at most these three labelled parts,** each a few short lines, with a
  blank line between them: `What happened:` (what was done, what is stuck and why), `Why
  it needs you:` (the goal sentence, word for word, then which kind of decision only the
  human can make this is), and `What each option costs:` (what happens under each option,
  naming it by its number only). Leave out any part with nothing to say.
- **A message that asks nothing** opens with a one-line heading that says what it is
  about, then short lines, one fact each.
- **Write no board id** (a card's or a thread's), in any part. Name a piece of work by what
  it changes and for whom.
- **Pass real line breaks.** A `\n` inside an ordinary quoted string reaches the human as
  those two characters. Pass the text through a quoted heredoc instead:
  `--brief "$(cat <<'EOF'` … `EOF` `)"`.

Example, as the human reads it. The bold second line is `--question`, the
three parts are `--brief`, the numbered lines are the `--option`s (`card ask` adds the
last), and the line after them is `--recommendation`:

```
03 Autonomouse system
Change the setup check's test line, or log this machine in with a classic GitHub token?

What happened: setup's GitHub access check is built and passed one live run.
One line of its test does not hold: it wants every permission ok, and the one
that reads CI results is missing. This machine's GitHub login can never have
it, and setup already warns with the fix and goes on, as the card asks.

Why it needs you: <the card's goal sentence, word for word>. Changing a card's
test changes its goal, and a new token is a credential; both are yours.

What each option costs: 1 costs nothing; the card is done and the next setup
card starts. 2 costs you a token change; the next wake repeats the live run.

1. Change the test line: every required permission ok, and a missing CI-results read is reported with its fix
2. Log gh in with a classic token that has the repo and workflow scopes
3. other: tell me
Recommendation: option 1, since setup already behaves as you asked, and the system reads CI results another way

Reply to this message with an option number or your own answer, or answer on the board screen; ...
```

## Why

The human decides only what the human alone can decide, and decides it from the question
alone, in plain words, with a way to answer in their own words. The layout is the owner's:
"The telegram messages that I receive are not formatted well at all. It is one continuous
stream of text with no line breaks, no indentation, no subheadings. It is just extremely hard
to read." Every question costs the human attention, so a question an agent could have answered
is a cost with no return.

## The failure it prevents

Questions the human cannot answer without reading the code, questions with no "other" option,
questions that were never the human's, and a brief that carries the whole decision in one
paragraph, its options and recommendation included, and reads on a phone as one block.
