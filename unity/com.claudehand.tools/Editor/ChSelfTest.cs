using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

namespace ClaudeHand.Editor
{
    /// <summary>
    /// Runs the tools that move model files for real, inside Unity, without a window:
    ///   Unity -batchmode -projectPath &lt;project&gt; -executeMethod ClaudeHand.Editor.ChSelfTest.Run
    /// `claude_hand.py selftest --unity-project &lt;path&gt;` does that for a project that is closed, and hands in two
    /// versions of a model that a real Blender exported (CLAUDE_HAND_SELFTEST_MODEL_A / _B); without them two small OBJ
    /// files stand in. It works in a new scene that is never saved, and deletes the assets it made when it is done.
    /// The result goes to CLAUDE_HAND_SELFTEST_OUT (default Library/ClaudeHand/selftest.json); the exit code is 1 when
    /// a check failed.
    /// </summary>
    public static class ChSelfTest
    {
        private const string Name = "ch_selftest";
        private const string Work = "Assets/ClaudeHand/SelfTest";

        public static void Run()
        {
            var checks = new List<object>();
            bool failed = false;
            void Check(string name, bool ok, string detail = "")
            {
                checks.Add(new { name, ok, detail });
                failed |= !ok;
            }

            string exported = null;
            try
            {
                EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
                var manifestA = Environment.GetEnvironmentVariable("CLAUDE_HAND_SELFTEST_MODEL_A");
                var manifestB = Environment.GetEnvironmentVariable("CLAUDE_HAND_SELFTEST_MODEL_B");
                bool fromBlender = !string.IsNullOrEmpty(manifestA) && !string.IsNullOrEmpty(manifestB);
                if (!fromBlender)
                {
                    manifestA = WriteObj("a", 1);
                    manifestB = WriteObj("b", 2);
                }

                // 1. A model that was never here: a new asset, placed in the scene.
                var first = Call(ChImportModel.HandleCommand, new { manifest = manifestA });
                var assetPath = first["data"]?.Value<string>("asset_path");
                Check("new model: imported and placed", Ok(first) && first["data"].Value<bool>("updated") == false && first["data"]["placed"].HasValues,
                    Describe(first));
                if (!Ok(first)) return;
                Check("new model: named after the transfer, without its time", assetPath.StartsWith($"Assets/ClaudeHand/Imports/{Name}/{Name}."), assetPath);
                var guid = AssetDatabase.AssetPathToGUID(assetPath);
                var instance = ChRefs.Resolve(first["data"]["placed"].Value<string>("ref"), out _);
                var meshBefore = instance != null ? instance.GetComponentInChildren<MeshFilter>()?.sharedMesh : null;
                int verticesBefore = meshBefore != null ? meshBefore.vertexCount : 0;
                var embedded = instance != null ? instance.GetComponentInChildren<Renderer>()?.sharedMaterial : null;
                bool hasMaterial = embedded != null && AssetDatabase.GetAssetPath(embedded) == assetPath;

                // A project material with the name the model's own material has.
                string projectMaterial = null;
                if (hasMaterial)
                {
                    ChPaths.EnsureAssetFolder(Work);
                    projectMaterial = $"{Work}/{embedded.name}.mat";
                    AssetDatabase.CreateAsset(new Material(embedded.shader), projectMaterial);
                }

                // 2. The same source again: says so beforehand, then replaces the file in place.
                var dry = Call(ChImportModel.HandleCommand, new { manifest = manifestB, dry_run = true });
                Check("same source again: dry run names the model it would replace", Ok(dry) && dry["data"].Value<string>("would_update") == assetPath, Describe(dry));

                var second = Call(ChImportModel.HandleCommand, new { manifest = manifestB, materials = "project" });
                var after = second["data"];
                Check("same source again: replaced in place", Ok(second) && after.Value<bool>("updated") && after.Value<string>("asset_path") == assetPath &&
                      AssetDatabase.AssetPathToGUID(assetPath) == guid, Describe(second));
                Check("same source again: the scene object stays, none is added",
                    Ok(second) && after.Value<int>("instance_count") == 1 && !after["placed"].HasValues &&
                    EditorSceneManager.GetActiveScene().rootCount == 1, Describe(second));
                var meshAfter = instance != null ? instance.GetComponentInChildren<MeshFilter>()?.sharedMesh : null;
                Check("same source again: the scene object shows the new mesh", meshAfter != null && meshAfter.vertexCount > verticesBefore,
                    $"{verticesBefore} -> {(meshAfter != null ? meshAfter.vertexCount : 0)} vertices");
                var previous = Ok(second) ? after.Value<string>("previous_file") : null;
                Check("same source again: the replaced file is kept", !string.IsNullOrEmpty(previous) && File.Exists(previous), previous ?? "");

                if (hasMaterial)
                {
                    var used = instance.GetComponentInChildren<Renderer>().sharedMaterial;
                    Check("materials=project: linked to the project material of the same name",
                        Ok(second) && AssetDatabase.GetAssetPath(used) == projectMaterial, $"{AssetDatabase.GetAssetPath(used)}; {after?["materials"]}");
                    var third = Call(ChImportModel.HandleCommand, new { manifest = manifestB });
                    Check("materials: a later re-send keeps the choice", Ok(third) && third["data"]["materials"].HasValues &&
                          AssetDatabase.GetAssetPath(instance.GetComponentInChildren<Renderer>().sharedMaterial) == projectMaterial, Describe(third));
                    var back = Call(ChImportModel.HandleCommand, new { manifest = manifestB, materials = "embedded" });
                    Check("materials=embedded: back to the model's own material", Ok(back) &&
                          AssetDatabase.GetAssetPath(instance.GetComponentInChildren<Renderer>().sharedMaterial) == assetPath,
                        AssetDatabase.GetAssetPath(instance.GetComponentInChildren<Renderer>().sharedMaterial));
                }
                else
                {
                    Check("materials=project", true, "skipped: the test model brought no material of its own");
                }

                // 3. A separate copy when asked for; the next re-send still goes to the first one.
                var copy = Call(ChImportModel.HandleCommand, new { manifest = manifestB, new_copy = true, place = false });
                var copyPath = copy["data"]?.Value<string>("asset_path");
                Check("new_copy: a separate model", Ok(copy) && copy["data"].Value<bool>("updated") == false && copyPath != assetPath, Describe(copy));
                var again = Call(ChImportModel.HandleCommand, new { manifest = manifestB, dry_run = true });
                Check("new_copy: later re-sends leave the copy alone", Ok(again) && again["data"].Value<string>("would_update") == assetPath, Describe(again));

                // 4. Back out: a scene object that comes from a model file is sent as that file.
                var builder = ChRefs.Begin();
                var reference = builder.Add(instance, ChRefs.HierarchyPath(instance.transform));
                builder.Save();
                var sent = Call(ChExportModel.HandleCommand, new { refs = new[] { reference }, name = Name + "_out" });
                exported = sent["data"]?.Value<string>("manifest_path");
                Check("export: the model file goes to the exchange folder", Ok(sent) && sent["data"].Value<string>("via") == "source_file" &&
                      File.Exists(sent["data"].Value<string>("model_path")) && File.Exists(exported), Describe(sent));

                var cube = GameObject.CreatePrimitive(PrimitiveType.Cube);
                builder = ChRefs.Begin();
                reference = builder.Add(cube, cube.name);
                builder.Save();
                var made = Call(ChExportModel.HandleCommand, new { refs = new[] { reference } });
                bool hasExporter = Type.GetType("UnityEditor.Formats.Fbx.Exporter.ModelExporter, Unity.Formats.Fbx.Editor") != null;
                Check(hasExporter ? "export: a mesh made in Unity is written by FBX Exporter" : "export: a mesh made in Unity says FBX Exporter is needed",
                    hasExporter ? Ok(made) && File.Exists(made["data"].Value<string>("model_path"))
                                : !Ok(made) && (made["data"]?.Value<string>("hint") ?? "").Contains("com.unity.formats.fbx"), Describe(made));
            }
            catch (Exception ex)
            {
                Check("ran to the end", false, ex.ToString());
            }
            finally
            {
                try
                {
                    foreach (var folder in AssetDatabase.GetSubFolders("Assets/ClaudeHand/Imports").Where(f => Path.GetFileName(f).StartsWith(Name)))
                    {
                        AssetDatabase.DeleteAsset(folder);
                    }
                    AssetDatabase.DeleteAsset(Work);
                    ChPaths.DeleteAssetFolderIfEmpty("Assets/ClaudeHand/Imports");
                    ChPaths.DeleteAssetFolderIfEmpty("Assets/ClaudeHand");
                }
                catch (Exception ex)
                {
                    Check("cleaned up", false, ex.Message);
                }

                var output = Environment.GetEnvironmentVariable("CLAUDE_HAND_SELFTEST_OUT");
                if (string.IsNullOrEmpty(output)) output = Path.Combine(ChPaths.LibraryDir, "selftest.json");
                Directory.CreateDirectory(Path.GetDirectoryName(output));
                File.WriteAllText(output, JsonConvert.SerializeObject(new
                {
                    ok = !failed,
                    unity = Application.unityVersion,
                    exported_manifest = exported,
                    checks,
                }, Formatting.Indented));
                if (Application.isBatchMode) EditorApplication.Exit(failed ? 1 : 0);
            }
        }

