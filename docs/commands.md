# Commands

Every command prints its options with `--help`, for example `autonomouse card new --help`.
The ones a human uses most:

| command | what it does |
|---|---|
| `autonomouse` | opens the board screen |
| `autonomouse project new <name>` | creates a project from its GitHub URL, and the board when there is none (see [First board](first-project.md)) |
| `autonomouse init` | creates the board without a project, imports a board export |
| `autonomouse run` | runs one wake now; `--project SLUG` wakes one project only |
| `autonomouse github merge <slug> <pr>...` | the super-orchestrator's merge of the pull requests the human told it to merge, named by number or URL: squash-merges into the default branch each one whose checks all passed and that merges cleanly, in stack order (one on another named pull request's branch merges after it, pointed at the default branch), and prints why it leaves any one open; it asks nobody and sends nothing, and with no pull request named it merges nothing; no wake runs it |
| `autonomouse timer install` | writes the three crontab lines (see [The timer](running.md#the-timer)) |
| `autonomouse timer status` | shows the three lines, the next wake and whether cron runs |
| `autonomouse timer remove` | deletes the three lines |
| `autonomouse config show` | prints the effective config, secrets masked |
| `autonomouse list` | prints one line per project: its card count per stack and the wake that holds it, or `free`; plain text, so it works over ssh |
| `autonomouse show <slug>` | prints a project's brief when it has one, then its goal, repository, claim, card counts, open pull requests and last five wakes, then its cards by stack; plain text, so it works over ssh |
| `autonomouse project list` | lists the projects |
| `autonomouse card list` | lists the cards |
| `autonomouse card show <id>` | prints one card |
| `autonomouse card answer <id>` | answers a card's open question |
| `autonomouse plan open` | opens a planning thread with a request |
| `autonomouse plan new` | starts a live planner session in this terminal |
| `autonomouse notify setup` | saves the Telegram chat id (see `docs/config.md`) |
| `autonomouse review daily` | writes the report of one day's work into the board |
| `autonomouse review improve` | proposes instruction changes as backlog cards, from the latest behaviour review |

All command groups:

| group | commands |
|---|---|
| `project` | `new`, `show`, `list`, `edit`, `claim`, `release` |
| `card` | `new`, `show`, `list`, `overlaps`, `move`, `cancel`, `start`, `ask`, `answer`, `progress`, `finish`, `edit`, `delete`, `stuck`, `follow-up` |
| `board` | `export`, `import`, `commit` |
| `plan` | `open`, `reply`, `step`, `join`, `new`, `say`, `cards`, `brief`, `close`, `show`, `list` |
| `run` | no command: one wake; `start`, `end`, `status`, `note` read and write run records; `interim <run id>` makes the wake's shared branch `wake/<run id>`, which the orchestrator asks for only when it cannot reshape the cards that change the same files |
| `log` | `show`, `derive`: how each agent behaved in a wake |
| `review` | `daily`, `behaviour`, `improve` |
| `timer` | `install`, `status`, `remove` |
| `notify` | `notify "<text>"` sends a message; `notify setup`, `notify poll` |
| `github` | `checks`, `comments`, `base-branch`, `sweep`, `merge` |
| `jev` | `probe` |
| `browser` | `lease`, `release <n>`, `logins`, `has-login <domain>`: a card's slot on the browser pool named by `browser.pool` (`docs/config.md`) |
| `config` | `show` |
| `init`, `list`, `show <slug>` | take no command |
| `release` | `major`, `minor`, `patch`: cuts a release of autonomouse itself |

The orchestrator and the workers run most of the `card`, `project`, `run` and `github`
commands themselves; `docs/orchestrator.md` and `docs/worker.md` say which.
