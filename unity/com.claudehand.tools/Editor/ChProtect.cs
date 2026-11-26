using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;
using UnityEngine.SceneManagement;

using static ClaudeHand.Editor.ChText;

namespace ClaudeHand.Editor
{
    /// <summary>
    /// Protection rules: what the user has said must not be changed in this project. A note (ClaudeHand/NOTES.md) is
    /// something Claude tries to keep; these are checked. The subagent's guard hook refuses what a call's own words
    /// show (tools/handlib/protect.py). The rest is caught here, while a request is at work:
    ///   - Unity asks before it saves, deletes or moves an asset: a protected one is refused there and then, and so
    ///     are the actions the user forbade (saving a scene, deleting an asset, play mode).
    ///   - What cannot be refused is found out afterwards and taken back: the hook asks before a call that may change
    ///     things (before: the protected scene objects are noted as they are) and after it (after: compared again;
    ///     a protected asset that was changed in memory, written past Unity, or made anew counts too). When something
    ///     protected differs, what the request did in this project is undone with its undo point (ChCheckpoint).
    /// The rules are the user's: only the chat window's buttons write them (and the [추가] under a rule Claude
    /// proposed). Nothing a tool can be called with does.
    ///   ClaudeHand/protect.json in the project           lasting, goes with the project
    ///   ~/.claude-hand/protect/unity-&lt;hash&gt;.json     this run of Claude Hand only ("이번만")
    /// The same file means the same to the guard and to the Blender add-on; tools/tests/protect_cases.json holds
    /// all three to it.
    /// </summary>
    [ChTool("ch_protect",
        Description = "This project's protection rules (what the user said must not be changed here). state: the rules. " +
                      "before and after are Claude Hand's own (its guard hook calls them around your calls): do not call them. " +
                      "Nothing here changes a rule: only the user does, in the chat window.")]
    public static class ChProtect
    {
        public static readonly string[] Modes = { "open", "look", "locked" };  // weakest first: of two files, the stricter holds
        public static readonly Dictionary<string, string> ModeWords = new Dictionary<string, string>
            { { "open", N("열림") }, { "look", N("보기만") }, { "locked", N("잠금") } };  // said through T: T(ModeWords[...])
        public static readonly Dictionary<string, string> ActionWords = new Dictionary<string, string>
            { { "save_scene", N("씬 저장") }, { "delete_asset", N("에셋 삭제") }, { "play_mode", N("플레이 모드") } };
        private const string PendingKey = "ClaudeHand.Protect.Pending";
        private const string SinceKey = "ClaudeHand.Protect.Since";
        private const int CoveredTold = 3000;    // names told to the guard at most
        private const int DeepComponents = 3000;  // components whose values are compared; past that, only what an object is made of

        public sealed class Row
        {
            public string kind;   // "window", "path", "object", "action"
            public string value;
            public bool lasting;  // in the project's file; false: this run's
        }

        public sealed class Rules
        {
            public string window = "open";
            public List<string> paths = new List<string>();
            public List<string> objects = new List<string>();
            public List<string> actions = new List<string>();
            public List<string> problems = new List<string>();  // a file that was there and could not be read as rules
            [JsonIgnore] public List<Row> rows = new List<Row>();

            [JsonIgnore] public bool Watched => paths.Count > 0 || objects.Count > 0 || actions.Count > 0;
        }

        public class Parameters
        {
            [ChParameter("state (default): the rules of this project. before, after: Claude Hand's guard hook only", Required = false)]
            public string action { get; set; }
        }

        /// <summary>Raised on the main thread when the rules change (the chat window repaints).</summary>
        public static event Action Changed;

        private static double Now => (DateTime.UtcNow - new DateTime(1970, 1, 1, 0, 0, 0, DateTimeKind.Utc)).TotalSeconds;

        // ------------------------------------------------------------------ the rules

        public static string LastingFile => Path.Combine(ChPaths.ProjectRoot, "ClaudeHand", "protect.json");

        public static string SessionFile => Path.Combine(ChPaths.ClaudeHandHome, "protect", "unity-" + ChBridge.ProjectHash() + ".json");

