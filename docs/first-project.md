# First board

Create the project autonomouse will work on, from its GitHub URL:

```
autonomouse project new
```

On a terminal the command asks each question with gum, as the install command does: you
type a line of text or pick an answer from a list with the arrow keys and Enter, and a
token you type is not shown. Esc or ctrl-c stops the command at that question; run it
again to continue (below). The command asks, in order: the project's name, as the board
shows it; the repository URL; the name and email the agents commit with; the branch their
pull requests go into; and the Linear project or issue URL the project is linked to, which
Enter leaves blank for none. Enter keeps the default that the commit name, the email and
the branch offer: your GitHub account's name and its no-reply address, and the
repository's default branch. No question asks the project's goal. The command ends by
starting an interactive Claude session in the project's repository folder
(`~/projects/autonomouse/repos/<slug>`), with the model Fable 5.1, high effort and
`--dangerously-skip-permissions`. Claude first asks whether you trust the new folder, with
"No, exit" under the cursor: choose "Yes, I trust this folder". The session's instruction,
two lines, says it is planning this project with you and names the commands it runs
when you agree: `autonomouse project edit <slug> --goal` writes the goal, `autonomouse
plan brief <thread> <file>` writes the project's brief, and `autonomouse project edit
<slug> --orchestrator-instruction "<text>"` or `--worker-instruction "<text>"` records
an instruction of yours for the project's orchestrator or for every worker, such as "Ask
only kite-skyler to review a pull request" or "Enable auto-merge (squash) on the PR".
Each flag repeats, one instruction each, and replaces its whole list; you can run the
same command yourself at any time, and `autonomouse project show <slug>` prints both
lists. When the session ends with the brief written, the project is set up.
When it ends before the brief is written, the planner asks the rest on Telegram (on the board
screen only, with Telegram off); answer it there, or on the board screen (`p` opens the planning threads, `r` answers one). A flag
answers its question in place of asking it: `<name>` or `--name`, `--repo`, `--git-name`,
`--git-email`, `--default-branch`, `--goal` and `--tracker` (`autonomouse project new
--help`); `--goal` is for a script, since no question asks the goal. With no terminal, as
when a script or an agent runs the command with flags, it asks no question with gum: each
is one line of stdin, as `input()` reads it. With stdin ended, each question with a
default takes it and the project is linked to no tracker. With no terminal no session
starts: the planner asks about the project on Telegram.

A setup that stopped before its end is not lost. It stopped at a question you left, at a
step that failed, at the GitHub access check, or at planning, until the project's brief
is written. Run `autonomouse project new` again on a terminal, with no name: it first
lists every such project with the step it stopped at, plus "start a new project". Choose
one, and the setup continues from the step it stopped at: it asks nothing you already
answered and skips each step already done. A setup that stopped at planning goes straight
to a new live Claude session in the project's repository folder, on the same planning
thread. When every setup finished, nothing is listed and the first question is
the project's name. With a name given, or with no terminal, the command starts that
setup from its first question. What each setup was told is kept on this machine, in
`~/.local/state/autonomouse/runs/setup/<slug>.json`, until the project's brief is written.

The Linear link names where a project's cards get their matching Linear issues: issues in
the linked Linear project, or sub-issues of the linked Linear issue. `autonomouse project
edit <slug> --tracker <url>` links a project later or changes its link, and `--tracker
none` removes it. autonomouse stores the link and each card's issue (`card edit <id>
--tracker-issue <url>`) and reads only their URLs; its code never reaches Linear. The
agents file those issues and keep them in step with the cards through the Linear MCP, as
[docs/trackers/linear.md](trackers/linear.md) says: each open card gets an issue, and
the issue's status follows the card's pull request. Changes go from the board to Linear
only. The agents reach Linear only through the Linear MCP in Claude Code, never with an
API key, so connect it first: run `claude`, type `/mcp`, choose the Linear server and log
in. When no Linear server is listed, add it with `claude mcp add --scope user --transport
http linear https://mcp.linear.app/mcp`.

Before a link is saved, by `project new` or by `project edit --tracker`, a small Claude
agent (Sonnet, high effort) checks it, following the charter `docs/probes/linear.md`.
First it reads the linked project or issue. Then it creates one test issue there, titled
`autonomouse access check <UTC timestamp>-<random suffix>`: an issue in the project, or a
sub-issue of the issue. It reads the test issue back, then deletes it. When the Linear MCP
offers no delete, it archives the issue, or else moves it to the team's canceled status,
and the command prints which it did. Before it removes anything, it checks that the issue
is its own test issue. The command prints each of the agent's steps as one plain line,
such as "linear: Created the test issue WID-57 in Widgets launch". When the Linear MCP is
not connected, or Linear refuses to create or remove the test issue, the link is not saved
and the command prints the fix. `project new` then asks for the link again, where a blank
answer links none; given with `--tracker`, it stops before the project is written.

To add another project later, run `autonomouse project new` again and answer the same
questions. Each project gets its own clone under `~/projects/autonomouse/repos/<slug>`,
with its own commit name and email and its own branch. The schedule stays one set of three
crontab lines for all projects.

The install command runs this for you on a fresh machine, as its fourth question: it runs
`autonomouse project new` on the terminal, and you answer these same questions there.

The repository may be empty, with no branch yet, as GitHub makes a new one. Then `project
new` pushes one commit with no files to the branch you chose, `main` by default, which
GitHub makes the default branch, since a worker's pull request needs a base branch. It
adds nothing else to the repository and changes nothing that was there.

