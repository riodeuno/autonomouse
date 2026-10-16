"""Create and read projects and cards.

Pure functions on `board.schema` objects, using `board.store` for the atomic file
I/O and `board.paths.board_root` for the default location. No argparse here —
`autonomouse.cli` wires these to the shell and picks plain-text vs JSON rendering.

One module per record type the commands serve: `projects`, `cards`, and `transfer`
(the whole board's export and import), with what they share in `common`. This
package re-exports every public name of the four, so a caller imports
`autonomouse.board.commands` as one module, and a new command for one record type
edits that type's module and nothing else. It also re-exports three private names by
name: `_find_card_path` and `_one_line`, which modules outside the package import from
it, and `_SLUG_RE`.
"""

from autonomouse.board.commands.cards import *
from autonomouse.board.commands.cards import _one_line as _one_line
from autonomouse.board.commands.common import *
from autonomouse.board.commands.common import _SLUG_RE as _SLUG_RE
from autonomouse.board.commands.common import _find_card_path as _find_card_path
from autonomouse.board.commands.projects import *
from autonomouse.board.commands.transfer import *
