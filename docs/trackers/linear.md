# Linear — each card's issue, kept in step

Your goal here: in a project linked to Linear, every card that needs one has a matching
Linear issue, and that issue always shows where the card's pull request stands. You read
this file because `autonomouse project show <slug>` prints `tracker: linear <link> <url>`.
A project that prints `tracker: (none)` has nothing on Linear, and only the last section,
on a worker's own test fixture, applies to its cards.

The rules for what an issue says and how its status follows a pull request are the
`project-management` skill's `LINEAR-ISSUES.md`
(`~/.claude/skills/project-management/LINEAR-ISSUES.md`). This
file adds only what is specific to autonomouse. Which parts of the file are yours depends
on your role: the planner and the orchestrator file issues, the worker moves its own card's
issue when it opens its PR, the orchestrator also runs the catch-up at the start of each
wake, and the super-orchestrator moves the issues of what it merges into the default branch. The fixtures section applies to any worker whose card needs a live Linear check.

## The link

The project's `tracker` (`docs/board-schema/project.md`) holds the owner's link:

- `link` is `project`: each card's issue is an issue in that Linear project, in the team
  the project belongs to (the first one the Linear MCP lists, when it has several).
- `link` is `issue`: each card's issue is a sub-issue of that Linear issue, in its team.

A card records its issue in `tracker_issue` (`docs/board-schema/card.md`). One card has one
issue.

## Reaching Linear

Reach Linear only through the Linear MCP server's tools. Use no API key and make no HTTP
call of your own. Changes flow one way, from the board to Linear: read Linear only to find
what you are about to write, such as an issue's status or the team's statuses. An edit
someone makes on Linear changes nothing on the board.

When your session has no Linear MCP tool, or a call to it fails, leave Linear alone for the
rest of your turn and carry on with your own work. Your board writes stay as they would be
without Linear. Name each card whose issue you left unfiled or unmoved, and why, in your
report, and the orchestrator also names them in its wake log. The next wake's catch-up
(below) files and moves them.

## The issue

Write each issue as `LINEAR-ISSUES.md`, "What every issue contains", says:

- **Title:** what the card's work changes, in plain words.
- **Description, first part:** the context from zero, for a reader who knows engineering
  and has never seen autonomouse: what is missing or wrong, and what the card changes, with
  every internal name explained at first use. Build it from the card's goal, which opens
  with the goal one level up.
- **Description, second part:** the technical part. It holds the card's goal, limits and
  test as the card states them, the card's PR link once it has one, and, on its last line,
  `autonomouse card: <card id>`.

Before you file, search the link for an issue that already holds this card's work: one
whose description ends with this card's `autonomouse card:` line, as after a filing that
reached Linear while its `card edit` did not run, or one whose scope is exactly the card's
goal and that names no other card. When one exists, record that issue and file none.

Set the new issue's status by the table below. Then record it on the card at once:

```
autonomouse card edit <card id> --tracker-issue <the issue's URL> --run-id <your run id>
```

A planner's run id is its thread id.

When `card edit` changes a card's goal, limits or test, the agent that made the edit
rewrites the card's issue to match.

## Who files

- **The planner** files the issue of each card its thread writes, right after it records
  the card with `plan cards`.
- **The orchestrator** files the issue of each card it writes with `card new`: a split's
  children, a follow-up card, a fix card, a steer's card. The catch-up files every other
  open card's issue.

## Status follows the PR

The issue's status follows the card's pull request, as the table in `LINEAR-ISSUES.md`,
"Status follows the PR", says:

| the card's PR | issue status |
|---|---|
| none yet | Todo |
| open, draft included | In Progress |
| merged | Done |
| closed without merging, and the card is `cancelled` | Canceled |
| none, and the card is `cancelled` | Canceled |
| closed without merging, and the card is still open | Todo |

A card is open while it is in `todo`, `in_progress`, `decision_needed` or `backlog`. An
open card whose PR was closed has its work still ahead, in a PR not yet opened, so its
issue is Todo.

A card split into children is cancelled, so its issue is Canceled too, with a comment that
links each child's issue.

