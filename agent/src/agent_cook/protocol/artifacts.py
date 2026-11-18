from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ArtifactRef:
    """A reference to an exported file, with a path relative to its export."""

    id: str
    path: str
    media_type: str