        private static JObject Call(Func<JObject, object> tool, object parameters)
        {
            return JObject.FromObject(tool(JObject.FromObject(parameters)));
        }

        private static bool Ok(JObject result) => result.Value<bool?>("success") == true;

        private static string Describe(JObject result)
        {
            var text = result.ToString(Formatting.None);
            return text.Length > 600 ? text.Substring(0, 600) + "..." : text;
        }

        /// <summary>A stand-in for what Blender's export_fbx leaves in the exchange folder: `quads` squares, one material.</summary>
        private static string WriteObj(string version, int quads)
        {
            var lines = new List<string> { "o ChSelfTest" };
            for (int q = 0; q < quads; q++)
            {
                lines.AddRange(new[] { $"v {q * 2} 0 0", $"v {q * 2 + 1} 0 0", $"v {q * 2 + 1} 1 0", $"v {q * 2} 1 0" });
            }
            lines.Add("usemtl ChSelfTestMat");
            for (int q = 0; q < quads; q++) lines.Add($"f {q * 4 + 1} {q * 4 + 2} {q * 4 + 3} {q * 4 + 4}");
            var id = $"{Name}_{version}";
            File.WriteAllLines(Path.Combine(ChPaths.ExchangeDir, id + ".obj"), lines);
            var manifest = Path.Combine(ChPaths.ExchangeDir, id + ".claudehand.json");
            File.WriteAllText(manifest, JsonConvert.SerializeObject(new
            {
                format = "claude-hand/model@1",
                name = Name,
                transfer_id = id,
                model_file = id + ".obj",
                source = "selftest/ch_selftest.blend",
                roots = new[] { "ChSelfTest" },
            }));
            return manifest;
        }
    }
}
