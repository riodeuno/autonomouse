"""`autonomouse release`: cut a release of autonomouse."""

from pathlib import Path

from autonomouse.cli.shared import _fail


def register(subparsers) -> None:
    release_cmd_parser = subparsers.add_parser(
        "release",
        help="cut a release: bump the version, write CHANGELOG.md, commit, tag, push",
        description=(
            "Run in a clean checkout of autonomouse on main. Bumps the version in "
            "pyproject.toml and uv.lock, prepends a CHANGELOG.md section listing the "
            "pull requests merged since the previous tag, commits, tags v<version> and "
            "pushes the commit and the tag to origin together."
        ),
    )
    release_cmd_parser.add_argument("part", choices=["major", "minor", "patch"])


def run(args) -> None:
    from autonomouse.release import ReleaseError, release

    try:
        cut = release(Path.cwd(), args.part)
    except (ReleaseError, OSError) as e:
        _fail(str(e))
    print(
        f"released {cut.tag} ({cut.previous} -> {cut.tag}, commit {cut.commit[:12]}, "
        f"{len(cut.titles)} pull requests) and pushed it"
    )
