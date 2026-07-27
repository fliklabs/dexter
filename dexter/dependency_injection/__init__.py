"""Async-native dependency injection container.

Wire an application with a `ContainerBuilder`, then resolve from the `Container` it builds::

    builder = ContainerBuilder()
    builder.register(Repository).to(SqlRepository, scope=Scope.Scoped)
    builder.register(Pool).to(open_pool, scope=Scope.Singleton)
    container = builder.build()

    async with container.scope() as scope:
        handler = await scope.resolve(Handler)

Resolution is asynchronous throughout; there is no synchronous entry point and nothing here
drives an event loop on your behalf.
"""

from .container import Container as Container
from .container_builder import Binder as Binder
from .container_builder import ContainerBuilder as ContainerBuilder
from .errors import CaptiveDependencyError as CaptiveDependencyError
from .errors import CircularDependencyError as CircularDependencyError
from .errors import ContainerClosedError as ContainerClosedError
from .errors import ContainerStateError as ContainerStateError
from .errors import DependencyInjectionError as DependencyInjectionError
from .errors import DuplicateRegistrationError as DuplicateRegistrationError
from .errors import IncompleteRegistrationError as IncompleteRegistrationError
from .errors import InvalidRegistrationError as InvalidRegistrationError
from .errors import PositionalOnlyParameterError as PositionalOnlyParameterError
from .errors import RegistrationError as RegistrationError
from .errors import ResolutionDepthExceededError as ResolutionDepthExceededError
from .errors import ResolutionError as ResolutionError
from .errors import ScopeClosedError as ScopeClosedError
from .errors import ScopeRequiredError as ScopeRequiredError
from .errors import UnregisteredDependencyError as UnregisteredDependencyError
from .errors import UnresolvableParameterError as UnresolvableParameterError
from .errors import UnresolvedAnnotationError as UnresolvedAnnotationError
from .models import Key as Key
from .models import Provider as Provider
from .models import Registration as Registration
from .models import Scope as Scope
