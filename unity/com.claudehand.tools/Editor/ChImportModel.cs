using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.RegularExpressions;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace ClaudeHand.Editor
{
    [ChTool("ch_import_model",
        Description = "Bring a 3D model file into this project, typically one a Blender session exported to the exchange folder " +
                      "(manifest format claude-hand/model@1). The first time, it is copied to Assets/ClaudeHand/Imports/<name>/, " +
                      "imported and by default placed in the active scene. When the same source (the same .blend and objects) is " +
                      "sent again, the model file this project already has is replaced in place instead: scene objects and " +
                      "prefabs that use it take the new shape and keep their place, and nothing new is added. Supports FBX and " +
                      "OBJ; GLB/glTF need the glTFast package.")]
    public static class ChImportModel
    {
        private const string ImportsFolder = "Assets/ClaudeHand/Imports";
        private const string ModelFormat = "claude-hand/model@1";
        private const string ImportLabel = "ClaudeHandImport";  // on every model that carries a record, so finding them is one indexed search
        private const int ReplacedKept = 10;
        private const int InstancesListed = 20;
        private static readonly string[] SupportedExtensions = { ".fbx", ".obj", ".glb", ".gltf" };
        private static readonly Regex TimestampSuffix = new Regex(@"_\d{8}_\d{6}$");

        public class Parameters
        {
            [ChParameter("Path of a .claudehand.json manifest (format claude-hand/model@1) or of the model file itself (.fbx, .obj, .glb). Relative paths are looked up in the exchange folder.")]
            public string manifest { get; set; }

            [ChParameter("Folder and file name under Assets/ClaudeHand/Imports for a new model (default: the transfer's name).", Required = false)]
            public string name { get; set; }

            [ChParameter("Place the model in the active scene. Default: yes for a new model; for one replaced in place, only when the open scenes hold no object that uses it.", Required = false)]
            public bool? place { get; set; }

            [ChParameter("Ref (from ch_scene_snapshot) of a GameObject to put the model under. Default: scene root.", Required = false)]
            public string parent { get; set; }

            [ChParameter("Import as a separate new model even though this project already has one from the same source (default false). Later re-sends leave the copy alone.", Required = false)]
            public bool? new_copy { get; set; }

            [ChParameter("embedded: keep the materials that come inside the model file. project: where this project has exactly one material of the same name, use that one instead. Default: embedded for a new model; for one replaced in place, what was chosen when it first came.", Required = false)]
            public string materials { get; set; }

            [ChParameter("Only report whether this would add a new model or replace one in place, and which; change nothing (default false).", Required = false)]
            public bool? dry_run { get; set; }
        }

        /// <summary>What is coming in: the file, and where it came from (what a re-send is recognized by).</summary>
        private sealed class Incoming
        {
            public string ModelPath;
            public string Extension;
            public string Name;
            public string Source;  // null: unknown (an unsaved .blend), so it never counts as a re-send
            public List<string> Roots = new List<string>();
        }

        /// <summary>
        /// Kept in the model's .meta (AssetImporter.userData), so it travels with the asset: moving or renaming the
        /// model in the Project window does not lose it, and there is no separate list to fall out of step.
        /// </summary>
        private sealed class Record
        {
            public string source;
            public List<string> roots = new List<string>();
            public string materials;
        }

        public static object HandleCommand(JObject @params)
        {
            var p = @params?.ToObject<Parameters>() ?? new Parameters();
            if (string.IsNullOrWhiteSpace(p.manifest))
            {
                return ChErrors.Make(ChErrors.InvalidArgs, "manifest is required.", "after_fix",
                    "Pass the manifest path the Blender session printed, or a model file path.");
            }

            var materials = (p.materials ?? string.Empty).Trim().ToLowerInvariant();
            if (materials.Length > 0 && materials != "embedded" && materials != "project")
            {
                return ChErrors.Make(ChErrors.InvalidArgs, "materials must be embedded or project.", "after_fix");
            }

            if (!TryResolveModel(p.manifest, out var incoming, out var resolveError)) return resolveError;

            var busy = ChErrors.BusyIfCompiling();
            if (busy != null) return busy;

            var existing = p.new_copy == true ? null : FindExisting(incoming);
            var name = ChPaths.Sanitize(string.IsNullOrWhiteSpace(p.name) ? incoming.Name : p.name);
            var folder = existing == null ? UniqueFolder($"{ImportsFolder}/{name}") : null;
            var assetPath = existing ?? $"{folder}/{name}{incoming.Extension}";

            if (p.dry_run == true)
            {
                return new ChSuccess("Dry run: nothing was changed.", new
                {
                    would_update = existing,
                    would_create = existing == null ? assetPath : null,
                    objects_in_open_scenes = existing == null ? 0 : InstancesInOpenScenes(AssetDatabase.LoadAssetAtPath<GameObject>(existing)).Count,
                    source = incoming.Source,
                });
            }

            Transform parent = null;
            if (p.place != false && !string.IsNullOrWhiteSpace(p.parent))
            {
                var parentGo = ChRefs.Resolve(p.parent, out var parentError);
                if (parentGo == null) return parentError;
                parent = parentGo.transform;
            }

            var warnings = new List<string>();
            string previous = null;
            try
            {
                var target = Path.Combine(ChPaths.ProjectRoot, assetPath);
                if (existing == null)
                {
                    ChPaths.EnsureAssetFolder(folder);
                }
                else
                {
                    previous = KeepReplaced(target);
                }
                File.Copy(incoming.ModelPath, target, true);
                AssetDatabase.ImportAsset(assetPath, ImportAssetOptions.ForceSynchronousImport | ImportAssetOptions.ForceUpdate);
            }
            catch (Exception ex)
            {
                return ChErrors.Make(ChErrors.Failed, "Could not copy or import the model: " + ex.Message, "check_first",
                    $"Look at {assetPath} before trying again; the file may already be there." +
                    (previous != null ? $" The file it replaced is kept at {previous}." : string.Empty));
            }

            var model = AssetDatabase.LoadAssetAtPath<GameObject>(assetPath);
            if (model == null)
            {
                var hint = incoming.Extension == ".glb"
                    ? "GLB/glTF needs the glTFast package (com.unity.cloud.gltfast). Install it, or export FBX from Blender instead."
                    : "Check read_console for the importer error.";
                return ChErrors.Make(ChErrors.Failed, $"Unity imported {assetPath} but it produced no model.", "check_first", hint,
                    new { asset_path = assetPath, previous_file = previous });
            }

            // The record, the label and the material links all live in the .meta: one more import applies them together.
            var importer = AssetImporter.GetAtPath(assetPath);
            var record = ReadRecord(importer);
            var mode = materials.Length > 0 ? materials : record?.materials ?? "embedded";
            bool dirty = false;
            var materialReport = mode == "project" ? UseProjectMaterials(importer as ModelImporter, model, assetPath, warnings, ref dirty) : null;
            if (incoming.Source != null && p.new_copy != true)
            {
                dirty |= WriteRecord(importer, model, new Record { source = incoming.Source, roots = incoming.Roots, materials = mode }, warnings);
            }
            if (dirty)
            {
                importer.SaveAndReimport();
                model = AssetDatabase.LoadAssetAtPath<GameObject>(assetPath);
            }

            bool updated = existing != null;
            var uses = updated ? InstancesInOpenScenes(model) : new List<GameObject>();
            bool place = p.place ?? uses.Count == 0;
            var builder = ChRefs.Begin();
            object placed = null;
            if (place)
            {
                var scene = SceneManager.GetActiveScene();
                var instance = parent != null
                    ? (GameObject)PrefabUtility.InstantiatePrefab(model, parent)
                    : (GameObject)PrefabUtility.InstantiatePrefab(model, scene);
                Undo.RegisterCreatedObjectUndo(instance, "Claude Hand import model");
                EditorSceneManager.MarkSceneDirty(instance.scene);
                var hierarchyPath = ChRefs.HierarchyPath(instance.transform);
                placed = new { @ref = builder.Add(instance, hierarchyPath), name = instance.name, path = hierarchyPath };
            }
            var instances = uses.Take(InstancesListed).Select(go =>
            {
                var hierarchyPath = ChRefs.HierarchyPath(go.transform);
                return new { @ref = builder.Add(go, hierarchyPath), name = go.name, path = hierarchyPath };
            }).ToList();
            if (place || instances.Count > 0) builder.Save();

            if (model.GetComponentsInChildren<Renderer>(true).Length == 0)
            {
                warnings.Add("The model has no renderers (no visible meshes). Check what was exported from Blender.");
            }

            string message;
            if (updated)
            {
                message = $"Replaced {assetPath} in place with the newer model from the same source. " +
                          (uses.Count > 0
                              ? $"{uses.Count} object(s) in the open scenes use it and show the new model now."
                              : place ? "No object in the open scenes used it, so one was placed. The scene is modified but not saved."
                                  : "No object in the open scenes uses it.") +
                          $" Undo does not cover the file itself; the one it replaced is kept at {previous}.";
            }
            else
            {
                message = place
                    ? $"Imported {assetPath} and placed it in the scene. The scene is modified but not saved."
                    : $"Imported {assetPath}.";
            }
            return new ChSuccess(message, new
            {
                asset_path = assetPath,
                updated,
                previous_file = previous,
                placed,
                instances,
                instance_count = uses.Count,
                materials = materialReport,
                warnings,
            });
        }

        // ------------------------------------------------------------------ what came before

        /// <summary>The model this project already has from the same source, if a re-send should replace it.</summary>
        private static string FindExisting(Incoming incoming)
        {
            if (incoming.Source == null) return null;
            foreach (var guid in AssetDatabase.FindAssets("l:" + ImportLabel))
            {
                var path = AssetDatabase.GUIDToAssetPath(guid);
                if (!path.StartsWith("Assets/", StringComparison.Ordinal)) continue;
                if (!string.Equals(Path.GetExtension(path), incoming.Extension, StringComparison.OrdinalIgnoreCase)) continue;
                if (!File.Exists(Path.Combine(ChPaths.ProjectRoot, path))) continue;
                var record = ReadRecord(AssetImporter.GetAtPath(path));
                if (record == null || !string.Equals(record.source, incoming.Source, StringComparison.OrdinalIgnoreCase)) continue;
                if ((record.roots ?? new List<string>()).SequenceEqual(incoming.Roots, StringComparer.Ordinal)) return path;
            }
            return null;
        }

        private static Record ReadRecord(AssetImporter importer)
        {
            if (importer == null || string.IsNullOrEmpty(importer.userData)) return null;
            try
            {
                return JObject.Parse(importer.userData)["claude_hand_import"]?.ToObject<Record>();
            }
            catch (Exception)
            {
                return null;  // some other tool's note
            }
        }

        /// <summary>Returns whether the .meta changed. Leaves userData alone when another tool already wrote its own there.</summary>
        private static bool WriteRecord(AssetImporter importer, GameObject model, Record record, List<string> warnings)
        {
            var text = JsonConvert.SerializeObject(new { claude_hand_import = record });
            if (importer.userData == text) return false;
            if (!string.IsNullOrEmpty(importer.userData) && ReadRecord(importer) == null)
            {
                warnings.Add("This model's import settings already hold another tool's note, so where it came from was not recorded; sending it again will add a new copy.");
                return false;
            }
            importer.userData = text;
            var labels = AssetDatabase.GetLabels(model);
            if (!labels.Contains(ImportLabel)) AssetDatabase.SetLabels(model, labels.Append(ImportLabel).ToArray());
            return true;
        }

        /// <summary>Copies the file about to be replaced to Library/ClaudeHand/replaced (the newest few are kept) and returns where.</summary>
        private static string KeepReplaced(string file)
        {
            var dir = Path.Combine(ChPaths.LibraryDir, "replaced");
            Directory.CreateDirectory(dir);
            var kept = Path.Combine(dir, $"{Path.GetFileNameWithoutExtension(file)}_{DateTime.Now:yyyyMMdd_HHmmss}{Path.GetExtension(file)}");
            File.Copy(file, kept, true);
            try
            {
                foreach (var old in new DirectoryInfo(dir).GetFiles().OrderByDescending(f => f.LastWriteTimeUtc).Skip(ReplacedKept)) old.Delete();
            }
            catch (Exception)
            {
                // housekeeping; never fail an import over it
            }
            return ChPaths.ToPosix(kept);
        }

        /// <summary>Roots of the prefab instances of this model in the loaded scenes (also ones nested in other prefabs).</summary>
        private static List<GameObject> InstancesInOpenScenes(GameObject model)
        {
            var found = new List<GameObject>();
            if (model == null) return found;
            for (int i = 0; i < SceneManager.sceneCount; i++)
            {
                var scene = SceneManager.GetSceneAt(i);
                if (!scene.isLoaded) continue;
                foreach (var root in scene.GetRootGameObjects())
                {
                    foreach (var transform in root.GetComponentsInChildren<Transform>(true))
                    {
                        var go = transform.gameObject;
                        if (PrefabUtility.IsAnyPrefabInstanceRoot(go) && PrefabUtility.GetCorrespondingObjectFromOriginalSource(go) == model) found.Add(go);
                    }
                }
            }
            return found;
        }

        // ------------------------------------------------------------------ materials

        /// <summary>
        /// Links each material the model brought along to the project material of the same name, where there is exactly
        /// one. A name the project has twice is left alone and reported: picking one silently would be a guess.
        /// </summary>
        private static object UseProjectMaterials(ModelImporter importer, GameObject model, string assetPath, List<string> warnings, ref bool dirty)
        {
            if (importer == null)
            {
                warnings.Add("materials=project works for FBX and OBJ; this model keeps the materials it came with.");
                return null;
            }

            var byName = new Dictionary<string, List<string>>(StringComparer.Ordinal);
            foreach (var guid in AssetDatabase.FindAssets("t:Material", new[] { "Assets" }))
            {
                var path = AssetDatabase.GUIDToAssetPath(guid);
                if (!path.EndsWith(".mat", StringComparison.OrdinalIgnoreCase)) continue;  // materials inside other models do not count
                var key = Path.GetFileNameWithoutExtension(path);
                if (!byName.TryGetValue(key, out var paths)) byName[key] = paths = new List<string>();
                if (!paths.Contains(path)) paths.Add(path);
            }

            var linked = new List<object>();
            var unmatched = new List<string>();
            var ambiguous = new List<object>();
            var used = model.GetComponentsInChildren<Renderer>(true).SelectMany(r => r.sharedMaterials).Where(m => m != null).Distinct();
            foreach (var material in used)
            {
                var from = AssetDatabase.GetAssetPath(material);
                if (!string.Equals(from, assetPath, StringComparison.Ordinal))
                {
                    // Linked when the model came before. (Unity's built-in default material is neither: the model named none.)
                    if (from.StartsWith("Assets/", StringComparison.Ordinal)) linked.Add(new { name = material.name, to = from });
                    continue;
                }
                if (!byName.TryGetValue(material.name, out var candidates))
                {
                    unmatched.Add(material.name);
                    continue;
                }
                if (candidates.Count > 1)
                {
                    ambiguous.Add(new { name = material.name, candidates });
                    continue;
                }
                var target = AssetDatabase.LoadAssetAtPath<Material>(candidates[0]);
                if (target == null)
                {
                    unmatched.Add(material.name);
                    continue;
                }
                importer.AddRemap(new AssetImporter.SourceAssetIdentifier(material), target);
                linked.Add(new { name = material.name, to = candidates[0] });
                dirty = true;
            }

            if (ambiguous.Count > 0)
            {
                warnings.Add($"{ambiguous.Count} material name(s) exist more than once in this project and were left as they came; see materials.ambiguous.");
            }
            return new { mode = "project", linked, unmatched, ambiguous };
        }

        // ------------------------------------------------------------------ the incoming file

        private static bool TryResolveModel(string input, out Incoming incoming, out ChError error)
        {
            incoming = new Incoming();
            error = null;
            var path = input.Trim().Trim('"');
            if (!Path.IsPathRooted(path)) path = Path.Combine(ChPaths.ExchangeDir, path);

            if (!File.Exists(path))
            {
                error = ChErrors.Make(ChErrors.NotFound, $"'{ChPaths.ToPosix(path)}' not found.", "after_fix",
                    "Use the exact path the Blender session printed. Transfers live in " + ChPaths.ToPosix(ChPaths.ExchangeDir) + ".");
                return false;
            }

            if (path.EndsWith(".claudehand.json", StringComparison.OrdinalIgnoreCase))
            {
                JObject manifest;
                try
                {
                    manifest = JObject.Parse(File.ReadAllText(path));
                }
                catch (Exception ex)
                {
                    error = ChErrors.Make(ChErrors.InvalidArgs, "Manifest is not valid JSON: " + ex.Message, "after_fix");
                    return false;
                }

                var format = manifest.Value<string>("format");
                if (format != ModelFormat)
                {
                    error = ChErrors.Make(ChErrors.InvalidArgs, $"This manifest is '{format}', not a model.", "after_fix",
                        format == ChTransferManifest.CurrentFormat ? "Unity-to-Unity transfers go through ch_import_package." : "Export the model again from Blender.");
                    return false;
                }

                var file = manifest.Value<string>("model_file");
                incoming.ModelPath = Path.Combine(Path.GetDirectoryName(path) ?? string.Empty, file ?? string.Empty);
                if (string.IsNullOrEmpty(file) || !File.Exists(incoming.ModelPath))
                {
                    error = ChErrors.Make(ChErrors.NotFound, $"Model file '{file}' from the manifest is missing.", "after_fix",
                        "Export it again from Blender.");
                    return false;
                }
                incoming.Name = manifest.Value<string>("name");
                if (string.IsNullOrWhiteSpace(incoming.Name))  // manifests from before 0.9 carry the name only inside the id
                {
                    incoming.Name = TimestampSuffix.Replace(manifest.Value<string>("transfer_id") ?? Path.GetFileNameWithoutExtension(file), string.Empty);
                }
                var source = manifest.Value<string>("source");
                incoming.Source = string.IsNullOrWhiteSpace(source) ? null : ChPaths.ToPosix(source.Trim());
                incoming.Roots = (manifest["roots"] as JArray)?.Select(r => r.ToString()).OrderBy(r => r, StringComparer.Ordinal).ToList() ?? new List<string>();
            }
            else
            {
                incoming.ModelPath = path;
                incoming.Name = Path.GetFileNameWithoutExtension(path);
                incoming.Source = "file:" + ChPaths.ToPosix(Path.GetFullPath(path));  // the same file given again is a re-send too
            }

            incoming.Extension = Path.GetExtension(incoming.ModelPath).ToLowerInvariant();
            if (!SupportedExtensions.Contains(incoming.Extension))
            {
                error = ChErrors.Make(ChErrors.InvalidArgs, $"'{incoming.Extension}' files are not supported here.", "after_fix",
                    "Export FBX from Blender (the add-on's export_fbx does that).");
                return false;
            }
            if (incoming.Extension == ".gltf")
            {
                error = ChErrors.Make(ChErrors.InvalidArgs, ".gltf keeps buffers and textures in separate files.", "after_fix",
                    "Export a single-file .glb or an .fbx instead.");
                return false;
            }
            return true;
        }

        private static string UniqueFolder(string folder)
        {
            var candidate = folder;
            for (int n = 2; AssetDatabase.IsValidFolder(candidate); n++) candidate = $"{folder}_{n}";
            return candidate;
        }
    }
}
