"""Ghost in the Template - GGUF chat template integrity verification tool."""

__version__ = "0.1.0"

# Exit codes
EXIT_CLEAN = 0
EXIT_DETECTION = 1
EXIT_ERROR = 2


class GhostError(Exception):
    """Base exception for all ghost-in-the-template errors."""


class ExtractionError(GhostError):
    """Failed to extract template from GGUF file."""


class NormalizationError(GhostError):
    """Failed to normalize a template string."""


class AnalysisError(GhostError):
    """Failed during structural analysis."""
