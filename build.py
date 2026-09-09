from __future__ import annotations

import argparse
import os
import shutil
import stat
import subprocess
import sys
import time
import zipfile
from pathlib import Path

from app_metadata import APP_NAME, VERSION_FILENAME, package_zip_filename

ENTRY = "sweptpc.py"
GENERATED_PATHS = ("build", "dist", "__pycache__", f"{APP_NAME}.spec")


def repo_root() -> Path:
    return Path(__file__).resolve().parent


def _case_path(path: Path) -> str:
    return os.path.normcase(os.path.normpath(str(path)))


def _existing_unique_paths(paths: list[Path]) -> list[str]:
    unique: dict[str, str] = {}
    for path in paths:
        if path.exists():
            unique.setdefault(_case_path(path), str(path))
    return list(unique.values())


def build_environment(
    *,
    environ: dict[str, str] | None = None,
    python_executable: str | Path | None = None,
) -> dict[str, str]:
    env = dict(os.environ if environ is None else environ)
    executable = Path(python_executable or sys.executable).resolve()
    python_roots = [
        executable.parent,
        Path(sys.prefix).resolve(),
        Path(sys.base_prefix).resolve(),
    ]
    python_paths: list[Path] = []
    for root in python_roots:
        python_paths.extend([root, root / "Scripts", root / "DLLs"])

    system_root = Path(env.get("SystemRoot") or env.get("WINDIR") or r"C:\Windows")
    system_paths = [
        system_root / "System32",
        system_root,
        system_root / "System32" / "Wbem",
        system_root / "System32" / "WindowsPowerShell" / "v1.0",
    ]

    env["PATH"] = os.pathsep.join(_existing_unique_paths(python_paths + system_paths))
    env.setdefault("PYTHONUTF8", "1")
    return env


def _is_reparse_point(path: Path) -> bool:
    if path.is_symlink():
        return True
    st = os.lstat(path)
    attrs = getattr(st, "st_file_attributes", 0) or 0
    tag = getattr(st, "st_reparse_tag", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool((attrs & reparse_flag) or tag)


def _assert_inside_repo(root: Path, path: Path) -> None:
    resolved_root = root.resolve()
    resolved_path = path.resolve(strict=False)
    if resolved_path == resolved_root or resolved_root not in resolved_path.parents:
        raise RuntimeError(f"Refusing to remove generated path outside repo: {path}")


def remove_generated_path(root: Path, relative_path: str) -> None:
    path = root / relative_path
    if not path.exists() and not path.is_symlink():
        return
    _assert_inside_repo(root, path)
    if _is_reparse_point(path):
        raise RuntimeError(f"Refusing to remove reparse-point build path: {path}")
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()


def clean_previous_outputs(root: Path) -> None:
    for relative_path in GENERATED_PATHS:
        remove_generated_path(root, relative_path)


def pyinstaller_command(root: Path) -> list[str]:
    icon = root / "icon.ico"
    if not icon.is_file():
        raise FileNotFoundError(f"Missing application icon: {icon}")

    version_file = root / VERSION_FILENAME
    if not version_file.is_file():
        raise FileNotFoundError(f"Missing version metadata: {version_file}")

    return [
        sys.executable,
        "-B",
        "-m",
        "PyInstaller",
        "--name",
        APP_NAME,
        "--onedir",
        "--windowed",
        "--noconfirm",
        "--clean",
        "--icon",
        str(icon),
        "--add-data",
        f"{version_file}{os.pathsep}.",
        str(root / ENTRY),
    ]


def root_icu_dlls(internal_dir: Path) -> list[str]:
    return sorted(path.name for path in internal_dir.glob("icu*.dll") if path.is_file())


def create_portable_zip_name(version: str | None = None) -> str:
    return package_zip_filename(version)


def assert_no_root_icu_dlls(internal_dir: Path) -> None:
    leaked = root_icu_dlls(internal_dir)
    if leaked:
        names = ", ".join(leaked)
        raise RuntimeError(
            "Unexpected root-level ICU DLLs collected by PyInstaller: "
            f"{names}. These are usually leaked from PATH by unrelated native "
            "toolchains and can shadow Windows/Qt DLL resolution."
        )


def create_portable_zip(root: Path) -> Path:
    dist_dir = root / "dist" / APP_NAME
    if not dist_dir.is_dir():
        raise FileNotFoundError(f"Missing packaged app directory: {dist_dir}")

    zip_path = root / "dist" / package_zip_filename()
    if zip_path.exists():
        zip_path.unlink()

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in sorted(dist_dir.rglob("*")):
            if path.is_file():
                zf.write(path, arcname=Path(APP_NAME) / path.relative_to(dist_dir))
    return zip_path


def build_app(root: Path | None = None) -> tuple[Path, Path]:
    root = (root or repo_root()).resolve()
    clean_previous_outputs(root)

    subprocess.run(
        pyinstaller_command(root),
        cwd=root,
        env=build_environment(),
        check=True,
    )

    exe = root / "dist" / APP_NAME / f"{APP_NAME}.exe"
    if not exe.is_file():
        raise FileNotFoundError(f"Build did not produce expected executable: {exe}")

    assert_no_root_icu_dlls(root / "dist" / APP_NAME / "_internal")
    zip_path = create_portable_zip(root)
    return exe, zip_path


def smoke_test_executable(exe: Path, *, timeout_seconds: int = 5) -> None:
    if os.name != "nt":
        raise RuntimeError("Packaged executable smoke tests are supported on Windows only")

    process = subprocess.Popen([str(exe)], cwd=str(exe.parent))
    try:
        time.sleep(timeout_seconds)
        exit_code = process.poll()
        if exit_code is not None:
            raise RuntimeError(f"Packaged executable exited during smoke test with code {exit_code}")
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=f"Build {APP_NAME} portable package")
    parser.add_argument(
        "--smoke-test",
        action="store_true",
        help="launch the packaged executable briefly and fail if it exits immediately",
    )
    args = parser.parse_args(argv)

    exe, zip_path = build_app()
    if args.smoke_test:
        smoke_test_executable(exe)

    size_mb = zip_path.stat().st_size / 1024 / 1024
    print(f"Done: {zip_path}  ({size_mb:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
