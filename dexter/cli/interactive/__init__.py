"""The keyboard-driven menu.

Importing this package imports `curses`, so nothing outside it does so at module scope.
`dexter.cli.run` imports it only after establishing that there is a terminal to draw on.

`Menu` is deliberately importable on its own: it holds every decision the menu makes and
touches no terminal, so it can be tested directly.
"""

from .menu import Level as Level
from .menu import Menu as Menu
from .navigator import navigate as navigate