Merged means merged into the project's default branch. A card's PR merged into the wake's
shared branch, `wake/<run id>`, leaves the issue In Progress: the card's work reaches the
default branch through that branch's final PR (`gh pr list --head wake/<run id> --state
all`), and from then on the issue follows the final PR's state. While no final PR exists,
the issue stays In Progress.

A team may name its statuses differently. Read the team's statuses and use the one that
carries each meaning.

Who moves the status:

- **The worker**, when it opens its card's PR: it sets the issue to In Progress, names the
  issue in the PR's description (`Linear: <identifier> <URL>`), and adds the PR's link to
  the issue. A card whose `tracker_issue` is null has no issue for the worker to move: the
  next wake's catch-up files it.
- **The orchestrator**, when it cancels a card, closes a card's PR, or splits a card.
- **The super-orchestrator**, right after it merges pull requests into the project's
  default branch ("After a merge into the default branch", below).
- **The catch-up** at the start of each wake, for every change no agent above saw, such as
  a merge the owner makes on GitHub, which happens outside any wake.

## The catch-up at wake start

The orchestrator runs this once per wake, after the first workers of the wake have started,
or at once in a wake that starts none (`docs/orchestrator.md`, "A wake": step 1 says to
run it, step 5 starts the workers):

1. For every open card whose `tracker_issue` is null, file its issue as above, with the
   status "Status follows the PR" gives.

   An open card whose issue is not under the project's current link counts as having
   none: its issue was filed under a link the owner has since replaced (`project edit
   --tracker`). Read each open card's `tracker_issue` through the Linear MCP. It is under
   the link when it is in the linked Linear project (`link` `project`), or when its parent
   is the linked issue (`link` `issue`). For each card whose issue is not, file its new
   issue under the current link as above, and record it with `card edit --tracker-issue`.
   Then set the old issue to Canceled, with a comment that links the new issue. When the
   old issue cannot be moved, name it by its URL in your wake log: no card names it any
   more, so no later catch-up finds it.
2. List the issues of the link that are not yet Done or Canceled, and the Canceled ones of
   open cards. For each one that a card's `tracker_issue` names, read that card's PR state
   (`gh pr view <pr> --json state,baseRefName`), or the final PR's for a PR merged into the
   wake's shared branch, and correct the issue's status when it differs from what "Status
   follows the PR" gives.

Cards in `done` or `cancelled` with no issue stay local: file nothing for them. So when the
owner adds a link to a project that already has cards, only its open cards get issues, at
the next wake's catch-up. When the owner changes the link, the issues of cards in `done` or
`cancelled` stay where they are, under the old link.

## After a merge into the default branch

The super-orchestrator merges the pull requests the owner names with `autonomouse github
merge` (`docs/super-orchestrator.md`), outside any wake. A project with no ready card gets
no wake, so without this step its merged cards' issues would stay In Progress.

Right after that command, for each pull request it printed as `merged <url> into <default
branch> …`, set these issues to Done:

- When the line ends with a card id, `(<card id>)`, that card's issue.
- When the pull request is a wake's final PR, its head branch `wake/<run id>`, the issue of
  each card whose PR merged into that branch. The PRs are `gh pr list --repo <owner>/<repo>
  --base wake/<run id> --state merged --json url`, and each card is the one whose `pr` is
  that URL in `autonomouse card list --project <slug> --json`.

A card's issue is its `tracker_issue`. A card whose `tracker_issue` is null has no issue to
move: the next wake's catch-up files it. When Linear cannot be reached, "Reaching Linear"
above applies, and the next wake's catch-up moves the issues left behind.

## A worker's own Linear test fixture

A card whose test needs a live check against Linear gets its own throwaway fixture through
the Linear MCP, whether or not its project is linked. It never uses the owner's real
projects or issues, the linked ones included.

1. Make a Linear project, or an issue, whichever the check needs, titled `autonomouse test
   fixture <card id> <run id>` and described as a throwaway made by autonomouse's test of
   that card. Use the team of the project's link when it has one, else a team the Linear MCP
   lists.
2. Run the check against the fixture alone.
3. Remove everything you made before your report: first the fixture's issues and
   sub-issues, then the fixture itself. Use the Linear MCP's delete. When it offers no
   delete, use the nearest removal it offers, such as archive or Canceled, and say in your
   report which one you used, naming each item by its URL.

When the Linear MCP is missing or refuses to make the fixture, the check cannot run, and
the card's test fails for want of access; "Reaching Linear" above, which lets you carry on,
covers only the card's own issue. Say so in your report with the MCP's answer, as
`docs/worker.md`, "When you have a question or are stuck", says; the orchestrator asks the
owner for the access.
