using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;

namespace ClaudeHand.Editor
{
    /// <summary>
    /// Undo points for Claude's requests. Just before a subagent's first Unity action for a request, its guard hook
    /// asks for a mark (a new Undo group, named after the request); "되돌리기" in the chat window reverts everything
    /// done in this editor since the newest mark. That covers what went through Unity's Undo: MCP for Unity's tools,
    /// ch_import_*, and execute_code that registers its changes with Undo.
    /// Unity's Undo knows nothing of files. What ch_import_model and ch_import_package do to them is written down
    /// with the mark (a model file replaced in place and where the one before is kept, the assets that came in), and
    /// undoing the mark puts the files back as well: the kept file returns, what came in is taken out of the project
    /// (to Library/ClaudeHand/removed, see TakeOut).
    /// A mark is set before the first action that might change something, and a tool that runs any code might, also
    /// when the request only wanted to know where things are. So a mark watches whether anything changed after it
    /// (Watch): one that nothing happened after has nothing to undo, is not offered, and an undo passes it by.
    /// Kept in SessionState: survives script reloads and ends with the editor, like the Undo history itself.
    /// </summary>
    [ChTool("ch_checkpoint",
        Description = "Undo points for requests. mark: set one (with Claude Hand running, the guard hook sets it before your " +
                      "first action of every request: do not set your own). undo: revert everything since the newest point " +
                      "that has something to undo (use it when the user asks to cancel what you just did; a point after which " +
                      "nothing changed is passed by); model files that ch_import_model replaced since then are put back and " +
                      "the assets ch_import_model and ch_import_package brought in are taken out of the project (kept in " +
                      "Library/ClaudeHand/removed). list: show the points that have something to undo.")]
    public static class ChCheckpoint
    {
        private const string Key = "ClaudeHand.Checkpoints";
        private const int Kept = 20;
        private const int ReplacedKept = 10;  // files (or one transfer's folder of files) kept in Library/ClaudeHand/replaced
        private const int RemovedKept = 10;   // assets taken out of the project, kept in Library/ClaudeHand/removed
        public const string RemovedFolder = "Library/ClaudeHand/removed";
        private static List<Mark> _marks;

        /// <summary>One thing a request did to the project's files, to be taken back with its mark.</summary>
        public sealed class FileNote
        {
            public string kind;  // "added": an asset that came in; "replaced": a file written over in place
            public string path;  // the asset (Assets/...)
            public string guid;  // added: its GUID when known (a package can land an asset under another name)
            public string kept;  // replaced: where the file it replaced is kept; its .meta lies next to it
        }

        public sealed class Mark
        {
            public int group;
            public string label;
            public string time;
            public List<FileNote> files = new List<FileNote>();

            /// <summary>
            /// Nothing has changed in the editor since the mark was set, so there is nothing to undo down to it (a
            /// request that only looked, with a tool that could have changed things). False once something changes,
            /// and for a mark of a version that did not watch.
            /// </summary>
            [JsonProperty(DefaultValueHandling = DefaultValueHandling.Ignore)]
            public bool untouched;

            /// <summary>After UndoLast: what happened to the files.</summary>
            [JsonIgnore] public FileReport report;
        }

        public sealed class FileReport
        {
            public List<string> restored = new List<string>();  // put back as they were before the request
            public List<string> removed = new List<string>();   // came in with the request: taken out of the project
            public List<string> problems = new List<string>();

            /// <summary>One sentence for the chat window, or null when no file was touched.</summary>
            public string Summary()
            {
                var parts = new List<string>();
                if (restored.Count > 0) parts.Add($"파일 {restored.Count}개를 이전 것으로 되돌렸어요");
                if (removed.Count > 0) parts.Add($"가져온 에셋 {removed.Count}개를 프로젝트에서 뺐어요({RemovedFolder}에 보관)");
                if (problems.Count > 0) parts.Add($"{problems.Count}개는 되돌리지 못했어요({problems[0]})");
                return parts.Count == 0 ? null : string.Join(", ", parts) + ".";
            }
        }

