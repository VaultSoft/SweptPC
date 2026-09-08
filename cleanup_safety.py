from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    path: Path | None = None
    reason: str = ""


@dataclass(frozen=True)
class DeleteResult:
    target: str
    category: str | None = None
    bytes_removed: int = 0
    deleted: bool = False
    blocked: bool = False
    error: str = ""


@dataclass
class CleanupOutcome:
    bytes_removed: int = 0
    deleted_count: int = 0
    blocked: list[DeleteResult] = field(default_factory=list)
    failed: list[DeleteResult] = field(default_factory=list)

    def add(self, result: DeleteResult) -> None:
        if result.deleted:
            self.deleted_count += 1
            self.bytes_removed += max(0, result.bytes_removed)
        elif result.blocked:
            self.blocked.append(result)
        elif result.error:
            self.failed.append(result)

    def extend(self, other: "CleanupOutcome") -> None:
        self.bytes_removed += max(0, other.bytes_removed)
        self.deleted_count += other.deleted_count
        self.blocked.extend(other.blocked)
        self.failed.extend(other.failed)


def _case_path(path: Path) -> str:
    return os.path.normcase(os.path.normpath(str(path)))


def _resolve_path(path: str | os.PathLike[str]) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(str(path)))).resolve(strict=False)


def _is_drive_or_filesystem_root(path: Path) -> bool:
    return path.parent == path


def _is_same_or_child(path: Path, root: Path) -> bool:
    path_s = _case_path(path)
    root_s = _case_path(root)
    try:
        return os.path.commonpath([path_s, root_s]) == root_s
    except ValueError:
        return False


def _is_same_path(left: Path, right: Path) -> bool:
    return _case_path(left) == _case_path(right)


def default_protected_roots(
    *,
    user_profile: str | os.PathLike[str] | None = None,
    environ: dict[str, str] | None = None,
) -> list[Path]:
    env = os.environ if environ is None else environ
    roots: list[str | os.PathLike[str]] = [
        user_profile or Path.home(),
        env.get("SystemRoot", r"C:\Windows"),
        env.get("WINDIR", r"C:\Windows"),
        env.get("ProgramFiles", r"C:\Program Files"),
        env.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
        env.get("ProgramData", r"C:\ProgramData"),
    ]
    system_root = env.get("SystemRoot") or env.get("WINDIR") or r"C:\Windows"
    roots.append(os.path.join(system_root, "System32"))
    resolved = []
    for root in roots:
        if root:
            try:
                resolved.append(_resolve_path(root))
            except (OSError, RuntimeError):
                pass
    return resolved


def validate_cleanup_path(
    path: str | os.PathLike[str] | None,
    approved_roots: list[str | os.PathLike[str]],
    *,
    allow_root: bool = False,
    protected_roots: list[str | os.PathLike[str]] | None = None,
) -> ValidationResult:
    if path is None or str(path).strip() == "":
        return ValidationResult(False, reason="empty path")

    raw = Path(os.path.expandvars(os.path.expanduser(str(path))))
    if str(raw).strip() == ".":
        return ValidationResult(False, reason="relative current directory path")
    if not raw.is_absolute():
        return ValidationResult(False, reason="relative path")
    if ".." in raw.parts:
        return ValidationResult(False, reason="parent-directory traversal is not allowed")

    try:
        resolved = raw.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        return ValidationResult(False, reason=f"path could not be resolved: {exc}")

    if _is_drive_or_filesystem_root(resolved):
        return ValidationResult(False, resolved, "drive or filesystem root is not a cleanup target")

    for protected in protected_roots or default_protected_roots():
        protected_path = _resolve_path(protected)
        if _is_same_path(resolved, protected_path):
            return ValidationResult(False, resolved, f"protected root is not a cleanup target: {protected_path}")

    resolved_roots: list[Path] = []
    for root in approved_roots:
        if root is None or str(root).strip() == "":
            continue
        try:
            root_path = _resolve_path(root)
        except (OSError, RuntimeError):
            continue
        if _is_drive_or_filesystem_root(root_path):
            continue
        if any(_is_same_path(root_path, protected) for protected in (protected_roots or default_protected_roots())):
            continue
        resolved_roots.append(root_path)

    if not resolved_roots:
        return ValidationResult(False, resolved, "no approved cleanup root is available")

    for root in resolved_roots:
        if _is_same_path(resolved, root):
            if allow_root:
                return ValidationResult(True, resolved)
            return ValidationResult(False, resolved, "refusing to delete cleanup root itself")
        if _is_same_or_child(resolved, root):
            return ValidationResult(True, resolved)

    return ValidationResult(False, resolved, "path is outside approved cleanup roots")


