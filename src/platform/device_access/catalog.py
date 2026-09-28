"""Server-owned, versioned network collection commands. No model-supplied CLI."""

from .contracts import Command

# Session-scoped terminal settings, selected only after platform identification.
TERMINAL_PREPARATION = {
    "comware": ("screen-length disable",),
    "nxos": ("terminal width 511", "terminal length 0"),
    "ios": ("terminal width 511", "terminal length 0"),
    "vrp": ("screen-length 0 temporary",),
}

DISCOVERY_PROFILES = {
    "comware-lldp-v1": (
        Command("paging", "screen-length disable"),
        Command("neighbors", "display lldp neighbor-information verbose"),
    ),
    "nxos-lldp-v1": (
        Command("paging", "terminal length 0"),
        Command("neighbors", "show lldp neighbors detail"),
    ),
}

DIAGNOSTIC_PROFILES = {
    "comware-interfaces-v1": (
        Command("paging", "screen-length disable"),
        Command("interfaces", "display interface brief"),
    ),
    "comware-routes-v1": (
        Command("paging", "screen-length disable"),
        Command("routes", "display ip routing-table"),
    ),
    "nxos-interfaces-v1": (
        Command("paging", "terminal length 0"),
        Command("interfaces", "show interface brief"),
    ),
    "nxos-routes-v1": (Command("paging", "terminal length 0"), Command("routes", "show ip route")),
    **DISCOVERY_PROFILES,
}
