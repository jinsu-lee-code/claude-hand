using System;
using System.IO;
using System.Linq;
using System.Text.RegularExpressions;
using MCPForUnity.Editor.Helpers;
using UnityEditor;
using UnityEngine;

namespace ClaudeHand.Editor
{
    /// <summary>
    /// Error envelope shared by every ch_* tool. Besides the code, each error says
    /// whether the agent may simply call again ("retry"):
    ///   safe        - nothing changed; call again as-is (after a new snapshot, after waiting, ...)
    ///   after_fix   - nothing changed; fix the arguments first
    ///   check_first - the change may already have happened; inspect state before retrying
    /// </summary>
    internal static class ChErrors
    {
        public const string InvalidArgs = "INVALID_ARGS";
        public const string StaleRef = "STALE_REF";
        public const string NotFound = "NOT_FOUND";
        public const string Conflict = "CONFLICT";
        public const string EditorBusy = "EDITOR_BUSY";
        public const string Failed = "FAILED";

        public static ErrorResponse Make(string code, string message, string retry, string hint = null, object details = null)
        {
            return new ErrorResponse(code, new { code, message, retry, hint, details });
        }

        public static ErrorResponse BusyIfCompiling()
        {
            if (EditorApplication.isCompiling || EditorApplication.isUpdating)
            {
                return Make(EditorBusy, "Unity is compiling scripts or importing assets.", "safe",
                    "Wait a few seconds and call again.");
            }
            return null;
        }
    }

    internal static class ChPaths
    {
        /// <summary>Folder that holds Assets/, Library/, ProjectSettings/.</summary>
        public static string ProjectRoot => Directory.GetParent(Application.dataPath).FullName;

        /// <summary>Same name MCP for Unity uses in instance ids (Name@hash): the project folder name.</summary>
        public static string ProjectName => new DirectoryInfo(ProjectRoot).Name;

        public static string LibraryDir => Path.Combine(ProjectRoot, "Library", "ClaudeHand");

        /// <summary>
        /// Shared folder where projects drop packages for each other.
        /// Default: %USERPROFILE%/.claude-hand/exchange. Override with CLAUDE_HAND_EXCHANGE_DIR.
        /// </summary>
        public static string ExchangeDir
        {
            get
            {
                var env = Environment.GetEnvironmentVariable("CLAUDE_HAND_EXCHANGE_DIR");
                var dir = string.IsNullOrEmpty(env)
                    ? Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), ".claude-hand", "exchange")
                    : env;
                Directory.CreateDirectory(dir);
                return dir;
            }
        }

        public static string ToPosix(string path) => path?.Replace('\\', '/');

        /// <summary>Keeps letters, digits, '-' and '_' so a name is safe for files and asset paths.</summary>
        public static string Sanitize(string name)
        {
            var cleaned = Regex.Replace(name ?? string.Empty, @"[^A-Za-z0-9_\-]+", "_").Trim('_');
            if (cleaned.Length > 48) cleaned = cleaned.Substring(0, 48);
            return string.IsNullOrEmpty(cleaned) ? "object" : cleaned;
        }

        /// <summary>Creates Assets/a/b/c one level at a time (AssetDatabase.CreateFolder needs existing parents).</summary>
        public static void EnsureAssetFolder(string assetFolder)
        {
            var parts = assetFolder.Split('/');
            var current = parts[0];
            for (int i = 1; i < parts.Length; i++)
            {
                var next = current + "/" + parts[i];
                if (!AssetDatabase.IsValidFolder(next)) AssetDatabase.CreateFolder(current, parts[i]);
                current = next;
            }
        }

        /// <summary>Deletes an asset folder only when it holds nothing but .meta files.</summary>
        public static void DeleteAssetFolderIfEmpty(string assetFolder)
        {
            if (!AssetDatabase.IsValidFolder(assetFolder)) return;
            var absolute = Path.Combine(ProjectRoot, assetFolder);
            bool empty = !Directory.Exists(absolute) ||
                         !Directory.EnumerateFileSystemEntries(absolute).Any(e => !e.EndsWith(".meta", StringComparison.OrdinalIgnoreCase));
            if (empty) AssetDatabase.DeleteAsset(assetFolder);
        }
    }
}