        private static Rules _rules;
        private static string _rulesStamp;

        private static string Stamp(string path)
        {
            try
            {
                var info = new FileInfo(path);
                return info.Exists ? info.LastWriteTimeUtc.Ticks + ":" + info.Length : "-";
            }
            catch (Exception)
            {
                return "?";
            }
        }

        /// <summary>The lasting rules and this run's as one. Read anew only when a file changed: the window draws often.</summary>
        public static Rules Current
        {
            get
            {
                var stamp = Stamp(LastingFile) + "|" + Stamp(SessionFile);
                if (_rules != null && stamp == _rulesStamp) return _rules;
                var rules = new Rules();
                ReadInto(rules, LastingFile, true);
                ReadInto(rules, SessionFile, false);
                _rules = rules;
                _rulesStamp = stamp;
                return rules;
            }
        }

        private static void ReadInto(Rules rules, string path, bool lasting)
        {
            if (!File.Exists(path)) return;
            JObject data;
            try
            {
                data = JObject.Parse(File.ReadAllText(path));
            }
            catch (Exception ex)
            {
                rules.problems.Add($"{ChPaths.ToPosix(path)}: {ex.Message}");  // a rule that was meant and cannot be read must not go unnoticed
                return;
            }
            var window = (data.Value<string>("window") ?? "open").Trim().ToLowerInvariant();
            if (Array.IndexOf(Modes, window) > 0)
            {
                rules.rows.Add(new Row { kind = "window", value = window, lasting = lasting });
                if (Array.IndexOf(Modes, window) > Array.IndexOf(Modes, rules.window)) rules.window = window;
            }
            ReadRows(data, "paths", "path", rules.paths, rules.rows, lasting, null);
            ReadRows(data, "objects", "object", rules.objects, rules.rows, lasting, null);
            ReadRows(data, "actions", "action", rules.actions, rules.rows, lasting, ActionWords);
        }

        private static void ReadRows(JObject data, string key, string kind, List<string> into, List<Row> rows, bool lasting, Dictionary<string, string> known)
        {
            if (!(data[key] is JArray array)) return;
            foreach (var token in array)
            {
                if (token.Type != JTokenType.String) continue;
                var value = token.ToString().Trim();
                if (value.Length == 0 || (known != null && !known.ContainsKey(value))) continue;
                rows.Add(new Row { kind = kind, value = value, lasting = lasting });
                if (!into.Contains(value)) into.Add(value);
            }
        }

        public static string Label(Row row)
        {
            var what = row.kind == "window" ? T("이 창: {0}", ModeWords.TryGetValue(row.value, out var mode) ? T(mode) : row.value)
                : row.kind == "action" ? T("금지: {0}", ActionWords.TryGetValue(row.value, out var action) ? T(action) : row.value)
                : row.kind == "path" ? T("경로 {0}", row.value)
                : T("오브젝트 {0}", row.value);
            return what + (row.lasting ? "" : T(" (이번만)"));
        }

        // ------------------------------------------------------------------ the user changes them (the chat window only)

        private static string Write(bool lasting, Action<JObject> change)
        {
            var path = lasting ? LastingFile : SessionFile;
            try
            {
                var data = File.Exists(path) ? JObject.Parse(File.ReadAllText(path)) : new JObject();
                if (data["version"] == null) data["version"] = 1;
                change(data);
                Directory.CreateDirectory(Path.GetDirectoryName(path));
                var tmp = path + ".tmp";
                File.WriteAllText(tmp, data.ToString(Formatting.Indented) + "\n");
                if (File.Exists(path)) File.Delete(path);
                File.Move(tmp, path);
            }
            catch (Exception ex)
            {
                return T("규칙을 적지 못했습니다: ") + ex.Message;
            }
            _rules = null;
            Changed?.Invoke();
            return null;
        }