        public class Parameters
        {
            [ChParameter("mark (default), undo or list", Required = false)]
            public string action { get; set; }

            [ChParameter("For mark: the request in a few words, shown on the chat window's undo button", Required = false)]
            public string label { get; set; }
        }

        /// <summary>Raised on the main thread whenever a mark is added or undone (the chat window repaints).</summary>
        public static event Action Changed;

        public static IReadOnlyList<Mark> Marks => _marks ??= JsonConvert.DeserializeObject<List<Mark>>(SessionState.GetString(Key, "[]")) ?? new List<Mark>();

        /// <summary>The marks that have something to undo, oldest first: what the undo button and `list` show.</summary>
        public static IReadOnlyList<Mark> Undoable => Marks.Where(m => !m.untouched).ToList();

        public static Mark Add(string label)
        {
            Undo.IncrementCurrentGroup();
            var mark = new Mark
            {
                group = Undo.GetCurrentGroup(),
                label = string.IsNullOrWhiteSpace(label) ? "Claude 요청" : label.Trim(),
                time = DateTime.Now.ToString("HH:mm"),
                untouched = true,
            };
            Undo.SetCurrentGroupName("Claude Hand: " + mark.label);
            // A mark that nothing happened after stands for the same state as this one, which takes its place.
            Save(Undoable.Append(mark).ToList());
            return mark;
        }

        /// <summary>
        /// Reverts everything since the newest mark that has something to undo (also edits made by hand after it) and
        /// drops the mark, with the ones after it that nothing happened after.
        /// </summary>
        public static Mark UndoLast()
        {
            var marks = Undoable.ToList();
            if (marks.Count == 0) return null;
            var mark = marks[marks.Count - 1];
            Undo.RevertAllDownToGroup(mark.group);
            mark.report = RevertFiles(mark);
            Save(marks.Take(marks.Count - 1).ToList());
            return mark;
        }

        // ------------------------------------------------------------------ whether a mark has something to undo

        [InitializeOnLoadMethod]
        private static void Watch()
        {
            // What Unity's Undo can take back shows up here, by the end of the editor tick it happened in: objects
            // made, removed or moved in the hierarchy, components added, properties changed (through Undo or a
            // SerializedObject). Looking at the scene or selecting in it does not, and neither does a change made
            // past Undo, which an undo could not take back anyway.
            ObjectChangeEvents.changesPublished += (ref ObjectChangeEventStream stream) => Touched();
        }

        /// <summary>Something changed in the editor: the newest mark has something to undo from now on.</summary>
        internal static void Touched()
        {
            if (Marks.Count == 0 || !Marks[Marks.Count - 1].untouched) return;
            Marks[Marks.Count - 1].untouched = false;
            Save(Marks.ToList());
        }

        private static void Save(List<Mark> marks)
        {
            _marks = marks.Skip(Math.Max(0, marks.Count - Kept)).ToList();
            SessionState.SetString(Key, JsonConvert.SerializeObject(_marks));
            Changed?.Invoke();
        }

        // ------------------------------------------------------------------ what a request did to files

        /// <summary>An asset (a file or a folder) came in with the request of the newest mark. False: there is no mark to undo it with.</summary>
        public static bool NoteAdded(string assetPath, string guid = null)
        {
            return Note(new[] { new FileNote { kind = "added", path = assetPath, guid = guid } });
        }

        /// <summary>A file was written over in place; `kept` is what Keep returned for it. False: there is no mark to undo it with.</summary>
        public static bool NoteReplaced(string assetPath, string kept)
        {
            return Note(new[] { new FileNote { kind = "replaced", path = assetPath, kept = kept } });
        }

