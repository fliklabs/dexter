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
| `clipboard.py` | `copy` — the platform's own tools, then OSC 52. Imports no curses |
| `interactive/` | The curses layer. `menu.py` and `pane.py` are its state; the rest is drawing |

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

## The running pane scrolls, and `None` means "follow"

`live_screen` takes an offset, and `None` follows the end. A pane that always jumped to the
bottom could not be read while anything was still writing to it — which is exactly the case a
long-running command creates. `scrolled` returns `None` again once the view reaches the bottom,
so scrolling back down resumes following rather than freezing one line short of it and looking
like the output had stopped.

**Every key waiting is taken in one turn.** Reading a single key per turn caps input at one
event per `_TICK`, and a held arrow key or a wheel emits them faster than that — so the backlog
grows for as long as someone keeps scrolling and the view lands where they were a moment ago.
`pane.pending` drains the buffer, with a ceiling so a pasted wall of input cannot starve the
command of the event loop.

`FakeScreen.keys_per_turn` exists for the same reason: a script is the whole session, so
without it a drain would swallow every key the test meant for later screens. One per turn is
the default; raise it to model input arriving faster than the loop turns.

## The wheel: a tick down and a drag are the same event

Two facts about the wheel, both measured by feeding raw legacy reports through ncurses under a
pty rather than reasoned about, because both are the opposite of what the constants suggest.

**`BUTTON5_PRESSED` frequently does not exist.** It is defined only when ncurses was built with
`NCURSES_MOUSE_VERSION` 2. Version 1 gives each of four buttons six mask bits and spends the next
three on `BUTTON_CTRL`, `BUTTON_SHIFT` and `BUTTON_ALT` — there is no bit left for a fifth
button. **macOS ships that build**, so `rendering.WHEEL_DOWN` is `0` there: a mask matching
nothing, on the platform most likely to be running the menu.

**What arrives instead is a bare `REPORT_MOUSE_POSITION` — the identical state a drag's motion
produces.** No amount of looking at one report can tell them apart. What separates them is
context, and only one thing supplies it: this menu asks for mode 1002, *button-event* tracking,
so motion is reported only while a button is held. A bare motion report with no drag in progress
therefore cannot be motion, and `Mouse.wheel(dragging=...)` is where that reasoning lives.

Both directions of getting it wrong are worth naming, because each looks like a different bug:
read as motion, a wheel tick extends a selection nobody is making; read as a wheel, a drag
scrolls the view out from under the text being selected.

**A mouse report must never dismiss a screen.** `getch` announces a tick as `KEY_MOUSE`, which is
simply one more code that is not an arrow key — so the finished-output pager's "any other key
returns" sent a reader who reached for the wheel straight back to the menu, taking the output they
were reading with it. Scrolling is the one gesture that cannot mean "I am finished". Any screen
that reads keys and grew a catch-all needs the same care, and it must *fetch* the report
(`mouse_report`) even when it intends to ignore it: an unfetched report stays queued and is
decoded as stray keys on the next read.

`Mouse` and `mouse_report` live in `rendering` rather than `pane` for an ordinary reason —
`pane` imports `screens`, so `screens` cannot import `pane`, and the pager needs them too.

**Escape sequences arrive in pieces while polling, and are put back together in `pane`.**
`nodelay` tells ncurses to return what it has rather than wait, and an arrow key is three bytes:
it turns up as `27, 91, 65` instead of `KEY_UP`. Mouse reports survive only because ncurses
reads their payload itself rather than through the key table — which is why a drag worked in a
pane whose arrow keys did not. Two symptoms, neither of which looks like its cause: arrow keys
did not scroll a running command, and the leading `27` reads as ESC in the *Stop this?* modal,
so reaching for `→` to answer it cancelled the question instead. `pane.SEQUENCES` reassembles
both cursor forms; a bare ESC is still an ESC, because bytes are only consumed when what follows
spells a key. **A fake screen cannot catch this** — it hands over whatever the script contains,
already assembled. It was found by driving the real menu under a pty and logging what the loop
received, which is the only way this class of bug shows up.

Three things that bit while writing this, all now pinned by tests:

- **`None` is not zero.** The finished pager has nothing to follow, so it resolves `None` to
  the last line. Treating it as `0` sent a reader who scrolled to the bottom back to the top.
