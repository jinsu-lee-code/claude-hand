using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.RegularExpressions;
using MCPForUnity.Editor.Helpers;
using Newtonsoft.Json;
using UnityEditor;
using UnityEngine;

namespace ClaudeHand.Editor
{
    /// <summary>
    /// Snapshot-scoped GameObject references, written "@s1a2b3c4:e12" (the agent-desktop idea).
    /// A ref is only meaningful together with the snapshot that produced it. When the object it
    /// points at is gone, resolving it fails with STALE_REF so the agent knows to snapshot again
    /// instead of acting on the wrong object.
    /// Snapshots are kept on disk in Library/ClaudeHand/snapshots so refs survive domain reloads.
    /// </summary>
    internal static class ChRefs
    {
        private const int MaxSnapshotsKept = 20;
        private static readonly Regex RefPattern = new Regex(@"^@(s[0-9a-f]{7}):e(\d+)$");
        private static readonly Dictionary<string, SnapshotFile> Cache = new Dictionary<string, SnapshotFile>();

        internal sealed class SnapshotFile
        {
            public string snapshot_id;
            public string created_utc;
            public List<Entry> entries = new List<Entry>();
        }

        internal sealed class Entry
        {
            public int instance_id;
            public string global_id;
            public string path;
        }

        private static string Dir => Path.Combine(ChPaths.LibraryDir, "snapshots");

        public sealed class Builder
        {
            private readonly SnapshotFile _file;

            internal Builder(SnapshotFile file)
            {
                _file = file;
            }

            public string SnapshotId => _file.snapshot_id;

            public string Add(GameObject go, string hierarchyPath)
            {
                _file.entries.Add(new Entry
                {
                    instance_id = go.GetInstanceID(),
                    global_id = GlobalObjectId.GetGlobalObjectIdSlow(go).ToString(),
                    path = hierarchyPath,
                });
                return "@" + _file.snapshot_id + ":e" + _file.entries.Count;
            }

            public void Save()
            {
                Directory.CreateDirectory(Dir);
                File.WriteAllText(Path.Combine(Dir, _file.snapshot_id + ".json"), JsonConvert.SerializeObject(_file));
                Cache[_file.snapshot_id] = _file;
                Prune();
            }
        }

        public static Builder Begin()
        {
            var id = "s" + Guid.NewGuid().ToString("N").Substring(0, 7);
            return new Builder(new SnapshotFile { snapshot_id = id, created_utc = DateTime.UtcNow.ToString("o") });
        }

        /// <summary>Returns the GameObject a ref points at, or null with an error the tool can return as-is.</summary>
        public static GameObject Resolve(string reference, out ErrorResponse error)
        {
            error = null;
            var match = RefPattern.Match(reference?.Trim() ?? string.Empty);
            if (!match.Success)
            {
                error = ChErrors.Make(ChErrors.InvalidArgs, $"'{reference}' is not a snapshot ref.", "after_fix",
                    "Refs look like @s1a2b3c4:e12 and come from ch_scene_snapshot. A bare @e12 is rejected on purpose.");
                return null;
            }

            var snapshotId = match.Groups[1].Value;
            var index = int.Parse(match.Groups[2].Value);
            var file = Load(snapshotId);
            if (file == null)
            {
                error = ChErrors.Make(ChErrors.StaleRef, $"Snapshot {snapshotId} has expired (only the last {MaxSnapshotsKept} are kept).", "safe",
                    "Run ch_scene_snapshot again and use the new refs.");
                return null;
            }

            if (index < 1 || index > file.entries.Count)
            {
                error = ChErrors.Make(ChErrors.InvalidArgs, $"Snapshot {snapshotId} has no element e{index}.", "after_fix",
                    $"Valid elements are e1..e{file.entries.Count}.");
                return null;
            }

            var entry = file.entries[index - 1];
            var go = EditorUtility.InstanceIDToObject(entry.instance_id) as GameObject;
            if (go == null && GlobalObjectId.TryParse(entry.global_id, out var globalId))
            {
                go = GlobalObjectId.GlobalObjectIdentifierToObjectSlow(globalId) as GameObject;
            }

            if (go == null)
            {
                error = ChErrors.Make(ChErrors.StaleRef, $"The object that was at '{entry.path}' no longer exists.", "safe",
                    "The scene changed since that snapshot. Run ch_scene_snapshot again and use the new refs.");
                return null;
            }

            return go;
        }

        public static string HierarchyPath(Transform transform)
        {
            var parts = new List<string>();
            for (var current = transform; current != null; current = current.parent) parts.Add(current.name);
            parts.Reverse();
            return string.Join("/", parts);
        }

        private static SnapshotFile Load(string snapshotId)
        {
            if (Cache.TryGetValue(snapshotId, out var cached)) return cached;
            var path = Path.Combine(Dir, snapshotId + ".json");
            if (!File.Exists(path)) return null;
            try
            {
                var file = JsonConvert.DeserializeObject<SnapshotFile>(File.ReadAllText(path));
                if (file != null) Cache[snapshotId] = file;
                return file;
            }
            catch (Exception)
            {
                return null;
            }
        }

        private static void Prune()
        {
            try
            {
                var stale = new DirectoryInfo(Dir).GetFiles("s*.json")
                    .OrderByDescending(f => f.LastWriteTimeUtc)
                    .Skip(MaxSnapshotsKept)
                    .ToList();
                foreach (var f in stale)
                {
                    Cache.Remove(Path.GetFileNameWithoutExtension(f.Name));
                    f.Delete();
                }
            }
            catch (Exception)
            {
                // Pruning is housekeeping; never fail a tool call over it.
            }
        }
    }
}
