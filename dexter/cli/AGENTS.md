# dexter/cli/AGENTS.md

A keyboard-navigable CLI built from commands registered into a container. Read the source
directly; this file records only the decisions that are not visible in it.

**This module ships no commands.** It is the interface and the conventions; a consumer supplies
everything else. `tools/cli/` in this repository is the worked example.

## Layout

| File | Holds |
| --- | --- |
| `models.py` | `Field` and the reduction of a click command to what a form renders |
| `errors.py` | The exception tree |
| `console.py` | `CliConsole` — the shared rich console and the colour vocabulary |
| `tree.py` | `CommandTree` — the nested click groups every path walks |
| `runner.py` | `invoke` — click parses, dexter awaits; and output capture |
| `use.py` | `use_cli`, `register_command`, `inject` |
| `interactive/` | The curses layer. `menu.py` is its state; the rest is drawing |

## The consumer starts the event loop, not dexter

`AGENTS.md` forbids the library from driving a loop, and a CLI starts in a synchronous process.
The bridge is therefore the consumer's, and it is three lines: `asyncio.run(main())` in their
`__main__.py`. `run` is a coroutine all the way down.

click makes this work. Invoked with `standalone_mode=False` it returns whatever the callback
returned instead of exiting the process, so an `async def` callback hands back a coroutine that
`runner._await` awaits. Nothing here calls `asyncio.run`, and a command is a normal `async def`.

## `inject`'s wrapper is synchronous, and must stay that way

click calls the wrapper inside its context and **leaves that context before the coroutine it
returned is awaited**. An `async def` wrapper looks identical and finds no context at all —
`RuntimeError: There is no active click context`, at a point far from the cause. So the wrapper
is a plain `def` that reads the container immediately and returns a coroutine for later.

## `curses` is imported lazily, and never at module scope

It is in the standard library but not on every platform. `dexter.cli` therefore imports and runs
non-interactively anywhere; only `dexter.cli.interactive` needs a terminal, and `run` reaches it
only after establishing there is one. `tests/test_public_api.py` asserts that importing
`dexter.cli` does not pull `curses` in — if that ever fails, the module has stopped being
portable and nothing else will say so.

## No terminal means help, not a crash

`run` prints help and returns 0 when stdin or stdout is not a terminal. CI, a pipe, and an agent
all take that path. The implementation this is modelled on omits the check and dies inside
curses instead, which reports a broken installation rather than an unsupported environment.

The corollary is the design rule: **the menu is a shell over the command tree, never a separate
path**. Anything reachable by hand is reachable from a script, which is why every option is a
declared click parameter rather than a passthrough — an option the CLI cannot see is an option
the form cannot draw.

## A running command is watched, not awaited

`navigator._run` starts the command as a task and polls the screen around it, with the window
in `nodelay` for the duration. Awaiting it directly — which is what this did first — means the
one thread there is sits inside `getch` for as long as the command lasts, so no key is read,
Ctrl+C cannot be delivered, and anything that does not finish on its own can never be stopped
without killing the terminal.

Three details are load-bearing, and each has a test:

- **The command gets its turn first.** The loop is `await asyncio.wait({task}, timeout=_TICK)`
  and only polls if that times out. `asyncio.wait` returns the moment the task finishes, so a
  command that ends immediately never reaches the poll — and must not, because a key read
  while watching is a key *swallowed*, and it would be the keystroke meant for the screen
  that follows.
- **The modal does not own the loop.** `Modal` is drawing plus one key step, so the question
  "stop this?" is drawn from inside the poll loop and the command keeps running while it sits
  there. Stopping something must not require it to already be stopped.
- **`runner._await` swallows `CancelledError`** and reports `ABORTED`, for the same reason it
  swallows `SystemExit`: the menu holds the terminal, and it also means the output the command
  produced before it was stopped still reaches the final pane.

`tests/cli/screen.py`'s fake window returns `curses.ERR` rather than raising `OutOfKeysError`
while `nodelay` is on. Both halves matter: a *blocking* read that runs out still means a screen
failed to return, which is the bug that error exists to catch.

## Menu state is separate from menu drawing

`interactive/menu.py` holds every decision — the stack, the per-level cursor, what a selection
resolves to — and touches no terminal. Keep new behaviour on that side of the line.

The drawing is tested too, through `tests/cli/screen.py`: a curses window is only `erase`,
`getmaxyx`, `addstr`, `refresh` and `getch`, so a fake one can replay a script of keypresses
and hand back what was drawn. The whole navigator loop runs that way, commands included. That
is what keeps the module above the coverage floor without a single `pragma: no cover`.

## `list_screen` handles going back itself

It returns only "something was selected" or "leave the menu"; ESC in a submenu pops a level and
redraws inside its own loop. Reporting "went back" upwards is what the first version did, and it
was wrong: once the level is popped the caller cannot tell *went back to the root* from *left
from the root*, so one ESC inside a submenu closed the whole menu. `tests/cli/test_navigator.py`
pins it.

## A command's failure must never reach the terminal

The menu holds the terminal in raw mode, so anything unwinding past it leaves a shell the user
has to reset by hand. `runner._await` therefore converts every ending into an exit code:
`SystemExit` is swallowed, a `ClickException` is shown, and an unexpected exception is rendered
into the output pane with its traceback. `Ctrl+C` is delivered as a key by `curses.raw()` rather
than as `KeyboardInterrupt`, and confirmed with a modal — it is far too easy to hit by accident
while reading a form.

## `CommandTree`, not `CommandRegistry`

`dexter.cqrs` already exports a `CommandRegistry`, for an entirely different kind of command.
Two identically named types in one dependency graph is a trap for whoever imports both, and
`dexter.application` will. The name also happens to be more honest: it is a tree of nested
groups, not a flat map.

## Not implemented yet

Shell completion, and a non-interactive `--json` output mode. Both are additive: the command
tree already describes everything either would need.

Leaving a command running in the background and returning to the menu. The watch loop is where
it would go, but the container scope, the output and the stopping of it all need an owner that
outlives one screen, and nothing needs it yet. Interrupting is enough.