        /// <summary>
        /// The user adds a rule: kind "window" (value "look" or "locked"), "path", "object" or "action". Returns what
        /// stands in the way, or null.
        /// </summary>
        internal static string Add(string kind, string value, bool lasting)
        {
            value = (value ?? string.Empty).Trim();
            if (kind == "window")
            {
                if (Array.IndexOf(Modes, value) <= 0) return T("'{0}'은(는) 창 규칙이 아닙니다.", value);
                return Write(lasting, data => data["window"] = value);
            }
            if (kind == "action" && !ActionWords.ContainsKey(value)) return T("'{0}'은(는) Unity에서 막을 수 있는 동작이 아닙니다.", value);
            if ((kind != "path" && kind != "object" && kind != "action") || value.Length == 0) return T("더할 규칙이 없습니다.");
            return Write(lasting, data =>
            {
                var rows = data[kind + "s"] as JArray ?? new JArray();
                if (!rows.Any(t => t.Type == JTokenType.String && t.ToString() == value)) rows.Add(value);
                data[kind + "s"] = rows;
            });
        }

        /// <summary>The user takes a rule away again ([풀기]): only this, never anything a tool can be called with.</summary>
        internal static string Remove(string kind, string value, bool lasting)
        {
            if (kind == "window") return Write(lasting, data => data["window"] = "open");
            return Write(lasting, data =>
            {
                var rows = data[kind + "s"] as JArray ?? new JArray();
                data[kind + "s"] = new JArray(rows.Where(t => !(t.Type == JTokenType.String && t.ToString() == value)));
            });
        }

        // ------------------------------------------------------------------ what a rule covers

        private static readonly Dictionary<string, Regex> Globs = new Dictionary<string, Regex>();

        private static Regex Glob(string pattern)
        {
            if (Globs.TryGetValue(pattern, out var found)) return found;
            var text = new StringBuilder(@"\A");
            foreach (var part in Regex.Split(pattern, @"(\*\*|\*|\?)"))
            {
                text.Append(part == "**" ? ".*" : part == "*" ? "[^/]*" : part == "?" ? "[^/]" : Regex.Escape(part));
            }
            return Globs[pattern] = new Regex(text.Append(@"\z").ToString(), RegexOptions.CultureInvariant);
        }

        private static string Clean(string path)
        {
            path = (path ?? string.Empty).Replace('\\', '/').Trim().Trim('/');
            if (path.StartsWith("./", StringComparison.Ordinal)) path = path.Substring(2);
            if (path.EndsWith(".meta", StringComparison.OrdinalIgnoreCase)) path = path.Substring(0, path.Length - 5);  // a file's .meta is the file's
            return path.ToLowerInvariant();
        }

        /// <summary>
        /// The rule that covers this asset path (project relative), or null. No case; * stays within a name, ** goes
        /// any depth; a folder rule covers the folder itself, and a rule with no wildcard everything below it.
        /// </summary>
        public static string PathRule(IEnumerable<string> rules, string assetPath)
        {
            var path = Clean(assetPath);
            foreach (var rule in rules)
            {
                var pattern = Clean(rule);
                if (pattern.Length == 0) continue;
                if (pattern.IndexOfAny(Wild) < 0)
                {
                    if (path == pattern || path.StartsWith(pattern + "/", StringComparison.Ordinal)) return rule;
                    continue;
                }
                var folder = pattern.EndsWith("/**", StringComparison.Ordinal) ? pattern.Substring(0, pattern.Length - 3) : string.Empty;
                if (Glob(pattern).IsMatch(path) || (folder.Length > 0 && folder.IndexOfAny(Wild) < 0 && path == folder)) return rule;
            }
            return null;
        }

        private static readonly char[] Wild = { '*', '?' };

        /// <summary>A rule about something inside this folder: deleting or moving the folder would take that along.</summary>
        private static string RuleBelow(IEnumerable<string> rules, string folder)
        {
            var above = Clean(folder) + "/";
            foreach (var rule in rules)
            {
                var pattern = Clean(rule);
                int wild = pattern.IndexOfAny(Wild);
                if ((wild < 0 ? pattern : pattern.Substring(0, wild)).StartsWith(above, StringComparison.Ordinal)) return rule;
            }
            return null;
        }

