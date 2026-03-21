from __future__ import annotations

from pathlib import Path


class PathAccessError(ValueError):
    """Raised when a path is outside approved roots."""


class PathGuard:
    @staticmethod
    def normalize(path: str | Path) -> Path:
        return Path(path).expanduser().resolve()

    @classmethod
    def ensure_within_roots(cls, candidate: str | Path, roots: list[str | Path]) -> Path:
        candidate_path = cls.normalize(candidate)
        normalized_roots = [cls.normalize(root) for root in roots]
        for root in normalized_roots:
            if candidate_path == root or root in candidate_path.parents:
                return candidate_path
        raise PathAccessError(f"Blocked path outside allowed roots: {candidate_path}")
