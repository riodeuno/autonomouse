"""`autonomouse github`: pull requests, checks and comments over the gh CLI."""

import json

from autonomouse import github, merge, sweep
from autonomouse.board import commands as board_commands
from autonomouse.board.paths import board_root
from autonomouse.board.schema import BoardValidationError, Card
from autonomouse.cli.shared import _fail


def register(subparsers) -> None:
    github_parser = subparsers.add_parser(
        "github",
        help=(
            "read a PR's checks and comments, compute a card's base branch, sweep a "
            "project's open PRs, merge the ones the owner named — over the gh CLI"
        ),
    )
    github_sub = github_parser.add_subparsers(dest="github_command")

    checks_parser = github_sub.add_parser(
        "checks", help="read every check on a commit, from GitHub Actions and any other CI"
    )
    checks_parser.add_argument("--repo", required=True, help="<owner>/<name>")
    checks_parser.add_argument("--head-sha", required=True)
    checks_parser.add_argument("--json", action="store_true")

    comments_parser = github_sub.add_parser(
        "comments",
        help=(
            "list a pull request's unanswered review comments, each with its link: the ones "
            "the sweep counts, where a reply naming one of the PR's commits answers a comment"
        ),
    )
    comments_parser.add_argument("--repo", required=True, help="<owner>/<name>")
    comments_parser.add_argument("--pr", required=True, type=int)
    comments_parser.add_argument("--exclude-login", default=None)
    comments_parser.add_argument("--json", action="store_true")

    base_branch_parser = github_sub.add_parser(
        "base-branch", help="compute a new card's pull request base branch"
    )
    base_branch_parser.add_argument("id")
    base_branch_parser.add_argument("--project")
    base_branch_parser.add_argument("--json", action="store_true")

    sweep_parser = github_sub.add_parser(
        "sweep",
        help=(
            "one line per open pull request autonomouse opened on a project's repository: "
            "card id, mergeable, the state of its checks, unanswered review comments; a "
            "finding is a conflict, a failed check or an unanswered comment"
        ),
    )
    sweep_parser.add_argument("slug", help="the project")
    sweep_parser.add_argument("--json", action="store_true")

    merge_parser = github_sub.add_parser(
        "merge",
        help=(
            "the super-orchestrator's merge of the PRs the owner told it to merge: "
            "squash-merge each named PR whose checks all passed and that merges cleanly "
            "into the default branch, in stack order, and print why any one is left open; "
            "it asks nobody and sends nothing; each merged or refused card PR is a history "
            "row on its card"
        ),
    )
    merge_parser.add_argument("slug", help="the project")
    merge_parser.add_argument(
        "prs",
        nargs="*",
        metavar="pr",
        help="a pull request the owner told you to merge, by number or URL",
    )

    github_parser.set_defaults(_parser=github_parser)


def run(args) -> None:
    try:
        if args.github_command == "checks":
            checks = github.read_checks(args.repo, head_sha=args.head_sha)
            if args.json:
                print(json.dumps([c.to_dict() for c in checks], indent=2))
            else:
                for c in checks:
                    note = f" {c.note}" if c.note else ""
                    print(f"{c.name}: {c.state} ({c.status}/{c.conclusion}) {c.url}{note}")
        elif args.github_command == "comments":
            pr = github.read_pr(args.repo, str(args.pr))
            review = github.read_unresolved_review(
                args.repo,
                pr_number=args.pr,
                exclude_login=args.exclude_login,
                commits=pr.commits,
            )
            if args.json:
                print(json.dumps(review.to_dict(), indent=2))
            else:
                print(
                    f"{len(review.thread_comments)} thread comment(s), "
                    f"{len(review.change_requests)} change request(s), "
                    f"{len(review.comments)} comment(s)"
                )
                for c in review.thread_comments:
                    where = f"{c.path}:{c.line}"
                    print(f"{c.url} thread comment by {c.author} on {where}: {_one_line(c.body)}")
                for r in review.change_requests:
                    print(f"{r.url} change request by {r.author}: {_one_line(r.body)}")
                for c in review.comments:
                    print(f"{c.url} comment by {c.author}: {_one_line(c.body)}")
        elif args.github_command == "base-branch":
            plan = _github_base_branch(args.id, project=args.project)
            if args.json:
                print(json.dumps(plan.to_dict(), indent=2))
            else:
                print(f"base_branch: {plan.base_branch}")
                for branch in plan.also_merge:
                    print(f"also_merge: {branch}")
        elif args.github_command == "sweep":
            lines = sweep.sweep(board_root(), args.slug)
            if args.json:
                print(json.dumps([line.to_dict() for line in lines], indent=2))
            else:
                for line in lines:
                    print(line.plain())
        elif args.github_command == "merge":
            merge.merge_project(board_root(), args.slug, args.prs)
        else:
            args._parser.error(
                "choose a github command: checks, comments, base-branch, sweep, merge"
            )
    except (github.GitHubError, board_commands.CommandError, BoardValidationError) as e:
        _fail(str(e))


def _one_line(text: str) -> str:
    return " ".join(text.split())


def _github_base_branch(card_id: str, *, project: str | None) -> github.BaseBranchPlan:
    root = board_root()
    card = board_commands.card_show(root, card_id, project=project)
    card_project = board_commands.project_show(root, card.project)

    dependency_cards = [board_commands.card_show(root, dep_id) for dep_id in card.depends_on]

    def _repo(dep_card: Card) -> str:
        dep_project = board_commands.project_show(root, dep_card.project)
        return github.repo_slug(dep_project.repo or card_project.repo)

    return github.compute_base_branch(
        dependency_cards,
        default_branch=card_project.default_branch,
        merged_into=lambda dep_card: github.pr_merged_into(_repo(dep_card), dep_card.pr),
        branch_merged=lambda dep_card, branch: github.branch_pr_merged(
            _repo(dep_card), branch
        ),
    )
