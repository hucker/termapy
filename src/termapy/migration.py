"""Config schema versioning and migration chain.

Each config has a "config_version" integer. Migration functions transform
configs from one version to the next. On load, migrate_config() runs all
needed migrations sequentially to bring the config up to date.

To add a migration:
    1. Bump CURRENT_CONFIG_VERSION
    2. Write a function: def _migrate_vN_to_vN1(cfg): ... return cfg
    3. Add it to MIGRATIONS: {N: _migrate_vN_to_vN1}
"""

CURRENT_CONFIG_VERSION = 1

# Migration functions: {from_version: callable(cfg) -> cfg}
MIGRATIONS: dict[int, callable] = {}


def migrate_config(cfg: dict) -> dict:
    """Run config through the migration chain to bring it up to date."""
    v = cfg.get("config_version", 0)
    while v < CURRENT_CONFIG_VERSION:
        if v in MIGRATIONS:
            cfg = MIGRATIONS[v](cfg)
        v += 1
    cfg["config_version"] = CURRENT_CONFIG_VERSION
    return cfg
