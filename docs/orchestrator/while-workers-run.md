# While workers run

1. **Watch** through one Monitor (ToolSearch `select:Monitor,TaskStop,SendMessage` loads the
   tools); a sleep loop in a foreground Bash call holds your turn. Any Bash command you run
   meanwhile that may pass two minutes, a `sleep` included, gets a `timeout` (up to 600000
   ms), or the Bash tool moves it to the background at two minutes. Run this command with
   `timeout_ms` 600000, `BOARD`, `<slug>` and `<run id>` filled in, and `AGENTS` a JSON
   object from each running card's id to its worker's `agentId` (`{"<card id>":"<agentId>"}`).
   Every 30 seconds it runs `autonomouse card stuck` on each running card and prints a line,
   once, when a signal starts to fire (`<id> climb`), a card leaves `in_progress`, a PR
   appears (`<id> pr <url>`), a card's note has not moved for 15 minutes, or a `todo` card
   of the project is ready (`<id> ready`: every card in its `depends_on`, in any project, is
   `done`), which you start when a slot is free (`docs/orchestrator.md`, "A wake", step 3).

   ```
   cd BOARD/projects/<slug>/cards && seen=/tmp/autonomouse-watch-<run id> && touch "$seen" && while true; do
     run=$(jq -c 'select(.stack=="in_progress")' *.json)
     { printf '%s\n' "$run" | jq -r '"\(.id) running", (select(.pr) | "\(.id) pr \(.pr)"),
         (select((.handoff.updated_at | fromdateiso8601) < now - 900) | "\(.id) note unmoved since \(.handoff.updated_at)")'
       printf '%s\n' "$run" | jq -r --argjson a 'AGENTS' '"\(.id) \($a[.id] // "")"' | while read -r id agent; do
         autonomouse card stuck "$id" --project <slug> --agent-id "$agent" 2>&1 | sed "s/:.*//; s/^/$id /"
       done
       jq -rs '[.[] | select(.stack=="done") | .id] as $done | .[] | select(.project=="<slug>" and .stack=="todo")
         | select(.depends_on - $done == []) | "\(.id) ready"' ../../*/cards/*.json
     } | sort > "$seen.now"
     comm -13 "$seen" "$seen.now" | grep -vx '[^ ]* running'
     comm -23 "$seen" "$seen.now" | sed -n 's/^\([^ ]*\) running$/\1 left in_progress/p'
     mv "$seen.now" "$seen"; sleep 30
   done
   ```


   A Monitor ends after ten minutes: restart it with the current `AGENTS` until no worker
   you started runs, then stop it with TaskStop; a card started in between is watched
   without its `agentId` until the restart. Read the card each line names. The three stuck
   signals below are an instrument: `autonomouse card stuck <id> --agent-id <agentId>`
   prints the words that fired one. `repeat`: a note
   repeats an earlier next step of the same `iteration`. `climb`: `pr` is unset, and notes
   name a third review round of the same `iteration`, or the worker spawned more than ten
   sub-agents since its last note. `wait`: the worker waits on the project's default
   branch or its base branch turning green, another card, the human, or a later wake,
   which a worker never does. A next step that says an earlier one again in other words is
   stuck too, which `card stuck` cannot see: read the running cards' next steps on each
   expiry.

   A worker writes its note before each step it starts (`docs/worker.md`, "The loop"), so its
   card's `next_step` names the step it is on. On a `note unmoved` line, compare that step
   with what the card's `worktree`, `branch` and `pr` show happened since its
   `handoff.updated_at`. A later step the note does not name, such as a push, a new PR or a CI
   run after a note that names an edit, shows a worker that started a step without a note,
   which is off its charter: tell it by SendMessage to write its note now and before every
   step. When it starts another step without a note after that message, stopping it is your
   decision by point 2. A note that names a step still under way, such as a CI wait, is on its
   charter.

2. **Stopping a worker is your decision**, made from its card's notes and what the signal
   quoted, and written in the card's history. Stop it with TaskStop, its `agentId` as
   `task_id`. After each TaskStop of a worker, stop that worker's background sub-agents as
   `docs/orchestrator.md`, "A wake", step 6, says. A card you stop for a repeat or a climb
   goes back with `autonomouse card move <id> --to todo --note "stopped: <signal>; <what you
   change>, because <why>"`, gets that change, `autonomouse card edit <id> --test "…" --note
   "<why>"` or `--limit …`, and starts again by steps 4 and 5 of "A wake" in
   `docs/orchestrator.md` (its model: `docs/orchestrator/tier-rubric.md`, last paragraph). A
   card you stop for a wait is not restarted: it goes behind what it waits for, and a
   dependency that is not `done` stays: a red base through `docs/orchestrator/red-main.md`,
   another card into its `depends_on`, a later wake by `card move <id> --to todo` alone. For
   a worker stuck again after a restart in this wake, whether it restarts again or its card
   waits in `todo` for the next wake is your decision too, with the reason in the card's
   history.

3. **Cards you file mid-wake**, such as a red base's fix, a capability a worker named (rules,
   item 2) or a split's children, start in this wake once their dependencies are `done` and
   a slot is free, after the ready cards that waited before them; a card whose dependencies
   are not `done` stays first in `todo` for the next wake.
