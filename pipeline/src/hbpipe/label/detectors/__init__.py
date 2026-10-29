"""Every detector family; importing this package registers them in base.REGISTRY."""

from importlib import import_module

FAMILIES = (
    "movement",
    "camera",
    "mining",
    "build",
    "combat",
    "inventory",
    "interact",
    "meta",
    "activities",
)

for _family in FAMILIES:
    import_module(f"{__name__}.{_family}")
