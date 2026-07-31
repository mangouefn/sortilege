"""Tests for sortilege.py's Verse reference fixup.

Bounty OP clarification: "no broken references" also covers Verse-side
references. Ground truth (Epic's Asset Reflection docs): an exposed
asset's Verse reference is its Content-folder path with the content root
stripped and "/" replaced by ".". After Sortilege moves an asset, any
`.verse` source that names it by folder-qualified name must be rewritten
to match, or the project stops compiling.

content_path_to_verse_ref/resolve_verse_search_dir/find_verse_files/
build_verse_edits/format_verse_preview/apply_verse_edits/undo_verse_edits
do not exist yet -- this file is expected to fail with AttributeError
until they are implemented (TDD RED-first).

.verse fixtures are real text files on disk (tempfile), not `unreal`
assets -- normal Python file I/O, no mock needed for the file content
itself. The mock IS still used for build_plan()/run_apply()/run_undo()
integration tests, exactly like every other test file in this suite.
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import helpers
import mock_unreal

from test_gui import (
    _FakeMessagebox, _make_fake_tk_module, _make_fake_ttk_module,
)


def asset(path, class_name):
    folder, name = path.rsplit("/", 1)
    return {"path": path, "name": name, "folder": folder, "class_name": class_name}


# ---------------------------------------------------------------------------
# content_path_to_verse_ref() -- the Asset Reflection ground-truth rule
# ---------------------------------------------------------------------------

class ContentPathToVerseRefTests(unittest.TestCase):
    def setUp(self):
        self.sortilege = helpers.load_sortilege()

    def test_nested_path(self):
        ref = self.sortilege.content_path_to_verse_ref(
            "/PremFN_1v1/PowersWheelAssets/Models/Textures/T_Hex", ["/PremFN_1v1"])
        self.assertEqual(ref, "PowersWheelAssets.Models.Textures.T_Hex")

    def test_root_level_asset_is_a_bare_name_with_no_dots(self):
        ref = self.sortilege.content_path_to_verse_ref(
            "/PremFN_1v1/T_Foo", ["/PremFN_1v1"])
        self.assertEqual(ref, "T_Foo")
        self.assertNotIn(".", ref)

    def test_multiple_roots_picks_the_one_that_actually_matches(self):
        ref = self.sortilege.content_path_to_verse_ref(
            "/PremFN_1v1/Foo/T_Hex", ["/OtherMount", "/PremFN_1v1"])
        self.assertEqual(ref, "Foo.T_Hex")

    def test_trailing_slash_on_root_is_tolerated(self):
        ref = self.sortilege.content_path_to_verse_ref(
            "/PremFN_1v1/Foo/T_Hex", ["/PremFN_1v1/"])
        self.assertEqual(ref, "Foo.T_Hex")

    def test_path_outside_every_root_returns_none(self):
        ref = self.sortilege.content_path_to_verse_ref(
            "/Other/Foo/T_Hex", ["/PremFN_1v1"])
        self.assertIsNone(ref)

    def test_the_bare_root_itself_returns_none(self):
        ref = self.sortilege.content_path_to_verse_ref("/PremFN_1v1", ["/PremFN_1v1"])
        self.assertIsNone(ref)


# ---------------------------------------------------------------------------
# resolve_verse_search_dir() -- CONFIG override chain
# ---------------------------------------------------------------------------

class ResolveVerseSearchDirTests(unittest.TestCase):
    def setUp(self):
        self.sortilege = helpers.load_sortilege()

    def test_uses_config_override_when_set(self):
        result = self.sortilege.resolve_verse_search_dir({"VERSE_SEARCH_DIR": "/some/dir"})
        self.assertEqual(result, os.path.normpath("/some/dir"))

    def test_returns_none_when_nothing_resolves(self):
        # The mock's unreal.Paths has no project_dir() and there is no
        # unreal.SystemLibrary.get_project_directory() either -- both
        # hasattr-gated links fall through, same as resolve_log_dir()'s
        # own optional-API chain. No sample_asset_paths given either, so
        # the auto-detect candidate never enters the picture.
        result = self.sortilege.resolve_verse_search_dir({"VERSE_SEARCH_DIR": ""})
        self.assertIsNone(result)

    def test_no_sample_asset_paths_given_falls_through_like_before(self):
        result = self.sortilege.resolve_verse_search_dir({})
        self.assertIsNone(result)


# ---------------------------------------------------------------------------
# resolve_verse_search_dir() auto-detect -- PROVEN BUG fix.
#
# Live diagnostic on a real UEFN session: unreal.Paths.project_dir()
# resolved to the FORTNITE ENGINE directory, not the user's project (the
# preview line read "Verse fixup: 7 .verse file(s) found under
# ..\..\..\FortniteGame" -- real .verse files, entirely the wrong project).
# find_verse_files() then scanned Fortnite's own .verse files and produced
# zero edits even though the user's real project had a genuine Verse
# reference to a moved asset.
#
# The fix: derive the real project directory from a scanned asset's own
# on-disk path (unreal.SystemLibrary.get_system_path(), the reliable UEFN
# way -- research-confirmed, not live-verified here, see README/report),
# and among every candidate directory, prefer whichever one demonstrably
# HAS real .verse files under it, checked in priority order rather than
# picked blindly.
# ---------------------------------------------------------------------------

class ResolveVerseSearchDirAutoDetectTests(unittest.TestCase):
    def setUp(self):
        self.sortilege = helpers.load_sortilege()
        self._tmp_dirs = []

    def tearDown(self):
        for d in self._tmp_dirs:
            shutil.rmtree(d, ignore_errors=True)
        # Paths is a plain class the mock never recreates on reset() --
        # any test-local monkeypatch of project_dir() must be undone by
        # hand or it leaks into every other test in this process.
        if "project_dir" in mock_unreal.Paths.__dict__:
            del mock_unreal.Paths.project_dir

    def _mkdtemp(self):
        d = tempfile.mkdtemp(prefix="sortilege_versedir_")
        self._tmp_dirs.append(d)
        return d

    def test_derives_project_dir_from_sample_asset_disk_path(self):
        # Pure derivation, isolated from the files-selection logic: the
        # ONLY candidate is the sample-derived one (no Paths.project_dir
        # configured), and it has no .verse files at all -- it still wins
        # via the "no candidate has files -> return the first anyway"
        # fallback, proving the parent-of-Content math itself is right.
        project_dir = self._mkdtemp()
        mock_unreal.set_project_disk_dir(project_dir)
        mock_unreal.add_asset("/Game/Textures/T_Foo", "Texture2D")

        result = self.sortilege.resolve_verse_search_dir(
            {}, sample_asset_paths=["/Game/Textures/T_Foo"])

        self.assertEqual(result, os.path.normpath(project_dir))

    def test_a_project_with_no_verse_files_never_falls_through_to_the_engine_dir(self):
        # The reproduced bug, and the reason this used to assert the
        # opposite: the user's real project has NO Verse code of its own,
        # so the sample-derived candidate has nothing under it, while
        # unreal.Paths.project_dir() (the Fortnite engine directory in
        # UEFN) has plenty. Preferring "whichever candidate has files"
        # then walked out of the project entirely and rewrote a file
        # inside the Fortnite install.
        #
        # "Has .verse files" is evidence of Verse source, not evidence of
        # WHOSE Verse source, so it can only ever choose between
        # directories already known to belong to this project. The real
        # project dir must win here even though it is empty.
        project_dir = self._mkdtemp()
        fortnite_engine_dir = self._mkdtemp()
        os.makedirs(os.path.join(fortnite_engine_dir, "FortniteGame"))
        with open(os.path.join(fortnite_engine_dir, "FortniteGame", "engine.verse"),
                  "w") as f:
            f.write("using { /Fortnite.com/Devices }\n")

        mock_unreal.set_project_disk_dir(project_dir)
        mock_unreal.add_asset("/Game/Textures/T_Foo", "Texture2D")
        mock_unreal.Paths.project_dir = staticmethod(lambda: fortnite_engine_dir)

        result = self.sortilege.resolve_verse_search_dir(
            {}, sample_asset_paths=["/Game/Textures/T_Foo"])

        self.assertEqual(result, os.path.normpath(project_dir))
        self.assertNotEqual(result, os.path.normpath(fortnite_engine_dir))
        # ...and nothing in the engine directory is ever even looked at.
        self.assertEqual(self.sortilege.find_verse_files(result), [])

    def test_paths_project_dir_is_not_a_candidate_at_all_without_a_sample(self):
        # Nothing to derive from -> None, rather than "trust whatever
        # unreal.Paths.project_dir() said". None means find_verse_files()
        # returns nothing and the whole feature no-ops; the preview says
        # so out loud and run_apply() refuses rather than half-doing it.
        verse_dir = self._mkdtemp()
        with open(os.path.join(verse_dir, "engine.verse"), "w") as f:
            f.write("using { /Fortnite.com/Devices }\n")
        mock_unreal.Paths.project_dir = staticmethod(lambda: verse_dir)

        self.assertIsNone(self.sortilege.resolve_verse_search_dir({}))

    def test_sample_derived_candidate_wins_over_paths_project_dir_even_when_both_have_verse_files(self):
        # The exact bug scenario, reproduced: Paths.project_dir() (the
        # Fortnite engine dir stand-in) genuinely has real .verse files
        # under it too, same as the live diagnostic's 7 files -- so "has
        # files" alone would not be enough without priority order. The
        # higher-priority sample-derived candidate must still win because
        # it comes first AND it also has files; Paths.project_dir is never
        # the answer even though it would also pass the files check.
        real_project_dir = self._mkdtemp()
        os.makedirs(os.path.join(real_project_dir, "Content"))
        with open(os.path.join(real_project_dir, "Content", "settings_device.verse"), "w") as f:
            f.write("set M.Texture = T_Overshield_Enabled\n")

        fortnite_engine_dir = self._mkdtemp()
        os.makedirs(os.path.join(fortnite_engine_dir, "FortniteGame"))
        with open(os.path.join(fortnite_engine_dir, "FortniteGame", "engine.verse"), "w") as f:
            f.write("using { /Fortnite.com/Devices }\n")

        mock_unreal.set_project_disk_dir(real_project_dir)
        mock_unreal.add_asset("/Game/Textures/T_Foo", "Texture2D")
        mock_unreal.Paths.project_dir = staticmethod(lambda: fortnite_engine_dir)

        result = self.sortilege.resolve_verse_search_dir(
            {}, sample_asset_paths=["/Game/Textures/T_Foo"])

        self.assertEqual(result, os.path.normpath(real_project_dir))
        self.assertNotEqual(result, os.path.normpath(fortnite_engine_dir))

    def test_override_wins_even_when_a_better_candidate_is_derivable(self):
        project_dir = self._mkdtemp()
        os.makedirs(os.path.join(project_dir, "Content"))
        with open(os.path.join(project_dir, "Content", "foo.verse"), "w") as f:
            f.write("using { /Game }\n")
        mock_unreal.set_project_disk_dir(project_dir)
        mock_unreal.add_asset("/Game/Textures/T_Foo", "Texture2D")

        result = self.sortilege.resolve_verse_search_dir(
            {"VERSE_SEARCH_DIR": "/manual/override"},
            sample_asset_paths=["/Game/Textures/T_Foo"])

        self.assertEqual(result, os.path.normpath("/manual/override"))

    def test_system_path_feature_off_falls_through_fail_soft(self):
        sortilege = helpers.load_sortilege(features={"system_path": False})
        mock_unreal.add_asset("/Game/Textures/T_Foo", "Texture2D")

        result = sortilege.resolve_verse_search_dir(
            {}, sample_asset_paths=["/Game/Textures/T_Foo"])

        self.assertIsNone(result)

    def test_asset_that_fails_to_load_is_skipped_not_fatal(self):
        # "/Game/Nope" was never add_asset()'d -- load_asset() returns
        # None for it, same as a real stale/deleted reference would.
        result = self.sortilege.resolve_verse_search_dir(
            {}, sample_asset_paths=["/Game/Nope"])
        self.assertIsNone(result)


# ---------------------------------------------------------------------------
# find_verse_files() -- normal file I/O, no unreal needed
# ---------------------------------------------------------------------------

class FindVerseFilesTests(unittest.TestCase):
    def setUp(self):
        self.sortilege = helpers.load_sortilege()
        self.tmp_dir = tempfile.mkdtemp(prefix="sortilege_verse_test_")

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _write(self, relpath, content="using { /Foo }\n"):
        full = os.path.join(self.tmp_dir, relpath)
        parent = os.path.dirname(full)
        if not os.path.isdir(parent):
            os.makedirs(parent)
        with open(full, "w", encoding="utf-8") as f:
            f.write(content)
        return os.path.abspath(full)

    def test_finds_a_real_verse_file(self):
        real = self._write("device.verse")
        found = self.sortilege.find_verse_files(self.tmp_dir)
        self.assertEqual(found, [real])

    def test_skips_digest_verse(self):
        self._write("Assets.digest.verse")
        found = self.sortilege.find_verse_files(self.tmp_dir)
        self.assertEqual(found, [])

    def test_skips_intermediate_and_saved_subfolders(self):
        self._write(os.path.join("Intermediate", "generated.verse"))
        self._write(os.path.join("Saved", "generated2.verse"))
        real = self._write("real.verse")
        found = self.sortilege.find_verse_files(self.tmp_dir)
        self.assertEqual(found, [real])

    def test_none_project_dir_returns_empty_list(self):
        self.assertEqual(self.sortilege.find_verse_files(None), [])

    def test_nonexistent_project_dir_returns_empty_list(self):
        self.assertEqual(
            self.sortilege.find_verse_files(os.path.join(self.tmp_dir, "nope")), [])


# ---------------------------------------------------------------------------
# build_verse_edits() -- boundary-safe scan
# ---------------------------------------------------------------------------

class BuildVerseEditsTests(unittest.TestCase):
    def setUp(self):
        self.sortilege = helpers.load_sortilege()
        self.tmp_dir = tempfile.mkdtemp(prefix="sortilege_verse_test_")

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _write(self, relpath, content):
        full = os.path.join(self.tmp_dir, relpath)
        with open(full, "w", encoding="utf-8") as f:
            f.write(content)
        return full

    def test_correct_old_to_new_ref_from_a_plan(self):
        move = {"path": "/PremFN_1v1/PowersWheelAssets/Models/Textures/T_Hex",
                "dest_path": "/PremFN_1v1/Textures/T_Hex",
                "dest_folder": "/PremFN_1v1/Textures"}
        verse_file = self._write(
            "a.verse", "MyHex := PowersWheelAssets.Models.Textures.T_Hex\n")

        edits = self.sortilege.build_verse_edits([move], [verse_file], ["/PremFN_1v1"])

        self.assertEqual(len(edits), 1)
        e = edits[0]
        self.assertEqual(e["file"], verse_file)
        self.assertEqual(e["line_no"], 1)
        self.assertEqual(e["old_ref"], "PowersWheelAssets.Models.Textures.T_Hex")
        self.assertEqual(e["new_ref"], "Textures.T_Hex")
        self.assertFalse(e["is_bare"])
        self.assertEqual(e["count"], 1)
        self.assertIn("Textures.T_Hex", e["new_line"])
        self.assertNotIn("PowersWheelAssets", e["new_line"])

    def test_boundary_safety_bare_ref_does_not_touch_a_longer_identifier(self):
        move = {"path": "/PremFN_1v1/T_Hex", "dest_path": "/PremFN_1v1/Sub/T_Hex",
                "dest_folder": "/PremFN_1v1/Sub"}
        verse_file = self._write(
            "a.verse",
            "Good := T_Hex\n"
            "Bad := T_Hex2\n"
            "# a comment mentioning T_HexFoo in passing\n"
            "AlsoBad := SomeT_Hex\n")

        edits = self.sortilege.build_verse_edits([move], [verse_file], ["/PremFN_1v1"])

        touched_lines = sorted(e["line_no"] for e in edits)
        self.assertEqual(touched_lines, [1])
        self.assertTrue(edits[0]["is_bare"])

    def test_boundary_safety_qualified_ref_not_matched_inside_a_longer_path(self):
        move = {"path": "/PremFN_1v1/A/B/T_Hex", "dest_path": "/PremFN_1v1/Z/T_Hex",
                "dest_folder": "/PremFN_1v1/Z"}
        verse_file = self._write(
            "a.verse",
            "Unrelated := X.A.B.T_Hex\n"
            "Real := A.B.T_Hex\n")

        edits = self.sortilege.build_verse_edits([move], [verse_file], ["/PremFN_1v1"])

        touched_lines = sorted(e["line_no"] for e in edits)
        self.assertEqual(touched_lines, [2])
        self.assertEqual(edits[0]["new_ref"], "Z.T_Hex")

    def test_move_with_no_ref_change_produces_no_edit(self):
        # Directly probes build_verse_edits' own defensive skip -- a
        # move whose old and new Verse ref come out identical must never
        # produce an edit, even though the text is genuinely present.
        move = {"path": "/PremFN_1v1/Sub/T_Hex", "dest_path": "/PremFN_1v1/Sub/T_Hex",
                "dest_folder": "/PremFN_1v1/Sub"}
        verse_file = self._write("a.verse", "Ref := Sub.T_Hex\n")

        edits = self.sortilege.build_verse_edits([move], [verse_file], ["/PremFN_1v1"])

        self.assertEqual(edits, [])

    def test_is_bare_flagged_for_root_level_asset(self):
        move = {"path": "/PremFN_1v1/T_Foo", "dest_path": "/PremFN_1v1/Sub/T_Foo",
                "dest_folder": "/PremFN_1v1/Sub"}
        verse_file = self._write("a.verse", "Ref := T_Foo\n")

        edits = self.sortilege.build_verse_edits([move], [verse_file], ["/PremFN_1v1"])

        self.assertEqual(len(edits), 1)
        self.assertTrue(edits[0]["is_bare"])
        self.assertEqual(edits[0]["old_ref"], "T_Foo")
        self.assertEqual(edits[0]["new_ref"], "Sub.T_Foo")

    def test_unreadable_verse_file_is_skipped_not_fatal(self):
        move = {"path": "/PremFN_1v1/T_Foo", "dest_path": "/PremFN_1v1/Sub/T_Foo",
                "dest_folder": "/PremFN_1v1/Sub"}
        missing_file = os.path.join(self.tmp_dir, "does_not_exist.verse")

        edits = self.sortilege.build_verse_edits([move], [missing_file], ["/PremFN_1v1"])

        self.assertEqual(edits, [])


# ---------------------------------------------------------------------------
# build_verse_edits() -- using-statement (whole-folder) rewriting
# ---------------------------------------------------------------------------

class UsingStatementRewriteTests(unittest.TestCase):
    def setUp(self):
        self.sortilege = helpers.load_sortilege()
        self.tmp_dir = tempfile.mkdtemp(prefix="sortilege_verse_test_")

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _write(self, relpath, content):
        full = os.path.join(self.tmp_dir, relpath)
        with open(full, "w", encoding="utf-8") as f:
            f.write(content)
        return full

    def test_using_statement_rewritten_for_a_moved_folder(self):
        move = {"path": "/PremFN_1v1/PowersWheelAssets/Models/Textures/T_Hex",
                "dest_path": "/PremFN_1v1/Textures/T_Hex",
                "dest_folder": "/PremFN_1v1/Textures"}
        verse_file = self._write(
            "a.verse", "using { /PremFN_1v1/PowersWheelAssets/Models/Textures }\n")

        edits = self.sortilege.build_verse_edits([move], [verse_file], ["/PremFN_1v1"])

        using_edits = [e for e in edits if e["old_ref"].startswith("/")]
        self.assertEqual(len(using_edits), 1)
        self.assertEqual(using_edits[0]["old_ref"],
                          "/PremFN_1v1/PowersWheelAssets/Models/Textures")
        self.assertEqual(using_edits[0]["new_ref"], "/PremFN_1v1/Textures")
        self.assertIn("using { /PremFN_1v1/Textures }", using_edits[0]["new_line"])

    def test_unrelated_using_statement_is_not_touched(self):
        move = {"path": "/PremFN_1v1/PowersWheelAssets/Models/Textures/T_Hex",
                "dest_path": "/PremFN_1v1/Textures/T_Hex",
                "dest_folder": "/PremFN_1v1/Textures"}
        verse_file = self._write("a.verse", "using { /PremFN_1v1/SomethingElse }\n")

        edits = self.sortilege.build_verse_edits([move], [verse_file], ["/PremFN_1v1"])

        self.assertEqual(edits, [])

    def test_ambiguous_folder_split_across_two_destinations_is_left_alone(self):
        # Conservative-by-design: two moves share the SAME old folder but
        # disagree on the new one -- neither wins a guess, so the
        # using-statement referencing that old folder is untouched.
        moves = [
            {"path": "/PremFN_1v1/Old/T_A", "dest_path": "/PremFN_1v1/Textures/T_A",
             "dest_folder": "/PremFN_1v1/Textures"},
            {"path": "/PremFN_1v1/Old/T_B", "dest_path": "/PremFN_1v1/Meshes/T_B",
             "dest_folder": "/PremFN_1v1/Meshes"},
        ]
        verse_file = self._write("a.verse", "using { /PremFN_1v1/Old }\n")

        edits = self.sortilege.build_verse_edits(moves, [verse_file], ["/PremFN_1v1"])

        using_edits = [e for e in edits if e["old_ref"].startswith("/")]
        self.assertEqual(using_edits, [])


# ---------------------------------------------------------------------------
# format_verse_preview() -- ASCII table lines
# ---------------------------------------------------------------------------

class FormatVersePreviewTests(unittest.TestCase):
    def setUp(self):
        self.sortilege = helpers.load_sortilege()

    def test_empty_edits_produces_no_lines(self):
        self.assertEqual(self.sortilege.format_verse_preview([]), [])

    def test_groups_by_file_and_flags_bare_names(self):
        edits = [
            {"file": "a.verse", "line_no": 3, "old_line": "x", "new_line": "y",
             "old_ref": "T_Foo", "new_ref": "Sub.T_Foo", "is_bare": True,
             "count": 1, "kind": "ref"},
            {"file": "a.verse", "line_no": 7, "old_line": "x", "new_line": "y",
             "old_ref": "Stuff.T_Bar", "new_ref": "Meshes.T_Bar", "is_bare": False,
             "count": 1, "kind": "ref"},
        ]
        lines = self.sortilege.format_verse_preview(edits)
        text = "\n".join(lines)

        self.assertIn("-- Verse reference edits (2) --", text)
        self.assertIn("a.verse", text)
        self.assertIn("T_Foo -> Sub.T_Foo (bare name - review)", text)
        self.assertIn("Stuff.T_Bar -> Meshes.T_Bar", text)
        self.assertNotIn("Stuff.T_Bar -> Meshes.T_Bar (bare name - review)", text)

    def test_using_kind_edits_are_never_flagged_bare_even_with_no_dots(self):
        edits = [
            {"file": "a.verse", "line_no": 1, "old_line": "x", "new_line": "y",
             "old_ref": "/Old/Folder", "new_ref": "/New/Folder", "is_bare": True,
             "count": 1, "kind": "using"},
        ]
        lines = self.sortilege.format_verse_preview(edits)
        text = "\n".join(lines)

        self.assertIn("/Old/Folder -> /New/Folder", text)
        self.assertNotIn("bare name", text)

    def test_is_ascii_only(self):
        edits = [
            {"file": "a.verse", "line_no": 1, "old_line": "x", "new_line": "y",
             "old_ref": "T_Foo", "new_ref": "Sub.T_Foo", "is_bare": True,
             "count": 1, "kind": "ref"},
        ]
        for line in self.sortilege.format_verse_preview(edits):
            self.assertTrue(all(ord(c) < 128 for c in line), "non-ascii in: %r" % line)


# ---------------------------------------------------------------------------
# apply_verse_edits() / undo_verse_edits() -- backup + apply + restore
# ---------------------------------------------------------------------------

class ApplyAndUndoVerseEditsTests(unittest.TestCase):
    def setUp(self):
        self.sortilege = helpers.load_sortilege()
        self.tmp_dir = tempfile.mkdtemp(prefix="sortilege_verse_files_")
        self.log_dir = tempfile.mkdtemp(prefix="sortilege_verse_logs_")

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)
        shutil.rmtree(self.log_dir, ignore_errors=True)

    def _write(self, relpath, content):
        full = os.path.join(self.tmp_dir, relpath)
        with open(full, "w", encoding="utf-8") as f:
            f.write(content)
        return full

    def test_apply_then_undo_round_trip_is_byte_identical(self):
        original_content = (
            "using { /PremFN_1v1 }\n"
            "MyHex := PowersWheelAssets.Models.Textures.T_Hex\n"
        )
        verse_file = self._write("a.verse", original_content)
        with open(verse_file, "rb") as f:
            original_bytes = f.read()

        move = {"path": "/PremFN_1v1/PowersWheelAssets/Models/Textures/T_Hex",
                "dest_path": "/PremFN_1v1/Textures/T_Hex",
                "dest_folder": "/PremFN_1v1/Textures"}
        edits = self.sortilege.build_verse_edits([move], [verse_file], ["/PremFN_1v1"])
        self.assertEqual(len(edits), 1)

        apply_result = self.sortilege.apply_verse_edits(edits, self.log_dir)
        self.assertEqual(apply_result["edited"], [verse_file])
        self.assertEqual(apply_result["failed"], [])
        self.assertIsNotNone(apply_result["backup_index"])
        self.assertTrue(os.path.isfile(apply_result["backup_index"]))

        with open(verse_file, "r", encoding="utf-8") as f:
            edited_content = f.read()
        self.assertIn("Textures.T_Hex", edited_content)
        self.assertNotIn("PowersWheelAssets.Models.Textures.T_Hex", edited_content)

        undo_result = self.sortilege.undo_verse_edits(apply_result["backup_index"])
        self.assertEqual(undo_result["restored"], [verse_file])
        self.assertEqual(undo_result["failed"], [])

        with open(verse_file, "rb") as f:
            restored_bytes = f.read()
        self.assertEqual(restored_bytes, original_bytes)

    def test_apply_with_no_edits_is_a_clean_noop(self):
        result = self.sortilege.apply_verse_edits([], self.log_dir)
        self.assertEqual(result, {"edited": [], "failed": [], "backup_index": None})
        self.assertEqual(os.listdir(self.log_dir), [])

    def test_apply_applies_two_edits_on_the_same_line_cumulatively(self):
        verse_file = self._write("a.verse", "Both := Stuff.T_A + Other.T_B\n")
        moves = [
            {"path": "/PremFN_1v1/Stuff/T_A", "dest_path": "/PremFN_1v1/Moved/T_A",
             "dest_folder": "/PremFN_1v1/Moved"},
            {"path": "/PremFN_1v1/Other/T_B", "dest_path": "/PremFN_1v1/Elsewhere/T_B",
             "dest_folder": "/PremFN_1v1/Elsewhere"},
        ]
        edits = self.sortilege.build_verse_edits(moves, [verse_file], ["/PremFN_1v1"])
        self.assertEqual(len(edits), 2)

        self.sortilege.apply_verse_edits(edits, self.log_dir)

        with open(verse_file, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("Moved.T_A", content)
        self.assertIn("Elsewhere.T_B", content)

    def test_undo_with_unreadable_index_fails_soft(self):
        result = self.sortilege.undo_verse_edits(
            os.path.join(self.tmp_dir, "does_not_exist.json"))
        self.assertEqual(result, {"restored": [], "failed": []})

    def test_undo_with_falsy_path_fails_soft(self):
        self.assertEqual(self.sortilege.undo_verse_edits(None),
                          {"restored": [], "failed": []})


# ---------------------------------------------------------------------------
# Preview integration -- build_plan()/format_preview()
# ---------------------------------------------------------------------------

class VersePreviewIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="sortilege_verse_preview_")

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _write(self, relpath, content):
        full = os.path.join(self.tmp_dir, relpath)
        with open(full, "w", encoding="utf-8") as f:
            f.write(content)
        return full

    def test_preview_shows_verse_reference_edits_section(self):
        sortilege = helpers.load_sortilege(
            config_overrides={"VERSE_SEARCH_DIR": self.tmp_dir})
        self._write("a.verse", "MyRock := Stuff.Rock\n")
        assets = [asset("/Game/Stuff/Rock", "StaticMesh")]
        plan = sortilege.build_plan(assets, sortilege.CONFIG, sortilege.probe_capabilities())

        self.assertEqual(len(plan["verse_edits"]), 1)
        lines = sortilege.format_preview(plan)
        text = "\n".join(lines)
        self.assertIn("-- Verse reference edits (1) --", text)
        self.assertIn("Stuff.Rock -> Meshes.Rock", text)
        self.assertIn("Verse reference edits proposed: 1", text)

    def test_fix_verse_references_false_shows_no_section(self):
        sortilege = helpers.load_sortilege(config_overrides={
            "VERSE_SEARCH_DIR": self.tmp_dir, "FIX_VERSE_REFERENCES": False})
        self._write("a.verse", "MyRock := Stuff.Rock\n")
        assets = [asset("/Game/Stuff/Rock", "StaticMesh")]
        plan = sortilege.build_plan(assets, sortilege.CONFIG, sortilege.probe_capabilities())

        self.assertEqual(plan["verse_edits"], [])
        lines = sortilege.format_preview(plan)
        text = "\n".join(lines)
        self.assertNotIn("Verse reference edits", text)

    def test_no_verse_search_dir_configured_is_a_quiet_noop(self):
        sortilege = helpers.load_sortilege()
        assets = [asset("/Game/Stuff/Rock", "StaticMesh")]
        plan = sortilege.build_plan(assets, sortilege.CONFIG, sortilege.probe_capabilities())

        self.assertEqual(plan["verse_edits"], [])
        lines = sortilege.format_preview(plan)
        self.assertNotIn("Verse reference edits", "\n".join(lines))


# ---------------------------------------------------------------------------
# run_apply() / run_undo() integration -- the real pipeline stages
# ---------------------------------------------------------------------------

class RunApplyAndRunUndoVerseIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="sortilege_verse_pipeline_")

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _write(self, relpath, content):
        full = os.path.join(self.tmp_dir, relpath)
        with open(full, "w", encoding="utf-8") as f:
            f.write(content)
        return full

    def test_run_apply_rewrites_verse_file_and_run_undo_restores_it(self):
        sortilege = helpers.load_sortilege(
            config_overrides={"VERSE_SEARCH_DIR": self.tmp_dir})
        verse_file = self._write("a.verse", "MyRock := Stuff.Rock\n")
        mock_unreal.add_asset("/Game/Stuff/Rock", "StaticMesh")
        assets = [asset("/Game/Stuff/Rock", "StaticMesh")]
        caps = sortilege.probe_capabilities()
        plan = sortilege.build_plan(assets, sortilege.CONFIG, caps)

        status_calls = []
        outcome = sortilege.run_apply(
            plan, caps, status_callback=lambda t: status_calls.append(t))

        self.assertIn("Rewriting Verse references...", status_calls)
        results = outcome["results"]
        self.assertIn("verse_edits", results)
        self.assertEqual(results["verse_edits"]["edited"], [verse_file])
        self.assertEqual(results["verse_edits"]["failed"], [])
        self.assertEqual(results["verse_edits"]["edit_count"], 1)

        with open(verse_file, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("Meshes.Rock", content)
        self.assertNotIn("Stuff.Rock", content)

        report_path = outcome["report_path"]
        with open(report_path, "r", encoding="utf-8") as f:
            report_text = f.read()
        self.assertIn("Verse references rewritten: 1 edit(s)", report_text)

        undo_status_calls = []
        undo_results = sortilege.run_undo(
            outcome["undo_log"].path, caps,
            status_callback=lambda t: undo_status_calls.append(t))

        self.assertIn("Restoring Verse references...", undo_status_calls)
        self.assertIn("verse_undo", undo_results)
        self.assertEqual(undo_results["verse_undo"]["restored"], [verse_file])
        self.assertEqual(undo_results["verse_undo"]["failed"], [])

        with open(verse_file, "r", encoding="utf-8") as f:
            restored_content = f.read()
        self.assertEqual(restored_content, "MyRock := Stuff.Rock\n")

    def test_safe_mode_skips_the_verse_reference_rewrite(self):
        sortilege = helpers.load_sortilege(config_overrides={
            "VERSE_SEARCH_DIR": self.tmp_dir, "SAFE_MODE": True})
        verse_file = self._write("a.verse", "MyRock := Stuff.Rock\n")
        mock_unreal.add_asset("/Game/Stuff/Rock", "StaticMesh")
        assets = [asset("/Game/Stuff/Rock", "StaticMesh")]
        caps = sortilege.probe_capabilities()
        plan = sortilege.build_plan(assets, sortilege.CONFIG, caps)

        outcome = sortilege.run_apply(plan, caps)

        self.assertNotIn("verse_edits", outcome["results"])
        with open(verse_file, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertEqual(content, "MyRock := Stuff.Rock\n")

    def test_no_verse_backup_means_run_undo_has_no_verse_undo_stage(self):
        # No VERSE_SEARCH_DIR configured -- FIX_VERSE_REFERENCES still
        # defaults True, but there is nothing to find, so run_apply's
        # verse-references stage is a clean no-op and run_undo has no
        # verse_backup_index to restore from.
        sortilege = helpers.load_sortilege()
        mock_unreal.add_asset("/Game/Stuff/Rock", "StaticMesh")
        assets = [asset("/Game/Stuff/Rock", "StaticMesh")]
        caps = sortilege.probe_capabilities()
        plan = sortilege.build_plan(assets, sortilege.CONFIG, caps)

        outcome = sortilege.run_apply(plan, caps)
        self.assertEqual(outcome["results"]["verse_edits"]["edited"], [])
        self.assertIsNone(outcome["results"]["verse_edits"]["backup_index"])

        undo_status_calls = []
        undo_results = sortilege.run_undo(
            outcome["undo_log"].path, caps,
            status_callback=lambda t: undo_status_calls.append(t))

        self.assertNotIn("Restoring Verse references...", undo_status_calls)
        self.assertNotIn("verse_undo", undo_results)


# ---------------------------------------------------------------------------
# THE END-TO-END PROOF: build_plan(), with NO VERSE_SEARCH_DIR override,
# auto-detects the real project directory from a scanned asset's on-disk
# path and finds the real .verse file living there -- mirroring the exact
# live-diagnosed failure this task fixes (unreal.Paths.project_dir()
# resolving to the Fortnite engine directory; zero edits produced against a
# project with a real Verse reference to a moved asset).
# ---------------------------------------------------------------------------

class AutoDetectProjectDirEndToEndTests(unittest.TestCase):
    def setUp(self):
        self.project_dir = tempfile.mkdtemp(prefix="sortilege_e2e_project_")
        self.fortnite_dir = tempfile.mkdtemp(prefix="sortilege_e2e_fortnite_")

    def tearDown(self):
        shutil.rmtree(self.project_dir, ignore_errors=True)
        shutil.rmtree(self.fortnite_dir, ignore_errors=True)
        if "project_dir" in mock_unreal.Paths.__dict__:
            del mock_unreal.Paths.project_dir

    def test_build_plan_auto_detects_project_dir_and_produces_the_verse_edit(self):
        content_dir = os.path.join(self.project_dir, "Content")
        os.makedirs(content_dir)
        verse_file = os.path.join(content_dir, "settings_device.verse")
        with open(verse_file, "w", encoding="utf-8") as f:
            f.write("set M.Texture = T_Overshield_Enabled\n")

        # Simulate the live bug precisely: unreal.Paths.project_dir()
        # resolves to somewhere that is NOT the real project, and no
        # VERSE_SEARCH_DIR override is configured either -- the old code
        # would have used this unconditionally and found zero real edits.
        mock_unreal.Paths.project_dir = staticmethod(lambda: self.fortnite_dir)

        # This test is about auto-DETECTION, and its fixture reference
        # happens to be a bare root-level name; FIX_VERSE_BARE_NAMES is
        # off by default now, so it is turned on explicitly here to keep
        # the test exercising what it was written to exercise.
        sortilege = helpers.load_sortilege(
            config_overrides={"FIX_VERSE_BARE_NAMES": True})
        mock_unreal.set_project_root("/Root")
        mock_unreal.set_project_disk_dir(self.project_dir)
        mock_unreal.add_asset("/Root/T_Overshield_Enabled", "Texture2D")

        assets = [asset("/Root/T_Overshield_Enabled", "Texture2D")]
        caps = sortilege.probe_capabilities()
        plan = sortilege.build_plan(assets, sortilege.CONFIG, caps)

        # The auto-detected dir is the real project, not the Fortnite dir.
        self.assertEqual(plan["verse_search_dir"], os.path.normpath(self.project_dir))
        self.assertNotEqual(plan["verse_search_dir"], os.path.normpath(self.fortnite_dir))
        self.assertEqual(plan["verse_files_count"], 1)

        # The move is planned (Texture2D at content root -> Textures/)...
        moves = [m for m in plan["moves"] if m["path"] == "/Root/T_Overshield_Enabled"]
        self.assertEqual(len(moves), 1)
        self.assertEqual(moves[0]["dest_path"], "/Root/Textures/T_Overshield_Enabled")

        # ...and THIS is the proof: the real .verse file's bare reference
        # was found and rewritten, entirely through auto-detection.
        matching = [e for e in plan["verse_edits"] if e["old_ref"] == "T_Overshield_Enabled"]
        self.assertEqual(len(matching), 1)
        edit = matching[0]
        self.assertEqual(edit["new_ref"], "Textures.T_Overshield_Enabled")
        self.assertIn("Textures.T_Overshield_Enabled", edit["new_line"])
        self.assertFalse(edit.get("skipped", False))


# ---------------------------------------------------------------------------
# Review follow-up (Minor): FIX_VERSE_BARE_NAMES toggle -- build_verse_edits
# gains a fix_bare_names kwarg (default True, matching current behavior).
# False skips a bare (dot-free) plain reference edit -- the qualified/dotted
# case is unaffected either way, and so is a using-statement folder rewrite
# (its old_ref is also dot-free, but it is a different, already-conservative
# category -- see build_verse_edits' docstring -- not a short bare name).
# ---------------------------------------------------------------------------

class FixVerseBareNamesToggleTests(unittest.TestCase):
    def setUp(self):
        self.sortilege = helpers.load_sortilege()
        self.tmp_dir = tempfile.mkdtemp(prefix="sortilege_verse_bare_")

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _write(self, relpath, content):
        full = os.path.join(self.tmp_dir, relpath)
        with open(full, "w", encoding="utf-8") as f:
            f.write(content)
        return full

    def test_config_defaults_the_toggle_off(self):
        # A bare name is a plain word, and nothing in a .verse file says
        # whether it is a moved asset or a local variable that happens to
        # share the name. Guessing wrong writes a file that no longer
        # compiles, so the default is to LIST these for the user instead
        # of rewriting them -- while every qualified reference is still
        # fixed automatically.
        sortilege = helpers.load_sortilege(
            config_overrides={"VERSE_SEARCH_DIR": self.tmp_dir})
        self.assertFalse(sortilege.CONFIG["FIX_VERSE_BARE_NAMES"])

        self._write("a.verse", "Ref := T_Foo\n")
        mock_unreal.add_asset("/Game/T_Foo", "StaticMesh")
        assets = [asset("/Game/T_Foo", "StaticMesh")]
        plan = sortilege.build_plan(assets, sortilege.CONFIG,
                                     sortilege.probe_capabilities())

        self.assertEqual(len(plan["verse_edits"]), 1)
        self.assertTrue(plan["verse_edits"][0]["skipped"])
        self.assertEqual(plan["verse_edits"][0]["old_ref"], "T_Foo")

    def test_default_true_still_rewrites_bare_names_flagged_not_skipped(self):
        move = {"path": "/PremFN_1v1/T_Foo", "dest_path": "/PremFN_1v1/Sub/T_Foo",
                "dest_folder": "/PremFN_1v1/Sub"}
        verse_file = self._write("a.verse", "Ref := T_Foo\n")

        edits = self.sortilege.build_verse_edits([move], [verse_file], ["/PremFN_1v1"])

        self.assertEqual(len(edits), 1)
        self.assertFalse(edits[0].get("skipped", False))
        self.assertTrue(edits[0]["is_bare"])
        self.assertIn("Sub.T_Foo", edits[0]["new_line"])

    def test_false_skips_the_bare_edit_but_keeps_the_qualified_one(self):
        # One fixture, both cases: a qualified (dotted) move must still be
        # rewritten; a bare (root-level) move must be skipped and listed,
        # never silently dropped.
        moves = [
            {"path": "/PremFN_1v1/Sub/T_Qualified",
             "dest_path": "/PremFN_1v1/Moved/T_Qualified",
             "dest_folder": "/PremFN_1v1/Moved"},
            {"path": "/PremFN_1v1/T_Bare", "dest_path": "/PremFN_1v1/Sub2/T_Bare",
             "dest_folder": "/PremFN_1v1/Sub2"},
        ]
        verse_file = self._write(
            "a.verse",
            "Real := Sub.T_Qualified\n"
            "Root := T_Bare\n")

        edits = self.sortilege.build_verse_edits(
            moves, [verse_file], ["/PremFN_1v1"], fix_bare_names=False)

        qualified = [e for e in edits if e["old_ref"] == "Sub.T_Qualified"]
        bare = [e for e in edits if e["old_ref"] == "T_Bare"]

        self.assertEqual(len(qualified), 1)
        self.assertFalse(qualified[0].get("skipped", False))
        self.assertEqual(qualified[0]["new_ref"], "Moved.T_Qualified")
        self.assertIn("Moved.T_Qualified", qualified[0]["new_line"])

        self.assertEqual(len(bare), 1)
        self.assertTrue(bare[0]["skipped"])
        self.assertEqual(bare[0]["new_ref"], "Sub2.T_Bare")

    def test_apply_never_touches_a_skipped_bare_edit(self):
        move = {"path": "/PremFN_1v1/T_Bare", "dest_path": "/PremFN_1v1/Sub/T_Bare",
                "dest_folder": "/PremFN_1v1/Sub"}
        verse_file = self._write("a.verse", "Root := T_Bare\n")
        edits = self.sortilege.build_verse_edits(
            [move], [verse_file], ["/PremFN_1v1"], fix_bare_names=False)
        self.assertEqual(len(edits), 1)
        self.assertTrue(edits[0]["skipped"])

        result = self.sortilege.apply_verse_edits(edits, self.tmp_dir)

        self.assertEqual(result, {"edited": [], "failed": [], "backup_index": None})
        with open(verse_file, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertEqual(content, "Root := T_Bare\n")

    def test_using_statement_edits_are_never_affected_by_the_toggle(self):
        move = {"path": "/PremFN_1v1/PowersWheelAssets/Models/Textures/T_Hex",
                "dest_path": "/PremFN_1v1/Textures/T_Hex",
                "dest_folder": "/PremFN_1v1/Textures"}
        verse_file = self._write(
            "a.verse", "using { /PremFN_1v1/PowersWheelAssets/Models/Textures }\n")

        edits = self.sortilege.build_verse_edits(
            [move], [verse_file], ["/PremFN_1v1"], fix_bare_names=False)

        using_edits = [e for e in edits if e["kind"] == "using"]
        self.assertEqual(len(using_edits), 1)
        self.assertFalse(using_edits[0].get("skipped", False))
        self.assertEqual(using_edits[0]["new_ref"], "/PremFN_1v1/Textures")

    def test_preview_and_report_show_the_skipped_bare_name_note(self):
        sortilege = helpers.load_sortilege(config_overrides={
            "VERSE_SEARCH_DIR": self.tmp_dir, "FIX_VERSE_BARE_NAMES": False})
        self._write("a.verse", "Root := T_Bare\n")
        mock_unreal.add_asset("/Game/T_Bare", "StaticMesh")
        assets = [asset("/Game/T_Bare", "StaticMesh")]
        plan = sortilege.build_plan(assets, sortilege.CONFIG, sortilege.probe_capabilities())

        self.assertEqual(len(plan["verse_edits"]), 1)
        self.assertTrue(plan["verse_edits"][0]["skipped"])

        lines = sortilege.format_preview(plan)
        text = "\n".join(lines)
        self.assertIn("skipped (bare name - fix manually): 1", text)
        # The proposed-edits headline must NOT count a skipped entry as
        # something that will actually be rewritten.
        self.assertNotIn("Verse reference edits proposed: 1", text)


# ---------------------------------------------------------------------------
# Review follow-up (Minor): plan_to_verse_edit_rows() -- the GUI row-shape
# function, tested directly with no tkinter involved (same style as
# PlanToMoveRowsTests/PlanToSkipRowsTests in test_gui.py).
# ---------------------------------------------------------------------------

class PlanToVerseEditRowsTests(unittest.TestCase):
    def setUp(self):
        self.sortilege = helpers.load_sortilege()

    def test_bare_ref_row_gets_the_review_note(self):
        plan = {"verse_edits": [
            {"file": "a.verse", "line_no": 3, "old_ref": "T_Foo", "new_ref": "Sub.T_Foo",
             "is_bare": True, "kind": "ref", "count": 1,
             "old_line": "x", "new_line": "y"},
        ]}
        rows = self.sortilege.plan_to_verse_edit_rows(plan)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["file"], "a.verse")
        self.assertEqual(rows[0]["line_no"], 3)
        self.assertEqual(rows[0]["old_ref"], "T_Foo")
        self.assertEqual(rows[0]["new_ref"], "Sub.T_Foo")
        self.assertIn("bare name", rows[0]["note"])

    def test_qualified_ref_row_has_no_note(self):
        plan = {"verse_edits": [
            {"file": "a.verse", "line_no": 7, "old_ref": "Stuff.T_Bar",
             "new_ref": "Meshes.T_Bar", "is_bare": False, "kind": "ref", "count": 1,
             "old_line": "x", "new_line": "y"},
        ]}
        rows = self.sortilege.plan_to_verse_edit_rows(plan)
        self.assertEqual(rows[0]["note"], "")

    def test_using_kind_row_never_gets_the_bare_note(self):
        plan = {"verse_edits": [
            {"file": "a.verse", "line_no": 1, "old_ref": "/Old/Folder",
             "new_ref": "/New/Folder", "is_bare": True, "kind": "using", "count": 1,
             "old_line": "x", "new_line": "y"},
        ]}
        rows = self.sortilege.plan_to_verse_edit_rows(plan)
        self.assertEqual(rows[0]["note"], "")

    def test_skipped_bare_row_gets_the_fix_manually_note(self):
        plan = {"verse_edits": [
            {"file": "a.verse", "line_no": 2, "old_ref": "T_Bare", "new_ref": "Sub.T_Bare",
             "is_bare": True, "kind": "ref", "count": 1, "skipped": True,
             "old_line": "x", "new_line": "x"},
        ]}
        rows = self.sortilege.plan_to_verse_edit_rows(plan)
        self.assertEqual(rows[0]["note"], "skipped - fix manually")

    def test_empty_plan_gives_empty_rows(self):
        self.assertEqual(self.sortilege.plan_to_verse_edit_rows({"verse_edits": []}), [])
        self.assertEqual(self.sortilege.plan_to_verse_edit_rows({}), [])


# ---------------------------------------------------------------------------
# Review follow-up (the real gap): the preview WINDOW must show the
# proposed Verse edits too, not just the Output Log.
# ---------------------------------------------------------------------------

class VerseEditsGuiTabTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="sortilege_verse_gui_")

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _write(self, relpath, content):
        full = os.path.join(self.tmp_dir, relpath)
        with open(full, "w", encoding="utf-8") as f:
            f.write(content)
        return full

    def test_verse_edits_tab_is_populated_from_the_plan_and_flags_bare_names(self):
        # The "(bare name - review)" note this asserts only exists for an
        # edit that will actually be made, so this test needs bare-name
        # rewriting explicitly on now that CONFIG defaults it off.
        sortilege = helpers.load_sortilege(config_overrides={
            "VERSE_SEARCH_DIR": self.tmp_dir, "FIX_VERSE_BARE_NAMES": True})
        self._write("a.verse", "Ref := T_Foo\n")
        mock_unreal.add_asset("/Game/T_Foo", "StaticMesh")
        assets = [asset("/Game/T_Foo", "StaticMesh")]
        caps = sortilege.probe_capabilities()
        plan = sortilege.build_plan(assets, sortilege.CONFIG, caps)
        self.assertEqual(len(plan["verse_edits"]), 1)

        handles = sortilege._build_preview_window(
            _make_fake_tk_module(), _make_fake_ttk_module(), _FakeMessagebox,
            plan, caps)

        self.assertIn("verse_tree", handles)
        rows = handles["verse_tree"].inserted_rows
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertTrue(any("T_Foo" in str(cell) for cell in row))
        self.assertTrue(any("bare name" in str(cell) for cell in row))

    def test_verse_edits_tab_updates_on_rescan(self):
        sortilege = helpers.load_sortilege(
            config_overrides={"VERSE_SEARCH_DIR": self.tmp_dir})
        self._write("a.verse", "Ref := Stuff.Rock\nOther := Stuff.Wood\n")
        mock_unreal.add_asset("/Game/Stuff/Rock", "StaticMesh")
        assets = [asset("/Game/Stuff/Rock", "StaticMesh")]
        caps = sortilege.probe_capabilities()
        plan = sortilege.build_plan(assets, sortilege.CONFIG, caps)
        handles = sortilege._build_preview_window(
            _make_fake_tk_module(), _make_fake_ttk_module(), _FakeMessagebox,
            plan, caps)
        self.assertEqual(len(handles["verse_tree"].inserted_rows), 1)

        # Re-scan after adding a second asset that also has a Verse
        # reference -- the tab must reflect the NEW plan, not the old one.
        mock_unreal.add_asset("/Game/Stuff/Wood", "Texture2D")
        assets2 = [asset("/Game/Stuff/Rock", "StaticMesh"),
                   asset("/Game/Stuff/Wood", "Texture2D")]
        sortilege.scan_assets = lambda scope: assets2
        handles["on_rescan"]()

        self.assertEqual(len(handles["verse_tree"].inserted_rows), 2)

    def test_no_verse_edits_leaves_the_tab_empty_without_crashing(self):
        sortilege = helpers.load_sortilege()
        mock_unreal.add_asset("/Game/Stuff/Rock", "StaticMesh")
        assets = [asset("/Game/Stuff/Rock", "StaticMesh")]
        caps = sortilege.probe_capabilities()
        plan = sortilege.build_plan(assets, sortilege.CONFIG, caps)

        handles = sortilege._build_preview_window(
            _make_fake_tk_module(), _make_fake_ttk_module(), _FakeMessagebox,
            plan, caps)  # must not raise

        self.assertEqual(handles["verse_tree"].inserted_rows, [])


class VerseEditsResultsBarTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="sortilege_verse_gui_bar_")

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _write(self, relpath, content):
        full = os.path.join(self.tmp_dir, relpath)
        with open(full, "w", encoding="utf-8") as f:
            f.write(content)
        return full

    def test_results_bar_reports_verse_reference_update_count_after_apply(self):
        sortilege = helpers.load_sortilege(
            config_overrides={"VERSE_SEARCH_DIR": self.tmp_dir})
        self._write("a.verse", "MyRock := Stuff.Rock\n")
        mock_unreal.add_asset("/Game/Stuff/Rock", "StaticMesh")
        assets = [asset("/Game/Stuff/Rock", "StaticMesh")]
        caps = sortilege.probe_capabilities()
        plan = sortilege.build_plan(assets, sortilege.CONFIG, caps)
        handles = sortilege._build_preview_window(
            _make_fake_tk_module(), _make_fake_ttk_module(), _FakeMessagebox,
            plan, caps)

        handles["apply_var"].set(True)
        handles["on_apply"]()

        result_var = handles["state"].get("result_var")
        self.assertIsNotNone(result_var)
        text = result_var.get()
        self.assertIn("1 Verse reference updated", text)
        self.assertNotIn("1 Verse references updated", text)

    def test_results_bar_omits_verse_line_when_nothing_was_rewritten(self):
        sortilege = helpers.load_sortilege()
        mock_unreal.add_asset("/Game/Stuff/Rock", "StaticMesh")
        assets = [asset("/Game/Stuff/Rock", "StaticMesh")]
        caps = sortilege.probe_capabilities()
        plan = sortilege.build_plan(assets, sortilege.CONFIG, caps)
        handles = sortilege._build_preview_window(
            _make_fake_tk_module(), _make_fake_ttk_module(), _FakeMessagebox,
            plan, caps)

        handles["apply_var"].set(True)
        handles["on_apply"]()

        text = handles["state"]["result_var"].get()
        self.assertNotIn("Verse reference", text)


# ---------------------------------------------------------------------------
# The Verse fixup must not fail soft AFTER the point of no return.
#
# Reproduced by running the code: the fixup ran only once execute_plan()
# had committed every move, so when it could not locate the project's
# .verse source at that point, it rewrote nothing -- and the run still
# finished with verify ok. Every asset had moved, every Verse reference
# still named the old location, and the tool reported success.
#
# Locating the source is READ-ONLY, so it now happens before the moves,
# and a run that can no longer see the .verse files the dry run just
# found is refused at a point where refusing still costs nothing.
# ---------------------------------------------------------------------------

class VerseFixupBeforePointOfNoReturnTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="sortilege_verse_gate_")

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _write(self, relpath, content):
        full = os.path.join(self.tmp_dir, relpath)
        with open(full, "w", encoding="utf-8") as f:
            f.write(content)
        return full

    def test_apply_is_refused_before_a_single_asset_moves(self):
        # "system_path": False is this build's way of being unable to
        # derive the project directory from an asset -- the dry run only
        # got there through the VERSE_SEARCH_DIR override, which is gone
        # by the time apply runs.
        sortilege = helpers.load_sortilege(
            features={"system_path": False},
            config_overrides={"VERSE_SEARCH_DIR": self.tmp_dir})
        verse_file = self._write("a.verse", "MyRock := Stuff.Rock\n")
        mock_unreal.add_asset("/Game/Stuff/Rock", "StaticMesh")
        assets = [asset("/Game/Stuff/Rock", "StaticMesh")]
        caps = sortilege.probe_capabilities()
        plan = sortilege.build_plan(assets, sortilege.CONFIG, caps)
        self.assertEqual(plan["verse_files_count"], 1)

        sortilege.CONFIG["VERSE_SEARCH_DIR"] = ""

        outcome = sortilege.run_apply(plan, caps)

        self.assertTrue(outcome.get("blocked"))
        self.assertIsNone(outcome["undo_log"])
        self.assertEqual(outcome["results"], {})
        # The whole point of refusing HERE: the project is untouched, so
        # there is nothing to undo and nothing left half-done.
        lib = mock_unreal.EditorAssetLibrary
        self.assertTrue(lib.does_asset_exist("/Game/Stuff/Rock"))
        self.assertFalse(lib.does_asset_exist("/Game/Meshes/Rock"))
        with open(verse_file, "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), "MyRock := Stuff.Rock\n")

    def test_the_reason_names_both_ways_out(self):
        sortilege = helpers.load_sortilege(
            features={"system_path": False},
            config_overrides={"VERSE_SEARCH_DIR": self.tmp_dir})
        self._write("a.verse", "MyRock := Stuff.Rock\n")
        mock_unreal.add_asset("/Game/Stuff/Rock", "StaticMesh")
        assets = [asset("/Game/Stuff/Rock", "StaticMesh")]
        caps = sortilege.probe_capabilities()
        plan = sortilege.build_plan(assets, sortilege.CONFIG, caps)
        sortilege.CONFIG["VERSE_SEARCH_DIR"] = ""

        reason = sortilege.run_apply(plan, caps)["blocked"]

        self.assertIn("VERSE_SEARCH_DIR", reason)
        self.assertIn("FIX_VERSE_REFERENCES", reason)
        self.assertIn("Nothing has been moved", reason)

    def test_a_project_with_no_verse_code_is_never_blocked(self):
        # The gate must be narrow enough that a project with no Verse
        # source at all sorts exactly as it always did -- it has no Verse
        # references to break, so it has nothing to lose.
        sortilege = helpers.load_sortilege(features={"system_path": False})
        mock_unreal.add_asset("/Game/Stuff/Rock", "StaticMesh")
        assets = [asset("/Game/Stuff/Rock", "StaticMesh")]
        caps = sortilege.probe_capabilities()
        plan = sortilege.build_plan(assets, sortilege.CONFIG, caps)
        self.assertEqual(plan["verse_files_count"], 0)

        outcome = sortilege.run_apply(plan, caps)

        self.assertIsNone(outcome.get("blocked"))
        self.assertTrue(
            mock_unreal.EditorAssetLibrary.does_asset_exist("/Game/Meshes/Rock"))

    def _blocked_run_apply(self):
        return lambda *a, **k: {
            "blocked": "the dry run found 1 .verse file(s) to check, but "
                       "the Verse source directory could not be located "
                       "again now. Nothing has been moved.",
            "plan_path": "x", "report_path": None, "undo_log": None,
            "results": {},
        }

    def test_console_apply_reports_the_block_instead_of_a_run_summary(self):
        # main()'s apply branch reads outcome["undo_log"].path to print
        # the undo command, so a blocked outcome has to be recognized
        # before any of that -- stubbed here (the same seam test_gui.py
        # uses) to test the CALLER, not the gate.
        sortilege = helpers.load_sortilege(config_overrides={
            "I_UNDERSTAND_THIS_MODIFIES_MY_PROJECT": True})
        mock_unreal.add_asset("/Game/Stuff/Rock", "StaticMesh")
        mock_unreal.set_dialog_answer("Yes")

        original_run_apply = sortilege.run_apply
        sortilege.run_apply = self._blocked_run_apply()
        try:
            sortilege.main(mode="apply")
        finally:
            sortilege.run_apply = original_run_apply

        state = mock_unreal.get_state()
        logged = "\n".join(state["log"])
        self.assertIn("APPLY BLOCKED", logged)
        self.assertNotIn("Sortilege apply complete", logged)
        self.assertIn("/Game/Stuff/Rock", state["assets"])

    def test_gui_apply_leaves_the_window_usable_instead_of_showing_results(self):
        # _show_results_bar() reads outcome["results"] and outcome
        # ["report_path"] unconditionally, so the blocked outcome must
        # never reach it. The window keeps its controls so the user can
        # fix CONFIG and re-scan.
        sortilege = helpers.load_sortilege()
        mock_unreal.add_asset("/Game/Stuff/Rock", "StaticMesh")
        assets = [asset("/Game/Stuff/Rock", "StaticMesh")]
        caps = sortilege.probe_capabilities()
        plan = sortilege.build_plan(assets, sortilege.CONFIG, caps)
        handles = sortilege._build_preview_window(
            _make_fake_tk_module(), _make_fake_ttk_module(), _FakeMessagebox,
            plan, caps)

        original_run_apply = sortilege.run_apply
        sortilege.run_apply = self._blocked_run_apply()
        try:
            handles["apply_var"].set(True)
            handles["on_apply"]()
        finally:
            sortilege.run_apply = original_run_apply

        self.assertIsNone(handles["state"]["apply_outcome"])
        self.assertIsNone(handles["state"].get("result_var"))
        self.assertFalse(handles["state"]["busy"])

    def test_verify_is_not_ok_when_a_verse_file_could_not_be_rewritten(self):
        # The other half of the same failure: the rewrite pass runs after
        # the moves, so a .verse file it could not write is a file whose
        # references now point at the old location. A run that ends that
        # way is not ok, and must not say it is.
        sortilege = helpers.load_sortilege()
        caps = sortilege.probe_capabilities()
        results = {
            "moved": [],
            "verse_edits": {"edited": [], "backup_index": None,
                            "failed": [("a.verse", "permission denied")]},
        }

        verify = sortilege.verify_results(results, [], caps)

        self.assertFalse(verify["ok"])
        self.assertEqual(verify["verse_failures"], [("a.verse", "permission denied")])

    def test_verify_stays_ok_when_the_verse_stage_never_ran(self):
        # SAFE_MODE, FIX_VERSE_REFERENCES off, or simply nothing to do:
        # no verse_edits key at all must read as "not checked", never as
        # a failure.
        sortilege = helpers.load_sortilege()
        caps = sortilege.probe_capabilities()

        verify = sortilege.verify_results({"moved": []}, [], caps)

        self.assertTrue(verify["ok"])
        self.assertEqual(verify["verse_failures"], [])


# ---------------------------------------------------------------------------
# A Verse reference is only a reference in CODE.
#
# Reproduced by running the code: the rewrite was pure regex over the
# whole line, so it edited matches inside comments and string literals,
# and it turned "var Sphere : int = 9" -- a variable that merely shares a
# moved asset's name -- into "var Meshes.Sphere : int = 9", which does
# not compile. Both cases now go through _replace_verse_ref(), which
# build_verse_edits() and apply_verse_edits() share, so the preview and
# the file on disk can never disagree about what gets touched.
# ---------------------------------------------------------------------------

class VerseSyntaxAwareRewriteTests(unittest.TestCase):
    def setUp(self):
        self.sortilege = helpers.load_sortilege()
        self.tmp_dir = tempfile.mkdtemp(prefix="sortilege_verse_syntax_")
        self.log_dir = tempfile.mkdtemp(prefix="sortilege_verse_syntax_logs_")

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)
        shutil.rmtree(self.log_dir, ignore_errors=True)

    def _write(self, relpath, content):
        full = os.path.join(self.tmp_dir, relpath)
        with open(full, "w", encoding="utf-8") as f:
            f.write(content)
        return full

    def _sphere_move(self):
        return {"path": "/PremFN_1v1/Sphere",
                "dest_path": "/PremFN_1v1/Meshes/Sphere",
                "dest_folder": "/PremFN_1v1/Meshes"}

    def test_a_declared_variable_sharing_the_name_is_never_rewritten(self):
        verse_file = self._write("a.verse", "var Sphere : int = 9\n")

        edits = self.sortilege.build_verse_edits(
            [self._sphere_move()], [verse_file], ["/PremFN_1v1"],
            fix_bare_names=True)

        self.assertEqual(edits, [])

    def test_a_defined_name_is_never_rewritten(self):
        verse_file = self._write("a.verse", "Sphere := 9\n")

        edits = self.sortilege.build_verse_edits(
            [self._sphere_move()], [verse_file], ["/PremFN_1v1"],
            fix_bare_names=True)

        self.assertEqual(edits, [])

    def test_a_genuine_reference_on_a_declaration_line_still_gets_rewritten(self):
        # The guard is per-occurrence, not per-line: the name being
        # declared here is Radius, and Sphere is a real reference.
        verse_file = self._write("a.verse", "Radius : float = Sphere\n")

        edits = self.sortilege.build_verse_edits(
            [self._sphere_move()], [verse_file], ["/PremFN_1v1"],
            fix_bare_names=True)

        self.assertEqual(len(edits), 1)
        self.assertEqual(edits[0]["new_line"], "Radius : float = Meshes.Sphere")

    def test_comments_and_string_literals_are_left_alone(self):
        # Dotted refs, so this holds regardless of FIX_VERSE_BARE_NAMES.
        move = {"path": "/PremFN_1v1/Stuff/Rock",
                "dest_path": "/PremFN_1v1/Meshes/Rock",
                "dest_folder": "/PremFN_1v1/Meshes"}
        verse_file = self._write(
            "a.verse",
            "# Stuff.Rock used to live here\n"
            'Label := "Stuff.Rock"\n'
            "Real := Stuff.Rock\n")

        edits = self.sortilege.build_verse_edits([move], [verse_file], ["/PremFN_1v1"])

        self.assertEqual([e["line_no"] for e in edits], [3])
        self.assertEqual(edits[0]["new_line"], "Real := Meshes.Rock")

    def test_a_trailing_comment_never_shields_the_code_before_it(self):
        move = {"path": "/PremFN_1v1/Stuff/Rock",
                "dest_path": "/PremFN_1v1/Meshes/Rock",
                "dest_folder": "/PremFN_1v1/Meshes"}
        verse_file = self._write("a.verse", "Real := Stuff.Rock # Stuff.Rock\n")

        edits = self.sortilege.build_verse_edits([move], [verse_file], ["/PremFN_1v1"])

        self.assertEqual(len(edits), 1)
        self.assertEqual(edits[0]["count"], 1)
        self.assertEqual(edits[0]["new_line"], "Real := Meshes.Rock # Stuff.Rock")

    def test_apply_refuses_on_disk_exactly_what_the_preview_refused(self):
        # apply_verse_edits() re-derives the substitution from old_ref/
        # new_ref against the file's current contents rather than writing
        # the preview's precomputed new_line, so the guard has to hold
        # THERE too. This hands it the edit the old code would have
        # produced for a declaration line and checks the file on disk.
        original = "var Sphere : int = 9\n"
        verse_file = self._write("a.verse", original)
        edits = [{
            "file": verse_file, "line_no": 1,
            "old_line": "var Sphere : int = 9",
            "new_line": "var Sphere : int = 9",
            "old_ref": "Sphere", "new_ref": "Meshes.Sphere",
            "is_bare": True, "count": 1, "kind": "ref", "skipped": False,
        }]

        self.sortilege.apply_verse_edits(edits, self.log_dir)

        with open(verse_file, "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), original)

    def test_apply_refuses_a_comment_occurrence_on_disk_too(self):
        original = "# Stuff.Rock used to live here\n"
        verse_file = self._write("a.verse", original)
        edits = [{
            "file": verse_file, "line_no": 1,
            "old_line": original.rstrip("\n"), "new_line": original.rstrip("\n"),
            "old_ref": "Stuff.Rock", "new_ref": "Meshes.Rock",
            "is_bare": False, "count": 1, "kind": "ref", "skipped": False,
        }]

        self.sortilege.apply_verse_edits(edits, self.log_dir)

        with open(verse_file, "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), original)


# ---------------------------------------------------------------------------
# One .verse file, one backup.
#
# Reproduced by running the code: backup names folded every path
# separator to "_", so "Content/a/b.verse" and "Content/a_b.verse" both
# became "Content_a_b.verse". The second backup overwrote the first, and
# undo then restored one file's contents into BOTH files -- an undo that
# corrupts is worse than no undo at all.
# ---------------------------------------------------------------------------

class VerseBackupCollisionTests(unittest.TestCase):
    def setUp(self):
        self.sortilege = helpers.load_sortilege()
        self.tmp_dir = tempfile.mkdtemp(prefix="sortilege.verse.collide.")
        self.log_dir = tempfile.mkdtemp(prefix="sortilege.verse.collide.logs.")

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)
        shutil.rmtree(self.log_dir, ignore_errors=True)

    def test_a_separator_and_a_literal_underscore_map_to_different_backups(self):
        nested = self.sortilege._verse_backup_path(
            "/backups", os.path.join("/proj", "Content", "a", "b.verse"))
        flat = self.sortilege._verse_backup_path(
            "/backups", os.path.join("/proj", "Content", "a_b.verse"))

        self.assertNotEqual(nested, flat)

    def test_undo_restores_each_file_its_own_contents(self):
        os.makedirs(os.path.join(self.tmp_dir, "a"))
        nested_path = os.path.join(self.tmp_dir, "a", "b.verse")
        flat_path = os.path.join(self.tmp_dir, "a_b.verse")
        nested_original = "Nested := Stuff.Rock\n"
        flat_original = "Flat := Stuff.Rock\n"
        for path, content in ((nested_path, nested_original),
                              (flat_path, flat_original)):
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)

        move = {"path": "/Game/Stuff/Rock", "dest_path": "/Game/Meshes/Rock",
                "dest_folder": "/Game/Meshes"}
        edits = self.sortilege.build_verse_edits(
            [move], [nested_path, flat_path], ["/Game"])
        self.assertEqual(len(edits), 2)

        apply_result = self.sortilege.apply_verse_edits(edits, self.log_dir)
        self.assertEqual(apply_result["failed"], [])

        undo_result = self.sortilege.undo_verse_edits(apply_result["backup_index"])
        self.assertEqual(undo_result["failed"], [])

        with open(nested_path, "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), nested_original)
        with open(flat_path, "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), flat_original)


if __name__ == "__main__":
    unittest.main()
