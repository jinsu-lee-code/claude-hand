using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace ClaudeHand.Editor
{
    [McpForUnityTool("ch_import_package",
        Description = "Import a transfer made by ch_export_objects in another project: checks it first, imports the .unitypackage, " +
                      "waits for Unity to finish importing and compiling, then (by default) places the exported prefabs in the " +
                      "active scene and returns refs to them. Run with dry_run=true first to see what would be added or overwritten.",
        RequiresPolling = true,
        PollAction = "status",
        MaxPollSeconds = 600)]
    public static class ChImportPackage
    {
        private const string ToolName = "ch_import_package";
        private const string TransfersFolder = "Assets/ClaudeHand/Transfers/";
        private const double ImportTimeoutSeconds = 540;
        private const double SettleSeconds = 3;

        public class Parameters
        {
            [ToolParameter("Path of the .claudehand.json manifest from ch_export_objects (the .unitypackage path next to it also works).")]
            public string manifest { get; set; }

            [ToolParameter("Only report what the import would add or overwrite; change nothing (default false).", Required = false)]
            public bool? dry_run { get; set; }

            [ToolParameter("Place the exported prefabs in the active scene after importing (default true).", Required = false)]
            public bool? place { get; set; }

            [ToolParameter("Ref (from ch_scene_snapshot) of a GameObject to put the placed prefabs under. Default: scene root.", Required = false)]
            public string parent { get; set; }

            [ToolParameter("Go ahead even though some assets already exist in this project and would be replaced (default false).", Required = false)]
            public bool? overwrite { get; set; }
        }

        private sealed class State
        {
            public string manifest_path;
            public string transfer_id;
            public string stage; // importing | placing | done | error
            public string started_utc;
            public string assets_ready_utc;
            public bool place;
            public string parent_ref;
            public string error;
            public List<string> warnings = new List<string>();
            public List<Placed> placed = new List<Placed>();
        }

        private sealed class Placed
        {
            [JsonProperty("ref")] public string reference;
            public string name;
            public string path;
            public string prefab;
        }

        private sealed class Plan
        {
            public string transfer_id;
            public string source_project;
            public int asset_count;
            public List<string> new_assets = new List<string>();
            public List<string> already_here = new List<string>();
            public List<string> would_overwrite = new List<string>();
            public List<object> same_guid_elsewhere = new List<object>();
            public List<string> scripts = new List<string>();
            public List<string> missing_packages = new List<string>();
            public List<string> prefabs_to_place = new List<string>();
            public List<string> warnings = new List<string>();

            [JsonIgnore] public bool NeedsOverwrite => would_overwrite.Count > 0 || same_guid_elsewhere.Count > 0;
            [JsonIgnore] public bool NothingToImport => new_assets.Count == 0 && would_overwrite.Count == 0 && same_guid_elsewhere.Count == 0;
        }

        public static object HandleCommand(JObject @params)
        {
            if (string.Equals(@params?["action"]?.ToString(), "status", StringComparison.OrdinalIgnoreCase)) return Status();

            var p = @params?.ToObject<Parameters>() ?? new Parameters();
            if (string.IsNullOrWhiteSpace(p.manifest))
            {
                return ChErrors.Make(ChErrors.InvalidArgs, "manifest is required.", "after_fix",
                    "Pass the manifest_path that ch_export_objects returned.");
            }

            if (!TryLoadManifest(p.manifest, out var manifest, out var manifestPath, out var loadError)) return loadError;
            var packagePath = Path.Combine(Path.GetDirectoryName(manifestPath) ?? string.Empty, manifest.package_file);
            if (!File.Exists(packagePath))
            {
                return ChErrors.Make(ChErrors.NotFound, $"Package file '{ChPaths.ToPosix(packagePath)}' is missing.", "after_fix",
                    "Export again from the source project.");
            }

            var plan = MakePlan(manifest);
            if (p.dry_run == true) return new SuccessResponse("Dry run: nothing was changed.", plan);

            if (plan.NeedsOverwrite && p.overwrite != true)
            {
                return ChErrors.Make(ChErrors.Conflict, "Some assets in this transfer already exist in this project and would be replaced.", "after_fix",
                    "Review would_overwrite and same_guid_elsewhere. If they came from an earlier attempt of this same transfer, or replacing them is intended, call again with overwrite=true.",
                    plan);
            }

            var busy = ChErrors.BusyIfCompiling();
            if (busy != null) return busy;

            var running = McpJobStateStore.LoadState<State>(ToolName);
            if (running != null && (running.stage == "importing" || running.stage == "placing") && SecondsSince(running.started_utc) < ImportTimeoutSeconds)
            {
                return ChErrors.Make(ChErrors.EditorBusy, $"Another import ({running.transfer_id}) is still running.", "safe",
                    "Wait for it to finish, then call again.");
            }

            bool place = p.place ?? true;
            if (place && !string.IsNullOrWhiteSpace(p.parent))
            {
                var parent = ChRefs.Resolve(p.parent, out var parentError);
                if (parent == null) return parentError;
            }

            var state = new State
            {
                manifest_path = manifestPath,
                transfer_id = manifest.transfer_id,
                stage = "importing",
                started_utc = DateTime.UtcNow.ToString("o"),
                place = place,
                parent_ref = p.parent,
                warnings = plan.warnings.ToList(),
            };
            McpJobStateStore.SaveState(ToolName, state);

            if (!plan.NothingToImport)
            {
                // interactive=false: no dialog; the import finishes on later editor ticks and may trigger a domain reload.
                AssetDatabase.ImportPackage(packagePath, false);
            }

            return new PendingResponse($"Importing {manifest.transfer_id} ({plan.asset_count} assets)...", 1.0, new { manifest.transfer_id, stage = state.stage });
        }

        private static object Status()
        {
            var state = McpJobStateStore.LoadState<State>(ToolName);
            if (state == null) return new { _mcp_status = "error", error = "No ch_import_package job found." };
            if (state.stage == "done") return Complete(state);
            if (state.stage == "error") return new { _mcp_status = "error", error = state.error, data = state };

            if (EditorApplication.isCompiling || EditorApplication.isUpdating)
            {
                return new PendingResponse("Unity is importing/compiling...", 1.0, new { state.transfer_id, state.stage });
            }

            if (!TryLoadManifest(state.manifest_path, out var manifest, out _, out _))
            {
                return Fail(state, "The manifest file disappeared during the import.");
            }

            var missing = manifest.assets.Where(a => !IsPresent(a.guid)).Select(a => a.path).ToList();
            if (missing.Count > 0)
            {
                if (SecondsSince(state.started_utc) > ImportTimeoutSeconds)
                {
                    return Fail(state, $"Import did not finish: {missing.Count} asset(s) never appeared, e.g. {missing[0]}.");
                }
                return new PendingResponse($"Waiting for assets ({manifest.assets.Count - missing.Count}/{manifest.assets.Count})...", 1.0,
                    new { state.transfer_id, missing = missing.Count });
            }

            // Script compilation starts a moment after the files land; give it time to begin before placing anything.
            if (string.IsNullOrEmpty(state.assets_ready_utc))
            {
                state.assets_ready_utc = DateTime.UtcNow.ToString("o");
                McpJobStateStore.SaveState(ToolName, state);
                return new PendingResponse("Assets imported; waiting for Unity to settle...", 1.0, new { state.transfer_id });
            }
            if (manifest.scripts.Count > 0 && SecondsSince(state.assets_ready_utc) < SettleSeconds)
            {
                return new PendingResponse("Waiting for script compilation...", 1.0, new { state.transfer_id });
            }

            if (manifest.scripts.Count > 0 && EditorUtility.scriptCompilationFailed)
            {
                state.warnings.Add("The project has script compilation errors after the import; imported components may not work. Check read_console.");
            }

            if (state.place && state.stage != "done")
            {
                state.stage = "placing";
                McpJobStateStore.SaveState(ToolName, state);
                PlacePrefabs(state, manifest);
            }

            state.stage = "done";
            McpJobStateStore.SaveState(ToolName, state);
            return Complete(state);
        }

        private static void PlacePrefabs(State state, ChTransferManifest manifest)
        {
            var scene = SceneManager.GetActiveScene();
            Transform parent = null;
            if (!string.IsNullOrWhiteSpace(state.parent_ref))
            {
                var parentGo = ChRefs.Resolve(state.parent_ref, out _);
                if (parentGo != null) parent = parentGo.transform;
                else state.warnings.Add($"Parent {state.parent_ref} no longer exists; placed at the scene root instead.");
            }

            var builder = ChRefs.Begin();
            foreach (var root in manifest.roots.Where(r => r.kind == "prefab"))
            {
                var assetPath = AssetDatabase.GUIDToAssetPath(root.guid);
                var prefab = AssetDatabase.LoadAssetAtPath<GameObject>(assetPath);
                if (prefab == null)
                {
                    state.warnings.Add($"Prefab for '{root.source_name}' was not found after import.");
                    continue;
                }

                var instance = parent != null
                    ? (GameObject)PrefabUtility.InstantiatePrefab(prefab, parent)
                    : (GameObject)PrefabUtility.InstantiatePrefab(prefab, scene);
                Undo.RegisterCreatedObjectUndo(instance, "Claude Hand import " + manifest.transfer_id);
                var hierarchyPath = ChRefs.HierarchyPath(instance.transform);
                state.placed.Add(new Placed
                {
                    reference = builder.Add(instance, hierarchyPath),
                    name = instance.name,
                    path = hierarchyPath,
                    prefab = assetPath,
                });
            }

            builder.Save();
            if (state.placed.Count > 0) EditorSceneManager.MarkSceneDirty(parent != null ? parent.gameObject.scene : scene);
        }

        private static object Complete(State state)
        {
            var message = state.placed.Count > 0
                ? $"Imported {state.transfer_id} and placed {state.placed.Count} object(s). The scene is modified but not saved."
                : $"Imported {state.transfer_id}.";
            return new
            {
                _mcp_status = "complete",
                message,
                data = new { state.transfer_id, placed = state.placed, state.warnings },
            };
        }

        private static object Fail(State state, string error)
        {
            state.stage = "error";
            state.error = error;
            McpJobStateStore.SaveState(ToolName, state);
            return new { _mcp_status = "error", error, data = new { state.transfer_id, retry = "check_first" } };
        }

        private static Plan MakePlan(ChTransferManifest manifest)
        {
            var plan = new Plan
            {
                transfer_id = manifest.transfer_id,
                source_project = manifest.source?.project,
                asset_count = manifest.assets.Count,
                scripts = manifest.scripts.ToList(),
                prefabs_to_place = manifest.roots.Where(r => r.kind == "prefab").Select(r => r.path).ToList(),
                warnings = manifest.warnings.ToList(),
            };

            foreach (var asset in manifest.assets)
            {
                var existingPath = IsPresent(asset.guid) ? AssetDatabase.GUIDToAssetPath(asset.guid) : null;
                bool inTransferFolder = asset.path.StartsWith(TransfersFolder, StringComparison.Ordinal);
                if (string.IsNullOrEmpty(existingPath))
                {
                    if (AssetDatabase.LoadMainAssetAtPath(asset.path) != null)
                    {
                        plan.warnings.Add($"{asset.path} exists here as a different asset; Unity will import the incoming one under a new name.");
                    }
                    plan.new_assets.Add(asset.path);
                }
                else if (!string.Equals(existingPath, asset.path, StringComparison.Ordinal))
                {
                    plan.same_guid_elsewhere.Add(new { incoming = asset.path, existing = existingPath });
                }
                else if (inTransferFolder)
                {
                    plan.already_here.Add(asset.path); // left by an earlier attempt of this same transfer
                }
                else
                {
                    plan.would_overwrite.Add(asset.path);
                }
            }

            foreach (var dependency in manifest.package_dependencies)
            {
                var name = dependency.Split('@')[0];
                if (!AssetDatabase.IsValidFolder("Packages/" + name)) plan.missing_packages.Add(dependency);
            }
            if (plan.missing_packages.Count > 0)
            {
                plan.warnings.Add("Install the missing_packages first (Window > Package Manager), or some assets will show missing shaders/scripts.");
            }
            if (Application.unityVersion != manifest.source?.unity_version)
            {
                plan.warnings.Add($"Exported from Unity {manifest.source?.unity_version}; this editor is {Application.unityVersion}.");
            }
            return plan;
        }

        private static bool TryLoadManifest(string input, out ChTransferManifest manifest, out string manifestPath, out ErrorResponse error)
        {
            manifest = null;
            error = null;
            manifestPath = input.Trim().Trim('"');
            if (manifestPath.EndsWith(".unitypackage", StringComparison.OrdinalIgnoreCase))
            {
                manifestPath = manifestPath.Substring(0, manifestPath.Length - ".unitypackage".Length) + ".claudehand.json";
            }
            if (!Path.IsPathRooted(manifestPath)) manifestPath = Path.Combine(ChPaths.ExchangeDir, manifestPath);

            if (!File.Exists(manifestPath))
            {
                error = ChErrors.Make(ChErrors.NotFound, $"Manifest '{ChPaths.ToPosix(manifestPath)}' not found.", "after_fix",
                    "Use the manifest_path returned by ch_export_objects. Transfers live in " + ChPaths.ToPosix(ChPaths.ExchangeDir) + ".");
                return false;
            }

            try
            {
                manifest = JsonConvert.DeserializeObject<ChTransferManifest>(File.ReadAllText(manifestPath));
            }
            catch (Exception ex)
            {
                error = ChErrors.Make(ChErrors.InvalidArgs, "Manifest is not valid JSON: " + ex.Message, "after_fix");
                return false;
            }

            if (manifest == null || manifest.format != ChTransferManifest.CurrentFormat)
            {
                error = ChErrors.Make(ChErrors.InvalidArgs, $"Unsupported manifest format '{manifest?.format}'.", "after_fix",
                    "Export again with the same version of Claude Hand Tools in both projects.");
                return false;
            }
            return true;
        }

        private static bool IsPresent(string guid)
        {
            if (string.IsNullOrEmpty(guid)) return false;
            var path = AssetDatabase.GUIDToAssetPath(guid);
            return !string.IsNullOrEmpty(path) && AssetDatabase.LoadMainAssetAtPath(path) != null;
        }

        private static double SecondsSince(string isoUtc)
        {
            return DateTime.TryParse(isoUtc, null, System.Globalization.DateTimeStyles.RoundtripKind, out var t)
                ? (DateTime.UtcNow - t.ToUniversalTime()).TotalSeconds
                : double.MaxValue;
        }
    }
}
