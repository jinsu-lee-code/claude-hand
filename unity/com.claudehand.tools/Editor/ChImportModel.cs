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
                      "prefabs that use it take the new shape and keep their place, and nothing new is added. Undoing the " +
                      "request (ch_checkpoint undo, the chat window's 되돌리기) takes the files back too: a model that came " +
                      "in is taken out of the project, one that was replaced gets its earlier file back. Supports FBX and OBJ; " +
                      "GLB/glTF need the glTFast package.")]
    public static class ChImportModel
    {
        private const string ImportsFolder = "Assets/ClaudeHand/Imports";
        private const string ModelFormat = "claude-hand/model@1";
        private const string ImportLabel = "ClaudeHandImport";  // on every model that carries a record, so finding them is one indexed search
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

            [ChParameter("embedded: the model's own materials. For a manifest from Blender (0.17 and later) they are made from what it says of each material (colour, metallic, roughness, normal, emission and their images), as this project's kind of material, in Materials/ and Textures/ next to the model; one that was edited here since is left alone. project: where this project has exactly one material of the same name, use that one instead. Default: embedded for a new model; for one replaced in place, what was chosen when it first came.", Required = false)]
            public string materials { get; set; }

            [ChParameter("humanoid: set the rig up as Unity's Humanoid (the bones must be a person's, in T-pose; falls back to generic with a warning when Unity finds no match). generic: Unity's Generic. Default: what the manifest says, else as the model is.", Required = false)]
            public string rig { get; set; }

            [ChParameter("Only report whether this would add a new model or replace one in place, and which; change nothing (default false).", Required = false)]
            public bool? dry_run { get; set; }
        }

        /// <summary>What is coming in: the file, and where it came from (what a re-send is recognized by).</summary>
        internal sealed class Incoming
        {
            public string ModelPath;
            public string Extension;
            public string Name;
            public string Source;  // null: unknown (an unsaved .blend), so it never counts as a re-send
            public List<string> Roots = new List<string>();

            // What a manifest from Blender says besides the file (0.17 and later; see ChModelLook).
            public string Axes;           // "blender": the file is in Blender's own axes, and Unity is to bake the conversion
            public string Rig;            // "humanoid" / "generic"; null: as the model is set
            public JArray Materials;      // what each material is made of
            public string MaterialsHash;  // of that, images included: what a model that follows is compared by
            public string TexturesDir;    // where the materials' image files are
            public List<string> NotCarried = new List<string>();  // what of the materials Blender could not put into words
            public Dictionary<string, string> Clips;  // take name -> clip name, of the takes that are this model's; null: all
            public List<string> Baked = new List<string>();  // the material inputs that came as an image baked from their nodes
            public JObject Check;  // where each object is and how far each mesh reaches, as Blender measured (ChModelLook.ShapeCheck)
        }

        /// <summary>
        /// Kept in the model's .meta (AssetImporter.userData), so it travels with the asset: moving or renaming the
        /// model in the Project window does not lose it, and there is no separate list to fall out of step.
        /// </summary>
        internal sealed class Record
        {
            public string source;
            public List<string> roots = new List<string>();
            public string materials;

            /// <summary>
            /// The model follows its .blend: when that is saved, Blender's Claude Hand panel sends the model again by
            /// itself and it is replaced here in place (ch_model_follow). Kept on this side, with the model: what gets
            /// changed is this project, so the say is here, and a model that is deleted takes it along.
            /// </summary>
            [JsonProperty(DefaultValueHandling = DefaultValueHandling.Ignore)]
            public bool follow;

            /// <summary>The manifest's hash of the materials this model has now (Blender compares its own with it).</summary>
            [JsonProperty(NullValueHandling = NullValueHandling.Ignore)]
            public string materials_hash;

            /// <summary>
            /// The materials made for this model from the manifest: name -> hash of the .mat as it was written. One
            /// whose file differs from that has been edited in this project since, and is not written over.
            /// </summary>
            [JsonProperty(NullValueHandling = NullValueHandling.Ignore)]
            public Dictionary<string, string> generated;
        }

        public static object HandleCommand(JObject @params)
        {
            return Import(@params?.ToObject<Parameters>() ?? new Parameters(), false);
        }

        /// <summary>follow: the model follows its .blend from now on (sent from Blender's panel, ch_model_follow receive).</summary>
        internal static object Import(Parameters p, bool follow)
        {
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

            var rig = (p.rig ?? string.Empty).Trim().ToLowerInvariant();
            if (rig.Length > 0 && rig != "humanoid" && rig != "generic")
            {
                return ChErrors.Make(ChErrors.InvalidArgs, "rig must be humanoid or generic.", "after_fix");
            }

            if (!TryResolveModel(p.manifest, out var incoming, out var resolveError)) return resolveError;
            if (rig.Length > 0) incoming.Rig = rig;  // the caller's word over the manifest's

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
            bool undoable = false;
            try
            {
                var target = Path.Combine(ChPaths.ProjectRoot, assetPath);
                if (existing == null)
                {
                    ChPaths.EnsureAssetFolder(folder);
                }
                else
                {
                    previous = ChCheckpoint.Keep(target);
                }
                File.Copy(incoming.ModelPath, target, true);
                // Unity's Undo knows nothing of files. Written down with the request's undo point, so that 되돌리기
                // takes this back too: the folder that came in is taken out of the project, the file that was replaced returns.
                undoable = existing == null
                    ? ChCheckpoint.NoteAdded(folder, AssetDatabase.AssetPathToGUID(folder))
                    : ChCheckpoint.NoteReplaced(assetPath, previous);
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
            bool dirty = ChModelLook.Settings(importer as ModelImporter, incoming);
            var materialReport = mode == "project" ? UseProjectMaterials(importer as ModelImporter, model, assetPath, warnings, ref dirty) : null;
            var unlinked = new List<string>();
            var generated = record?.generated;
            object made = null;
            if (mode == "embedded" && record?.materials != "project")
            {
                generated = ChModelLook.Materials(importer as ModelImporter, incoming, assetPath, generated, warnings, ref dirty, out made);
            }
            if (mode == "embedded" && record?.materials == "project" && importer is ModelImporter linked)
            {
                // Asked to go back to the model's own materials: drop the links an earlier materials=project made.
                foreach (var id in linked.GetExternalObjectMap().Keys.Where(k => k.type == typeof(Material)).ToList())
                {
                    if (!linked.RemoveRemap(id)) continue;
                    dirty = true;
                    unlinked.Add(id.name);
                }
                materialReport = new { mode = "embedded", linked = new List<object>(), unmatched = new List<string>(), ambiguous = new List<object>(), unlinked };
                // The model's own materials again, made from the manifest like a new model's.
                generated = ChModelLook.Materials(importer as ModelImporter, incoming, assetPath, generated, warnings, ref dirty, out made);
            }
            if (incoming.Source != null && p.new_copy != true)
            {
                dirty |= WriteRecord(importer, model, new Record { source = incoming.Source, roots = incoming.Roots, materials = mode,
                                                                   follow = follow || record?.follow == true, generated = generated,
                                                                   materials_hash = incoming.MaterialsHash ?? record?.materials_hash }, warnings);
            }
            if (dirty)
            {
                importer.SaveAndReimport();
                if (ChModelLook.RigFailed(importer as ModelImporter, assetPath, warnings)) importer.SaveAndReimport();
                model = AssetDatabase.LoadAssetAtPath<GameObject>(assetPath);
            }

            var shape = ChModelLook.ShapeCheck(incoming, assetPath, warnings);
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
                var controller = updated ? null : ChModelLook.Controller(assetPath);
                if (controller != null)
                {
                    // A model with clips, placed for the first time: it moves when the scene runs.
                    var animator = instance.GetComponent<Animator>();
                    if (animator == null) animator = Undo.AddComponent<Animator>(instance);
                    if (animator.runtimeAnimatorController == null) animator.runtimeAnimatorController = controller;
                }
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
                          (undoable
                              ? $" The chat window's 되돌리기 (ch_checkpoint undo) puts the file it replaced back; it is kept at {previous}."
                              : $" No undo point is set, so 되돌리기 will not put the file it replaced back; it is kept at {previous}.");
            }
            else
            {
                message = place
                    ? $"Imported {assetPath} and placed it in the scene. The scene is modified but not saved."
                    : $"Imported {assetPath}.";
            }
            if (unlinked.Count > 0)
            {
                message += $" It uses its own materials again: {unlinked.Count} link(s) to project materials were removed.";
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
                materials_made = made,
                // What Blender could not say of the materials (an input fed through other nodes): tell the user.
                not_carried = incoming.NotCarried,
                // Material inputs that came as an image baked from their nodes: as the nodes were then, no longer nodes.
                baked = incoming.Baked,
                // The imported model held against what Blender measured: same, or which parts differ.
                shape_check = shape,
                warnings,
            });
        }

        /// <summary>
        /// The model at `assetPath` gets the file that came in, in place, because its .blend was saved (ch_model_follow
        /// update). That is the user's own doing, not a request's: nothing is noted for an undo, nothing is placed in
        /// the scene, and the file it replaces is kept apart from the requests' files. Returns where that file is kept.
        /// `materials`: which materials were made and which were left as edited here (null when none came along).
        /// </summary>
        internal static string ReplaceFollowed(Incoming incoming, string assetPath, List<string> warnings, out object materials)
        {
            materials = null;
            using (ChCheckpoint.UsersOwn(assetPath))
            {
                var target = Path.Combine(ChPaths.ProjectRoot, assetPath);
                var previous = ChCheckpoint.KeepFollowed(target);
                File.Copy(incoming.ModelPath, target, true);
                AssetDatabase.ImportAsset(assetPath, ImportAssetOptions.ForceSynchronousImport | ImportAssetOptions.ForceUpdate);
                var importer = AssetImporter.GetAtPath(assetPath);
                var model = AssetDatabase.LoadAssetAtPath<GameObject>(assetPath);
                var record = ReadRecord(importer);
                if (model == null || record == null) return previous;
                bool dirty = ChModelLook.Settings(importer as ModelImporter, incoming);
                if (record.materials == "project")
                {
                    // A material the model has only now gets its project material too, as chosen when the model first came.
                    UseProjectMaterials(importer as ModelImporter, model, assetPath, warnings, ref dirty);
                }
                else
                {
                    record.generated = ChModelLook.Materials(importer as ModelImporter, incoming, assetPath, record.generated, warnings, ref dirty,
                                                             out materials, ChCheckpoint.AlsoUsersOwn);
                }
                record.materials_hash = incoming.MaterialsHash ?? record.materials_hash;
                dirty |= WriteRecord(importer, model, record, warnings);
                if (dirty)
                {
                    importer.SaveAndReimport();
                    if (ChModelLook.RigFailed(importer as ModelImporter, assetPath, warnings)) importer.SaveAndReimport();
                }
                return previous;
            }
        }

        // ------------------------------------------------------------------ what came before

        /// <summary>Every model here that carries a record of where it came from, with that record.</summary>
        internal static IEnumerable<KeyValuePair<string, Record>> Recorded()
        {
            foreach (var guid in AssetDatabase.FindAssets("l:" + ImportLabel))
            {
                var path = AssetDatabase.GUIDToAssetPath(guid);
                if (!path.StartsWith("Assets/", StringComparison.Ordinal)) continue;
                if (!File.Exists(Path.Combine(ChPaths.ProjectRoot, path))) continue;
                var record = ReadRecord(AssetImporter.GetAtPath(path));
                if (record != null) yield return new KeyValuePair<string, Record>(path, record);
            }
        }

        /// <summary>The model this project already has from the same source, if a re-send should replace it.</summary>
        internal static string FindExisting(Incoming incoming)
        {
            if (incoming.Source == null) return null;
            foreach (var found in Recorded())
            {
                if (!string.Equals(Path.GetExtension(found.Key), incoming.Extension, StringComparison.OrdinalIgnoreCase)) continue;
                if (!string.Equals(found.Value.source, incoming.Source, StringComparison.OrdinalIgnoreCase)) continue;
                if ((found.Value.roots ?? new List<string>()).SequenceEqual(incoming.Roots, StringComparer.Ordinal)) return found.Key;
            }
            return null;
        }

        internal static Record ReadRecord(AssetImporter importer)
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
        internal static bool WriteRecord(AssetImporter importer, GameObject model, Record record, List<string> warnings)
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

        /// <summary>Roots of the prefab instances of this model in the loaded scenes (also ones nested in other prefabs).</summary>
        internal static List<GameObject> InstancesInOpenScenes(GameObject model)
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

            // The materials made for this model from its manifest (ChModelLook) are its own, like the ones inside the file.
            var own = (Path.GetDirectoryName(assetPath)?.Replace('\\', '/') ?? "Assets") + "/Materials/";
            var byName = new Dictionary<string, List<string>>(StringComparer.Ordinal);
            foreach (var guid in AssetDatabase.FindAssets("t:Material", new[] { "Assets" }))
            {
                var path = AssetDatabase.GUIDToAssetPath(guid);
                if (!path.EndsWith(".mat", StringComparison.OrdinalIgnoreCase)) continue;  // materials inside other models do not count
                if (path.StartsWith(own, StringComparison.Ordinal)) continue;
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
                if (!string.Equals(from, assetPath, StringComparison.Ordinal) && !from.StartsWith(own, StringComparison.Ordinal))
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

        internal static bool TryResolveModel(string input, out Incoming incoming, out ChError error)
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
                incoming.Axes = manifest.Value<string>("axes");
                incoming.Rig = manifest.Value<string>("rig");
                incoming.Materials = manifest["materials"] as JArray;
                incoming.MaterialsHash = manifest.Value<string>("materials_hash");
                var textures = manifest.Value<string>("textures_dir");
                incoming.TexturesDir = string.IsNullOrEmpty(textures) ? null : Path.Combine(Path.GetDirectoryName(path) ?? string.Empty, textures);
                incoming.NotCarried = (manifest["not_carried"] as JArray)?.Select(n => n.ToString()).ToList() ?? new List<string>();
                incoming.Baked = (manifest["baked"] as JArray)?.Select(n => n.ToString()).ToList() ?? new List<string>();
                incoming.Check = manifest["check"] as JObject;
                incoming.Clips = (manifest["clips"] as JArray)?.OfType<JObject>()
                    .Where(c => !string.IsNullOrEmpty(c.Value<string>("take")) && !string.IsNullOrEmpty(c.Value<string>("name")))
                    .GroupBy(c => c.Value<string>("take")).ToDictionary(g => g.Key, g => g.First().Value<string>("name"));
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