def is_safe_cleanup_path(
    path: str | os.PathLike[str] | None,
    approved_roots: list[str | os.PathLike[str]],
    *,
    allow_root: bool = False,
    protected_roots: list[str | os.PathLike[str]] | None = None,
) -> bool:
    return validate_cleanup_path(
        path,
        approved_roots,
        allow_root=allow_root,
        protected_roots=protected_roots,
    ).ok


def get_path_size(path: str | os.PathLike[str]) -> int:
    p = Path(path)
    if p.is_file() or p.is_symlink():
        try:
            return max(0, p.stat().st_size)
        except (OSError, PermissionError):
            return 0
    total = 0
    try:
        for dirpath, _, filenames in os.walk(p):
            for filename in filenames:
                fp = os.path.join(dirpath, filename)
                try:
                    if not os.path.islink(fp):
                        total += os.path.getsize(fp)
                except (OSError, PermissionError):
                    pass
    except (OSError, PermissionError):
        pass
    return max(0, total)


def delete_validated_path(
    path: str | os.PathLike[str],
    *,
    approved_roots: list[str | os.PathLike[str]],
    category: str | None = None,
    allow_root: bool = False,
    protected_roots: list[str | os.PathLike[str]] | None = None,
) -> DeleteResult:
    validation = validate_cleanup_path(
        path,
        approved_roots,
        allow_root=allow_root,
        protected_roots=protected_roots,
    )
    target = str(path)
    if not validation.ok:
        return DeleteResult(target, category=category, blocked=True, error=validation.reason)

    resolved = validation.path
    if resolved is None:
        return DeleteResult(target, category=category, blocked=True, error="path could not be resolved")

    try:
        if not resolved.exists() and not resolved.is_symlink():
            return DeleteResult(str(resolved), category=category)
        freed = get_path_size(resolved)
        if resolved.is_file() or resolved.is_symlink():
            resolved.unlink()
        elif resolved.is_dir():
            shutil.rmtree(resolved)
        else:
            return DeleteResult(str(resolved), category=category, blocked=True, error="unsupported filesystem item")
        return DeleteResult(str(resolved), category=category, bytes_removed=freed, deleted=True)
    except (OSError, PermissionError) as exc:
        return DeleteResult(str(resolved), category=category, error=str(exc))


def safe_delete_path(
    path: str | os.PathLike[str],
    *,
    approved_roots: list[str | os.PathLike[str]],
    category: str | None = None,
    allow_root: bool = False,
    protected_roots: list[str | os.PathLike[str]] | None = None,
) -> DeleteResult:
    return delete_validated_path(
        path,
        approved_roots=approved_roots,
        category=category,
        allow_root=allow_root,
        protected_roots=protected_roots,
    )


def cleanup_paths(
    paths: list[str | os.PathLike[str]],
    *,
    approved_roots: list[str | os.PathLike[str]],
    category: str | None = None,
    allow_root: bool = False,
    protected_roots: list[str | os.PathLike[str]] | None = None,
    deleter=None,
) -> CleanupOutcome:
    outcome = CleanupOutcome()
    active_deleter = deleter or delete_validated_path
    for path in paths:
        result = active_deleter(
            path,
            approved_roots=approved_roots,
            category=category,
            allow_root=allow_root,
            protected_roots=protected_roots,
        )
        outcome.add(result)
    return outcome
