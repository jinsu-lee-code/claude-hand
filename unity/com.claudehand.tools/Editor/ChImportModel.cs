using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace ClaudeHand.Editor
{
    [ChTool("ch_import_model",
        Description = "Bring a 3D model file into this project, typically one a Blender session exported to the exchange folder " +
                      "(manifest format claude-hand/model@1). Copies it under Assets/ClaudeHand/Imports/<name>/, imports it, and by " +
                      "default places it in the active scene and returns its ref. Supports FBX and OBJ; GLB/glTF need the glTFast package.")]
    public static class ChImportModel
    {
        private const string ImportsFolder = "Assets/ClaudeHand/Imports";
        private const string ModelFormat = "claude-hand/model@1";
        private static readonly string[] SupportedExtensions = { ".fbx", ".obj", ".glb", ".gltf" };

        public class Parameters
        {
            [ChParameter("Path of a .claudehand.json manifest (format claude-hand/model@1) or of the model file itself (.fbx, .obj, .glb). Relative paths are looked up in the exchange folder.")]
            public string manifest { get; set; }

            [ChParameter("Folder name under Assets/ClaudeHand/Imports (default: the transfer id or file name).", Required = false)]
            public string name { get; set; }

            [ChParameter("Place the model in the active scene after importing (default true).", Required = false)]
            public bool? place { get; set; }

            [ChParameter("Ref (from ch_scene_snapshot) of a GameObject to put the model under. Default: scene root.", Required = false)]
            public string parent { get; set; }
        }

        public static object HandleCommand(JObject @params)
        {
            var p = @params?.ToObject<Parameters>() ?? new Parameters();
            if (string.IsNullOrWhiteSpace(p.manifest))
            {
                return ChErrors.Make(ChErrors.InvalidArgs, "manifest is required.", "after_fix",
                    "Pass the manifest path the Blender session printed, or a model file path.");
            }

            if (!TryResolveModel(p.manifest, out var modelPath, out var defaultName, out var resolveError)) return resolveError;

            var busy = ChErrors.BusyIfCompiling();
            if (busy != null) return busy;

            bool place = p.place ?? true;
            Transform parent = null;
            if (place && !string.IsNullOrWhiteSpace(p.parent))
            {
                var parentGo = ChRefs.Resolve(p.parent, out var parentError);
                if (parentGo == null) return parentError;
                parent = parentGo.transform;
            }

            var folder = UniqueFolder($"{ImportsFolder}/{ChPaths.Sanitize(string.IsNullOrWhiteSpace(p.name) ? defaultName : p.name)}");
            var assetPath = $"{folder}/{Path.GetFileName(modelPath)}";
            var warnings = new List<string>();

            try
            {
                ChPaths.EnsureAssetFolder(folder);
                File.Copy(modelPath, Path.Combine(ChPaths.ProjectRoot, assetPath));
                AssetDatabase.ImportAsset(assetPath, ImportAssetOptions.ForceSynchronousImport | ImportAssetOptions.ForceUpdate);
            }
            catch (Exception ex)
            {
                return ChErrors.Make(ChErrors.Failed, "Could not copy or import the model: " + ex.Message, "check_first",
                    $"Look under {folder} before trying again; the file may already be there.");
            }

            var model = AssetDatabase.LoadAssetAtPath<GameObject>(assetPath);
            if (model == null)
            {
                var hint = assetPath.EndsWith(".glb", StringComparison.OrdinalIgnoreCase) || assetPath.EndsWith(".gltf", StringComparison.OrdinalIgnoreCase)
                    ? "GLB/glTF needs the glTFast package (com.unity.cloud.gltfast). Install it, or export FBX from Blender instead."
                    : "Check read_console for the importer error.";
                return ChErrors.Make(ChErrors.Failed, $"Unity imported {assetPath} but it produced no model.", "check_first", hint,
                    new { asset_path = assetPath });
            }

            object placed = null;
            if (place)
            {
                var scene = SceneManager.GetActiveScene();
                var instance = parent != null
                    ? (GameObject)PrefabUtility.InstantiatePrefab(model, parent)
                    : (GameObject)PrefabUtility.InstantiatePrefab(model, scene);
                Undo.RegisterCreatedObjectUndo(instance, "Claude Hand import model");
                EditorSceneManager.MarkSceneDirty(instance.scene);
                var builder = ChRefs.Begin();
                var hierarchyPath = ChRefs.HierarchyPath(instance.transform);
                placed = new { @ref = builder.Add(instance, hierarchyPath), name = instance.name, path = hierarchyPath };
                builder.Save();
            }

            if (model.GetComponentsInChildren<Renderer>(true).Length == 0)
            {
                warnings.Add("The model has no renderers (no visible meshes). Check what was exported from Blender.");
            }

            return new ChSuccess(place
                    ? $"Imported {assetPath} and placed it in the scene. The scene is modified but not saved."
                    : $"Imported {assetPath}.",
                new { asset_path = assetPath, placed, warnings });
        }

        private static bool TryResolveModel(string input, out string modelPath, out string defaultName, out ChError error)
        {
            modelPath = null;
            defaultName = null;
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
                modelPath = Path.Combine(Path.GetDirectoryName(path) ?? string.Empty, file ?? string.Empty);
                defaultName = manifest.Value<string>("transfer_id") ?? Path.GetFileNameWithoutExtension(file);
                if (string.IsNullOrEmpty(file) || !File.Exists(modelPath))
                {
                    error = ChErrors.Make(ChErrors.NotFound, $"Model file '{file}' from the manifest is missing.", "after_fix",
                        "Export it again from Blender.");
                    return false;
                }
            }
            else
            {
                modelPath = path;
                defaultName = Path.GetFileNameWithoutExtension(path);
            }

            var extension = Path.GetExtension(modelPath).ToLowerInvariant();
            if (!SupportedExtensions.Contains(extension))
            {
                error = ChErrors.Make(ChErrors.InvalidArgs, $"'{extension}' files are not supported here.", "after_fix",
                    "Export FBX from Blender (the 'Unity로 보내기' recipe does that).");
                return false;
            }
            if (extension == ".gltf")
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