        /// <summary>
        /// The rule that covers the object at this hierarchy path ("Environment/Trees/Oak"), or null: a plain name
        /// covers that object wherever it is and all below it, "A/**" covers A and all below it, "A/B" that one object.
        /// </summary>
        public static string ObjectRule(IEnumerable<string> rules, string hierarchyPath)
        {
            var parts = (hierarchyPath ?? string.Empty).Replace('\\', '/').Split(new[] { '/' }, StringSplitOptions.RemoveEmptyEntries);
            foreach (var rule in rules)
            {
                if (rule.StartsWith("collection:", StringComparison.OrdinalIgnoreCase) || rule.Trim().Length == 0) continue;  // Blender's
                var wanted = rule.Replace('\\', '/').Split(new[] { '/' }, StringSplitOptions.RemoveEmptyEntries);
                if (wanted.Length == 0) continue;
                if (wanted.Length == 1)
                {
                    if (parts.Any(part => Glob(wanted[0]).IsMatch(part))) return rule;
                    continue;
                }
                bool below = wanted[wanted.Length - 1] == "**";
                int head = below ? wanted.Length - 1 : wanted.Length;
                for (int start = 0; start <= parts.Length - head; start++)
                {
                    bool all = true;
                    for (int i = 0; i < head && all; i++) all = Glob(wanted[i]).IsMatch(parts[start + i]);
                    if (all && (below || start + head == parts.Length)) return rule;
                }
            }
            return null;
        }

        // ------------------------------------------------------------------ what Unity is about to do (refused here)

        /// <summary>A request is at work, and what happens is neither an undo nor this package's own passing business.</summary>
        private static bool Active => ChCheckpoint.SomebodyAtWork && !ChCheckpoint.Reverting && !ChCheckpoint.Unnoting;

        private sealed class Found
        {
            public List<string> blocked = new List<string>();  // refused there and then
            public List<string> changed = new List<string>();  // found afterwards: to be taken back
        }

        private static Found _found;

        // Kept in SessionState: an asset written past Unity is found when Unity reads it, which may be a script reload away.
        private static Found Pending => _found ??= JsonConvert.DeserializeObject<Found>(SessionState.GetString(PendingKey, "{}")) ?? new Found();

        private static void Note(bool blocked, string what)
        {
            var list = blocked ? Pending.blocked : Pending.changed;
            if (list.Contains(what) || list.Count >= 50) return;
            list.Add(what);
            SessionState.SetString(PendingKey, JsonConvert.SerializeObject(Pending));
        }

        private static Found TakePending()
        {
            var found = Pending;
            _found = new Found();
            SessionState.EraseString(PendingKey);
            return found;
        }

        private static bool Forbidden(string action) => Current.actions.Contains(action);

        /// <summary>Unity is about to write these files: the ones to go ahead with. A protected asset is left out, and with "씬 저장" forbidden every scene.</summary>
        internal static string[] FilterSaves(string[] paths)
        {
            if (paths == null || paths.Length == 0 || !Active) return paths;
            var rules = Current;
            if (rules.paths.Count == 0 && !rules.actions.Contains("save_scene")) return paths;
            var kept = new List<string>(paths.Length);
            foreach (var path in paths)
            {
                if (rules.actions.Contains("save_scene") && path.EndsWith(".unity", StringComparison.OrdinalIgnoreCase)) Note(true, T("씬 저장"));
                else if (PathRule(rules.paths, path) != null) Note(true, T("{0} (저장)", path));
                else kept.Add(path);
            }
            return kept.Count == paths.Length ? paths : kept.ToArray();
        }

        /// <summary>Unity is about to delete this asset: true when the rules do not let it.</summary>
        internal static bool RefusesDelete(string path)
        {
            if (!Active) return false;
            var rules = Current;
            if (rules.actions.Contains("delete_asset"))
            {
                Note(true, T("에셋 삭제 ({0})", path));
                return true;
            }
            if (PathRule(rules.paths, path) == null && RuleBelow(rules.paths, path) == null) return false;
            Note(true, T("{0} (지우기)", path));
            return true;
        }

