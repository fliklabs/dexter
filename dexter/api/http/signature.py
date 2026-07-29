"""Turning a request model into a signature the web framework can read.

FastAPI decides what an endpoint accepts by calling `inspect.signature` on it, which honours a
`__signature__` attribute — it never reads source. So an endpoint that really takes `**bound`
can present whatever parameters we want, and every one of them is documented, coerced and
validated by the framework itself. That is the whole reason to build on it rather than parsing
requests by hand, and it is why no code-generation dependency is needed to do this.

**One rule: the path names the path parameters; everything else becomes one payload
parameter.** Two consequences follow, and the split is worth understanding:

- With no `{name}` in the path, the payload parameter *is* the request model. The schema is
  named after it, and every validator on it — including `@model_validator`, which no derived
  model could carry — runs inside the framework's own validation, so the 422 a caller gets is
  as precise as it can be.
- With path parameters, those fields cannot also be read from the body or the query, so the
  remainder is copied into a derived model. Each field is copied as `(annotation, FieldInfo)`,
  which carries its constraints, description, default and alias across intact.

Both branches converge on rebuilding the real request model, so a model-level validator always
runs even when a derived model was used to parse.
"""

import inspect
from collections.abc import Callable
from typing import Annotated, Any

from fastapi import Body, Path, Query
from pydantic import BaseModel, create_model

from ..exposure import HttpExposure, PayloadSource

HTTP_REQUEST = "_dexter_http_request"
"""Name of the injected transport request parameter."""

HTTP_RESPONSE = "_dexter_http_response"
"""Name of the injected temporary response parameter."""

PAYLOAD = "_dexter_payload"
"""Name of the parameter carrying every field the path does not name.

All three begin with an underscore, which pydantic forbids in a field name — so none of them
can ever collide with a field of a consumer's request model.
"""


def build_signature(
    model: type[BaseModel], exposure: HttpExposure, transport: tuple[Any, Any], /
) -> tuple[inspect.Signature, type[BaseModel] | None]:
    """Return the signature to present, and the derived payload model if one was needed.

    Args:
        model: The handler's request model.
        exposure: The route being built.
        transport: The framework's request and response types, passed in so this module
            names them once rather than importing them for annotations.

    Returns:
        The signature, and the derived model — or `None` when the payload parameter is the
        request model itself.
    """
    request_type, response_type = transport
    names = exposure.parameters
    parameters = [
        _keyword(HTTP_REQUEST, request_type),
        _keyword(HTTP_RESPONSE, response_type),
    ]

    for name in names:
        field = model.model_fields[name]
        # `Annotated[...]` takes a tuple, which is what lets the field's own constraints be
        # spread in beside the marker instead of being copied attribute by attribute.
        parameters.append(
            _keyword(
                name,
                Annotated[
                    (
                        field.annotation,
                        Path(description=field.description),
                        *field.metadata,
                    )
                ],
            )
        )

    derived = None if not names else _derive(model, frozenset(names), exposure.source)
    payload = model if derived is None else derived
    marker = Body() if exposure.source is PayloadSource.BODY else Query()
    parameters.append(_keyword(PAYLOAD, Annotated[(payload, marker)]))

    return inspect.Signature(parameters), derived


def build_assembler(
    model: type[BaseModel], exposure: HttpExposure, /
) -> Callable[[dict[str, Any]], BaseModel]:
    """Return a function rebuilding the request model from what the framework bound.

    The rebuild is not redundant. The framework validated whichever model it was given, but
    when that was a derived one it could not have run a `@model_validator` declared on the
    real request model — so the real model is constructed here, and every rule it declares
    applies whichever branch was taken.
    """
    derives = bool(exposure.parameters)

    def assemble(values: dict[str, Any]) -> BaseModel:
        payload: BaseModel = values.pop(PAYLOAD)
        if not derives:
            # No path parameters, so the framework built and validated the real model itself.
            return payload
        carried = {name: getattr(payload, name) for name in type(payload).model_fields}
        return model(**values, **carried)

    return assemble


def _derive(
    model: type[BaseModel], exclude: frozenset[str], source: PayloadSource, /
) -> type[BaseModel]:
    """A model of every field `exclude` does not name.

    Fields are copied as `(annotation, FieldInfo)` pairs, which is what carries their
    constraints and documentation into the generated schema — no attribute of the field is
    reconstructed by hand, so nothing can be dropped by omission.
    """
    fields: dict[str, Any] = {
        name: (field.annotation, field)
        for name, field in model.model_fields.items()
        if name not in exclude
    }
    suffix = "Body" if source is PayloadSource.BODY else "Query"
    derived: type[BaseModel] = create_model(f"{model.__name__}{suffix}", **fields)
    return derived


def _keyword(name: str, annotation: Any) -> inspect.Parameter:
    """One keyword-only parameter. Dependencies are never passed positionally."""
    return inspect.Parameter(
        name, inspect.Parameter.KEYWORD_ONLY, annotation=annotation
    )