- **A page is a page.** Line steps and page steps are separate tables. Folding them into one
  needs a magnitude to mean "and this one is a page", which then multiplies by the window and
  moves two.

## A selection is held in document coordinates, never screen ones

`Selection` is a line index and a column, and `Pane` is what maps a screen row onto a line so it
can be. Anchoring to the screen instead is the obvious implementation and it is wrong: output
arrives mid-drag and the view scrolls under it, so the highlight slides onto words nobody chose.

The rest of the drag follows from that one decision:

- **The release defines the selection, not the motion before it.** ncurses asks the terminal for
  mouse reporting using the `XM` terminfo capability and, where that is missing — which includes
  `xterm-256color` — falls back to a built-in default of mode 1000: presses and releases, *no
  motion at all*. `REPORT_MOUSE_POSITION` surviving in the mask `mousemask` hands back means
  only that ncurses can represent a motion event, never that one will arrive. A selection built
  out of motion alone therefore selects nothing on most terminals: the anchor is never extended,
  the release copies an empty range, and the whole feature looks inert while the view still
  visibly pins. Extending to the release point makes press-then-release sufficient on its own.
- **Mode 1002 is requested by hand**, in `rendering.DRAG_ON`, because ncurses never asks for it.
  That is what makes the highlight live and auto-scroll possible; without it the feature still
  works, it just has nothing to draw until the button comes up. Sent through `sys.stdout` rather
  than `curses.putp`: `putp` writes into the C stdio buffer ncurses flushes on its own schedule,
  and a menu whose whole job is holding a killable command cannot rely on that being flushed.
- **A press pins the view.** Text that is still scrolling cannot be selected. A click that never
  became a drag un-pins it again, so a stray click does not silently freeze the pane.
- **Auto-scroll is applied on the tick, not on the report.** A terminal reports the mouse only
  when it *moves*, so a pointer held just past the bottom edge produces no further events —
  and a scroll that stopped the moment the pointer settled would stop exactly when it was
  wanted. `Pane.drift` is set by the last report and spent every turn, proportional to the
  overshoot and capped, so it reads as aimed rather than switched on.
- **A drag never lands outside the window.** A report past the edge clamps to the first or last
  *visible* line; the auto-scroll then brings the next line to the pointer. Clamping to the
  document instead selects text the reader cannot see being selected.
- **`clipboard.copy` returns whether anything happened**, and the toast says which. Claiming
  "copied" when nothing was is worse than admitting it could not be.

**Mouse reporting takes over the terminal's own selection** for as long as the menu is up. That
is the trade, and it is worth stating plainly: native selection is wiped every time a live pane
redraws, so in the one place it matters most it does not work. Most terminals still give it back
while Shift is held.

`KEY_MOUSE` is only half an event — the details must be fetched with `getmouse` before the next
`getch` or they are lost. That is why draining input lives in `pane.py` next to the decoding
rather than in the navigator: separating them makes the ordering a rule someone has to remember.
`curses.getmouse` is a *module* function, so `pane._mouse` asks the window for one first; that
is the only seam a `FakeScreen` can stand in through.

## A modal is a bordered box, and it floats

It appears over output that is still arriving, and a prompt indistinguishable from one more
line of that output is a prompt people answer by accident. Nothing underneath is erased, and the
pane is repainted immediately *before* the box is drawn — otherwise an interrupt arriving before
the first repaint leaves the box floating over the previous screen.

`Modal` is drawing plus one key step for the same reason: the command carries on while the
question is up.

`Toast` is the same box in the top-right, and deliberately so — a reader has already learnt that
a box means "the menu talking, not the command". It differs where it matters: it reports rather
than interrupts, so it asks nothing and dismisses itself. Expiry is *asked*, not counted:
`expired(now)` takes the time rather than reading a clock, so three seconds is a test assertion
rather than three seconds of waiting.

## Menu state is separate from menu drawing

`interactive/menu.py` holds every decision — the stack, the per-level cursor, what a selection
resolves to — and touches no terminal. Keep new behaviour on that side of the line.

The drawing is tested too, through `tests/cli/screen.py`: a curses window is only `erase`,
`getmaxyx`, `addstr`, `refresh`, `getch` and `getmouse`, so a fake one can replay a script of
keypresses and mouse reports and hand back what was drawn. The whole navigator loop runs that way, commands included. That
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
