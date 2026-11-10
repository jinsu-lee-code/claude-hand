using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;
using UnityEngine.SceneManagement;
using Object = UnityEngine.Object;

namespace ClaudeHand.Editor
{
    [ChTool("ch_export_model",
        Description = "Write a 3D model for Blender (or for ch_import_model in another Unity project) to the shared exchange " +
                      "folder, with a claude-hand/model@1 manifest. A scene object or asset that comes from a model file (FBX, " +
                      "OBJ, GLB) is sent as that file, as it was imported: what was changed on it in the scene does not go " +
                      "along. Anything else (primitives, meshes made in Unity) is written as FBX by Unity's FBX Exporter " +
                      "package (com.unity.formats.fbx), when the project has it. This project is not changed.")]
    public static class ChExportModel
    {
        private const string ModelFormat = "claude-hand/model@1";
        private static readonly string[] ModelExtensions = { ".fbx", ".obj", ".glb" };  // one file each, and what both receivers read

        public class Parameters
        {
            [ChParameter("Snapshot refs (from ch_scene_snapshot) of the scene GameObjects to send.", Required = false)]
            public string[] refs { get; set; }

            [ChParameter("Instead of refs: a model asset to send as it is, e.g. Assets/Models/Rock.fbx.", Required = false)]
            public string asset_path { get; set; }

            [ChParameter("Short name for this transfer, e.g. rock (default: the first object's or the file's name).", Required = false)]
            public string name { get; set; }

            [ChParameter("auto (default): the model file the objects come from when there is one, else FBX Exporter. source_file or fbx_exporter: only that way. fbx_exporter sends the objects as they are in the scene now.", Required = false)]
            public string via { get; set; }
        }

        public static object HandleCommand(JObject @params)
        {
            var p = @params?.ToObject<Parameters>() ?? new Parameters();
            var refs = (p.refs ?? Array.Empty<string>()).Where(r => !string.IsNullOrWhiteSpace(r)).ToArray();
            var assetPath = string.IsNullOrWhiteSpace(p.asset_path) ? null : ChPaths.ToPosix(p.asset_path.Trim());
            var via = string.IsNullOrWhiteSpace(p.via) ? "auto" : p.via.Trim().ToLowerInvariant();
            if (via != "auto" && via != "source_file" && via != "fbx_exporter")
            {
                return ChErrors.Make(ChErrors.InvalidArgs, "via must be auto, source_file or fbx_exporter.", "after_fix");
            }
            if ((refs.Length == 0) == (assetPath == null))
            {
                return ChErrors.Make(ChErrors.InvalidArgs, "Pass either refs (scene objects) or asset_path (a model asset).", "after_fix",
                    "Refs come from ch_scene_snapshot; asset paths start with Assets/.");
            }

            var busy = ChErrors.BusyIfCompiling();
            if (busy != null) return busy;

            var warnings = new List<string>();
            var objects = new List<GameObject>();
            string sourceFile = null;   // the model file to send as it is; null: write one with FBX Exporter
            if (assetPath != null)
            {
                if (!IsModelFile(assetPath) || AssetDatabase.LoadAssetAtPath<GameObject>(assetPath) == null)
                {
                    return ChErrors.Make(ChErrors.NotFound, $"'{assetPath}' is not a model asset of this project.", "after_fix",
                        "Use the path as the Project window shows it, e.g. Assets/Models/Rock.fbx.");
                }
                if (via == "fbx_exporter")
                {
                    return ChErrors.Make(ChErrors.InvalidArgs, "A model asset is sent as its own file; fbx_exporter is for scene objects.", "after_fix");
                }
                sourceFile = assetPath;
            }
            else
            {
                foreach (var r in refs)
                {
                    var go = ChRefs.Resolve(r, out var error);
                    if (go == null) return error;
                    objects.Add(go);
                }

                var files = objects.Select(go => SourceModelOf(go, warnings)).ToList();
                var distinct = files.Where(f => f != null).Distinct(StringComparer.Ordinal).ToList();
                bool oneFile = distinct.Count == 1 && files.All(f => f != null);
                if (via == "source_file" && !oneFile)
                {
                    return ChErrors.Make(ChErrors.InvalidArgs, "These objects do not all come from one model file.", "after_fix",
                        "Send them one model at a time, or use via=fbx_exporter.", new { model_files = distinct, without_model_file = Without(objects, files) });
                }
                if (via != "fbx_exporter" && oneFile)
                {
                    sourceFile = distinct[0];
                }
                else if (FbxExportObjects() == null)
                {
                    return ChErrors.Make(ChErrors.Failed, "These objects are not one imported model file, and this project has no FBX Exporter to write one.", "after_fix",
                        "Install FBX Exporter (com.unity.formats.fbx) in Window > Package Manager, or send objects that come from one model file.",
                        new { model_files = distinct, without_model_file = Without(objects, files) });
                }
                else
                {
                    warnings.Clear();  // those were about sending a file as it was imported; this sends the scene objects as they are
                }
            }