        /// <summary>Unity is about to move or rename this asset: true when the rules do not let it (out of, or into, what is protected).</summary>
        internal static bool RefusesMove(string from, string to)
        {
            if (!Active) return false;
            var rules = Current;
            if (rules.paths.Count == 0) return false;
            if (PathRule(rules.paths, from) == null && RuleBelow(rules.paths, from) == null && PathRule(rules.paths, to) == null) return false;
            Note(true, T("{0} (옮기기 · 이름 바꾸기)", from));
            return true;
        }

        /// <summary>Unity is about to make an asset. That cannot be refused: one made where the rules protect is taken back afterwards.</summary>
        internal static void WillCreate(string name)
        {
            if (!Active || !name.StartsWith("Assets/", StringComparison.Ordinal)) return;
            var path = name.EndsWith(".meta", StringComparison.OrdinalIgnoreCase) ? name.Substring(0, name.Length - 5) : name;
            if (File.Exists(Path.Combine(ChPaths.ProjectRoot, path)) || Directory.Exists(Path.Combine(ChPaths.ProjectRoot, path))) return;  // saved again, not made
            if (PathRule(Current.paths, path) != null) Note(false, T("{0} (새로 만듦)", path));
        }

        /// <summary>
        /// Unity has read these files anew, or lost them. A protected one that changed on disk while the request was
        /// at work was written past Unity (a save through Unity is refused before it happens); one that went without
        /// Unity deleting it was deleted past it.
        /// </summary>
        internal static void AssetsChanged(string[] imported, string[] deleted)
        {
            if (!Active) return;
            var rules = Current;
            if (rules.paths.Count == 0) return;
            double since = Since;
            foreach (var path in imported)
            {
                if (PathRule(rules.paths, path) == null) continue;
                var file = Path.Combine(ChPaths.ProjectRoot, path);
                if (!File.Exists(file)) continue;  // a folder
                // Read again because something it depends on changed: the file itself is as it was.
                if ((File.GetLastWriteTimeUtc(file) - new DateTime(1970, 1, 1, 0, 0, 0, DateTimeKind.Utc)).TotalSeconds < since - 2) continue;
                if (Pending.changed.Contains(T("{0} (새로 만듦)", path))) continue;
                Note(false, T("{0} (파일이 바뀜)", path));
            }
            foreach (var path in deleted)
            {
                if (PathRule(rules.paths, path) != null) Note(false, T("{0} (지워짐)", path));
            }
        }

        private static double Since
        {
            get => double.TryParse(SessionState.GetString(SinceKey, "0"), System.Globalization.NumberStyles.Float,
                                   System.Globalization.CultureInfo.InvariantCulture, out var since) ? since : 0;
            set => SessionState.SetString(SinceKey, value.ToString("R", System.Globalization.CultureInfo.InvariantCulture));
        }

        [InitializeOnLoadMethod]
        private static void Watch()
        {
            // Entering play mode cannot be refused beforehand either, but it can be called off on its way in.
            EditorApplication.playModeStateChanged += state =>
            {
                if (state != PlayModeStateChange.ExitingEditMode || !Active || !Forbidden("play_mode")) return;
                EditorApplication.isPlaying = false;
                Note(true, T("플레이 모드"));
            };
            // A protected asset changed in memory (a material's colour through a SerializedObject): saving it would be
            // refused, but it would stay changed until the next save went through. Found here, taken back in After.
            ObjectChangeEvents.changesPublished += (ref ObjectChangeEventStream stream) =>
            {
                if (!Active) return;
                var rules = Current;
                if (rules.paths.Count == 0) return;
                for (int i = 0; i < stream.length; i++)
                {
                    GUID guid;
                    string how;
                    switch (stream.GetEventType(i))
                    {
                        case ObjectChangeKind.ChangeAssetObjectProperties:
                            stream.GetChangeAssetObjectPropertiesEvent(i, out var changed);
                            guid = changed.guid;
                            how = T("속성이 바뀜");
                            break;
                        case ObjectChangeKind.DestroyAssetObject:
                            stream.GetDestroyAssetObjectEvent(i, out var destroyed);
                            guid = destroyed.guid;
                            how = T("안의 것이 지워짐");
                            break;
                        default:
                            continue;
                    }
                    var path = AssetDatabase.GUIDToAssetPath(guid);
                    if (!string.IsNullOrEmpty(path) && PathRule(rules.paths, path) != null) Note(false, $"{path} ({how})");
                }
            };
        }

