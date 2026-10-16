# autonomouse

Right after a pull request merges into `main`, whoever merged it cuts a release: on `main`, pulled and with no local changes, run `uv run autonomouse release <part>`. Pick `major` when the change breaks existing installs or config, else `minor` when it adds a feature, else `patch` (fixes, docs, tests, refactors). The command bumps the version, writes the changelog, tags and pushes; if it fails, stop and report its error.
