using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace ClaudeHand.Editor
{
    /// <summary>
    /// Undo points for Claude's requests. Just before a subagent's first Unity action for a request, its guard hook
    /// asks for a mark (a new Undo group, named after the request); "되돌리기" in the chat window reverts everything
    /// done in this editor since the newest mark. That covers what went through Unity's Undo: MCP for Unity's tools,
    /// ch_import_*, and execute_code that registers its changes with Undo.
    /// Unity's Undo knows nothing of files. What a request does to them is written down with its mark, and undoing
    /// the mark takes that back as well. Three sources:
    ///   - ch_import_model and ch_import_package say what they replace and bring in (Note, Keep).
    ///   - What Unity tells beforehand while the request is at work, whatever tool caused it (WillCreate, WillSave,
    ///     WillDelete, WillMove): a new asset, a save through Unity (the file is copied first), a deletion (the asset
    ///     is moved to Library/ClaudeHand/removed instead), a move.
    ///   - What Unity is not told of beforehand, a file written over directly (a script, a shader): the subagent's
    ///     guard hook copies the files a call names before the call runs (tools/handlib/keeps.py) and writes that
    ///     down in the request's folder, where an undo finds it.
    /// "While the request is at work" is what the guard hook says in Library/ClaudeHand/at-work.json; a file the user
    /// makes or changes by hand afterwards is not the request's, and an undo leaves it alone. (What went through
    /// Unity's Undo is different: reverting to a mark takes along everything done after it.)
    /// A mark is set before the first action that might change something, and a tool that runs any code might, also
    /// when the request only wanted to know where things are. So a mark watches whether anything changed after it
    /// (Watch): one that nothing happened after has nothing to undo, is not offered, and an undo passes it by.
    /// Kept in SessionState: survives script reloads and ends with the editor, like the Undo history itself.
    /// </summary>
    [ChTool("ch_checkpoint",
        Description = "Undo points for requests. mark: set one (with Claude Hand running, the guard hook sets it before your " +
                      "first action of every request: do not set your own). undo: revert everything since the newest point " +
                      "that has something to undo (use it when the user asks to cancel what you just did; a point after which " +
                      "nothing changed is passed by). Files go back too, whatever tool touched them while the request was " +
                      "at work: files it changed are as before (restored), assets it made or brought in are taken out of " +
                      "the project (removed, kept in Library/ClaudeHand/removed), assets it deleted are back (returned), " +
                      "assets it moved or renamed are where they were (moved). unkept lists files that changed with no " +
                      "copy of what was there before: they stay as they are. list: show the points that have something to undo.")]
    public static class ChCheckpoint
    {
        private const string Key = "ClaudeHand.Checkpoints";
        private const int Kept = 20;
        private const int ReplacedKept = 10;  // requests (or one transfer's folder of files) kept in Library/ClaudeHand/replaced
        private const int FollowKept = 5;     // earlier files of models that follow their .blend, in replaced/follow (one folder of the ten)
        private const double QuietSeconds = 30;  // how long after the user's own doing Unity may still tell of that asset
        private const int RemovedKept = 10;   // requests and undos whose assets are kept in Library/ClaudeHand/removed
        private const long MaxKeepBytes = 64L * 1024 * 1024;  // a larger file is not copied before every save (keeps.py MAX_BYTES)
        private const double WorkSeconds = 300;  // with no word that the subagent stopped: how long its last call counts (keeps.py WORK_SECONDS)
        private const double WorkGrace = 15;     // after it stopped: what Unity still imports of its doing (a script written just now)
        public const string RemovedFolder = "Library/ClaudeHand/removed";
        private static List<Mark> _marks;
        private static bool _unsaved;
        private static bool _reverting;

        /// <summary>One thing a request did to the project's files, to be taken back with its mark.</summary>
        public sealed class FileNote
        {
            // "added": an asset that was made or came in; "replaced": a file about to be written over, copied first;
            // "deleted": an asset the request deleted, kept instead; "moved": an asset moved or renamed;
            // "changed": a file written over or deleted past Unity, of which this package has no copy.
            public string kind;
            public string path;  // the asset (Assets/...); moved: where it went

            [JsonProperty(NullValueHandling = NullValueHandling.Ignore)]
            public string guid;  // added: its GUID when known (a package can land an asset under another name)

            [JsonProperty(NullValueHandling = NullValueHandling.Ignore)]
            public string kept;  // replaced: where the copy is, its .meta next to it; deleted: where the asset lies now

            [JsonProperty(NullValueHandling = NullValueHandling.Ignore)]
            public string origin;  // moved: where it was

            [JsonProperty(DefaultValueHandling = DefaultValueHandling.Ignore)]
            public double ts;  // when (Unix seconds): an undo goes back through the notes and the guard's copies in order

            [JsonIgnore] public bool guard;  // a copy the guard hook made, read from the request's folder at undo
        }

        public sealed class Mark
        {
            public int group;
            public string label;
            public string time;

            /// <summary>
            /// The name of the request, as the guard hook gave it (the chat channel's request id; without Claude Hand
            /// a name the hook made up). What happens to files while that request is at work is noted here, and the
            /// files the hook kept under that name go back with the mark. Null for a mark set with no hook around.
            /// </summary>
            [JsonProperty(NullValueHandling = NullValueHandling.Ignore)]
            public string request;

            [JsonProperty(DefaultValueHandling = DefaultValueHandling.Ignore)]
            public double stamp;  // when it was set (Unix seconds)

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
            public List<string> restored = new List<string>();  // changed by the request: as they were before it
            public List<string> removed = new List<string>();   // made or brought in by the request: taken out of the project
            public List<string> returned = new List<string>();  // deleted by the request: back
            public List<string> moved = new List<string>();     // moved or renamed by the request: where they were
            public List<string> unkept = new List<string>();    // changed with no copy of what was there: left as they are
            public List<string> problems = new List<string>();

            /// <summary>One sentence for the chat window, or null when no file was touched.</summary>
            public string Summary()
            {
                var parts = new List<string>();
                if (restored.Count > 0) parts.Add($"파일 {restored.Count}개를 이전 것으로 되돌렸어요");
                if (removed.Count > 0) parts.Add($"이 요청으로 생긴 에셋 {removed.Count}개를 프로젝트에서 뺐어요({RemovedFolder}에 보관)");
                if (returned.Count > 0) parts.Add($"지워졌던 에셋 {returned.Count}개를 되살렸어요");
                if (moved.Count > 0) parts.Add($"옮겨졌던 에셋 {moved.Count}개를 제자리로 돌렸어요");
                if (unkept.Count > 0) parts.Add($"이전 내용이 보관되지 않은 파일 {unkept.Count}개는 그대로예요({Few(unkept)})");
                if (problems.Count > 0) parts.Add($"{problems.Count}개는 되돌리지 못했어요({problems[0]})");
                return parts.Count == 0 ? null : string.Join(", ", parts) + ".";
            }
        }

        /// <summary>What undoing a mark would do to files: counted beforehand, for the chat window's confirmation.</summary>
        public sealed class FilePlan
        {
            public int restore;   // files that go back to what they were
            public int remove;    // assets taken out of the project
            public int putBack;   // deleted assets that return
            public int moveBack;  // assets that go back to where they were
            public List<string> unkept = new List<string>();  // changed with no copy: they stay
        }

        public class Parameters
        {
            [ChParameter("mark (default), undo, list, or undo_request (Claude Hand's own: see request)", Required = false)]
            public string action { get; set; }

            [ChParameter("For mark: the request in a few words, shown on the chat window's undo button", Required = false)]
            public string label { get; set; }

            [ChParameter("The request's name. For mark, Claude Hand's guard hook fills it in; leave it out. undo_request " +
                         "undoes the request of this name, only when its point is the newest one with something to undo " +
                         "(Claude Hand's chat channel calls it in every window a request changed)", Required = false)]
            public string request { get; set; }
        }

        /// <summary>Raised on the main thread whenever a mark is added or undone (the chat window repaints).</summary>
        public static event Action Changed;

        public static IReadOnlyList<Mark> Marks => _marks ??= JsonConvert.DeserializeObject<List<Mark>>(SessionState.GetString(Key, "[]")) ?? new List<Mark>();

        /// <summary>The marks that have something to undo, oldest first: what the undo button and `list` show.</summary>
        public static IReadOnlyList<Mark> Undoable => Marks.Where(m => !m.untouched).ToList();

        private static double Now => (DateTime.UtcNow - new DateTime(1970, 1, 1, 0, 0, 0, DateTimeKind.Utc)).TotalSeconds;

        internal static string Few(List<string> paths)
        {
            return string.Join(", ", paths.Take(2).Select(Path.GetFileName)) + (paths.Count > 2 ? " 등" : "");
        }

        /// <summary>request: the request's name; when not given, the one the guard hook says is at work now, if any.</summary>
        public static Mark Add(string label, string request = null)
        {
            Undo.IncrementCurrentGroup();
            var mark = new Mark
            {
                group = Undo.GetCurrentGroup(),
                label = string.IsNullOrWhiteSpace(label) ? "Claude 요청" : label.Trim(),
                time = DateTime.Now.ToString("HH:mm"),
                request = string.IsNullOrWhiteSpace(request) ? RequestAtWork() : request.Trim(),
                stamp = Now,
                untouched = true,
            };
            Undo.SetCurrentGroupName("Claude Hand: " + mark.label);
            // A mark that nothing happened after stands for the same state as this one, which takes its place.
            Save(Undoable.Append(mark).ToList());
            return mark;
        }

        /// <summary>
        /// Reverts everything since the newest mark that has something to undo (also edits made by hand after it; of
        /// files, what changed while the mark's request was at work) and drops the mark, with the ones after it that
        /// nothing happened after.
        /// </summary>
        public static Mark UndoLast()
        {
            var marks = Undoable.ToList();
            if (marks.Count == 0) return null;
            var mark = marks[marks.Count - 1];
            _reverting = true;  // what the undo itself does to files is nobody's request
            try
            {
                Undo.RevertAllDownToGroup(mark.group);
                mark.report = RevertFiles(mark);
            }
            finally
            {
                _reverting = false;
            }
            Save(marks.Take(marks.Count - 1).ToList());
            return mark;
        }

        /// <summary>An undo is putting things back right now: what it does to files and objects is nobody's request.</summary>
        internal static bool Reverting => _reverting;

        /// <summary>What happens to files right now is this package's own passing business, or the user's own doing (Unnoted, UsersOwn).</summary>
        internal static bool Unnoting => _unnoted > 0;

        /// <summary>
        /// For ChProtect, which found something protected changed: undoes what the request at work did in this
        /// project, with its point. Only when that point is the newest one: the point of an earlier request would
        /// take that request's work back instead. Null when there is no such point (nothing is undone then).
        /// </summary>
        internal static Mark UndoForProtection()
        {
            if (Marks.Count == 0) return null;
            var mark = Marks[Marks.Count - 1];
            if (string.IsNullOrEmpty(mark.request) || mark.request != RequestAtWork()) return null;
            Touched();  // a change past Undo (which is how it got past the notices) leaves the point looking as if nothing happened after it
            return UndoLast();
        }

        // ------------------------------------------------------------------ whether a mark has something to undo

        [InitializeOnLoadMethod]
        private static void Watch()
        {
            // What Unity's Undo can take back shows up here, by the end of the editor tick it happened in: objects
            // made, removed or moved in the hierarchy, components added, properties changed (through Undo or a
            // SerializedObject). Looking at the scene or selecting in it does not, and neither does a change made
            // past Undo, which an undo could not take back anyway.
            ObjectChangeEvents.changesPublished += (ref ObjectChangeEventStream stream) =>
            {
                // A model the user's own save in Blender replaced (UsersOwn): the objects that use it show it anew a
                // tick or two later, and that is not a change a request made.
                if (UsersOwnLately() && OnlyAssetsAnew(ref stream)) return;
                Touched();
            };
            AssemblyReloadEvents.beforeAssemblyReload += Flush;  // notes not written yet must outlive the reload
        }

        /// <summary>
        /// Nothing but assets read anew and the objects made of them brought up to date (what a replaced model gives:
        /// UpdatePrefabInstances, seen in Unity 6.3): no object was made, moved or edited.
        /// </summary>
        private static bool OnlyAssetsAnew(ref ObjectChangeEventStream stream)
        {
            for (int i = 0; i < stream.length; i++)
            {
                switch (stream.GetEventType(i))
                {
                    case ObjectChangeKind.UpdatePrefabInstances:
                    case ObjectChangeKind.CreateAssetObject:
                    case ObjectChangeKind.DestroyAssetObject:
                    case ObjectChangeKind.ChangeAssetObjectProperties:
                        continue;
                    default:
                        return false;
                }
            }
            return true;
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
            _unsaved = false;
            _marks = marks.Skip(Math.Max(0, marks.Count - Kept)).ToList();
            SessionState.SetString(Key, JsonConvert.SerializeObject(_marks));
            Changed?.Invoke();
        }

        /// <summary>
        /// Writes the marks once this editor tick is over: an import tells of its assets one by one, and writing all
        /// the notes out after each would take longer with every one.
        /// </summary>
        private static void SaveSoon()
        {
            if (_unsaved) return;
            _unsaved = true;
            EditorApplication.delayCall += Flush;
        }

        private static void Flush()
        {
            if (_unsaved) Save(Marks.ToList());
        }

        // ------------------------------------------------------------------ which request is at work

        private sealed class Work
        {
            public string request;
            public double ts;       // the subagent's last call
            public double stopped;  // when it finished its turn; 0 while it works
        }

        private static string WorkFile => Path.Combine(ChPaths.LibraryDir, "at-work.json");
        private static Work _work;
        private static long _workRead;

        /// <summary>
        /// The request a subagent works for in this project right now, as its guard hook wrote it down before its
        /// last call (tools/handlib/keeps.py), or null: nobody is at work, and what happens to files is the user's own.
        /// </summary>
        /// <summary>A subagent is at work in this project for some request right now.</summary>
        internal static bool SomebodyAtWork => RequestAtWork() != null;

        private static string RequestAtWork()
        {
            try
            {
                var info = new FileInfo(WorkFile);
                if (!info.Exists)
                {
                    _work = null;
                    return null;
                }
                long written = info.LastWriteTimeUtc.Ticks + info.Length;
                if (_work == null || written != _workRead)
                {
                    _work = JsonConvert.DeserializeObject<Work>(File.ReadAllText(WorkFile));
                    _workRead = written;
                }
                if (_work == null || string.IsNullOrEmpty(_work.request)) return null;
                bool atWork = _work.stopped > 0 ? Now - _work.stopped <= WorkGrace : Now - _work.ts <= WorkSeconds;
                return atWork ? _work.request : null;
            }
            catch (Exception)
            {
                return null;  // being written this instant, or unreadable: this one event goes unnoted
            }
        }

        /// <summary>
        /// For the self-test, which runs with no guard hook: says what the hook would, that a subagent is at work for
        /// this request (null: that nobody is).
        /// </summary>
        internal static void SetWork(string request)
        {
            Directory.CreateDirectory(ChPaths.LibraryDir);
            if (request == null) File.Delete(WorkFile);
            else File.WriteAllText(WorkFile, JsonConvert.SerializeObject(new Work { request = request, ts = Now }));
            _work = null;
        }

        /// <summary>The newest mark, while a subagent is at work for the request it was set for; else null.</summary>
        private static Mark AtWork()
        {
            if (_reverting || _unnoted > 0 || Marks.Count == 0) return null;
            var mark = Marks[Marks.Count - 1];
            return !string.IsNullOrEmpty(mark.request) && mark.request == RequestAtWork() ? mark : null;
        }

        private static int _unnoted;
        private static readonly List<string> OwnFolders = new List<string>();  // staged by this package: all in them
        private static readonly List<string> OwnParents = new List<string>();  // the folders above those: only they themselves

        /// <summary>
        /// Until the returned scope is closed, what happens to files is this package's own passing business: the
        /// prefabs an export stages in `folder` and removes again before it returns. None of it is noted for the
        /// request at work (an undo would bring the staging folder back just to take it out again), a deletion goes
        /// Unity's usual way, and what Unity tells of that folder afterwards is no change to the project either.
        /// </summary>
        internal static IDisposable Unnoted(string folder)
        {
            _unnoted++;
            if (!OwnFolders.Contains(folder)) OwnFolders.Add(folder);
            if (OwnFolders.Count > 32) OwnFolders.RemoveAt(0);
            for (var parent = Path.GetDirectoryName(folder)?.Replace('\\', '/'); !string.IsNullOrEmpty(parent) && parent != "Assets";
                 parent = Path.GetDirectoryName(parent)?.Replace('\\', '/'))
            {
                if (!OwnParents.Contains(parent)) OwnParents.Add(parent);
            }
            return new UnnotedScope();
        }

        private sealed class UnnotedScope : IDisposable
        {
            private bool _closed;

            public void Dispose()
            {
                if (_closed) return;
                _closed = true;
                _unnoted--;
            }
        }

        private static readonly Dictionary<string, double> UsersOwnPaths = new Dictionary<string, double>();  // asset -> until when

        /// <summary>
        /// Until the returned scope is closed, what happens to `assetPath` is the user's own doing, though a tool of
        /// this package carries it out: a model that follows its .blend and was sent again because the user saved
        /// there (ch_model_follow). It is no request's, whichever request is at work in this project: nothing of it
        /// is noted for an undo, and for a short while afterwards what Unity tells of that asset, and of the scene
        /// objects that show it anew, does not make the newest mark one with something to undo (a request that only
        /// looked would get a 되돌리기 that takes nothing back).
        /// </summary>
        internal static IDisposable UsersOwn(string assetPath)
        {
            _unnoted++;
            UsersOwnPaths[assetPath] = Now + QuietSeconds;
            return new UnnotedScope();
        }

        /// <summary>Inside a UsersOwn scope: this asset is written as part of the same doing (a followed model's material or image).</summary>
        internal static void AlsoUsersOwn(string assetPath)
        {
            UsersOwnPaths[assetPath] = Now + QuietSeconds;
        }

        /// <summary>The user's own doing replaced an asset a short while ago (and Unity may still be telling of it).</summary>
        private static bool UsersOwnLately()
        {
            if (UsersOwnPaths.Count == 0) return false;
            foreach (var over in UsersOwnPaths.Where(p => p.Value < Now).Select(p => p.Key).ToList()) UsersOwnPaths.Remove(over);
            return UsersOwnPaths.Count > 0;
        }

        private static string[] NotOwn(string[] paths)
        {
            UsersOwnLately();
            if (OwnFolders.Count == 0 && UsersOwnPaths.Count == 0) return paths;
            return paths.Where(p => !OwnFolders.Any(folder => Under(p, folder)) && !OwnParents.Contains(p) &&
                                    !UsersOwnPaths.ContainsKey(Asset(p))).ToArray();
        }

        /// <summary>Unity has imported, lost or moved assets: a change (Touched), and maybe a request's doing (Imported).</summary>
        internal static void AssetsChanged(string[] imported, string[] deleted, string[] moved)
        {
            imported = NotOwn(imported);
            deleted = NotOwn(deleted);
            moved = NotOwn(moved);
            if (imported.Length + deleted.Length + moved.Length == 0) return;
            ChProtect.AssetsChanged(imported, deleted);
            Touched();
            Imported(imported, deleted);
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
            foreach (var note in notes)
            {
                if (note.ts <= 0) note.ts = Now;
                // Unity told of it already while the tool made it (WillCreate): one note, with the GUID the tool knows.
                var same = note.kind == "added" ? Notes(mark).FirstOrDefault(n => n.kind == "added" && n.path == note.path) : null;
                if (same == null) Notes(mark).Add(note);
                else same.guid ??= note.guid;
            }
            mark.untouched = false;
            Save(Marks.ToList());
            return true;
        }

        private static bool InAssets(string path) => path != null && path.StartsWith("Assets/", StringComparison.Ordinal);

        /// <summary>The asset a path names, also when it is the path of its .meta.</summary>
        private static string Asset(string path)
        {
            return path.EndsWith(".meta", StringComparison.OrdinalIgnoreCase) ? path.Substring(0, path.Length - ".meta".Length) : path;
        }

        private static string Absolute(string assetPath) => Path.Combine(ChPaths.ProjectRoot, assetPath);

        private static bool Exists(string absolute) => File.Exists(absolute) || Directory.Exists(absolute);

        /// <summary>The path itself, or something in the folder `folder`.</summary>
        private static bool Under(string path, string folder)
        {
            return path == folder || path.StartsWith(folder + "/", StringComparison.Ordinal);
        }

        private static List<FileNote> Notes(Mark mark) => mark.files ??= new List<FileNote>();

        private static bool Has(Mark mark, string kind, string path) => Notes(mark).Any(n => n.kind == kind && n.path == path);

        /// <summary>The mark's request made this asset itself (or the folder it is in): there is no "before" of it to keep.</summary>
        private static bool Came(Mark mark, string path) => Notes(mark).Any(n => n.kind == "added" && Under(path, n.path));

        private static bool IsOpenScene(string assetPath)
        {
            for (int i = 0; i < SceneManager.sceneCount; i++)
            {
                var scene = SceneManager.GetSceneAt(i);
                if (scene.isLoaded && !string.IsNullOrEmpty(scene.path) && Under(scene.path, assetPath)) return true;
            }
            return false;
        }

        /// <summary>The folder name, in replaced/ and removed/, of what is kept for the mark's request (keeps.py request_folder).</summary>
        private static string Folder(Mark mark) => "req_" + mark.request;

        private static void AddNote(Mark mark, FileNote note)
        {
            note.ts = Now;
            Notes(mark).Add(note);
            mark.untouched = false;
            SaveSoon();
        }

        private static void Quietly(Action act)
        {
            try
            {
                act();
            }
            catch (Exception ex)
            {
                // Noting is a nicety: it must never get in the way of the save or the import it watches.
                Debug.LogWarning("[Claude Hand] Could not note a file change for undo: " + ex.Message);
            }
        }

        /// <summary>
        /// Unity is about to make an asset, or the .meta of a file that was written into the project directly. It also
        /// says so when an asset that is there is saved again (a scene), so: new only while what it names is not on disk.
        /// </summary>
        internal static void WillCreate(string name)
        {
            Quietly(() =>
            {
                var mark = AtWork();
                if (mark == null || !InAssets(name) || Exists(Absolute(name))) return;
                var path = Asset(name);
                if (!Came(mark, path)) AddNote(mark, new FileNote { kind = "added", path = path });
            });
        }

        /// <summary>
        /// Unity is about to write these files (a material, a prefab, import settings in a .meta); what was there is
        /// still on disk. The first time for a file in a request, that is the file as it was before the request: copied.
        /// </summary>
        internal static void WillSave(string[] names)
        {
            Quietly(() =>
            {
                var mark = AtWork();
                if (mark == null) return;
                foreach (var name in names)
                {
                    if (!InAssets(name)) continue;
                    var path = Asset(name);
                    var file = Absolute(path);
                    if (!File.Exists(file)) continue;  // not there yet (WillCreate has it), or a folder
                    if (Came(mark, path) || Has(mark, "replaced", path) || Has(mark, "changed", path)) continue;
                    // An open scene's file is not put back from under the editor (it would ask to reload): it is saved again instead.
                    if (IsOpenScene(path)) continue;
                    if (new FileInfo(file).Length > MaxKeepBytes) AddNote(mark, new FileNote { kind = "changed", path = path });
                    else AddNote(mark, new FileNote { kind = "replaced", path = path, kept = Keep(file, Folder(mark)) });
                }
            });
        }

        /// <summary>
        /// Unity is about to delete an asset (to the system's trash or for good). While a request is at work the asset
        /// is moved to Library/ClaudeHand/removed instead, and Unity is told the deleting is done: undoing the request
        /// puts it back with its GUID. The trash would not do: one set to delete at once keeps nothing. True when taken.
        /// </summary>
        internal static bool WillDelete(string path)
        {
            try
            {
                var mark = AtWork();
                if (mark == null || !InAssets(path) || !Exists(Absolute(path)) || IsOpenScene(path)) return false;
                var kept = TakeOut(path, Folder(mark));
                AddNote(mark, new FileNote { kind = "deleted", path = path, kept = kept });
                return true;
            }
            catch (Exception ex)
            {
                Debug.LogWarning($"[Claude Hand] Could not keep {path} for undo, so Unity deletes it as usual: {ex.Message}");
                return false;
            }
        }

        /// <summary>Unity is about to move or rename an asset.</summary>
        internal static void WillMove(string from, string to)
        {
            Quietly(() =>
            {
                var mark = AtWork();
                if (mark != null && InAssets(from) && InAssets(to)) AddNote(mark, new FileNote { kind = "moved", path = to, origin = from });
            });
        }

        /// <summary>
        /// Unity has imported or lost these. A file that was written while the request is at work, and that neither
        /// came in with it nor was copied first, was written over directly; one that went without Unity deleting it
        /// was deleted directly. The guard hook has a copy if the call named the file; if not, there is none, and an
        /// undo says so instead of passing over it.
        /// </summary>
        private static void Imported(string[] imported, string[] deleted)
        {
            Quietly(() =>
            {
                var mark = AtWork();
                if (mark == null) return;
                foreach (var path in imported)
                {
                    if (!InAssets(path) || Came(mark, path) || Has(mark, "replaced", path) || Has(mark, "changed", path)) continue;
                    // An asset's name is in its file: moving or renaming one writes the file anew, and moving it back sets that right.
                    if (Notes(mark).Any(n => n.kind == "moved" && Under(path, n.path))) continue;
                    var file = Absolute(path);
                    if (!File.Exists(file)) continue;  // a folder
                    // Imported again because something it depends on changed: the file itself is as it was.
                    if ((File.GetLastWriteTimeUtc(file) - new DateTime(1970, 1, 1, 0, 0, 0, DateTimeKind.Utc)).TotalSeconds < mark.stamp - 2) continue;
                    AddNote(mark, new FileNote { kind = "changed", path = path });
                }
                foreach (var path in deleted)
                {
                    if (!InAssets(path) || Came(mark, path) || Has(mark, "changed", path)) continue;
                    if (Notes(mark).Any(n => (n.kind == "deleted" && Under(path, n.path)) ||      // kept by WillDelete
                                             (n.kind == "moved" && Under(path, n.origin)))) continue;  // not gone: elsewhere
                    AddNote(mark, new FileNote { kind = "changed", path = path });
                }
            });
        }

        /// <summary>
        /// Copies a project file that is about to be written over, and its .meta, to Library/ClaudeHand/replaced
        /// (the newest few are kept; `group` puts the files of one transfer, or of one request, in a folder that
        /// counts as one) and returns where the file went.
        /// </summary>
        public static string Keep(string file, string group = null)
        {
            // While a request is at work, all it replaces lies in its one folder, with the guard hook's copies: a
            // request that replaces many files must not push its own earlier copies out of the newest few.
            var mark = AtWork();
            if (mark != null) group = Folder(mark);
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
        /// Keep for a model that follows its .blend, replaced because the user saved there: all such files lie in one
        /// folder, replaced/follow, which counts as one of the newest few (so saving often never pushes out what the
        /// requests' undos need), and of them only the newest few stay.
        /// </summary>
        internal static string KeepFollowed(string file)
        {
            var dir = Path.Combine(ChPaths.LibraryDir, "replaced", "follow");
            Directory.CreateDirectory(dir);
            Directory.SetCreationTimeUtc(dir, DateTime.UtcNow);  // in use now: not the oldest folder there, which Keep would drop
            var kept = Keep(file, "follow");
            Prune(dir, FollowKept);
            return kept;
        }

        /// <summary>
        /// Moves an asset (a file, or a folder with what is in it) and its .meta out of the project, into Library/
        /// ClaudeHand/removed, and returns where it went: an asset a request made, when the request is undone, or one a
        /// request deletes. `group` names the folder there: what one undo takes out, or one request deletes, lies
        /// together and counts as one (the newest few are kept). Putting the two back under Assets brings the asset
        /// back with its GUID. Not the system's trash: a Recycle Bin set to delete at once, or a drive that has none,
        /// would make "it is in the trash" untrue, and the user would learn it only when wanting the asset back.
        /// </summary>
        private static string TakeOut(string assetPath, string group)
        {
            var root = Path.Combine(ChPaths.LibraryDir, "removed");
            var name = Path.GetFileName(assetPath.TrimEnd('/'));
            var dir = Path.Combine(root, ChPaths.Sanitize(group));
            // A second asset of the same name in the group: a numbered folder of its own, so that each keeps its name.
            for (int n = 2; Exists(Path.Combine(dir, name)); n++) dir = Path.Combine(root, ChPaths.Sanitize(group), n.ToString());
            Directory.CreateDirectory(dir);
            var target = Path.Combine(dir, name);
            Move(Absolute(assetPath), target);
            Prune(root, RemovedKept);
            return ChPaths.ToPosix(target);
        }

        /// <summary>Moves a file or a folder, and the .meta next to it. All or nothing: a file in use leaves both where they were.</summary>
        private static void Move(string source, string target)
        {
            // Unity keeps the file of an asset it has read open for a while (a model's .controller, read anew by the
            // Undo just before when the model was imported again since): a folder with such a file cannot be moved.
            AssetDatabase.ReleaseCachedFileHandles();
            bool meta = File.Exists(source + ".meta");
            if (meta)
            {
                File.Delete(target + ".meta");  // a stray one; no error when there is none
                File.Move(source + ".meta", target + ".meta");
            }
            try
            {
                if (Directory.Exists(source)) Directory.Move(source, target);
                else File.Move(source, target);
            }
            catch (Exception)
            {
                if (meta) File.Move(target + ".meta", source + ".meta");
                throw;
            }
        }

        /// <summary>The two files hold the same bytes (two that are both missing count as the same).</summary>
        internal static bool Same(string one, string other)
        {
            var a = new FileInfo(one);
            var b = new FileInfo(other);
            if (!a.Exists || !b.Exists) return a.Exists == b.Exists;
            if (a.Length != b.Length) return false;
            using (var left = a.OpenRead())
            using (var right = b.OpenRead())
            {
                var x = new byte[81920];
                var y = new byte[81920];
                while (true)
                {
                    int read = left.Read(x, 0, x.Length);
                    if (read == 0) return true;
                    int got = 0;
                    while (got < read)
                    {
                        int more = right.Read(y, got, read - got);
                        if (more == 0) return false;
                        got += more;
                    }
                    for (int i = 0; i < read; i++)
                    {
                        if (x[i] != y[i]) return false;
                    }
                }
            }
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

        private sealed class KeptLine
        {
            public string path;
            public string kept;
            public double ts;
        }

        /// <summary>
        /// The files the guard hook copied for the mark's request before the calls that named them (tools/handlib/
        /// keeps.py writes a line for each into kept.jsonl in the request's folder), as "replaced" notes.
        /// </summary>
        private static List<FileNote> GuardKept(Mark mark)
        {
            var notes = new List<FileNote>();
            if (string.IsNullOrEmpty(mark.request)) return notes;
            var journal = Path.Combine(ChPaths.LibraryDir, "replaced", ChPaths.Sanitize(Folder(mark)), "kept.jsonl");
            if (!File.Exists(journal)) return notes;
            foreach (var line in File.ReadAllLines(journal))
            {
                try
                {
                    var entry = JsonConvert.DeserializeObject<KeptLine>(line);
                    if (entry == null || !InAssets(entry.path) || string.IsNullOrEmpty(entry.kept)) continue;
                    notes.Add(new FileNote { kind = "replaced", path = entry.path, kept = entry.kept, ts = entry.ts, guard = true });
                }
                catch (JsonException)
                {
                    // a line cut short: the others still count
                }
            }
            return notes;
        }

        /// <summary>
        /// Everything the mark's request did to files, in the order it happened: this package's notes and the guard
        /// hook's copies. A copy the hook made comes before what its call then did, also when the clock gives both
        /// the same time; notes without a time (from before notes had one) come first, as they were written.
        /// </summary>
        private static List<FileNote> Timeline(Mark mark)
        {
            return Notes(mark).Concat(GuardKept(mark)).OrderBy(n => n.ts).ThenBy(n => n.guard ? 0 : 1).ToList();
        }

        /// <summary>The copy at `index` is of a file the request itself had made by then: not something to go back to.</summary>
        private static bool OfOwnMaking(List<FileNote> timeline, int index)
        {
            for (int i = 0; i < index; i++)
            {
                if (timeline[i].kind == "added" && Under(timeline[index].path, timeline[i].path)) return true;
            }
            return false;
        }

        /// <summary>Files that changed, or went, past Unity while the request was at work, and that nobody has a copy of.</summary>
        private static List<string> Unkept(List<FileNote> timeline)
        {
            return timeline.Where(n => n.kind == "changed" && !timeline.Any(c => c.kind == "replaced" && c.path == n.path))
                .Select(n => n.path).Distinct().ToList();
        }

        /// <summary>
        /// Takes back what the mark's request did to files, newest first: what it moved goes back, what it deleted
        /// returns, what it changed is as before (going back through every copy ends at the oldest: the file before
        /// the request), what it made is taken out of the project.
        /// </summary>
        private static FileReport RevertFiles(Mark mark)
        {
            var report = new FileReport();
            var timeline = Timeline(mark);
            var group = $"undo_{DateTime.Now:yyyyMMdd_HHmmss}";
            var restored = new List<string>();
            bool moved = false;
            for (int i = timeline.Count - 1; i >= 0; i--)
            {
                var note = timeline[i];
                try
                {
                    if (note.kind == "replaced")
                    {
                        if (!OfOwnMaking(timeline, i) && Restore(note, timeline, report)) restored.Add(note.path);
                    }
                    else if (note.kind == "added") moved |= Remove(note, group, report);
                    else if (note.kind == "deleted") moved |= PutBack(note, report);
                    else if (note.kind == "moved") moved |= MoveBack(note, report);
                }
                catch (Exception ex)
                {
                    report.problems.Add($"{note.path}: {ex.Message}");
                }
            }
            report.unkept = Unkept(timeline);
            if (moved) AssetDatabase.Refresh();  // files went and came from under the asset database
            foreach (var path in restored.Distinct())
            {
                // Where it still is after the moves above; one that moved back, the refresh has read at its place.
                if (File.Exists(Absolute(path))) AssetDatabase.ImportAsset(path, ImportAssetOptions.ForceSynchronousImport | ImportAssetOptions.ForceUpdate);
            }
            return report;
        }

        /// <summary>
        /// Puts a kept file and its .meta (same GUID; the import settings of before) back where it was copied from,
        /// unless what is there is the same anyway. True when it wrote.
        /// </summary>
        private static bool Restore(FileNote note, List<FileNote> timeline, FileReport report)
        {
            if (string.IsNullOrEmpty(note.kept) || !File.Exists(note.kept))
            {
                report.problems.Add($"{note.path}: the file it replaced is no longer kept");
                return false;
            }
            var target = Absolute(note.path);
            if (!File.Exists(target))
            {
                // Gone since. Put back when the request itself made it go past Unity (noted as changed); one the user
                // deleted by hand afterwards stays deleted.
                if (!timeline.Any(n => n.kind == "changed" && n.path == note.path)) return false;
                Directory.CreateDirectory(Path.GetDirectoryName(target));
            }
            bool meta = File.Exists(note.kept + ".meta");
            if (Same(note.kept, target) && (!meta || Same(note.kept + ".meta", target + ".meta"))) return false;
            File.Copy(note.kept, target, true);
            File.SetLastWriteTimeUtc(target, DateTime.UtcNow);  // a copy has the date of what it was copied from: the editor and an IDE look for a newer one
            if (meta)
            {
                File.Copy(note.kept + ".meta", target + ".meta", true);
                File.SetLastWriteTimeUtc(target + ".meta", DateTime.UtcNow);
            }
            if (!report.restored.Contains(note.path)) report.restored.Add(note.path);
            return true;
        }

        private static bool Remove(FileNote note, string group, FileReport report)
        {
            var path = string.IsNullOrEmpty(note.guid) ? note.path : AssetDatabase.GUIDToAssetPath(note.guid);
            if (!InAssets(path) || !Exists(Absolute(path))) return false;  // already gone
            if (IsOpenScene(path))
            {
                report.problems.Add($"{path}: a scene that is open stays where it is");
                return false;
            }
            TakeOut(path, group);
            report.removed.Add(path);
            return true;
        }

        private static bool PutBack(FileNote note, FileReport report)
        {
            if (string.IsNullOrEmpty(note.kept) || !Exists(note.kept))
            {
                report.problems.Add($"{note.path}: what was deleted is no longer kept");
                return false;
            }
            var target = Absolute(note.path);
            if (Exists(target))
            {
                report.problems.Add($"{note.path}: something else is there now; what was deleted is kept at {note.kept}");
                return false;
            }
            Directory.CreateDirectory(Path.GetDirectoryName(target));
            Move(note.kept, target);
            report.returned.Add(note.path);
            return true;
        }

        private static bool MoveBack(FileNote note, FileReport report)
        {
            var now = Absolute(note.path);
            var before = Absolute(note.origin);
            if (!Exists(now)) return false;  // moved on or gone since: a later note has it, or nothing does
            if (Exists(before))
            {
                report.problems.Add($"{note.path}: cannot go back to {note.origin}, something else is there now");
                return false;
            }
            // Through Unity when it can: an asset's name is written in its file too, and Unity sets that right. It cannot
            // when the place to go back to is a folder this same undo has just put back, which Unity has not read yet.
            if (string.IsNullOrEmpty(AssetDatabase.ValidateMoveAsset(note.path, note.origin)) &&
                string.IsNullOrEmpty(AssetDatabase.MoveAsset(note.path, note.origin)))
            {
                report.moved.Add(note.origin);
                return true;
            }
            Directory.CreateDirectory(Path.GetDirectoryName(before));
            Move(now, before);
            report.moved.Add(note.origin);
            return true;
        }

        /// <summary>What undoing the mark would do to files, counted without touching any (the moves it would make are not played through).</summary>
        public static FilePlan Plan(Mark mark)
        {
            var plan = new FilePlan();
            var timeline = Timeline(mark);
            var oldest = new Dictionary<string, FileNote>();
            for (int i = 0; i < timeline.Count; i++)
            {
                var note = timeline[i];
                if (note.kind == "added" && InAssets(note.path) && Exists(Absolute(note.path)) && !IsOpenScene(note.path)) plan.remove++;
                else if (note.kind == "deleted" && !string.IsNullOrEmpty(note.kept) && Exists(note.kept)) plan.putBack++;
                else if (note.kind == "moved" && Exists(Absolute(note.path))) plan.moveBack++;
                else if (note.kind == "replaced" && !oldest.ContainsKey(note.path) && !OfOwnMaking(timeline, i)) oldest[note.path] = note;
            }
            foreach (var note in oldest.Values)
            {
                if (string.IsNullOrEmpty(note.kept) || !File.Exists(note.kept)) continue;
                var target = Absolute(note.path);
                bool gone = !File.Exists(target);
                if (gone ? timeline.Any(n => n.kind == "changed" && n.path == note.path)
                         : !Same(note.kept, target) || (File.Exists(note.kept + ".meta") && !Same(note.kept + ".meta", target + ".meta")))
                {
                    plan.restore++;
                }
            }
            plan.unkept = Unkept(timeline);
            return plan;
        }

        /// <summary>What an undo did, for the subagent or the chat channel that asked for it.</summary>
        private static object Undone(Mark undone)
        {
            var files = undone.report;
            var message = $"Reverted everything since '{undone.label}' ({undone.time})";
            if (files.restored.Count > 0) message += $"; {files.restored.Count} file(s) are back as they were before";
            if (files.removed.Count > 0) message += $"; {files.removed.Count} asset(s) the request made or brought in were taken out of the project (kept in {RemovedFolder})";
            if (files.returned.Count > 0) message += $"; {files.returned.Count} asset(s) it deleted are back";
            if (files.moved.Count > 0) message += $"; {files.moved.Count} asset(s) it moved are back where they were";
            if (files.unkept.Count > 0) message += $"; {files.unkept.Count} file(s) changed with no copy of what was there before, and stay as they are (see unkept)";
            if (files.problems.Count > 0) message += $"; {files.problems.Count} file(s) could not be put back (see problems)";
            return new ChSuccess(message, new
            {
                state = "undone", undone.label, undone.time, files.restored, files.removed, files.returned, files.moved, files.unkept, files.problems,
                summary = files.Summary(),  // the same sentence the chat window shows, for the channel to pass on
            });
        }

        public static object HandleCommand(JObject @params)
        {
            var p = @params?.ToObject<Parameters>() ?? new Parameters();
            switch ((p.action ?? "mark").Trim().ToLowerInvariant())
            {
                case "mark":
                    // Also while scripts compile: a request that follows one that changed a script starts then, and
                    // with no mark its changes would be undone only together with that earlier request's.
                    var mark = Add(p.label, p.request);
                    return new ChSuccess($"Undo point {mark.group} set", new { mark.group, mark.label, mark.time });
                case "undo":
                    var undone = UndoLast();
                    if (undone == null) return ChErrors.Make(ChErrors.NotFound, "There is no undo point to go back to.", "after_fix");
                    return Undone(undone);
                case "undo_request":
                    // One request, in the window it is asked of, and only in order: a point of a later request on top
                    // would go back with it, so then nothing is undone and the answer says what is in the way.
                    var name = (p.request ?? string.Empty).Trim();
                    if (name.Length == 0) return ChErrors.Make(ChErrors.InvalidArgs, "undo_request needs the request's name.", "after_fix");
                    var points = Undoable;
                    if (!points.Any(m => m.request == name))
                    {
                        return new ChSuccess("Nothing of that request is left to undo in this project.", new { state = "nothing" });
                    }
                    var top = points[points.Count - 1];
                    if (top.request != name)
                    {
                        return new ChSuccess($"Not undone: '{top.label}' ({top.time}) changed this project after that request and comes first.",
                            new { state = "blocked", top = $"{top.label} ({top.time})" });
                    }
                    return Undone(UndoLast());
                case "list":
                    var shown = Undoable;
                    int skipped = Marks.Count - shown.Count;
                    return new ChSuccess($"{shown.Count} undo point(s)" + (skipped > 0 ? $" ({skipped} more that nothing happened after: skipped)" : ""),
                        new { marks = shown });
                default:
                    return ChErrors.Make(ChErrors.InvalidArgs, "action must be mark, undo, undo_request or list.", "after_fix");
            }
        }
    }

    /// <summary>Assets that came in, went or moved are a change as well (ChCheckpoint.Touched), and may be a request's doing.</summary>
    internal sealed class ChCheckpointAssets : AssetPostprocessor
    {
        private static void OnPostprocessAllAssets(string[] imported, string[] deleted, string[] moved, string[] movedFrom)
        {
            ChCheckpoint.AssetsChanged(imported, deleted, moved);
        }
    }

    /// <summary>
    /// What Unity says before it touches asset files: ChCheckpoint notes it for the request at work, and takes over a
    /// deletion (the asset is kept instead). Before that, ChProtect refuses what the user's protection rules do not
    /// allow. With nobody at work every one of these does nothing.
    /// </summary>
    internal sealed class ChCheckpointFiles : UnityEditor.AssetModificationProcessor
    {
        private static void OnWillCreateAsset(string assetName)
        {
            ChProtect.WillCreate(assetName);
            ChCheckpoint.WillCreate(assetName);
        }

        private static string[] OnWillSaveAssets(string[] paths)
        {
            paths = ChProtect.FilterSaves(paths);
            ChCheckpoint.WillSave(paths);
            return paths;
        }

        private static AssetDeleteResult OnWillDeleteAsset(string assetPath, RemoveAssetOptions options)
        {
            if (ChProtect.RefusesDelete(assetPath)) return AssetDeleteResult.FailedDelete;
            return ChCheckpoint.WillDelete(assetPath) ? AssetDeleteResult.DidDelete : AssetDeleteResult.DidNotDelete;
        }

        private static AssetMoveResult OnWillMoveAsset(string sourcePath, string destinationPath)
        {
            if (ChProtect.RefusesMove(sourcePath, destinationPath)) return AssetMoveResult.FailedMove;
            ChCheckpoint.WillMove(sourcePath, destinationPath);
            return AssetMoveResult.DidNotMove;
        }
    }
}