        // ------------------------------------------------------------------ the protected scene objects, before and after a call

        private sealed class Seen
        {
            public GameObject go;
            public string path;
            public string[] print;
        }

        private static List<Seen> _base;
        // What of an object is no longer as it was, as it is said after its name ("Hero (자리)"). Two lists, not T():
        // "켜짐" is here whether the object is active, and elsewhere the "On" of a switch.
        private static readonly string[] PrintWords = { "이름", "켜짐", "자리", "자식", "컴포넌트", "값" };
        private static readonly string[] PrintWordsEnglish = { "name", "active", "position", "children", "components", "values" };

        private static string HashOf(string text)
        {
            using (var md5 = MD5.Create())
            {
                return string.Concat(md5.ComputeHash(Encoding.UTF8.GetBytes(text)).Take(8).Select(b => b.ToString("x2")));
            }
        }

        /// <summary>What an object is like now, in the parts PrintWords names. deep: with the values of its components.</summary>
        private static string[] Print(GameObject go, string parent, bool deep)
        {
            var t = go.transform;
            var components = go.GetComponents<Component>();
            var values = new StringBuilder();
            if (deep)
            {
                foreach (var component in components)
                {
                    if (component == null || component is Transform) continue;
                    values.Append(EditorJsonUtility.ToJson(component)).Append('\n');
                }
            }
            return new[]
            {
                go.name,
                $"{go.activeSelf}|{go.tag}|{go.layer}|{go.isStatic}",
                $"{parent}|{t.GetSiblingIndex()}|{t.localPosition.ToString("F5")}|{t.localRotation.ToString("F5")}|{t.localScale.ToString("F5")}",
                string.Join("|", Enumerable.Range(0, t.childCount).Select(i => t.GetChild(i).name)),
                string.Join("|", components.Select(c => c == null ? "(missing)" : c.GetType().FullName)),
                deep ? HashOf(values.ToString()) : string.Empty,
            };
        }

        /// <summary>Every object of the loaded scenes the rules cover, each with the rule, from the top down.</summary>
        private static void Covered(List<string> rules, Action<GameObject, string, string, string> each)
        {
            for (int s = 0; s < SceneManager.sceneCount; s++)
            {
                var scene = SceneManager.GetSceneAt(s);
                if (!scene.isLoaded) continue;
                foreach (var root in scene.GetRootGameObjects()) Walk(root.transform, string.Empty, null, rules, each);
            }
        }

        private static void Walk(Transform t, string parent, string inherited, List<string> rules, Action<GameObject, string, string, string> each)
        {
            var path = parent.Length == 0 ? t.name : parent + "/" + t.name;
            // A rule that covered an object above covers all below it, except one that names a single object ("A/B").
            var rule = inherited ?? ObjectRule(rules, path);
            if (rule != null) each(t.gameObject, path, parent, rule);
            var passed = rule != null && (rule.IndexOf('/') < 0 || rule.Replace('\\', '/').TrimEnd('/').EndsWith("/**", StringComparison.Ordinal)) ? rule : null;
            for (int i = 0; i < t.childCount; i++) Walk(t.GetChild(i), path, passed, rules, each);
        }

        private static List<string> Compare(List<Seen> before)
        {
            var changed = new List<string>();
            foreach (var seen in before)
            {
                if (seen.go == null)
                {
                    changed.Add(T("{0} (지워짐)", seen.path));
                    continue;
                }
                var parent = seen.go.transform.parent != null ? ChRefs.HierarchyPath(seen.go.transform.parent) : string.Empty;
                var now = Print(seen.go, parent, seen.print[5].Length > 0);
                var said = ChText.Language == "en" ? PrintWordsEnglish : PrintWords;
                var words = Enumerable.Range(0, now.Length).Where(i => now[i] != seen.print[i]).Select(i => said[i]).ToList();
                if (words.Count > 0) changed.Add($"{seen.path} ({string.Join(", ", words)})");
            }
            return changed;
        }

        // ------------------------------------------------------------------ the tool (the guard hook's)

