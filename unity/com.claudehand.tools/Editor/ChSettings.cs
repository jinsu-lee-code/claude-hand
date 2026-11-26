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
    /// The user's settings and the lab, as this editor reads and writes them.
    ///
    /// The values are in ~/.claude-hand/config.json, the one file the chat channel, the commands, the hooks and
    /// Blender's panel read too: {"language": "en", "show_cost": false, "lab": {"english_prompts": true}, ...}.
    /// What can be set is listed once, in the claude-hand checkout (settings/catalog.json); the settings window
    /// (ChSettingsWindow, the gear button of the chat window) draws a row for each entry of it. The file is read
    /// anew whenever it changes, so a change made in any window holds here at once; <see cref="Changed"/> tells
    /// the open windows to draw again.
    /// </summary>
    [InitializeOnLoad]
    internal static class ChSettings
    {
        /// <summary>One entry of the catalog: a setting, or a feature of the lab.</summary>
        internal sealed class Entry
        {
            public string key;
            public string kind;       // switch | choice | days (a lab feature is a switch)
            public JToken fallback;   // its default
            public string applies;    // now | start
            public string since;      // lab: the version it came in with
            public JObject name;      // {"ko": ..., "en": ...}
            public JObject hint;
            public List<KeyValuePair<string, string>> choices = new List<KeyValuePair<string, string>>();  // value, label
        }

        /// <summary>config.json was changed (here or by another window): draw again.</summary>
        public static event Action Changed;

        private static JObject _config = new JObject();
        private static string _configStamp;
        private static List<Entry> _settings = new List<Entry>();
        private static List<Entry> _lab = new List<Entry>();
        private static string _catalogStamp;
        private static double _lookedAt;

        static ChSettings()
        {
            EditorApplication.update += Watch;
        }

        /// <summary>The self-test's own file, so that it never writes the user's settings.</summary>
        internal static string FileOverride;

        public static string ConfigFile => FileOverride ?? Path.Combine(ChPaths.ClaudeHandHome, "config.json");

        /// <summary>Twice a second: has another window (or the command) changed the settings?</summary>
        private static void Watch()
        {
            if (EditorApplication.timeSinceStartup - _lookedAt < 0.5) return;
            _lookedAt = EditorApplication.timeSinceStartup;
            var before = _configStamp;
            Read();
            if (before != null && before != _configStamp) Changed?.Invoke();
        }

        private static string Stamp(string path)
        {
            try
            {
                var info = new FileInfo(path);
                return info.Exists ? $"{path}|{info.LastWriteTimeUtc.Ticks}|{info.Length}" : path + "|none";
            }
            catch (Exception)
            {
                return path + "|none";
            }
        }

        /// <summary>config.json as it is now. Missing: empty. Caught half written (another window is saving it): what was last read.</summary>
        public static JObject Read()
        {
            var stamp = Stamp(ConfigFile);
            if (stamp == _configStamp) return _config;
            try
            {
                _config = File.Exists(ConfigFile) ? JObject.Parse(File.ReadAllText(ConfigFile)) : new JObject();
                _configStamp = stamp;
            }
            catch (Exception)
            {
                // being written just now, or not JSON: as it was, and looked at again next time
            }
            return _config;
        }

        /// <summary>
        /// Change config.json: read anew just before (another window may have written it), and put in place whole, so
        /// nobody reads half of it. Returns what stood in the way (a text for the user), or null.
        /// </summary>
        public static string Write(Action<JObject> change)
        {
            try
            {
                var config = File.Exists(ConfigFile) ? JObject.Parse(File.ReadAllText(ConfigFile)) : new JObject();
                change(config);
                Directory.CreateDirectory(Path.GetDirectoryName(ConfigFile) ?? ".");
                var tmp = ConfigFile + "." + Guid.NewGuid().ToString("N").Substring(0, 12) + ".tmp";  // of this write alone
                File.WriteAllText(tmp, config.ToString(Formatting.Indented) + "\n");
                for (int attempt = 0; ; attempt++)
                {
                    try
                    {
                        if (File.Exists(ConfigFile)) File.Replace(tmp, ConfigFile, null);
                        else File.Move(tmp, ConfigFile);
                        break;
                    }
                    catch (IOException) when (attempt < 7)
                    {
                        System.Threading.Thread.Sleep(30);  // another window has the old file open to read it: a moment
                    }
                    catch (UnauthorizedAccessException) when (attempt < 7)
                    {
                        System.Threading.Thread.Sleep(30);
                    }
                }
                _config = config;
                _configStamp = Stamp(ConfigFile);
            }
            catch (Exception ex)
            {
                return ChText.T("설정을 적지 못했습니다: {0}", ex.Message);
            }
            Changed?.Invoke();
            return null;
        }

        /// <summary>"ko" or "en". CLAUDE_HAND_LANG, when it is one of the two, wins over the setting (the self-test).</summary>
        public static string Language
        {
            get
            {
                var forced = (Environment.GetEnvironmentVariable("CLAUDE_HAND_LANG") ?? string.Empty).Trim().ToLowerInvariant();
                if (forced == "ko" || forced == "en") return forced;
                var value = Read().Value<string>("language");
                return value == "en" ? "en" : "ko";
            }
        }

        /// <summary>A switch this package itself goes by, as it stands now (on unless config.json says false).</summary>
        public static bool On(string key)
        {
            var value = Read()[key];
            return value == null || value.Type != JTokenType.Boolean || value.Value<bool>();
        }

        // ------------------------------------------------------------------ the catalog

        /// <summary>settings/catalog.json of the claude-hand checkout: where config.json says it is ("repo", written by
        /// setup), else where this package lies when it was installed from that folder.</summary>
        internal static string CatalogFile
        {
            get
            {
                var repo = Read().Value<string>("repo");
                if (!string.IsNullOrEmpty(repo))
                {
                    var listed = Path.Combine(repo, "settings", "catalog.json");
                    if (File.Exists(listed)) return listed;
                }
                try
                {
                    return Path.GetFullPath(Path.Combine(Path.GetFullPath("Packages/com.claudehand.tools"), "..", "..", "settings", "catalog.json"));
                }
                catch (Exception)
                {
                    return string.Empty;
                }
            }
        }

        private static void ReadCatalog()
        {
            var path = CatalogFile;
            var stamp = Stamp(path);
            if (stamp == _catalogStamp) return;
            _catalogStamp = stamp;
            _settings = new List<Entry>();
            _lab = new List<Entry>();
            try
            {
                if (!File.Exists(path)) return;
                var data = JObject.Parse(File.ReadAllText(path));
                _settings = Entries(data["settings"] as JArray, false);
                _lab = Entries(data["lab"] as JArray, true);
            }
            catch (Exception)
            {
                // no list: the window says so, and still offers the language
            }
        }

        private static List<Entry> Entries(JArray rows, bool lab)
        {
            var found = new List<Entry>();
            foreach (var row in (rows ?? new JArray()).OfType<JObject>())
            {
                var key = row.Value<string>("key");
                if (string.IsNullOrEmpty(key)) continue;
                if (lab && (row.Value<string>("stage") ?? "lab") != "lab") continue;  // one that left the lab is on for everybody
                var entry = new Entry
                {
                    key = key, kind = lab ? "switch" : row.Value<string>("kind"), fallback = row["default"],
                    applies = row.Value<string>("applies") ?? "start", since = row.Value<string>("since"),
                    name = row["name"] as JObject, hint = row["hint"] as JObject,
                };
                foreach (var choice in (row["choices"] as JArray ?? new JArray()).OfType<JObject>())
                {
                    entry.choices.Add(new KeyValuePair<string, string>(choice.Value<string>("value"), choice.Value<string>("label") ?? choice.Value<string>("value")));
                }
                found.Add(entry);
            }
            return found;
        }

        /// <summary>The settings the window lists. The language is there whatever the catalog holds.</summary>
        public static List<Entry> Settings
        {
            get
            {
                ReadCatalog();
                if (_settings.Any(e => e.key == "language")) return _settings;
                var language = new Entry
                {
                    key = "language", kind = "choice", fallback = "ko", applies = "now",
                    name = new JObject { ["ko"] = "언어 · Language", ["en"] = "언어 · Language" },
                };
                language.choices.Add(new KeyValuePair<string, string>("ko", "한국어"));
                language.choices.Add(new KeyValuePair<string, string>("en", "English"));
                return new List<Entry> { language }.Concat(_settings).ToList();
            }
        }

        /// <summary>The features still in the lab.</summary>
        public static List<Entry> Lab
        {
            get
            {
                ReadCatalog();
                return _lab;
            }
        }

        /// <summary>Whether the list of settings could be read (the language alone is offered when it could not).</summary>
        public static bool CatalogFound
        {
            get
            {
                ReadCatalog();
                return _settings.Count > 0;
            }
        }

        /// <summary>Of an entry's {"ko": ..., "en": ...}, the one in the language now in use.</summary>
        public static string Pick(JObject names)
        {
            return names?.Value<string>(Language) ?? names?.Value<string>("ko") ?? string.Empty;
        }

        public static bool Switch(Entry entry)
        {
            var value = Read()[entry.key];
            if (value != null && value.Type == JTokenType.Boolean) return value.Value<bool>();
            return entry.fallback != null && entry.fallback.Type == JTokenType.Boolean && entry.fallback.Value<bool>();
        }

        public static string Choice(Entry entry)
        {
            var value = Read()[entry.key];
            var said = value != null && value.Type == JTokenType.String ? value.Value<string>() : null;
            return entry.choices.Any(c => c.Key == said) ? said : entry.fallback?.ToString();
        }

        public static int Days(Entry entry)
        {
            var value = Read()[entry.key];
            if (value != null && value.Type == JTokenType.Integer && value.Value<int>() >= 0 && value.Value<int>() <= 3650) return value.Value<int>();
            return entry.fallback != null && entry.fallback.Type == JTokenType.Integer ? entry.fallback.Value<int>() : 0;
        }

        public static bool LabOn(Entry entry)
        {
            var value = (Read()["lab"] as JObject)?[entry.key];
            if (value != null && value.Type == JTokenType.Boolean) return value.Value<bool>();
            return entry.fallback != null && entry.fallback.Type == JTokenType.Boolean && entry.fallback.Value<bool>();
        }

        public static string Set(string key, JToken value) => Write(config => config[key] = value);

        public static string SetLab(string key, bool on)
        {
            return Write(config =>
            {
                var chosen = config["lab"] as JObject ?? new JObject();
                chosen[key] = on;
                config["lab"] = chosen;
            });
        }
    }
}
