import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cleanup_safety import (
    CleanupOutcome,
    DeleteResult,
    cleanup_paths,
    delete_validated_path,
    get_scan_size,
    is_reparse_point,
    is_safe_cleanup_path,
    validate_cleanup_path,
)


class CleanupSafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.root = self.base / "approved"
        self.root.mkdir()
        self.protected = self.base / "profile"
        self.protected.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def _protected_roots(self):
        return [self.protected]

    def _make_dir_symlink_or_skip(self, target, link):
        try:
            os.symlink(target, link, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"directory symlink unavailable: {exc}")

    def _make_junction_or_skip(self, target, link):
        if os.name != "nt":
            self.skipTest("Windows junctions require Windows")
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            self.skipTest(f"junction creation unavailable: {result.stderr or result.stdout}")
        self.addCleanup(self._remove_junction, link)

    def _remove_junction(self, link):
        if os.name == "nt" and os.path.exists(link):
            subprocess.run(["cmd", "/c", "rmdir", str(link)], capture_output=True, text=True)

    def test_allows_file_under_approved_root(self):
        target = self.root / "cache.tmp"
        target.write_bytes(b"abc")

        self.assertTrue(
            is_safe_cleanup_path(target, [self.root], protected_roots=self._protected_roots())
        )

    def test_allows_nested_directory_under_approved_root(self):
        target = self.root / "profile" / "cache2"
        target.mkdir(parents=True)

        self.assertTrue(
            is_safe_cleanup_path(target, [self.root], protected_roots=self._protected_roots())
        )

    def test_allows_normal_real_directory_under_approved_root(self):
        target = self.root / "real-cache"
        target.mkdir()
        (target / "cache.tmp").write_bytes(b"abcd")

        result = validate_cleanup_path(target, [self.root], protected_roots=self._protected_roots())

        self.assertTrue(result.ok)

    def test_blocks_filesystem_root(self):
        result = validate_cleanup_path(
            Path(self.base.anchor), [self.root], protected_roots=self._protected_roots()
        )

        self.assertFalse(result.ok)
        self.assertIn("root", result.reason)

    def test_blocks_parent_of_approved_root(self):
        self.assertFalse(
            is_safe_cleanup_path(self.base, [self.root], protected_roots=self._protected_roots())
        )

    def test_blocks_sibling_outside_approved_root(self):
        sibling = self.base / "sibling"
        sibling.mkdir()

        self.assertFalse(
            is_safe_cleanup_path(sibling, [self.root], protected_roots=self._protected_roots())
        )

    def test_blocks_empty_path(self):
        result = validate_cleanup_path("", [self.root], protected_roots=self._protected_roots())

        self.assertFalse(result.ok)
        self.assertIn("empty", result.reason)

    def test_blocks_relative_path(self):
        result = validate_cleanup_path("cache.tmp", [self.root], protected_roots=self._protected_roots())

        self.assertFalse(result.ok)
        self.assertIn("relative", result.reason)

    def test_blocks_traversal_attempt(self):
        target = self.root / ".." / "outside.tmp"

        result = validate_cleanup_path(target, [self.root], protected_roots=self._protected_roots())

        self.assertFalse(result.ok)
        self.assertIn("traversal", result.reason)

    def test_windows_style_case_variation_when_relevant(self):
        target = self.root / "CaseCache.tmp"
        target.write_bytes(b"x")
        varied = str(target).swapcase()

        self.assertTrue(
            is_safe_cleanup_path(varied, [self.root], protected_roots=self._protected_roots())
        )

    def test_blocks_approved_root_symlink_redirect_to_outside(self):
        outside = self.base / "outside"
        outside.mkdir()
        (outside / "victim.tmp").write_bytes(b"secret")
        root_link = self.base / "root-link"
        self._make_dir_symlink_or_skip(outside, root_link)

        result = validate_cleanup_path(
            root_link / "victim.tmp",
            [root_link],
            protected_roots=self._protected_roots(),
        )

        self.assertFalse(result.ok)
        self.assertIn("reparse-point", result.reason)

    def test_blocks_approved_root_junction_redirect_to_outside(self):
        outside = self.base / "junction-outside"
        outside.mkdir()
        (outside / "victim.tmp").write_bytes(b"secret")
        root_junction = self.base / "root-junction"
        self._make_junction_or_skip(outside, root_junction)

        result = validate_cleanup_path(
            root_junction / "victim.tmp",
            [root_junction],
            protected_roots=self._protected_roots(),
        )

        self.assertFalse(result.ok)
        self.assertIn("reparse-point", result.reason)

    def test_blocks_injected_approved_root_reparse_redirect(self):
        redirected_root = self.base / "redirected-root"
        redirected_root.mkdir()
        target = redirected_root / "victim.tmp"
        target.write_bytes(b"secret")

        result = validate_cleanup_path(
            target,
            [redirected_root],
            protected_roots=self._protected_roots(),
            reparse_checker=lambda path: path == redirected_root,
        )

        self.assertFalse(result.ok)
        self.assertIn("reparse-point", result.reason)

    def test_blocks_parent_component_junction_under_anchor(self):
        outside = self.base / "parent-junction-outside"
        outside.mkdir()
        redirected_cache = outside / "cache"
        redirected_cache.mkdir()
        (redirected_cache / "victim.tmp").write_bytes(b"secret")
        anchor = self.base / "anchor"
        anchor.mkdir()
        parent_junction = anchor / "redirected-parent"
        self._make_junction_or_skip(outside, parent_junction)

        result = validate_cleanup_path(
            parent_junction / "cache" / "victim.tmp",
            [parent_junction / "cache"],
            protected_roots=self._protected_roots(),
        )

        self.assertFalse(result.ok)
        self.assertIn("reparse-point", result.reason)

    def test_blocks_injected_parent_component_reparse(self):
        anchor = self.base / "injected-anchor"
        parent = anchor / "redirected-parent"
        approved = parent / "cache"
        target = approved / "victim.tmp"
        approved.mkdir(parents=True)
        target.write_bytes(b"secret")

        result = validate_cleanup_path(
            target,
            [approved],
            protected_roots=self._protected_roots(),
            reparse_checker=lambda path: path == parent,
        )

        self.assertFalse(result.ok)
        self.assertIn("reparse-point", result.reason)

    def test_blocks_nested_symlink_redirect_to_outside(self):
        outside = self.base / "outside-nested"
        outside.mkdir()
        (outside / "victim.tmp").write_bytes(b"secret")
        nested_link = self.root / "nested-link"
        self._make_dir_symlink_or_skip(outside, nested_link)

        result = validate_cleanup_path(
            nested_link / "victim.tmp",
            [self.root],
            protected_roots=self._protected_roots(),
        )

        self.assertFalse(result.ok)
        self.assertIn("reparse-point", result.reason)

    def test_blocks_nested_child_junction_to_outside(self):
        outside = self.base / "nested-junction-outside"
        outside.mkdir()
        (outside / "victim.tmp").write_bytes(b"secret")
        target = self.root / "parent-with-junction"
        target.mkdir()
        nested_junction = target / "nested-junction"
        self._make_junction_or_skip(outside, nested_junction)

        result = delete_validated_path(
            target,
            approved_roots=[self.root],
            protected_roots=self._protected_roots(),
        )

        self.assertFalse(result.deleted)
        self.assertTrue(result.blocked)
        self.assertIn("reparse-point child", result.error)
        self.assertTrue((outside / "victim.tmp").exists())

    def test_blocks_directory_delete_when_subtree_contains_reparse_child(self):
        target = self.root / "parent"
        target.mkdir()
        (target / "cache.tmp").write_bytes(b"safe")
        redirect_child = target / "redirect-child"
        redirect_child.mkdir()

        result = delete_validated_path(
            target,
            approved_roots=[self.root],
            protected_roots=self._protected_roots(),
            reparse_checker=lambda path: path == redirect_child,
        )

        self.assertFalse(result.deleted)
        self.assertTrue(result.blocked)
        self.assertIn("reparse-point child", result.error)
        self.assertTrue(target.exists())

    def test_blocks_sensitive_documents_descendant_even_if_approved_root(self):
        documents = self.protected / "Documents"
        documents.mkdir()
        target = documents / "important.txt"
        target.write_bytes(b"secret")

        result = validate_cleanup_path(
            target,
            [documents],
            protected_roots=self._protected_roots(),
        )

        self.assertFalse(result.ok)
        self.assertIn("sensitive protected descendant", result.reason)

    def test_blocks_redirect_to_sensitive_documents_descendant(self):
        documents = self.protected / "Documents"
        documents.mkdir()
        (documents / "important.txt").write_bytes(b"secret")
        root_link = self.base / "cache-link-to-documents"
        self._make_dir_symlink_or_skip(documents, root_link)

        result = validate_cleanup_path(
            root_link / "important.txt",
            [root_link],
            protected_roots=self._protected_roots(),
        )

        self.assertFalse(result.ok)
        self.assertIn("reparse-point", result.reason)

    def test_allows_explicit_logical_target_beneath_protected_anchor(self):
        logical_temp = self.protected / "AppData" / "Local" / "Temp"
        logical_temp.mkdir(parents=True)
        target = logical_temp / "cache.tmp"
        target.write_bytes(b"cache")

        result = validate_cleanup_path(
            target,
            [logical_temp],
            protected_roots=self._protected_roots(),
        )

        self.assertTrue(result.ok)

    def test_blocks_when_reparse_safety_cannot_be_established(self):
        target = self.root / "cache.tmp"
        target.write_bytes(b"abc")

        def unavailable(_path):
            raise OSError("inspection unavailable")

        result = validate_cleanup_path(
            target,
            [self.root],
            protected_roots=self._protected_roots(),
            reparse_checker=unavailable,
        )

        self.assertFalse(result.ok)
        self.assertIn("could not inspect", result.reason)

    def test_blocks_user_profile_equivalent_root(self):
        result = validate_cleanup_path(
            self.protected,
            [self.protected],
            allow_root=True,
            protected_roots=self._protected_roots(),
        )

        self.assertFalse(result.ok)
        self.assertIn("protected root", result.reason)

    def test_deletes_validated_file_and_reports_bytes(self):
        target = self.root / "cache.tmp"
        target.write_bytes(b"abcd")

        result = delete_validated_path(
            target,
            approved_roots=[self.root],
            protected_roots=self._protected_roots(),
        )

        self.assertTrue(result.deleted)
        self.assertEqual(4, result.bytes_removed)
        self.assertFalse(target.exists())

    def test_deletes_validated_nested_directory(self):
        target = self.root / "nested"
        target.mkdir()
        (target / "cache.tmp").write_bytes(b"abcd")

        result = delete_validated_path(
            target,
            approved_roots=[self.root],
            protected_roots=self._protected_roots(),
        )

        self.assertTrue(result.deleted)
        self.assertEqual(4, result.bytes_removed)
        self.assertFalse(target.exists())

    def test_deletion_failure_is_captured(self):
        target = self.root / "locked"
        target.mkdir()
        (target / "cache.tmp").write_bytes(b"abcd")

        with mock.patch.object(shutil, "rmtree", side_effect=PermissionError("locked")):
            result = delete_validated_path(
                target,
                approved_roots=[self.root],
                category="cache",
                protected_roots=self._protected_roots(),
            )

        self.assertFalse(result.deleted)
        self.assertFalse(result.blocked)
        self.assertEqual("cache", result.category)
        self.assertIn("locked", result.error)
        self.assertTrue(target.exists())

    def test_one_failed_item_does_not_stop_processing_another(self):
        failed = self.root / "locked.tmp"
        ok = self.root / "ok.tmp"
        failed.write_bytes(b"x")
        ok.write_bytes(b"yy")

        def fake_deleter(path, **kwargs):
            if Path(path).name == "locked.tmp":
                return DeleteResult(str(path), category=kwargs.get("category"), error="locked")
            Path(path).unlink()
            return DeleteResult(str(path), category=kwargs.get("category"), bytes_removed=2, deleted=True)

        outcome = cleanup_paths(
            [failed, ok],
            approved_roots=[self.root],
            category="cache",
            protected_roots=self._protected_roots(),
            deleter=fake_deleter,
        )

        self.assertEqual(1, outcome.deleted_count)
        self.assertEqual(2, outcome.bytes_removed)
        self.assertEqual(1, len(outcome.failed))
        self.assertTrue(failed.exists())
        self.assertFalse(ok.exists())

    def test_clean_worker_passes_runtime_category_roots_to_safety_validator(self):
        import sweptpc

        category_root = self.root / "runtime-category"
        category_root.mkdir()
        child = category_root / "cache.bin"
        child.write_bytes(b"x")
        cfg = dict(sweptpc.CLEANUP_TARGETS["chrome_cache"])
        cfg["paths"] = [str(category_root)]
        calls = []

        def fake_delete(path, **kwargs):
            calls.append((str(path), kwargs))
            return DeleteResult(str(path), category=kwargs.get("category"))

        with mock.patch.dict(sweptpc.CLEANUP_TARGETS, {"chrome_cache": cfg}):
            with mock.patch.object(sweptpc, "validate_cleanup_path", wraps=sweptpc.validate_cleanup_path) as validate_spy:
                with mock.patch.object(sweptpc, "safe_delete_path", side_effect=fake_delete):
                    sweptpc.CleanWorker(["chrome_cache"]).run()

        self.assertTrue(
            any(
                call.args[:2] == (str(category_root), [str(category_root)])
                and call.kwargs.get("allow_root") is True
                for call in validate_spy.call_args_list
            )
        )
        self.assertEqual([(str(child), {"approved_roots": [str(category_root)], "category": "chrome_cache"})], calls)
        self.assertTrue(child.exists())

    def test_clean_worker_blocks_runtime_category_symlink_root_before_deleting(self):
        import sweptpc

        outside = self.base / "worker-outside"
        outside.mkdir()
        victim = outside / "victim.tmp"
        victim.write_bytes(b"secret")
        root_link = self.base / "worker-cache-link"
        self._make_dir_symlink_or_skip(outside, root_link)
        cfg = dict(sweptpc.CLEANUP_TARGETS["chrome_cache"])
        cfg["paths"] = [str(root_link)]
        calls = []

        def fake_delete(path, **kwargs):
            calls.append((str(path), kwargs))
            return DeleteResult(str(path), category=kwargs.get("category"))

        with mock.patch.dict(sweptpc.CLEANUP_TARGETS, {"chrome_cache": cfg}):
            with mock.patch.object(sweptpc, "safe_delete_path", side_effect=fake_delete):
                sweptpc.CleanWorker(["chrome_cache"]).run()

        self.assertEqual([], calls)
        self.assertTrue(victim.exists())

    def test_scan_size_skips_nested_junction_and_counts_plain_files(self):
        outside = self.base / "scan-outside"
        outside.mkdir()
        (outside / "keep.txt").write_bytes(b"k" * 700)
        (self.root / "junk.tmp").write_bytes(b"j" * 300)
        (self.root / "sub").mkdir()
        (self.root / "sub" / "deep.tmp").write_bytes(b"d" * 50)
        self._make_junction_or_skip(outside, self.root / "link-out")

        self.assertEqual(350, get_scan_size(self.root))
        self.assertTrue(is_reparse_point(self.root / "link-out"))

    def test_scan_size_of_root_reached_through_junction_is_zero(self):
        outside = self.base / "scan-outside-root"
        outside.mkdir()
        (outside / "keep.txt").write_bytes(b"k" * 700)
        link = self.base / "scan-root-link"
        self._make_junction_or_skip(outside, link)

        self.assertEqual(0, get_scan_size(link))

    def test_scan_size_counts_a_file_root(self):
        dump = self.root / "memory.dmp"
        dump.write_bytes(b"m" * 123)

        self.assertEqual(123, get_scan_size(dump))

    def test_scan_worker_does_not_follow_junctions(self):
        import sweptpc

        outside = self.base / "worker-scan-outside"
        outside.mkdir()
        (outside / "keep.log").write_bytes(b"k" * 900)
        logs = self.root / "logs"
        logs.mkdir()
        (logs / "a.log").write_bytes(b"a" * 40)
        (logs / "skip.txt").write_bytes(b"s" * 5)
        self._make_junction_or_skip(outside, logs / "link-out")
        temp_cfg = dict(sweptpc.CLEANUP_TARGETS["windows_temp"], paths=[str(logs)])
        log_cfg = dict(sweptpc.CLEANUP_TARGETS["windows_logs"], paths=[str(logs)], older_than_days=None)
        results = {}

        with mock.patch.dict(sweptpc.CLEANUP_TARGETS, {"windows_temp": temp_cfg, "windows_logs": log_cfg}):
            worker = sweptpc.ScanWorker(["windows_temp", "windows_logs"])
            worker.finished.connect(results.update)
            worker.run()

        self.assertEqual({"windows_temp": 45, "windows_logs": 40}, results)

    def test_cleanup_outcome_tracks_safety_blocked_paths(self):
        outcome = CleanupOutcome()
        outcome.add(DeleteResult(str(self.base), blocked=True, error="outside approved roots"))

        self.assertEqual(1, len(outcome.blocked))
        self.assertEqual(0, outcome.deleted_count)


if __name__ == "__main__":
    unittest.main()
