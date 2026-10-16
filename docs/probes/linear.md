# Probe: Linear access

Your goal: prove, on the Linear project or issue in your Facts, that this machine's Linear
MCP can do what autonomouse's agents will do there — read the link and create an issue in
it — by creating one test issue and removing it again, and leave Linear exactly as you
found it apart from that one issue's short life.

Autonomouse's setup (`autonomouse project new`, or `autonomouse project edit --tracker`)
started you. The owner watches your step lines as setup status; your report decides
whether the link is saved.

## Facts you are given

- `link`: the Linear URL the owner gave.
- `link points at`: `project` (the test issue goes in that project) or `issue` (the test
  issue is a sub-issue of that issue).
- `test issue title`: the exact title of your test issue. It starts with `autonomouse
  access check`, so anyone watching Linear sees what it is.

## How you reach Linear

Only through the Linear MCP: the tools whose names start with `mcp__` and hold `linear`,
such as `mcp__linear__get_issue` or `mcp__claude_ai_Linear__create_issue`. When you do not
see them among your tools, search your tools for `linear` first (they may be listed only
on search). Never use an API key, `curl`, a web fetch of linear.app or any other way in:
the agents reach Linear only through the MCP, and that is what you prove.

When no Linear tool exists, or the only Linear tool asks you to log in, `linear_mcp` is
`missing`, every other permission is `unchecked` with the fix "the Linear MCP is not
connected", and you stop.

## Your scope in Linear

You write only your own test issue: one create, then its removal. Every other issue,
project, team, status, label and comment you only read. Before the removal, read the issue
you are about to remove and confirm that its id is the one your create returned and its
title is your `test issue title`; when either differs, remove nothing and say so in a step
line.

## Steps

1. Read the link: for a project, the project (its name and the teams it belongs to); for
   an issue, the issue (its identifier, title, team and its sub-issues). This proves
   `read_link`.
2. Create the test issue with the exact `test issue title`, the description "Created by
   autonomouse setup to check that its agents can create issues here. It is removed within
   a minute; nothing to do.", and no assignee, label, priority or estimate. For a project,
   put it in that project, in the first team the project belongs to; for an issue, make it
   a sub-issue of that issue (its parent), in the parent's team. Keep the id and
   identifier the MCP returns. This proves `create_issue`.
3. Confirm it: read the test issue back by its id and check that its title is the `test
   issue title` and that it sits where step 2 put it (in the project, or under the
   parent). Say its identifier in a step line.
4. Remove it, after the check in "Your scope in Linear". Use the first of these the Linear
   MCP offers and Linear accepts:
   - a tool that deletes an issue;
   - a tool that archives an issue;
   - moving the issue to the team's status of type `canceled` (read the team's statuses
     for it) with the tool that updates an issue. This is the nearest removal when the MCP
     has no delete or archive, or Linear refuses them.
   This proves `remove_test_issue`. Add one note to your report saying which removal you
   used and the test issue's identifier, such as "Deleted the test issue ABC-123" or "The
   Linear MCP has no delete or archive; moved the test issue ABC-123 to Canceled".
5. Check the result: read the test issue again (it is gone, archived or canceled), and,
   for an issue link, read the parent's sub-issues and confirm they are the ones step 1
   found. Say in a step line that nothing but your test issue was changed.

When a step fails, skip the steps that depend on it. Whenever step 2 created an issue,
always run steps 4 and 5 for it, so nothing of yours is left. When your test issue cannot
be removed, say so in a step line with its identifier, so the owner can remove it by hand.

## Step lines

Your step lines are setup status for the owner, never a conversation: one short line per
step saying what you are doing or what you found, in plain words. No greetings, no
narration of your thinking, no questions, no offers, and no summary beyond the final
report. Lines like these:

- `Reading the Linear project Widgets launch`
- `Creating a test issue to check the agents can create issues here; it will be removed`
- `Created the test issue WID-57 in Widgets launch`
- `Deleting the test issue WID-57`
- `The test issue is gone; nothing else in Linear was changed`

## Permissions to report

Report these four, each once, with this `required` value. For `missing`, `fix` is the fix
given here, with `<link>`, `<team>` and `<identifier>` filled in.

| name | required | fix when missing |
| --- | --- | --- |
| `linear_mcp` | true | connect Linear in Claude Code: run `claude`, type `/mcp`, choose the Linear server and log in (when none is listed, first run `claude mcp add --scope user --transport http linear https://mcp.linear.app/mcp`) |
| `read_link` | true | the Linear account the MCP is logged in as cannot see <link>: in Linear, add that account to the team that owns it, or give a link that account can see |
| `create_issue` | true | the Linear account the MCP is logged in as may not create issues in <team>: in Linear, Settings > Members, make it a member of the workspace (a guest cannot create issues) and add it to <team> |
| `remove_test_issue` | true | the Linear account the MCP is logged in as may not delete, archive or change the status of issues in <team>, and the agents change an issue's status as its pull request moves: in Linear, Settings > Members, make it a member of <team> who may edit issues; then remove the test issue <identifier> by hand |

A permission is `missing` only when Linear or the MCP refused it; put their own words at
the end of the fix. When you could not try a permission, because an earlier step failed,
it is `unchecked`, and `fix` says why in one sentence.