        private static object Before()
        {
            // Nobody asked after the last call (the hook that does was not run), or Unity found something since: looked
            // at now, before the next call goes ahead. What is found is told with this answer, and that call is refused.
            var late = _base != null || Pending.blocked.Count + Pending.changed.Count > 0 ? Look(false) : null;
            var rules = Current;
            var covered = new Dictionary<string, string>();
            _base = null;
            if (rules.objects.Count > 0)
            {
                var seen = new List<Seen>();
                int components = 0;
                Covered(rules.objects, (go, path, parent, rule) =>
                {
                    components += go.GetComponents<Component>().Length;
                    seen.Add(new Seen { go = go, path = path, print = Print(go, parent, components <= DeepComponents) });
                    if (covered.Count < CoveredTold && !covered.ContainsKey(go.name)) covered[go.name] = rule;
                });
                _base = seen;
            }
            Since = Now;
            return new ChSuccess("noted", new { covered, pending = late != null && late.blocked.Count + late.changed.Count > 0 ? late : null });
        }

        /// <summary>What After answers: what was refused, what was found changed, and whether that was taken back.</summary>
        private sealed class Looked
        {
            public List<string> blocked = new List<string>();
            public List<string> changed = new List<string>();
            public bool undone;

            [JsonProperty(NullValueHandling = NullValueHandling.Ignore)]
            public string detail;

            [JsonProperty(NullValueHandling = NullValueHandling.Ignore)]
            public string request;
        }

        /// <summary>
        /// What happened to the protected things since Before: refused by Unity itself (blocked), or found changed
        /// (changed), in which case the work of the request at work is taken back. refresh: have Unity read the
        /// project again first, so that a file written past it shows now, not whenever Unity next gets around to it.
        /// </summary>
        private static Looked Look(bool refresh)
        {
            var rules = Current;
            if (refresh && rules.paths.Count > 0 && !EditorApplication.isCompiling && !EditorApplication.isUpdating &&
                !EditorApplication.isPlayingOrWillChangePlaymode)
            {
                AssetDatabase.Refresh();
            }
            var found = TakePending();
            var before = _base;
            _base = null;
            var looked = new Looked { blocked = found.blocked, changed = new List<string>(found.changed) };
            if (before != null) looked.changed.AddRange(Compare(before));
            if (looked.changed.Count == 0) return looked;
            var mark = ChCheckpoint.UndoForProtection();
            if (mark == null)
            {
                looked.detail = T("이 요청의 되돌리기 지점이 없음");
                return looked;
            }
            var left = before != null ? Compare(before) : new List<string>();
            TakePending();  // what the undo itself stirred up is nobody's doing
            ChChatWindow.TellUndone(mark.request);
            var problems = mark.report != null ? mark.report.problems.Concat(mark.report.unkept).ToList() : new List<string>();
            looked.request = mark.request;
            looked.undone = left.Count == 0 && problems.Count == 0;
            if (!looked.undone)
            {
                looked.detail = left.Count > 0 ? T("Undo를 거치지 않은 변경이 남음: ") + string.Join(", ", left.Take(3))
                                               : T("되돌리지 못한 파일: ") + string.Join(", ", problems.Take(3));
            }
            return looked;
        }

        private static object After()
        {
            var looked = Look(true);
            return new ChSuccess(looked.changed.Count == 0 ? "as it was" : looked.undone ? "taken back" : "not taken back", looked);
        }

        public static object HandleCommand(JObject @params)
        {
            var p = @params?.ToObject<Parameters>() ?? new Parameters();
            switch ((p.action ?? "state").Trim().ToLowerInvariant())
            {
                case "state":
                    var rules = Current;
                    return new ChSuccess(rules.rows.Count == 0 ? "No protection rules in this project." : $"{rules.rows.Count} protection rule(s)",
                        new { rules.window, rules.paths, rules.objects, rules.actions, rules.problems, rows = rules.rows.Select(Label).ToList() });
                case "before":
                    return Before();
                case "after":
                    return After();
                default:
                    return ChErrors.Make(ChErrors.InvalidArgs, "action must be state.", "after_fix");
            }
        }
    }
}
