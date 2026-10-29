"""Semantic layer (ontology) for TSG PM. See docs/ONTOLOGY_ARCHITECTURE.md.

Structure is declared in ``metadata/*.yaml``; behaviour lives in ``rules.py``.
"""
from .compiler import DEFAULT_METADATA_DIR, OntologyMetadataError, load_ontology
from .runtime import ActionValidationError, OntologyNotFoundError, OntologyService
from .security import OntologyPermissionError
from .versioning import OntologyVersionConflict, OntologyVersionRegistry

__all__ = [
    "DEFAULT_METADATA_DIR",
    "ActionValidationError",
    "OntologyMetadataError",
    "OntologyNotFoundError",
    "OntologyPermissionError",
    "OntologyService",
    "OntologyVersionConflict",
    "OntologyVersionRegistry",
    "load_ontology",
]