`autonomouse project new` creates your first project, and the board with it. It asks
each question its flag does not answer (`autonomouse project new --help`), with gum when
its stdin is a terminal and as a line of stdin when it is not. In
order: it checks that Claude Code is logged in (`claude auth status`), since the GitHub
access check and the planner run as Claude sessions, and when it is not, it stops before
its first question and names `claude auth login`; on a terminal with no name given, it
lists every project whose setup did not finish, plus start a new project, and continues
the one you choose from the step it stopped at (above); it asks the project's name when
no name is given; makes the board folder (`~/.local/share/autonomouse/board`) and makes it a git
repository when there is none; asks the repository URL,
`https://github.com/<owner>/<name>` or `git@github.com:<owner>/<name>.git`; checks that
`gh` is logged in to github.com (`gh auth status`) and, when it is not, asks how: a
personal access token, read without echo and handed to `gh auth login` (it needs Contents
read and write, Pull requests read and write, Actions read and write and Commit statuses
read on the repository; a classic token also `read:org`), or
the path of an SSH key, which the clone then pushes with; clones it into
`~/projects/autonomouse/repos/<slug>` (a clone of the same repository already there is
reused, so a run that stopped continues); asks the name and email the agents commit with
(`--git-name`, `--git-email`) and sets them with `git config` on the clone only, even when
git already has an author; asks the branch the agents' pull requests go into
(`--default-branch`), which must exist on GitHub (one that does not is asked again, and
given as a flag it stops the command), and records it as the project's `default_branch`,
which the access check starts its branch from and opens its pull request against;
checks GitHub access with a small agent (below); writes the project, with the goal
`--goal` gave or an empty one, and commits the board; writes the crontab
lines (see [The timer](running.md#the-timer)); checks that Telegram is set up, that
`TELEGRAM_BOT_TOKEN` is in `~/.config/autonomouse/.env` and the chat id is saved, and
prints the three steps that set it up when it is not (with Telegram off, one line says you
answer on the board); and last plans the project's brief
on its brief thread, a planning thread whose one job is the brief. On a terminal it opens
the thread and runs `claude` in the clone, interactive, with `--model claude-fable-5-1
--effort high --dangerously-skip-permissions` and a two-line instruction that names
`project edit <slug> --goal`, `plan brief <thread> <file>` and `project edit <slug>
--orchestrator-instruction` and `--worker-instruction`. The thread counts as
joined while the session runs, so the planner on Telegram does not answer it then. A
session that ends with the brief written closes the thread; one that ends before leaves
the thread to the planner on Telegram. With no terminal it opens the thread, runs the
planner on it once and prints the planner's first question, which asks what the project
is for; the planner asks the rest on Telegram. Either way the brief is written before the
project gets any card; when the planner's first turn fails, the command says so, and the
next `plan step` runs it again. A
step that fails prints its fix (the token permission that is missing, or adding the key's
`.pub` to your GitHub account) and exits before the project is written, except the GitHub
access check, which waits for you to re-check or quit (below);
autonomouse writes no credential, `gh` stores its own login.

The GitHub access check is a small Claude agent (Sonnet, high effort) that follows the
charter `docs/probes/github.md`. It checks that the agents can do their jobs on a pull
request, on the throwaway branch `probe/autonomouse` made from the project's branch; the
branch always has that name. A check that stopped before its end can leave that branch,
and a pull request from it, on GitHub: the next check resets the branch to its own commit
and closes that pull request before it opens its own. First it lists the open pull
requests and the branches on GitHub. Then it pushes an empty commit to the throwaway
branch and opens a draft pull request from it against the project's branch. Next it reads
that pull request's CI status, its review comments, and whether it has merge conflicts.
After that it asks GitHub to cancel the repository's latest finished Actions run: GitHub
answers 409 and changes nothing, which shows that the login may cancel runs. Last it
closes the pull request, deletes the branch on GitHub and in the clone, lists the open
pull requests and the branches again, and confirms that nothing else changed. Before each
change it makes, it checks that the change touches only its own branch and pull request.
It never changes the clone's checked-out branch. Every file it writes lies in one
temporary folder that it makes first and removes last, also when a step failed, so two
checks at once never share a file and none leaves one behind.

While the agent works, the command prints each of its steps as one plain line, such as
"access: I am creating a throwaway pull request to check the agents can open pull
requests; it will be removed", "access: Checking that CI status can be read", "access:
Closing the pull request" and "access: Removed the branch; nothing changed on main". The
token never reaches the agent's prompt or any printed line.

The agent reports eight abilities. Five are required, the ones the agents need: push a
branch, open a pull request, read its CI status, read its review comments, and read
whether it has merge conflicts. When one of them is missing, the command prints its fix,
the permission to grant on the gh token, and waits for your choice: re-check runs the check
again, and quit stops the command before the project is written. It never stops on its own
there while you can answer. With no one to answer, as when a script or an agent runs it
with flags and stdin has ended, it stops before the project is written and prints the fix.
CI status counts as read when any one of the three ways GitHub publishes it works: Actions
runs, check runs or commit statuses. A fine-grained token can never read check runs; while
it reads Actions runs, the command says nothing about it.

The other three abilities only cancel or clean up, and never stop the command: cancelling
a CI run, closing the test pull request and deleting the throwaway branch. When one is
missing, the command prints a warning and goes on. For the cancel, the warning gives its
fix; a worker then cannot cancel a CI run whose answer is already in, and each such run
uses up Actions minutes. For closing or deleting, the warning names what is left behind on
GitHub, the pull request or the branch, and the command that removes it.

With `--repo-path`, `project new` records an existing checkout and runs nothing else,
except the Linear check when `--tracker` links one.

`autonomouse init` makes the board without a project: the folder and its git repository.
`--import FILE` fills it from a board export (a file written
by `autonomouse board export --out FILE`). On a board with no project it prints
`create one with: autonomouse project new <name>`. Running either command again keeps
what is already there.
