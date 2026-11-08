using System.Collections.Generic;

namespace ClaudeHand.Editor
{
    /// <summary>
    /// Sidecar file written next to every exported .unitypackage (&lt;transfer_id&gt;.claudehand.json).
    /// The importing project reads it to know what the package holds before importing it,
    /// and to find the root prefabs again by GUID afterwards (GUIDs survive a package import).
    /// </summary>
    internal sealed class ChTransferManifest
    {
        public const string CurrentFormat = "claude-hand/transfer@1";

        public string format = CurrentFormat;
        public string transfer_id;
        public string created_utc;
        public string package_file;
        public Source source = new Source();
        public List<Root> roots = new List<Root>();
        public List<Asset> assets = new List<Asset>();
        public List<string> scripts = new List<string>();
        public List<string> excluded_scripts = new List<string>();
        public List<string> package_dependencies = new List<string>();
        public List<string> warnings = new List<string>();

        internal sealed class Source
        {
            public string project;
            public string project_path;
            public string unity_version;
        }

        internal sealed class Root
        {
            /// <summary>"prefab" (made from a scene object) or "asset" (exported as-is).</summary>
            public string kind;
            public string path;
            public string guid;
            public string source_name;
            public string source_ref;
            public string source_path;
        }

        internal sealed class Asset
        {
            public string path;
            public string guid;
        }
    }
}