        /// <summary>Several at once, in the order they happened (a package). False: there is no mark to undo them with.</summary>
        public static bool Note(IEnumerable<FileNote> notes)
        {
            if (Marks.Count == 0) return false;
            var mark = Marks[Marks.Count - 1];
            (mark.files ??= new List<FileNote>()).AddRange(notes);
            mark.untouched = false;
            Save(Marks.ToList());
            return true;
        }

        /// <summary>
        /// Copies a project file that is about to be written over, and its .meta, to Library/ClaudeHand/replaced
        /// (the newest few are kept; `group` puts the files of one transfer in a folder that counts as one) and
        /// returns where the file went.
        /// </summary>
        public static string Keep(string file, string group = null)
        {
            var root = Path.Combine(ChPaths.LibraryDir, "replaced");
            var dir = string.IsNullOrEmpty(group) ? root : Path.Combine(root, ChPaths.Sanitize(group));
            Directory.CreateDirectory(dir);
            var stem = $"{Path.GetFileNameWithoutExtension(file)}_{DateTime.Now:yyyyMMdd_HHmmss}";
            var kept = Path.Combine(dir, stem + Path.GetExtension(file));
            for (int n = 2; File.Exists(kept); n++)
            {
                // Replaced twice within a second: the earlier file must not be the one that gets written over.
                kept = Path.Combine(dir, $"{stem}_{n}{Path.GetExtension(file)}");
            }
            File.Copy(file, kept, false);
            if (File.Exists(file + ".meta")) File.Copy(file + ".meta", kept + ".meta", true);
            Prune(root, ReplacedKept);
            return ChPaths.ToPosix(kept);
        }

        /// <summary>
        /// Moves an asset that came in with a request (a file, or a folder with what is in it) and its .meta out of the
        /// project, to a folder of its own in Library/ClaudeHand/removed (the newest few are kept), and returns where it
        /// went. Putting the two back under Assets brings the asset back with its GUID. Not the system's trash: a
        /// Recycle Bin set to delete at once, or a drive that has none, would make "it is in the trash" untrue, and
        /// the user would learn it only when wanting the asset back.
        /// </summary>
        private static string TakeOut(string assetPath)
        {
            var root = Path.Combine(ChPaths.LibraryDir, "removed");
            var name = Path.GetFileName(assetPath.TrimEnd('/'));
            var stem = Path.Combine(root, $"{DateTime.Now:yyyyMMdd_HHmmss}_{ChPaths.Sanitize(name)}");
            var dir = stem;
            for (int n = 2; Directory.Exists(dir); n++) dir = $"{stem}_{n}";
            Directory.CreateDirectory(dir);
            var source = Path.Combine(ChPaths.ProjectRoot, assetPath);
            var target = Path.Combine(dir, name);
            if (Directory.Exists(source)) Directory.Move(source, target);
            else File.Move(source, target);
            if (File.Exists(source + ".meta")) File.Move(source + ".meta", target + ".meta");
            Prune(root, RemovedKept);
            return ChPaths.ToPosix(target);
        }

        /// <summary>Keeps the newest `keep` entries of a holding folder (a file with its .meta counts as one).</summary>
        private static void Prune(string root, int keep)
        {
            try
            {
                // By when they were kept, not by the files' own dates: a copy keeps the date of what it was copied from.
                var old = new DirectoryInfo(root).GetFileSystemInfos().Where(e => !e.Name.EndsWith(".meta", StringComparison.OrdinalIgnoreCase))
                    .OrderByDescending(e => e.CreationTimeUtc).Skip(keep);
                foreach (var entry in old)
                {
                    if (entry is DirectoryInfo folder) folder.Delete(true);
                    else
                    {
                        entry.Delete();
                        File.Delete(entry.FullName + ".meta");
                    }
                }
            }
            catch (Exception)
            {
                // housekeeping; never fail an import or an undo over it
            }
        }

