# Run the tests

From a checkout:

```
uv sync --all-groups
uv run ruff check .
uv run pytest -q -n auto
```

`-n auto` (pytest-xdist) runs the tests in one process per CPU core; plain `uv run pytest -q`
runs them one after another and passes too.

CI runs the same three commands on every pull request and on every push to
`main` or to a wake's interim branch `wake/<run id>` (`.github/workflows/ci.yml`). `setup/test-install.sh` runs `install.sh` in a clean
Ubuntu container, and `setup/test-install-questions.sh` answers its setup questions there
on a terminal; both need Docker.
