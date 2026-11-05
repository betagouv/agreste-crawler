"""Map ``disaron:nom_old`` to ``disaron:nom`` from inventaire / mapping CSVs.

See ``disaron_mapper.mapping`` for merge order, Garder/Exclure rules, and
defaults (Inventaire v19 + old/new mapping CSV).
"""

from disaron_mapper.mapping import (
    DEFAULT_DISARON_MAPPING_FILES,
    AmbiguousNomError,
    load_disaron_nom_map,
    load_inventaire_nom_map,
    load_old_new_nom_map,
    resolve_disaron_nom,
)

__all__ = [
    "DEFAULT_DISARON_MAPPING_FILES",
    "AmbiguousNomError",
    "load_disaron_nom_map",
    "load_inventaire_nom_map",
    "load_old_new_nom_map",
    "resolve_disaron_nom",
]
