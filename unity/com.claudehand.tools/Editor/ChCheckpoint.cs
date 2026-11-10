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
    /// undoing the mark puts the files back as well: the kept file returns, what came in goes to the trash.
    /// Kept in SessionState: survives script reloads and ends with the editor, like the Undo history itself.
    /// </summary>
    [ChTool("ch_checkpoint",
        Description = "Undo points for requests. mark (default): set one before changing anything for a new request (the " +
                      "guard hook already does this before your first action). undo: revert everything since the newest point " +
                      "(use it when the user asks to cancel what you just did); model files that ch_import_model replaced " +
                      "since then are put back and the assets ch_import_model and ch_import_package brought in go to the " +
                      "trash. list: show the points.")]
    public static class ChCheckpoint
    {
        private const string Key = "ClaudeHand.Checkpoints";
        private const int Kept = 20;
        private const int ReplacedKept = 10;  // files (or one transfer's folder of files) kept in Library/ClaudeHand/replaced
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

            /// <summary>After UndoLast: what happened to the files.</summary>
            [JsonIgnore] public FileReport report;
        }

        public sealed class FileReport
        {
            public List<string> restored = new List<string>();  // put back as they were before the request
            public List<string> removed = new List<string>();   // came in with the request: moved to the trash
            public List<string> problems = new List<string>();

            /// <summary>One sentence for the chat window, or null when no file was touched.</summary>
            public string Summary()
            {
                var parts = new List<string>();
                if (restored.Count > 0) parts.Add($"파일 {restored.Count}개를 이전 것으로 되돌렸어요");
                if (removed.Count > 0) parts.Add($"가져온 에셋 {removed.Count}개를 휴지통으로 옮겼어요");
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

        public static Mark Add(string label)
        {
            Undo.IncrementCurrentGroup();
            var mark = new Mark
            {
                group = Undo.GetCurrentGroup(),
                label = string.IsNullOrWhiteSpace(label) ? "Claude 요청" : label.Trim(),
                time = DateTime.Now.ToString("HH:mm"),
            };
            Undo.SetCurrentGroupName("Claude Hand: " + mark.label);
            Save(Marks.Append(mark).ToList());
            return mark;
        }

        /// <summary>Reverts everything since the newest mark (also edits made by hand after it) and drops the mark.</summary>
        public static Mark UndoLast()
        {
            if (Marks.Count == 0) return null;
            var mark = Marks[Marks.Count - 1];
            Undo.RevertAllDownToGroup(mark.group);
            mark.report = RevertFiles(mark);
            Save(Marks.Take(Marks.Count - 1).ToList());
            return mark;
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
            try
            {
                // By when they were kept, not by the files' own dates: a copy keeps the date of what it was copied from.
                var old = new DirectoryInfo(root).GetFileSystemInfos().Where(e => !e.Name.EndsWith(".meta", StringComparison.OrdinalIgnoreCase))
                    .OrderByDescending(e => e.CreationTimeUtc).Skip(ReplacedKept);
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
                // housekeeping; never fail an import over it
            }
            return ChPaths.ToPosix(kept);
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
            if (AssetDatabase.MoveAssetToTrash(path)) report.removed.Add(path);
            else report.problems.Add($"{path}: could not be moved to the trash");
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
                    if (files.removed.Count > 0) message += $"; {files.removed.Count} asset(s) that came in went to the trash";
                    if (files.problems.Count > 0) message += $"; {files.problems.Count} file(s) could not be put back (see problems)";
                    return new ChSuccess(message, new { undone.label, undone.time, files.restored, files.removed, files.problems });
                case "list":
                    return new ChSuccess($"{Marks.Count} undo point(s)", new { marks = Marks });
                default:
                    return ChErrors.Make(ChErrors.InvalidArgs, "action must be mark, undo or list.", "after_fix");
            }
        }
    }
}
