"""The commands this repository offers.

One module per subject. To add a command: write it here with the `@click.command` decorator,
then register it in `tools/cli/wiring.py`. The menu picks it up with no further changes,
because the menu is generated from the same tree the shell parses against.
"""
