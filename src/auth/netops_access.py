"""Shared capability policy for BFF and FastAPI; patrol use is separate from configuration."""
NETOPS_CONFIGURE = "netops.configure"
NETOPS_CONFIG_GROUP = "netops-config"
READ_METHODS = {"GET", "HEAD", "OPTIONS"}

def requires_netops_configuration(method: str, path: str) -> bool:
    path = path.split("?", 1)[0].rstrip("/")
    for prefix in ("/api/v1", "/api"):
        if path.startswith(prefix + "/"):
            path = path[len(prefix):]
            break
    if path == "/parser" or path.startswith("/parser/"):
        # Read-only definitions needed to explain a patrol's bound scheme.
        return not (method in READ_METHODS and path in {"/parser/command-profiles", "/parser/check-items"})
    if path == "/devices" or path.startswith("/devices/"):
        if path.startswith("/devices/credentials") or path.startswith("/devices/import"):
            return True
        if method in READ_METHODS:
            return False
        if path in {"/devices/patrol/preview", "/devices/patrol/launch"}:
            return False
        if len(path.split("/")) == 4 and path.endswith("/effective-plan"):
            return False
        return True
    return False
