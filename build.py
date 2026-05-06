import os, sys, shutil, subprocess, zipfile
from pathlib import Path

APP_NAME    = "SweptPC"
APP_VERSION = "1.0.0"
ENTRY       = "sweptpc.py"

def main():
    root = Path(__file__).parent
    for d in ("build", "dist", "__pycache__"):
        t = root / d
        if t.exists():
            shutil.rmtree(t)
    spec = root / f"{APP_NAME}.spec"
    if spec.exists():
        spec.unlink()

    icon_args = []
    for name in ("icon.ico", "icon.png", r"C:\Users\Josh\Desktop\Screenshot 2026-04-17 224518.png"):
        p = name if os.path.isabs(name) else str(root / name)
        if os.path.exists(p):
            icon_args = ["--icon", p]
            break

    cmd = [sys.executable, "-m", "PyInstaller",
           "--name", APP_NAME, "--onedir", "--windowed",
           "--noconfirm", "--clean"] + icon_args + [ENTRY]
    subprocess.run(cmd, check=True)

    exe = root / "dist" / APP_NAME / f"{APP_NAME}.exe"
    if not exe.exists():
        print("Build failed"); sys.exit(1)

    zip_path = root / "dist" / f"{APP_NAME}-v{APP_VERSION}-portable.zip"
    dist_dir = root / "dist" / APP_NAME
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in dist_dir.rglob("*"):
            zf.write(f, arcname=Path(APP_NAME) / f.relative_to(dist_dir))
    print(f"Done: {zip_path}  ({zip_path.stat().st_size/1024/1024:.1f} MB)")

if __name__ == "__main__":
    main()
