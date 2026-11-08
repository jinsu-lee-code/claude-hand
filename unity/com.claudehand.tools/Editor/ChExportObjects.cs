using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.Compilation;
using UnityEngine;
using Object = UnityEngine.Object;

namespace ClaudeHand.Editor
{
    [McpForUnityTool("ch_export_objects",
        Description = "Pack scene GameObjects (by snapshot ref) and/or project assets, together with every asset they depend on " +
                      "(meshes, materials, textures, scripts...), into a .unitypackage in the shared exchange folder, plus a " +
                      ".claudehand.json manifest. Another Unity project imports it with ch_import_package. The source scene is not " +
                      "changed; scene objects are saved as temporary prefabs that are removed again after packing.")]
    public static class ChExportObjects
    {
        private const string TransfersFolder = "Assets/ClaudeHand/Transfers";

        public class Parameters
        {
            [ToolParameter("Snapshot refs (from ch_scene_snapshot) of scene GameObjects to export. Each becomes a prefab with its children.", Required = false)]
            public string[] refs { get; set; }

            [ToolParameter("Project asset paths to export as they are, e.g. Assets/Prefabs/Enemy.prefab or Assets/Materials/Rock.mat.", Required = false)]
            public string[] asset_paths { get; set; }

            [ToolParameter("Short name for this transfer, e.g. player_rig (default: the first object's name).", Required = false)]
            public string name { get; set; }

            [ToolParameter("Include the C# scripts the objects use (default true). The target project then has to compile them.", Required = false)]
            public bool? include_scripts { get; set; }
        }

        public static object HandleCommand(JObject @params)
        {
            var p = @params?.ToObject<Parameters>() ?? new Parameters();
            var refs = (p.refs ?? Array.Empty<string>()).Where(r => !string.IsNullOrWhiteSpace(r)).ToArray();
            var assetPaths = (p.asset_paths ?? Array.Empty<string>()).Where(a => !string.IsNullOrWhiteSpace(a)).Select(ChPaths.ToPosix).ToList();
            bool includeScripts = p.include_scripts ?? true;

            if (refs.Length == 0 && assetPaths.Count == 0)
            {
                return ChErrors.Make(ChErrors.InvalidArgs, "Nothing to export.", "after_fix",
                    "Pass refs from ch_scene_snapshot and/or asset_paths under Assets/.");
            }

            var busy = ChErrors.BusyIfCompiling();
            if (busy != null) return busy;

            // Resolve every input before touching the project, so a bad argument changes nothing.
            var objects = new List<GameObject>();
            foreach (var r in refs)
            {
                var go = ChRefs.Resolve(r, out var error);
                if (go == null) return error;
                objects.Add(go);
            }
            foreach (var a in assetPaths)
            {
                if (!a.StartsWith("Assets/", StringComparison.Ordinal) || AssetDatabase.LoadMainAssetAtPath(a) == null)
                {
                    return ChErrors.Make(ChErrors.NotFound, $"'{a}' is not an asset under Assets/.", "after_fix",
                        "Use paths as the Project window shows them, starting with Assets/.");
                }
            }

            var baseName = ChPaths.Sanitize(!string.IsNullOrWhiteSpace(p.name)
                ? p.name
                : objects.Count > 0 ? objects[0].name : Path.GetFileNameWithoutExtension(assetPaths[0]));
            var transferId = $"{baseName}_{DateTime.Now:yyyyMMdd_HHmmss}";
            var stagingFolder = $"{TransfersFolder}/{transferId}";
            var manifest = new ChTransferManifest
            {
                transfer_id = transferId,
                created_utc = DateTime.UtcNow.ToString("o"),
                package_file = transferId + ".unitypackage",
                source =
                {
                    project = ChPaths.ProjectName,
                    project_path = ChPaths.ToPosix(ChPaths.ProjectRoot),
                    unity_version = Application.unityVersion,
                },
            };

