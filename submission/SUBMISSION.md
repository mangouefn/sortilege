# Sortilege - Bounty Submission

A single-file UEFN Python editor tool (`sortilege.py`) that scans a project's Content Drawer, classifies every asset by type, and sorts it into a standard folder structure using reference-safe move APIs. Dry-run preview is the default; nothing changes until a deliberate two-gate confirm passes.

---

## Requirements checklist

| Requirement | How Sortilege meets it | Where to see it |
|---|---|---|
| Scans and classifies assets by type | `scan_assets()` walks the asset registry for every discovered content root (or a given scope) and returns each asset's class name; `classify()` maps that class name to a category (Meshes, Materials, Textures, Audio, Animations, Props, UI, VFX, Other) via the editable `CONFIG["CLASSIFICATION"]` table, falling back to "Other" (or a skip, under `STRICT_MODE`) for anything unlisted. | `scan_assets()`, `classify()` in `sortilege.py` |
| Dry-run preview is the default | Running the script with no argument, or with `preview`, only scans, builds a plan, prints it, and writes `sortilege_plan_<timestamp>.json`. No asset API that mutates anything is ever called on this path. If the build's Python supports it, that same preview invocation also opens an interactive preview window (see below); if it does not, or `CONFIG["USE_GUI"]` is set `False`, the console-only flow is used and behaves identically either way. | `main()`'s `preview` branch; `build_plan()`, `format_preview()`, `print_preview()`, `launch_preview_window()` |
| A deliberate confirm is required before executing | Two ways to confirm, depending on which flow is running. Console flow (used whenever the preview window is off or unavailable): two independent gates, both must pass: (1) `CONFIG["I_UNDERSTAND_THIS_MODIFIES_MY_PROJECT"]` must be hand-edited from `False` to `True` in the file itself; (2) if the build supports it, a Yes/No `EditorDialog` confirm naming the actual move/rename counts. Either gate failing blocks the run with an explanation printed to the Output Log; nothing is changed. GUI flow: the preview window's own "I understand this will modify my project" checkbox plus its Apply button is the deliberate confirm for that run instead (the checkbox defaults unchecked every time and is never persisted); the config flag and the EditorDialog popup are not also shown on top of it. Either way, nothing runs without an explicit, on-purpose action. | `confirmed_to_execute()`; the literal flag `"I_UNDERSTAND_THIS_MODIFIES_MY_PROJECT": False,` in `CONFIG`; the checkbox + Apply button in `_build_preview_window()` |
| Proper asset-move APIs so references do not break | The only move primitive used is a per-asset `unreal.EditorAssetLibrary.rename_asset()` call (the same official "Move" operation the Content Drawer's own rename/drag-move uses), never `os.rename`/`shutil.move`/raw filesystem operations. Every move is verified afterward with `does_asset_exist()`. A best-effort soft-reference fix-up pass follows, using `AssetTools.rename_referencing_soft_object_paths()` where the build supports it. | `execute_plan()`, `object_path()`, `fix_soft_references()` |
| Auto redirector cleanup | After a real run, `cleanup_redirectors()` finds every redirector left behind in the touched folders, tries the future-proofed `fix_up_redirectors()` API first if a build ever exposes it, then falls through to the manual recipe: resave every referencer, then delete the redirector once confirmed unreferenced. Scope is always just the folders the run actually touched, never a whole-project sweep. | `cleanup_redirectors()`, `find_redirectors()`; gated by `CONFIG["CLEAN_REDIRECTORS"]` (default `True`) |
| Editable folder-map config at the top of the file | `CONFIG["FOLDER_MAP"]` sits in the CONFIG block at the very top of `sortilege.py`, with a comment above it explaining it is safe to hand-edit, no coding knowledge needed. Renaming the value on the right of any line changes that category's destination folder name; the folder is created automatically if missing. | `CONFIG["FOLDER_MAP"]`, lines near the top of `sortilege.py` |
| Never-move guard for structural project assets | Checked first, before any other rule, in both flat and group-by-asset mode, regardless of `STRICT_MODE`: the project's GameFeatureData asset, every Level/World (and its MapBuildData and World Partition data layers), and anything Verse-linked are always skipped, never relocated, with an exact-class set plus a case-insensitive substring net (`gamefeature`/`worldpartition`/`verse`) so an unenumerated subclass is still caught. There is deliberately no "Verse" entry in `FOLDER_MAP` either. Epic's asset-move APIs do not rewrite Verse source references (folder-qualified names), so moving a Verse-linked asset could silently break Verse code with no redirector protection to catch it; moving the GameFeatureData asset breaks the project outright ("missing its GameFeatureData"). These are safety decisions, not a missing feature. | `NEVER_MOVE_CLASSES`, `_never_move_reason()`, `PROTECTED_CATEGORIES["VerseClass"]`, `classify()`, `CONFIG["FOLDER_MAP"]` (no "Verse" key) |
| Summary log of what moved/was skipped and why | Every real run writes `sortilege_report_<timestamp>.txt`: what moved (old to new), what was renamed, what failed, and a full skipped list grouped by reason ("already sorted", "protected system folder", "excluded folder", "outside project content", "unknown class (STRICT_MODE)", "destination occupied", "name collision with planned move", "invalid target name"), plus the redirector cleanup result, a "Cleaned up N empty folder(s)" section (removed folders listed, kept folders listed with the reason they were kept), and the post-run verification result. | `write_summary()`, `_group_by_reason()`, `cleanup_empty_folders()` |
| Bonus: prefix renaming with dry-run | `CONFIG["ENABLE_PREFIX_RENAME"]` (default `False`) turns on renaming assets to match a class-to-prefix convention (`CONFIG["PREFIX_MAP"]`), correctly stripping a wrong existing prefix before applying the right one. Fully covered by the same preview-first, two-gate confirm as every other change; nothing renames in preview mode. | `_compute_new_name()`, `CONFIG["ENABLE_PREFIX_RENAME"]`, `CONFIG["PREFIX_MAP"]` |
| Bonus: sort-only-selected-folder mode | `CONFIG["SCOPE_FOLDERS"]` limits a run to specific folder paths. `CONFIG["USE_SELECTION"]` (marked experimental in the README, off by default) additionally tries to read the Content Drawer's live folder selection and falls back to `SCOPE_FOLDERS` if that read is unavailable or empty on a given build. | `resolve_scope()`, `CONFIG["SCOPE_FOLDERS"]`, `CONFIG["USE_SELECTION"]` |
| Bonus: undo log | Every real run writes an incrementally-updated `sortilege_undo_<timestamp>.json`, one entry per move, the instant that move commits (so a mid-run crash never loses an already-committed move). `py "sortilege.py" undo` reverses the most recent (or a given) undo log, through the same two confirm gates, printing exactly what it is about to restore first. | `UndoLog`, `undo()`, the `undo` branch in `main()` |

---

## Competitive edge

Things Sortilege does that go beyond the base requirements:

- **Interactive preview window.** An optional tkinter window (falls back automatically to the console flow on any build that cannot open it) shows the same scan results in a sortable, full-path table instead of truncated console text, lets you edit the folder-map/sort-root and re-scan live without touching the file or re-running the script, and gates apply behind its own in-window confirm checkbox with a one-click undo right after a run finishes.
- **Group-by-asset mode (dependency clustering).** Instead of scattering every imported prop's kit across /Meshes, /Materials and /Textures, an optional mode follows each asset's dependency chain and keeps the whole kit together under one folder named after the prop (mesh, then its materials, then their textures, nested along the chain), with shared dependencies routed to a Shared folder and everything else sorting flat as usual. Falls back to flat sorting automatically on builds without a dependency-query API.
- **Crash-safe undo log**, written incrementally as each move commits, so a mid-run editor crash or power loss never loses the ability to reverse what already happened.
- **Best-effort redirector cleanup and post-run verification**, scoped only to the folders an actual run touched, never a whole-project sweep, with anything left over named explicitly in the report rather than hidden.
- **Two independent confirm gates on the console flow** (a hand-edited config flag plus a native Yes/No dialog), so nothing executes from a stray command or a misclick.
- **Post-flight empty-folder sweep.** After the moves (and after redirector cleanup, so cleaned redirectors do not hold folders open), source folders left empty are removed, including parent folders that empty out once their children go, deepest first. Folders still holding anything, including a leftover redirector or an empty subfolder, are kept and listed in the report with the reason (the engine's folder delete is a force delete, so the sweep double-checks for subfolders before ever calling it). Never touches content roots, protected paths, or excluded folders; runs after undo too, sweeping the sorted folders the restore vacated.
- **Fails soft everywhere an optional UEFN API might not exist** on a given build: every capability is probed once and gated, never assumed, so the tool degrades gracefully instead of crashing on an unsupported version.
- **Never-move guard for structural project assets.** GameFeatureData, Levels/maps, MapBuildData, World Partition data layers, and anything Verse-linked are excluded before classification even runs, in both flat and group-by-asset mode -- closing a real field failure where one of these was moved and the project came back broken.
- **Live status during the whole apply, not just the moves.** The preview window's status line and on-top behavior cover every stage (soft-reference fixup, redirector cleanup, empty-folder sweep, verification, report writing), not only the initial move pass, with a running count on the slower passes so a multi-minute apply never looks frozen.

---

## How it keeps references safe

Sortilege moves assets by looping a single, official per-asset call, `unreal.EditorAssetLibrary.rename_asset()`, which is the engine's own reference-safe "Move" operation, the same underlying mechanism the Content Drawer's own drag-move and right-click Rename use. Every move is followed by a `does_asset_exist()` check on the destination before it counts as a success. This move leaves a redirector behind at the old path (a small pointer asset that keeps anything still pointing at the old location working), and Sortilege then attempts to clean that redirector up: it resaves everything that references it and deletes the redirector only once its referencer list is confirmed empty. After the whole run, a verification pass (on by default via the `VERIFY_AFTER` config setting) confirms every moved asset actually exists at its new path and no longer resolves to a real (non-redirector) asset at its old one, and a soft-reference fix-up pass runs where the build supports it. An undo log records every committed move as it happens, so the whole run can be reversed later even if something goes wrong afterward.

This cleanup is best-effort, not a guarantee. Anything Sortilege cannot safely resave and clear (for example a referencer it cannot load in the current session) is left in place and listed by name in the run's report file, under both the redirector cleanup section and the verification section, rather than being silently force-deleted or hidden.

The hardest reference-safety guarantee is a step before any of that: some assets are never handed to `rename_asset()` at all. The project's GameFeatureData asset, every Level/World and its MapBuildData/World Partition data layers, and anything Verse-linked are excluded up front by a never-move guard, checked before classification, before grouping, and before `STRICT_MODE`'s "Other" fallback -- so a Blueprint kit that depends on one of these leaves it untouched instead of pulling it into the kit's folder. This closes an actual field failure: a live run once moved a project's GameFeatureData asset, and the project came back broken ("missing its GameFeatureData") with broken references left behind. No amount of redirector cleanup can fix that after the fact, so this class of asset is now simply never moved.

---

## Demo recording script

1. Create a scratch UEFN project (or a throwaway folder inside an existing one) containing a handful of test assets: a couple of Static Meshes, a couple of Textures, at least one Material, and make sure one Material actually references one of the Textures (so there is a real reference to prove safe afterward).
2. Open the Reference Viewer on that Material (right-click it in the Content Drawer, "Reference Viewer") and take a screenshot or clip showing it correctly referencing the Texture. This is the BEFORE state.
3. Open the Output Log, switch to Cmd mode, and run:
   ```
   py "C:/path/to/sortilege.py"
   ```
   Show the printed dry-run preview on screen: the header stats, the per-category move table, and the footer's dry-run notice and caution lines. Point out that nothing has changed yet.
4. Open `sortilege.py` in a text editor, find `"I_UNDERSTAND_THIS_MODIFIES_MY_PROJECT": False,` in the CONFIG block, change it to `True`, and save.
5. Back in the Output Log, run:
   ```
   py "C:/path/to/sortilege.py" apply
   ```
   Show the confirm dialog (if the build supports it) before accepting, then let the run complete.
6. In the Content Drawer, show the assets now sitting in their new Meshes / Materials / Textures folders.
7. Open the Reference Viewer on the same Material again. Show it still correctly references the (now-moved) Texture. This is the AFTER state, proving the reference survived the move.
8. Open the run's report file (`sortilege_report_<timestamp>.txt`) and show the redirector cleanup line: either zero redirectors remaining in the scope that was touched, or, if any are left, point to the named remainder list in that same report.
9. Open the project's map/island and show it still opens normally with no errors.
10. Run undo:
    ```
    py "C:/path/to/sortilege.py" undo
    ```
    Show the "about to restore N move(s)" preview it prints, confirm through the dialog if present, and then show the assets back in their original folders in the Content Drawer.

---

## License

I agree to license this under Apache 2.0 + Commons Clause

## Credit

mangoUEFN (X/Twitter @mango_UEFN, YouTube @mangoUEFN)

## Zip contents

- `sortilege.py`
- `README.md`
