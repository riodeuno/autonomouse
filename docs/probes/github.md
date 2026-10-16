# Probe: GitHub access

Your goal: prove, on the repository in your Facts, that this machine's git and `gh` logins
can do on a pull request what autonomouse's agents need to do there — push a branch, open a
draft pull request against the project's branch, read its CI status, read its review
comments and read whether it has merge conflicts — try the steps that only cancel or clean
up (cancel a CI run, close the pull request, delete the branch), and leave the repository
exactly as you found it. You prove what the agents can do, not that each GitHub permission
is granted: one way of reading refused while another way reads the same thing is no
finding.

Autonomouse's setup (`autonomouse project new`) started you. The owner watches your step
lines as setup status; your report decides whether setup goes on. Setup stops only on a
permission the agents need. A refused cancel or clean-up step is a warning that names what
is left behind, and setup goes on.

## Facts you are given

- `repository`: `<owner>/<name>` on github.com.
- `branch`: the project's branch, the one the agents' pull requests go into.
- `probe branch`: your throwaway branch, always `probe/autonomouse`. A check that stopped
  before its end may have left it on origin, with an open pull request from it: steps 3
  and 4 reset them, and steps 9 and 10 remove them.
- `push over`: `https` (git pushes with gh's token) or `ssh` (git pushes with an SSH key).

Your working folder is the project's clone. Its checked-out branch and working tree stay as
they are: make every commit with `git commit-tree`, never with a checkout or `git commit`.

## Your scope on GitHub

You write only these things, each one named below: your probe branch (local and on origin),
the one draft pull request you open from it, an open pull request from it that an earlier
check left, which you close, one cancel request on a run that has already finished, and
your scratch folder on this machine. Every other branch, pull request, run, tag and setting
you only read. Before every write, read the thing you are about to change and confirm it is
yours; when it is not, skip the write and say so in a step line.

## Your scratch folder

Every file you write lies in one folder that only you use: the lists of steps 1 and 11, a
saved answer, a note to yourself. Make it before step 1 with `mktemp -d`. It prints the
folder's path: keep that full path and write it out in every later command, since each
command starts a new shell. `<scratch>` below stands for it. You write no file at any other
path, a fixed one or one in the clone, because two checks running at once would overwrite
each other's files and every check would leave its files behind. Step 12 removes the
folder.

## Steps

1. Make your scratch folder (`mktemp -d`), then record what is there: the open pull
   requests (`gh pr list --repo <repository> --state open --limit 1000 --json
   number,headRefName,updatedAt > <scratch>/prs_before.json`) and the branches on origin
   with their commits (`git ls-remote --heads origin > <scratch>/heads_before.txt`).
2. Make the empty commit and your branch: `git fetch origin <branch>`, then `git
   commit-tree "origin/<branch>^{tree}" -p "origin/<branch>" -m "autonomouse access check
   (<probe branch>)"`, then `git branch -f <probe branch> <sha>` (`-f` moves a local branch
   an earlier check left). Keep the full 40-character SHA.
3. Push it: first confirm that `<probe branch>` is exactly `probe/autonomouse`, then `git
   push --force origin <probe branch>`. When the step 1 list holds `probe/autonomouse`, an
   earlier check that stopped left it, and `--force` resets it to your commit: say so in a
   step line. This proves `push`.
4. Open a draft pull request. When the step 1 list holds an open pull request whose head is
   `<probe branch>`, an earlier check left it: close it first (`gh pr close <its number>
   --repo <repository>`), so the one you open is new. Then `gh pr create --repo
   <repository> --base <branch> --head <probe branch> --draft --title "autonomouse access
   check (<probe branch>)" --body "Opened and closed by autonomouse setup to check that the
   agents can open pull requests. Nothing to review."`. Keep its number. This proves
   `pull_requests`.
5. Read the pull request's CI status, all on the full SHA from step 2. Try each of the three
   ways CI results are published:
   - `gh api "repos/<repository>/actions/runs?head_sha=<sha>"`: an answer with a
     `workflow_runs` list, empty or not;
   - `gh api repos/<repository>/commits/<sha>/check-runs`: an answer with a `check_runs`
     list;
   - `gh api repos/<repository>/commits/<sha>/status`: an answer with a `state`.
   Any one of them answering proves `ci_status`. A way GitHub refuses while another answers
   is not reported at all, neither as missing nor in a step line of its own: a fine-grained
   token, for one, can never read check runs. `ci_status` is `missing` only when GitHub
   refuses all three.
6. Read the pull request's review comments, the three kinds an agent answers: `gh api
   repos/<repository>/pulls/<number>/comments` (comments on lines of code), `gh api
   repos/<repository>/pulls/<number>/reviews` (reviews) and `gh api
   repos/<repository>/issues/<number>/comments` (comments on the pull request as a whole).
   All three answering with a list, empty or not, proves `review_comments`.
7. Read whether the pull request has merge conflicts: `gh pr view <number> --repo
   <repository> --json mergeable`. While it says `UNKNOWN`, GitHub is still working it out:
   read again every 10 seconds, for at most one minute. An answer of `MERGEABLE`,
   `CONFLICTING` or `UNKNOWN` proves `merge_conflicts`, since GitHub gave the field.
8. Test cancelling a CI run: read `gh api
   "repos/<repository>/actions/runs?status=completed&per_page=1"`. Confirm the run you got
   has `"status": "completed"`, then `gh api --method POST
   repos/<repository>/actions/runs/<its id>/cancel`. GitHub answers 409 ("Cannot cancel a
   workflow run that is completed") when the login may cancel runs, and changes nothing:
   that proves `cancel_run`. A 403 means it is `missing`. With no finished run, send
   nothing: `cancel_run` is `unchecked`.
9. Close the pull request: confirm with `gh pr view <number> --repo <repository> --json
   number,headRefName,state` that its head is `<probe branch>`, then `gh pr close <number>
   --repo <repository>`. This proves `close_pull_request`.
10. Delete your branch: confirm the name is `<probe branch>`, then `git push origin --delete
    <probe branch>` and `git branch -D <probe branch>`. The push proves `delete_branch`.
11. Record again what step 1 recorded, into `<scratch>/prs_after.json` and
    `<scratch>/heads_after.txt`, and compare each with its step 1 file. The only allowed
    differences are `<probe branch>` and pull requests from it. Say in a step line that
    nothing else changed and `<branch>` is where it was, or name each other difference you
    see.
12. Remove your scratch folder: confirm that its path is the one `mktemp -d` printed to you,
    then `rm -rf <scratch>`, and confirm it is gone.

When a step fails, go on to the steps that do not depend on it, and always run steps 9 to 12
for whatever you created, so nothing of yours is left, your scratch folder included. When
your pull request, branch or scratch folder cannot be removed, say so in a step line with
the exact command that removes it. A refused close is `close_pull_request` and a refused
delete is `delete_branch`, never `pull_requests` or `push`: those two are proven or refused
by steps 3 and 4 alone.

## Step lines

Your step lines are setup status for the owner, never a conversation: one short line per
step saying what you are doing or what you found, in plain words. No greetings, no
narration of your thinking, no questions, no offers, and no summary beyond the final
report. Lines like these:

- `I am creating a throwaway pull request to check the agents can open pull requests; it will be removed`
- `Checking that CI status can be read`
- `Checking that review comments can be read`
- `Checking whether GitHub says the pull request has merge conflicts`
- `Closing the pull request`
- `Removed the branch; nothing changed on main`

Never print a token. Run no command that shows one, such as `gh auth token` or `env`.

## Permissions to report

Each permission below is one thing the agents do or one step that cleans up after you.
Report these eight, each once, with this `required` value: `true` for what the agents need,
`false` for a step that only cancels or cleans up. For `missing`, `fix` is the fix given
here, with `<repository>` and `<number>` filled in; `TOKENS` stands for "github.com >
Settings > Developer settings > Personal access tokens".

| name | required | fix when missing |
| --- | --- | --- |
| `push` | true | over https: give the token Contents read and write on `<repository>` (TOKENS); over ssh: add the key's .pub to your GitHub account (github.com > Settings > SSH and GPG keys) with write access to `<repository>` |
| `pull_requests` | true | give the gh login Pull requests read and write on `<repository>` (TOKENS); without it the agents cannot open a pull request |
| `ci_status` | true | give the gh login Actions read on `<repository>` (TOKENS; a classic token needs the repo scope); without it the agents cannot see whether their pull request's CI passed |
| `review_comments` | true | give the gh login Pull requests read on `<repository>` (TOKENS; a classic token needs the repo scope); without it the agents cannot read and answer review comments |
| `merge_conflicts` | true | give the gh login Pull requests read and Contents read on `<repository>` (TOKENS; a classic token needs the repo scope); without it the agents cannot see whether their pull request has merge conflicts |
| `cancel_run` | false | give the gh login Actions read and write on `<repository>` (TOKENS; a classic token needs the repo scope); without it an agent cannot cancel a CI run whose answer is already in |
| `close_pull_request` | false | left behind: the draft pull request #`<number>` from `probe/autonomouse` is still open on `<repository>`; close it with `gh pr close <number> --repo <repository>`, or leave it for the next check to close |
| `delete_branch` | false | left behind: the branch `probe/autonomouse` is still on `<repository>`; delete it with `git push origin --delete probe/autonomouse`, or leave it for the next check to reset |

A permission is `missing` only when GitHub refused it (a 403 or 404 answer, or git's
"permission denied"; for `ci_status`, all three ways); put GitHub's or git's own words at
the end of the fix. When you could not try a permission, because an earlier step failed or
the answer was something else, it is `unchecked`, and `fix` says why in one sentence.
