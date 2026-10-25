"""Platform parser control-plane interfaces."""

from .identity import DeviceIdentity, DeviceIdentityResolver
from .policy import ParserPolicy
from .runtime import ParserRuntime, ParserRuntimeResult

__all__ = [
    "DeviceIdentity",
    "DeviceIdentityResolver",
    "ParserPolicy",
    "ParserRuntime",
    "ParserRuntimeResult",
]
