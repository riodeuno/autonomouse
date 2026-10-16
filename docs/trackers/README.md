# Trackers

A project can be linked to one third-party tracker, where each of its cards gets a matching
issue that stays in step with the card. This folder holds one agent doc per tracker,
`<kind>.md`, named by the `kind` in the project's `tracker`
(`docs/board-schema/project.md`).
Linear, `linear.md`, is the only tracker today.

The orchestrator, the planner, the worker and the super-orchestrator each have one step
that sends them to `docs/trackers/<kind>.md` when their card's project has a tracker, and to
nothing when it has none. Those steps name no tracker, so adding a tracker changes none of them.

## What a tracker is made of

Adding a tracker, such as GitHub Projects, adds these parts and changes nothing else:

1. **Reading its URLs:** one `Tracker` entry in `src/autonomouse/board/trackers.py`, which
   says which URLs are the tracker's projects and issues. `project new`, `project edit
   --tracker` and `card edit --tracker-issue` accept its URLs from then on.
2. **Setup's access check:** a probe charter, `docs/probes/<kind>.md`, that a small agent
   runs when the owner saves a link, to prove the tracker is reachable before the link is
   stored.
3. **Access for the sessions:** the sessions autonomouse starts can use the tracker's MCP
   server, the only way an agent reaches a tracker.
4. **The agent doc:** `docs/trackers/<kind>.md`, which fulfils the contract below.

## The contract an agent doc fulfils

For its tracker, the doc says:

- how an agent reaches the tracker: through its MCP server, with no API key;
- where a card's issue goes for each kind of link the tracker has;
- what an issue says, per the `project-management` skill's `LINEAR-ISSUES.md`, "What every
  issue contains": context from zero first, then the technical part;
- who files a card's issue (the planner and the orchestrator, for the cards each writes),
  and that it is recorded at once with `autonomouse card edit <id> --tracker-issue <url>`;
- how the issue's status follows the card's PR, per `LINEAR-ISSUES.md`, "Status follows
  the PR", and which agent moves it at each change;
- the orchestrator's catch-up at each wake's start, which files the missing issues of the
  open cards, re-files an open card's issue that sits under a link the owner has since
  replaced and cancels the old one, corrects every status that differs, and says which
  cards stay local;
- how the super-orchestrator, right after it merges pull requests into the project's
  default branch, sets the issues of the merged cards to Done, in a section titled "After
  a merge into the default branch";
- what an agent does when the tracker cannot be reached: it carries on with its own work
  and names what it left unfiled, for the next catch-up;
- how a worker whose card needs a live check makes its own throwaway fixture on the tracker
  and removes it afterwards.