        /// <summary>Takes back what the mark's request did to files, newest first.</summary>
        private static FileReport RevertFiles(Mark mark)
        {
            var report = new FileReport();
            foreach (var note in Enumerable.Reverse(mark.files ?? new List<FileNote>()))
            {
                try
                {
                    if (note.kind == "replaced") Restore(note, report);
                    else if (note.kind == "added") Remove(note, report);
                }
                catch (Exception ex)
                {
                    report.problems.Add($"{note.path}: {ex.Message}");
                }
            }
            if (report.removed.Count > 0) AssetDatabase.Refresh();  // the files went from under the asset database
            return report;
        }

        private static void Restore(FileNote note, FileReport report)
        {
            if (string.IsNullOrEmpty(note.kept) || !File.Exists(note.kept))
            {
                report.problems.Add($"{note.path}: the file it replaced is no longer kept");
                return;
            }
            var target = Path.Combine(ChPaths.ProjectRoot, note.path);
            if (!File.Exists(target)) return;  // gone since (it came in with this same request, or was deleted by hand): nothing to put back into
            File.Copy(note.kept, target, true);
            if (File.Exists(note.kept + ".meta")) File.Copy(note.kept + ".meta", target + ".meta", true);  // same GUID; the import settings of before
            AssetDatabase.ImportAsset(note.path, ImportAssetOptions.ForceSynchronousImport | ImportAssetOptions.ForceUpdate);
            if (!report.restored.Contains(note.path)) report.restored.Add(note.path);
        }

        private static void Remove(FileNote note, FileReport report)
        {
            var path = string.IsNullOrEmpty(note.guid) ? note.path : AssetDatabase.GUIDToAssetPath(note.guid);
            if (string.IsNullOrEmpty(path) || !path.StartsWith("Assets/", StringComparison.Ordinal)) return;
            var absolute = Path.Combine(ChPaths.ProjectRoot, path);
            if (!File.Exists(absolute) && !Directory.Exists(absolute)) return;  // already gone
            TakeOut(path);
            report.removed.Add(path);
        }

        public static object HandleCommand(JObject @params)
        {
            var p = @params?.ToObject<Parameters>() ?? new Parameters();
            switch ((p.action ?? "mark").Trim().ToLowerInvariant())
            {
                case "mark":
                    var busy = ChErrors.BusyIfCompiling();
                    if (busy != null) return busy;
                    var mark = Add(p.label);
                    return new ChSuccess($"Undo point {mark.group} set", new { mark.group, mark.label, mark.time });
                case "undo":
                    var undone = UndoLast();
                    if (undone == null) return ChErrors.Make(ChErrors.NotFound, "There is no undo point to go back to.", "after_fix");
                    var files = undone.report;
                    var message = $"Reverted everything since '{undone.label}' ({undone.time})";
                    if (files.restored.Count > 0) message += $"; {files.restored.Count} file(s) are back as they were before";
                    if (files.removed.Count > 0) message += $"; {files.removed.Count} asset(s) that came in were taken out of the project (kept in {RemovedFolder})";
                    if (files.problems.Count > 0) message += $"; {files.problems.Count} file(s) could not be put back (see problems)";
                    return new ChSuccess(message, new { undone.label, undone.time, files.restored, files.removed, files.problems });
                case "list":
                    var shown = Undoable;
                    int skipped = Marks.Count - shown.Count;
                    return new ChSuccess($"{shown.Count} undo point(s)" + (skipped > 0 ? $" ({skipped} more that nothing happened after: skipped)" : ""),
                        new { marks = shown });
                default:
                    return ChErrors.Make(ChErrors.InvalidArgs, "action must be mark, undo or list.", "after_fix");
            }
        }
    }

    /// <summary>Assets that came in, went or moved are a change as well (ChCheckpoint.Touched).</summary>
    internal sealed class ChCheckpointAssets : AssetPostprocessor
    {
        private static void OnPostprocessAllAssets(string[] imported, string[] deleted, string[] moved, string[] movedFrom)
        {
            if (imported.Length + deleted.Length + moved.Length > 0) ChCheckpoint.Touched();
        }
    }
}
