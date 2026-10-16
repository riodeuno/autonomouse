# Rule 11. Every code change ships with a test in the repository's own suite

**Reader:** the planner, who names the test in the card; the worker, who adds it; and the
orchestrator, whose finish step checks it.

## What the reader does

Every PR that changes code carries at least one test in the repository's own suite (for this
repository: pytest under `tests/`) that fails on the base branch without the change and
passes with it, and that test covers the behaviour the card's goal names. The card's `test`
names that test file. Two kinds of code change carry one more test each. A change to the
shape of a file that a long-lived process reads (a card, project, run or decision file,
which a viewer or a wake keeps reading while the code under it is upgraded) carries a test
that the reader at the previous release tag still loads a file written by the new code: the
test loads `git show <tag>:src/autonomouse/board/schema.py` as a module, or installs the
wheel the installer serves (`WHEEL_URL` in the served `install.sh`), and hands it the new
file. A change to a screen of the viewer carries a headless test that clicks what a mouse
can click on that screen, such as a card title. A doc or charter change carries no test. The one exception is a card that changes no
code: a card that changes only docs or charters, or a card with no code and no doc (a spike,
a judgment for the human). Such a card adds no test to the suite, and its notes say so. The
planner (`docs/planner.md`), the worker (`docs/worker.md`) and the orchestrator's finish
step (`docs/orchestrator.md`, "A wake", step 6) each carry their part.

## Why

A test that fails without the change and passes with it is the evidence that the change does
what its card says, and that evidence is what lets the human trust work that no human
watched being done. For a doc or charter change the human judged that no such test is
needed.

A test that the new code works says nothing about a viewer or a wake that started before the
upgrade and still runs the old code. On the machine the installer set up, that process runs
the installed wheel, which is the previous release, so the previous release's reader is the
one the test hands the new file to. A test that drives a screen only with keys misses what a
mouse does on it.

## The failure it prevents

Code changes that merge with no proof that they work, which the human cannot trust.

The previous-release test and the click test prevent two viewer crashes. A viewer started
before a new card field landed read a card that carried it and died on the unknown field. A
click on a card title while the board changed raised inside Textual and killed the viewer.
