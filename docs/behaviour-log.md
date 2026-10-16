# The behaviour log and the behaviour review: where each row comes from

The behaviour log (`autonomouse log show <run-id>`) and the behaviour review
(`autonomouse review behaviour --since <days>`) say how the agents of a wake behaved, never
what they built. Every row comes from data that needs no reading of shell commands. A
shell reader's counts were wrong in each of the first three wakes and had to be corrected
from the transcripts, so no row depends on one.

## The sources

- **The transcripts**: the session's orchestrator transcript and its sub-agents'
  (`~/.claude/projects/<folder>/<session>.jsonl` and `<session>/subagents/`). Every agent
  file in `subagents/` whose first record falls inside the run's part of the session is
  the run's, whatever its depth or the agent that started it, a skill's fork included. The
  log reads each record's time, each message's model and token usage, and each tool
  call's name. It reads no tool call's input or output.
- **The card histories and decisions on the board.** Every `autonomouse` command that
  writes a card appends one history row naming the run, and never changes an earlier row.
- **The board's git history**: which commits changed which card file, and how.
- **The run record** (`runs/<run-id>.json`): the run's total cost and Jev's answers.
- **`gh pr view`** on each card's `pr`, when the log is derived.

## The log's rows

| row | source |
|---|---|
| each agent: its role, card, model, effort and agent type | transcripts: the spawn prompt that names a card file makes a worker; the records carry model and effort; the sub-agent's meta file carries its type |
| each agent: turns, duration, tool calls by name, longest idle gap | transcripts: the records' times and the tool calls' names |
| sub-agents spawned by workers | transcripts: the workers' spawn calls and the sub-agents' meta files |
| cost of each agent, cost per model, models with no rates | transcripts: each message's token usage at the model's rates (`src/autonomouse/behaviour/price.py`) |
| the run's total cost | run record: `result.total_cost_usd` |
| board writes, by command | card histories: the rows naming the run; the command is read from the row's stacks and the start of its note |
| stack transitions | card histories: the rows naming the run that moved a card between stacks |
| phase changes, iterations and their cause | card histories: `card progress` rows (`phase=`, `iteration=`); the cause is the phase the card was in |
| decisions: kind and who answered | card decisions and the history rows that asked and answered them |
| PR state at finish | `gh pr view` on the card's `pr` |
| usage-limit stalls: start, reset, agents affected | transcripts: `system`/`api_error` records with a 429 status and `rateLimits.rateLimitType`, from any agent, grouped by their reset time |
| Jev answers | run record: `jev`, without the prose `reason` |
| times that cannot be true | card histories and decisions, and the time of the run's last transcript message |

The board's rows are read when the log is derived. When the board cannot be read, each
of them says it was not checked.

## The review's sections

| section | source |
|---|---|
| Runs | the logs |
| Cards done | card histories, read when the review is written |
| Decisions | card decisions and histories, read when the review is written |
| Iterations | card histories, read when the review is written |
| Cost | the logs: transcripts' usage and the run record |
| Rule breaches: hand edits of cards | the board's git history: a change to a card file that appended no history row, or rewrote an earlier one |
| Rule breaches: times on the board that cannot be true | the logs |
| Waits | the logs: each agent's longest idle gap |
| PR state at finish | the logs |
| Jev and the orchestrator | the logs |

`autonomouse review improve` reads four patterns from the review: hand edits of cards,
times on the board that cannot be true, workers or their helpers idle 30 minutes or more,
and PRs conflicting at finish.

## The rows that were dropped

Only the shell reader could fill these rows, so neither the log nor the review has them:

- file writes by location: in the worktree, in the project's checkout, on the board,
  elsewhere, or at a place the log could not resolve;
- test and lint runs, with their passes and fails;
- sleep and poll calls;
- stack transitions by workers, and hand edits split by the orchestrator or workers: the
  board records the run that wrote a row, never the agent;
- hand edits of files other than cards, such as run records.

## What the rows cannot show

- Which agent of a run wrote a history row.
- A hand edit committed together with a command's write to the same card.
- A hand edit that appends a well-formed history row and keeps the earlier ones.
