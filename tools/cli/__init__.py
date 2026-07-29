"""This repository's own CLI, built on `dexter.cli`.

`dexter.cli` ships the interface and no commands; everything offered here is registered in
`wiring.py`. That split is the point of the module: another repository writes its own
`wiring.py` and gets the same menu.
"""
