# Bounded tests

Every card's test ends on its own and gives the human no step (rules, item 9). Before a card
starts, rewrite a test that falls short with `autonomouse card edit <id> --test "…" --note
"<why>"`: a check with no end becomes a command that exits with a result or a file that
holds a named thing, never a review the worker runs (rules, item 9), and a step the human
had becomes what the worker does in its place. Where that step gave an input only the
human can produce, the rewritten test names the fixture that stands for that input: written
in the real input's shape, stored in the repository's own test suite, with the step of the
test that feeds it in. When no bounded test can stand for the goal, the decision gate gets
the question first.