            try
            {
                if (objects.Count > 0) ChPaths.EnsureAssetFolder(stagingFolder);

                var usedNames = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
                for (int i = 0; i < objects.Count; i++)
                {
                    var go = objects[i];
                    var fileName = UniqueName(ChPaths.Sanitize(go.name), usedNames);
                    var prefabPath = $"{stagingFolder}/{fileName}.prefab";
                    if (!SaveAsPrefab(go, prefabPath, manifest.warnings))
                    {
                        return ChErrors.Make(ChErrors.Failed, $"Could not save '{go.name}' as a prefab.", "safe",
                            "Check read_console for the Unity error. Nothing was exported.");
                    }
                    manifest.roots.Add(new ChTransferManifest.Root
                    {
                        kind = "prefab",
                        path = prefabPath,
                        guid = AssetDatabase.AssetPathToGUID(prefabPath),
                        source_name = go.name,
                        source_ref = refs[i],
                        source_path = ChRefs.HierarchyPath(go.transform),
                    });
                }

                foreach (var a in assetPaths)
                {
                    manifest.roots.Add(new ChTransferManifest.Root
                    {
                        kind = "asset",
                        path = a,
                        guid = AssetDatabase.AssetPathToGUID(a),
                        source_name = Path.GetFileNameWithoutExtension(a),
                    });
                }

                var exported = CollectAssets(manifest, includeScripts);
                var packagePath = Path.Combine(ChPaths.ExchangeDir, manifest.package_file);
                AssetDatabase.ExportPackage(exported.ToArray(), packagePath, ExportPackageOptions.Default);
                if (!File.Exists(packagePath))
                {
                    return ChErrors.Make(ChErrors.Failed, "Unity did not write the package file.", "safe",
                        "Check read_console for the Unity error.");
                }

                var manifestPath = Path.Combine(ChPaths.ExchangeDir, transferId + ".claudehand.json");
                File.WriteAllText(manifestPath, JsonConvert.SerializeObject(manifest, Formatting.Indented));

                return new SuccessResponse($"Exported {manifest.roots.Count} root(s) with {manifest.assets.Count} asset(s) as {transferId}", new
                {
                    transfer_id = transferId,
                    manifest_path = ChPaths.ToPosix(manifestPath),
                    package_path = ChPaths.ToPosix(packagePath),
                    roots = manifest.roots.Select(r => new { r.kind, r.source_name, r.path }),
                    asset_count = manifest.assets.Count,
                    scripts = manifest.scripts,
                    excluded_scripts = manifest.excluded_scripts,
                    package_dependencies = manifest.package_dependencies,
                    warnings = manifest.warnings,
                    next_step = "In the target project call ch_import_package with manifest=<manifest_path> (try dry_run=true first).",
                });
            }
            catch (Exception ex)
            {
                return ChErrors.Make(ChErrors.Failed, "Export failed: " + ex.Message, "safe", "Nothing was written to the exchange folder.");
            }
            finally
            {
                // The temporary prefabs are inside the package by now; remove them so the source project stays as it was.
                if (AssetDatabase.IsValidFolder(stagingFolder)) AssetDatabase.DeleteAsset(stagingFolder);
                ChPaths.DeleteAssetFolderIfEmpty(TransfersFolder);
                ChPaths.DeleteAssetFolderIfEmpty("Assets/ClaudeHand");
            }
        }

        /// <summary>
        /// Saves a copy of a scene object as a prefab without changing the object. A child inside a prefab
        /// instance cannot be saved directly, so a temporary unpacked copy is saved instead.
        /// </summary>
        private static bool SaveAsPrefab(GameObject go, string prefabPath, List<string> warnings)
        {
            GameObject temporaryCopy = null;
            var source = go;
            if (PrefabUtility.IsPartOfPrefabInstance(go) && !PrefabUtility.IsOutermostPrefabInstanceRoot(go))
            {
                temporaryCopy = Object.Instantiate(go);
                temporaryCopy.name = go.name;
                source = temporaryCopy;
                warnings.Add($"'{go.name}' sits inside a prefab instance; it was exported as a standalone prefab. The source scene may show as modified.");
            }

            try
            {
                var saved = PrefabUtility.SaveAsPrefabAsset(source, prefabPath, out bool success);
                return success && saved != null;
            }
            finally
            {
                if (temporaryCopy != null) Object.DestroyImmediate(temporaryCopy);
            }
        }

        /// <summary>Fills manifest.assets/scripts/package_dependencies and returns the asset paths to pack.</summary>
        private static List<string> CollectAssets(ChTransferManifest manifest, bool includeScripts)
        {
            var exported = new List<string>();
            var packages = new SortedSet<string>(StringComparer.Ordinal);
            var dependencies = AssetDatabase.GetDependencies(manifest.roots.Select(r => r.path).ToArray(), true)
                .Select(ChPaths.ToPosix)
                .Distinct();

            foreach (var path in dependencies)
            {
                if (path.StartsWith("Packages/", StringComparison.Ordinal))
                {
                    // Assets inside UPM packages cannot go into a .unitypackage; the target needs the same package installed.
                    packages.Add(PackageNameOf(path));
                    continue;
                }
                if (!path.StartsWith("Assets/", StringComparison.Ordinal)) continue; // built-in resources

                if (path.EndsWith(".cs", StringComparison.OrdinalIgnoreCase))
                {
                    if (!includeScripts)
                    {
                        manifest.excluded_scripts.Add(path);
                        continue;
                    }
                    manifest.scripts.Add(path);
                    var asmdef = CompilationPipeline.GetAssemblyDefinitionFilePathFromScriptPath(path);
                    if (!string.IsNullOrEmpty(asmdef))
                    {
                        manifest.warnings.Add($"{path} belongs to {asmdef}, which is not exported; in the target it compiles into the default assembly.");
                    }
                }

                exported.Add(path);
                manifest.assets.Add(new ChTransferManifest.Asset { path = path, guid = AssetDatabase.AssetPathToGUID(path) });
            }

            manifest.package_dependencies.AddRange(packages);
            if (manifest.excluded_scripts.Count > 0)
            {
                manifest.warnings.Add($"{manifest.excluded_scripts.Count} script(s) left out; components using them show as Missing Script unless the target already has them.");
            }
            return exported;
        }

        private static string PackageNameOf(string assetPath)
        {
            var parts = assetPath.Split('/');
            var name = parts.Length > 1 ? parts[1] : assetPath;
            var info = UnityEditor.PackageManager.PackageInfo.FindForAssetPath(assetPath);
            return info != null ? $"{name}@{info.version}" : name;
        }

        private static string UniqueName(string name, HashSet<string> used)
        {
            var candidate = name;
            for (int n = 2; !used.Add(candidate); n++) candidate = $"{name}_{n}";
            return candidate;
        }
    }
}
