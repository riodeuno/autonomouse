# autonomouse

CI automatically cuts a patch release after lint and tests pass for a push to `main`. It bumps `pyproject.toml` and `uv.lock`, writes the changelog, and pushes the release commit and tag atomically. Do not cut another patch release after merging. Check the CI release job; report a failure rather than treating the merge as released.

For an intentional minor or major release, run `uv run autonomouse release <minor|major>` from a clean, tested `main` checkout. CI skips commits already carrying their version tag. Automatic releases do not publish the installer; `setup/publish.sh` remains the publishing step.
