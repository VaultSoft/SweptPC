import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

import app_metadata
import build
import sweptpc


ROOT = Path(__file__).resolve().parents[1]


class ReleasePackagingTests(unittest.TestCase):
    def test_version_json_is_authoritative_app_version(self):
        metadata = json.loads((ROOT / app_metadata.VERSION_FILENAME).read_text(encoding="utf-8"))

        self.assertRegex(metadata["version"], r"^\d+\.\d+\.\d+$")
        self.assertEqual(metadata["version"], app_metadata.APP_VERSION)
        self.assertEqual(metadata["version"], sweptpc.APP_VERSION)

    def test_package_filename_uses_authoritative_version(self):
        expected = f"SweptPC-v{app_metadata.APP_VERSION}-portable.zip"

        self.assertEqual(expected, app_metadata.package_zip_filename())
        self.assertEqual(expected, build.create_portable_zip_name())

    def test_no_legacy_hardcoded_app_version_assignments(self):
        assignment = re.compile(r"\bAPP_VERSION\s*=\s*['\"]\d+\.\d+\.\d+['\"]")

        for relative_path in ("sweptpc.py", "build.py"):
            text = (ROOT / relative_path).read_text(encoding="utf-8")
            self.assertNotRegex(text, assignment, relative_path)
            self.assertNotIn("1.0.0", text, relative_path)

    def test_build_configuration_has_no_josh_specific_fallback_paths(self):
        text = (ROOT / "build.py").read_text(encoding="utf-8")
        command = build.pyinstaller_command(ROOT)

        self.assertNotIn(r"C:\Users\Josh", text)
        self.assertNotIn("Screenshot 2026-04-17", text)
        self.assertIn(str(ROOT / "icon.ico"), command)

    def test_build_environment_excludes_external_dll_search_paths(self):
        original_path = os.pathsep.join(
            [
                r"C:\Users\Josh\.cache\codex-runtimes\codex-primary-runtime\dependencies\native\poppler\Library\bin",
                r"C:\Tools\SomeOtherDllBin",
            ]
        )

        env = build.build_environment(environ={"PATH": original_path}, python_executable=sys.executable)
        path_entries = env["PATH"].lower().split(os.pathsep)

        self.assertFalse(any("codex-runtimes" in entry for entry in path_entries))
        self.assertFalse(any("someotherdllbin" in entry for entry in path_entries))

    def test_root_level_icu_guard_fails_loudly_but_allows_nested_qt_dlls(self):
        with tempfile.TemporaryDirectory() as tmp:
            internal = Path(tmp)
            qt_bin = internal / "PyQt6" / "Qt6" / "bin"
            qt_bin.mkdir(parents=True)
            (qt_bin / "icuuc.dll").write_bytes(b"qt-owned")

            build.assert_no_root_icu_dlls(internal)

            (internal / "icuuc.dll").write_bytes(b"path-leaked")
            with self.assertRaises(RuntimeError):
                build.assert_no_root_icu_dlls(internal)


if __name__ == "__main__":
    unittest.main()
