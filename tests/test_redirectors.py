"""Tests for sortilege.py's redirector discovery + cleanup, and the
verify pass -- Task 4.

Covers find_redirectors(), cleanup_redirectors(), and verify_results().
These do not exist yet -- this file is expected to fail with
AttributeError until Task 4 implements them.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import helpers
import mock_unreal


class RedirectorTestBase(unittest.TestCase):
    def setUp(self):
        self.sortilege = helpers.load_sortilege()
        mock_unreal.set_project_root("/ProjectX")


class FindRedirectorsTests(RedirectorTestBase):
    def test_finds_redirector_via_is_redirector_primary_path(self):
        mock_unreal.add_asset("/ProjectX/Stuff/Rock", "StaticMesh")
        mock_unreal.EditorAssetLibrary.rename_asset("/ProjectX/Stuff/Rock", "/ProjectX/Meshes/Rock")

        caps = self.sortilege.probe_capabilities()
        found = self.sortilege.find_redirectors(["/ProjectX/Stuff"], caps)

        self.assertEqual(found, ["/ProjectX/Stuff/Rock"])

    def test_scope_limited_to_touched_folders_only(self):
        mock_unreal.add_asset("/ProjectX/Stuff/Rock", "StaticMesh")
        mock_unreal.add_asset("/ProjectX/Untouched/Pebble", "StaticMesh")
        mock_unreal.EditorAssetLibrary.rename_asset("/ProjectX/Stuff/Rock", "/ProjectX/Meshes/Rock")
        mock_unreal.EditorAssetLibrary.rename_asset(
            "/ProjectX/Untouched/Pebble", "/ProjectX/Untouched/Renamed/Pebble")

        caps = self.sortilege.probe_capabilities()
        # Scope only covers /ProjectX/Stuff and /ProjectX/Meshes -- the
        # redirector left behind in /ProjectX/Untouched must never surface.
        found = self.sortilege.find_redirectors(["/ProjectX/Stuff", "/ProjectX/Meshes"], caps)

        self.assertEqual(found, ["/ProjectX/Stuff/Rock"])
        self.assertNotIn("/ProjectX/Untouched/Pebble", found)

    def test_falls_back_to_registry_when_is_redirector_missing(self):
        mock_unreal.add_asset("/ProjectX/Stuff/Rock", "StaticMesh")
        mock_unreal.EditorAssetLibrary.rename_asset("/ProjectX/Stuff/Rock", "/ProjectX/Meshes/Rock")

        caps = self.sortilege.probe_capabilities()
        original = mock_unreal.AssetData.is_redirector
        del mock_unreal.AssetData.is_redirector
        try:
            found = self.sortilege.find_redirectors(["/ProjectX/Stuff"], caps)
        finally:
            mock_unreal.AssetData.is_redirector = original

        self.assertEqual(found, ["/ProjectX/Stuff/Rock"])


class CleanupRedirectorsManualRecipeTests(RedirectorTestBase):
    def setUp(self):
        super(CleanupRedirectorsManualRecipeTests, self).setUp()
        self.sortilege = helpers.load_sortilege(features={"fix_up_redirectors": False})
        mock_unreal.set_project_root("/ProjectX")

    def test_resaves_stale_referencer_then_deletes(self):
        mock_unreal.add_asset("/ProjectX/Stuff/Rock", "StaticMesh")
        mock_unreal.EditorAssetLibrary.rename_asset("/ProjectX/Stuff/Rock", "/ProjectX/Meshes/Rock")
        # Referencer added after the move with a deliberately stale dep --
        # simulates a package cached before the move, never resaved.
        mock_unreal.add_asset("/ProjectX/Blueprints/BP_User", "Blueprint",
                               deps=["/ProjectX/Stuff/Rock"])

        caps = self.sortilege.probe_capabilities()
        self.assertFalse(caps.fix_up_redirectors)

        result = self.sortilege.cleanup_redirectors(["/ProjectX/Stuff", "/ProjectX/Meshes"], caps)

        self.assertEqual(result["fixed"], ["/ProjectX/Stuff/Rock"])
        self.assertEqual(result["remaining"], [])
        self.assertEqual(result["method"], "manual")

        state = mock_unreal.get_state()
        self.assertNotIn("/ProjectX/Stuff/Rock", state["redirectors"])
        self.assertIn("/ProjectX/Blueprints/BP_User", state["saved"])
        self.assertEqual(
            state["assets"]["/ProjectX/Blueprints/BP_User"]["deps"],
            ["/ProjectX/Meshes/Rock"],
        )

    def test_still_referenced_redirector_survives_to_remaining_with_why(self):
        mock_unreal.add_asset("/ProjectX/Stuff/Rock", "StaticMesh")
        mock_unreal.EditorAssetLibrary.rename_asset("/ProjectX/Stuff/Rock", "/ProjectX/Meshes/Rock")
        mock_unreal.add_asset("/ProjectX/Blueprints/BP_Broken", "Blueprint",
                               deps=["/ProjectX/Stuff/Rock"])

        # Simulate a referencer package that cannot be loaded (missing,
        # corrupted, whatever) -- load_asset returns None for it, so it
        # can never be resaved and the redirector must survive.
        original_load = mock_unreal.EditorAssetLibrary.load_asset

        def flaky_load(path):
            if "BP_Broken" in path:
                return None
            return original_load(path)

        mock_unreal.EditorAssetLibrary.load_asset = staticmethod(flaky_load)
        try:
            caps = self.sortilege.probe_capabilities()
            result = self.sortilege.cleanup_redirectors(["/ProjectX/Stuff", "/ProjectX/Meshes"], caps)
        finally:
            mock_unreal.EditorAssetLibrary.load_asset = staticmethod(original_load)

        self.assertEqual(result["fixed"], [])
        self.assertEqual(len(result["remaining"]), 1)
        path, why = result["remaining"][0]
        self.assertEqual(path, "/ProjectX/Stuff/Rock")
        self.assertIn("still referenced by", why)
        self.assertIn("BP_Broken", why)

        state = mock_unreal.get_state()
        self.assertIn("/ProjectX/Stuff/Rock", state["redirectors"])

    def test_collect_garbage_called_exactly_once_for_twelve_redirectors(self):
        for i in range(12):
            path = "/ProjectX/Stuff/Rock%02d" % i
            mock_unreal.add_asset(path, "StaticMesh")
            mock_unreal.EditorAssetLibrary.rename_asset(path, "/ProjectX/Meshes/Rock%02d" % i)

        caps = self.sortilege.probe_capabilities()
        self.assertTrue(caps.collect_garbage)

        result = self.sortilege.cleanup_redirectors(["/ProjectX/Stuff", "/ProjectX/Meshes"], caps)

        self.assertEqual(len(result["fixed"]), 12)
        self.assertEqual(mock_unreal.get_state()["gc_calls"], 1)

    def test_collect_garbage_called_exactly_twice_for_twenty_redirectors(self):
        for i in range(20):
            path = "/ProjectX/Stuff/Rock%02d" % i
            mock_unreal.add_asset(path, "StaticMesh")
            mock_unreal.EditorAssetLibrary.rename_asset(path, "/ProjectX/Meshes/Rock%02d" % i)

        caps = self.sortilege.probe_capabilities()

        result = self.sortilege.cleanup_redirectors(["/ProjectX/Stuff", "/ProjectX/Meshes"], caps)

        self.assertEqual(len(result["fixed"]), 20)
        self.assertEqual(mock_unreal.get_state()["gc_calls"], 2)

    def test_collect_garbage_not_called_for_fewer_than_ten_redirectors(self):
        for i in range(5):
            path = "/ProjectX/Stuff/Rock%02d" % i
            mock_unreal.add_asset(path, "StaticMesh")
            mock_unreal.EditorAssetLibrary.rename_asset(path, "/ProjectX/Meshes/Rock%02d" % i)

        caps = self.sortilege.probe_capabilities()

        result = self.sortilege.cleanup_redirectors(["/ProjectX/Stuff", "/ProjectX/Meshes"], caps)

        self.assertEqual(len(result["fixed"]), 5)
        self.assertEqual(mock_unreal.get_state()["gc_calls"], 0)

    def test_no_redirectors_in_scope_is_a_no_op(self):
        caps = self.sortilege.probe_capabilities()
        result = self.sortilege.cleanup_redirectors(["/ProjectX/Empty"], caps)
        self.assertEqual(result["fixed"], [])
        self.assertEqual(result["remaining"], [])


class CleanupRedirectorsFixupPathTests(RedirectorTestBase):
    def test_fixup_path_used_when_capability_present(self):
        mock_unreal.add_asset("/ProjectX/Stuff/Rock", "StaticMesh")
        mock_unreal.EditorAssetLibrary.rename_asset("/ProjectX/Stuff/Rock", "/ProjectX/Meshes/Rock")
        mock_unreal.add_asset("/ProjectX/Blueprints/BP_User", "Blueprint",
                               deps=["/ProjectX/Stuff/Rock"])

        caps = self.sortilege.probe_capabilities()
        self.assertTrue(caps.fix_up_redirectors)

        result = self.sortilege.cleanup_redirectors(["/ProjectX/Stuff", "/ProjectX/Meshes"], caps)

        self.assertEqual(result["fixed"], ["/ProjectX/Stuff/Rock"])
        self.assertEqual(result["method"], "fix_up_redirectors")
        state = mock_unreal.get_state()
        self.assertNotIn("/ProjectX/Stuff/Rock", state["redirectors"])


class VerifyResultsTests(RedirectorTestBase):
    def test_detects_deliberately_missing_dest(self):
        results = {"moved": [("/ProjectX/Stuff/Rock", "/ProjectX/Meshes/Rock")], "failed": []}
        caps = self.sortilege.probe_capabilities()

        out = self.sortilege.verify_results(results, ["/ProjectX/Stuff", "/ProjectX/Meshes"], caps)

        self.assertFalse(out["ok"])
        self.assertIn("/ProjectX/Meshes/Rock", out["missing"])

    def test_detects_leftover_redirector_but_does_not_fail_ok(self):
        mock_unreal.add_asset("/ProjectX/Stuff/Rock", "StaticMesh")
        mock_unreal.EditorAssetLibrary.rename_asset("/ProjectX/Stuff/Rock", "/ProjectX/Meshes/Rock")
        results = {"moved": [("/ProjectX/Stuff/Rock", "/ProjectX/Meshes/Rock")], "failed": []}
        caps = self.sortilege.probe_capabilities()

        out = self.sortilege.verify_results(results, ["/ProjectX/Stuff", "/ProjectX/Meshes"], caps)

        self.assertIn("/ProjectX/Stuff/Rock", out["leftover_redirectors"])
        self.assertTrue(out["ok"])
        self.assertEqual(out["missing"], [])
        self.assertEqual(out["old_paths_alive"], [])

    def test_ok_true_on_fully_clean_run(self):
        mock_unreal.add_asset("/ProjectX/Stuff/Rock", "StaticMesh")
        mock_unreal.EditorAssetLibrary.rename_asset("/ProjectX/Stuff/Rock", "/ProjectX/Meshes/Rock")
        results = {"moved": [("/ProjectX/Stuff/Rock", "/ProjectX/Meshes/Rock")], "failed": []}
        caps = self.sortilege.probe_capabilities()

        self.sortilege.cleanup_redirectors(["/ProjectX/Stuff", "/ProjectX/Meshes"], caps)
        out = self.sortilege.verify_results(results, ["/ProjectX/Stuff", "/ProjectX/Meshes"], caps)

        self.assertTrue(out["ok"])
        self.assertEqual(out["missing"], [])
        self.assertEqual(out["old_paths_alive"], [])
        self.assertEqual(out["leftover_redirectors"], [])
        self.assertEqual(out["referencer_spot_checks"], 1)

    def test_detects_old_path_still_alive_as_a_real_asset(self):
        # Simulate corruption: the move "succeeded" per results, but the old
        # path was never actually vacated (still a real, non-redirector
        # asset there) -- a genuine verify failure.
        mock_unreal.add_asset("/ProjectX/Stuff/Rock", "StaticMesh")
        mock_unreal.add_asset("/ProjectX/Meshes/Rock", "StaticMesh")
        results = {"moved": [("/ProjectX/Stuff/Rock", "/ProjectX/Meshes/Rock")], "failed": []}
        caps = self.sortilege.probe_capabilities()

        out = self.sortilege.verify_results(results, ["/ProjectX/Stuff", "/ProjectX/Meshes"], caps)

        self.assertFalse(out["ok"])
        self.assertIn("/ProjectX/Stuff/Rock", out["old_paths_alive"])


if __name__ == "__main__":
    unittest.main()
