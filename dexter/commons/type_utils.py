"""Rendering types as the names a developer would recognise.

Every dexter module writes error messages naming a class the reader has to find in their own
wiring, so they all need the same rendering. That is why this lives here rather than in the
module that happened to need it first.
"""


def describe_type(target: object) -> str:
    """Render a type as a readable name, dropping uninformative module prefixes.

    A fully qualified name is what a reader needs to locate the class, except when the prefix
    tells them nothing: `builtins` and `__main__` are noise, and `__qualname__` already carries
    the enclosing class for a nested definition.
    """
    module = getattr(target, "__module__", None)
    name = getattr(target, "__qualname__", None) or getattr(target, "__name__", None)
    if name is None:
        return repr(target)
    if module in (None, "builtins", "__main__"):
        return str(name)
    return f"{module}.{name}"