            var name = ChPaths.Sanitize(!string.IsNullOrWhiteSpace(p.name) ? p.name
                : objects.Count > 0 ? objects[0].name : Path.GetFileNameWithoutExtension(sourceFile));
            var transferId = $"{name}_{DateTime.Now:yyyyMMdd_HHmmss}";
            var extension = sourceFile != null ? Path.GetExtension(sourceFile).ToLowerInvariant() : ".fbx";
            var modelPath = Path.Combine(ChPaths.ExchangeDir, transferId + extension);
            try
            {
                if (sourceFile != null)
                {
                    File.Copy(Path.Combine(ChPaths.ProjectRoot, sourceFile), modelPath, true);
                }
                else
                {
                    var written = FbxExportObjects().Invoke(null, new object[] { modelPath, objects.Cast<Object>().ToArray() }) as string;
                    if (string.IsNullOrEmpty(written) || !File.Exists(modelPath))
                    {
                        return ChErrors.Make(ChErrors.Failed, "FBX Exporter did not write the file.", "safe", "Check read_console for its error.");
                    }
                    warnings.Add("Written by FBX Exporter: materials go by name and base color; textures do not go along.");
                }

                var scene = SceneManager.GetActiveScene();
                var manifestPath = Path.Combine(ChPaths.ExchangeDir, transferId + ".claudehand.json");
                File.WriteAllText(manifestPath, JsonConvert.SerializeObject(new
                {
                    format = ModelFormat,
                    name,
                    transfer_id = transferId,
                    model_file = transferId + extension,
                    // What a receiver knows this transfer by when it comes again (see ChImportModel): where it is from, and which objects.
                    source = "unity:" + ChPaths.ToPosix(ChPaths.ProjectRoot) + ":" + (sourceFile ?? scene.path),
                    roots = sourceFile != null ? new List<string>() : objects.Select(go => ChRefs.HierarchyPath(go.transform)).ToList(),
                    objects = objects.Select(go => go.name).ToList(),
                    unity_version = Application.unityVersion,
                    via = sourceFile != null ? "source_file" : "fbx_exporter",
                }, Formatting.Indented));

                return new ChSuccess($"Wrote {transferId + extension} to the exchange folder.", new
                {
                    transfer_id = transferId,
                    manifest_path = ChPaths.ToPosix(manifestPath),
                    model_path = ChPaths.ToPosix(modelPath),
                    via = sourceFile != null ? "source_file" : "fbx_exporter",
                    source_asset = sourceFile,
                    warnings,
                    next_step = "A Blender session takes it with claude_hand_chat.import_model(<manifest_path>); another Unity project with ch_import_model.",
                });
            }
            catch (Exception ex)
            {
                return ChErrors.Make(ChErrors.Failed, "Export failed: " + (ex.InnerException ?? ex).Message, "safe",
                    "Nothing in this project was changed.");
            }
        }

        private static bool IsModelFile(string assetPath)
        {
            return !string.IsNullOrEmpty(assetPath) && assetPath.StartsWith("Assets/", StringComparison.Ordinal) &&
                   ModelExtensions.Contains(Path.GetExtension(assetPath).ToLowerInvariant());
        }

        /// <summary>
        /// The model file a scene object comes from: the one it is an instance of, else the one all its meshes are in.
        /// Null when there is none (a primitive, a mesh made in Unity, meshes from several files).
        /// </summary>
        private static string SourceModelOf(GameObject go, List<string> warnings)
        {
            var original = PrefabUtility.GetCorrespondingObjectFromOriginalSource(go);
            var path = original != null ? ChPaths.ToPosix(AssetDatabase.GetAssetPath(original)) : null;
            if (IsModelFile(path))
            {
                if (original.transform.parent != null)
                {
                    warnings.Add($"'{go.name}' is a part of {path}; the whole file is sent.");
                }
                var root = PrefabUtility.GetNearestPrefabInstanceRoot(go);
                if (root != null && PrefabUtility.HasPrefabInstanceAnyOverrides(root, false))
                {
                    warnings.Add($"'{root.name}' was changed in the scene (materials, added or removed parts, components); {path} is sent as it was imported, without those changes.");
                }
                return path;
            }

            var meshes = go.GetComponentsInChildren<MeshFilter>(true).Select(f => f.sharedMesh)
                .Concat(go.GetComponentsInChildren<SkinnedMeshRenderer>(true).Select(r => r.sharedMesh))
                .Where(m => m != null).Distinct().ToList();
            var files = meshes.Select(m => ChPaths.ToPosix(AssetDatabase.GetAssetPath(m))).Distinct(StringComparer.Ordinal).ToList();
            if (meshes.Count == 0 || files.Count != 1 || !IsModelFile(files[0])) return null;
            warnings.Add($"'{go.name}' is built in the scene from the meshes of {files[0]}; that file is sent as it was imported, not the object as it stands in the scene.");
            return files[0];
        }

        private static List<string> Without(List<GameObject> objects, List<string> files)
        {
            return objects.Where((go, i) => files[i] == null).Select(go => go.name).ToList();
        }

        /// <summary>
        /// FBX Exporter's ModelExporter.ExportObjects(string, Object[]), found by name so this package neither needs
        /// nor references com.unity.formats.fbx. Null when the project does not have it.
        /// </summary>
        private static MethodInfo FbxExportObjects()
        {
            var type = Type.GetType("UnityEditor.Formats.Fbx.Exporter.ModelExporter, Unity.Formats.Fbx.Editor");
            return type?.GetMethod("ExportObjects", BindingFlags.Public | BindingFlags.Static, null,
                new[] { typeof(string), typeof(Object[]) }, null);
        }
    }
}
