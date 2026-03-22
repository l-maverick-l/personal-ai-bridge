from __future__ import annotations

from pathlib import Path


class PathAccessError(ValueError):
    """Raised when a path is outside approved roots."""


class PathGuard:
    @staticmethod
    def normalize(path: str | Path) -> Path:
        return Path(path).expanduser().resolve()

    @classmethod
    def normalize_roots(cls, roots: list[str | Path]) -> list[Path]:
        return [cls.normalize(root) for root in roots]

    @classmethod
    def ensure_within_roots(cls, candidate: str | Path, roots: list[str | Path]) -> Path:
        candidate_path = cls.normalize(candidate)
        normalized_roots = cls.normalize_roots(roots)
        for root in normalized_roots:
            if candidate_path == root or root in candidate_path.parents:
                return candidate_path
        raise PathAccessError(f"Blocked path outside allowed roots: {candidate_path}")

    @classmethod
    def ensure_allowed_root(cls, root: str | Path, roots: list[str | Path]) -> Path:
        root_path = cls.normalize(root)
        for allowed_root in cls.normalize_roots(roots):
            if root_path == allowed_root:
                return root_path
        raise PathAccessError(f"Blocked root outside allowed roots: {root_path}")

    @classmethod
    def resolve_relative_path(cls, root: str | Path, relative_path: str) -> Path:
        root_path = cls.normalize(root)
        relative = relative_path.strip().replace('\\', '/')
        if not relative or relative == '.':
            return root_path
        candidate = (root_path / relative).resolve()
        if candidate != root_path and root_path not in candidate.parents:
            raise PathAccessError(f"Blocked path traversal outside allowed root: {candidate}")
        return candidate
