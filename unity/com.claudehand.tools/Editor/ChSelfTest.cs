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
    /// Runs the tools that move files for real, inside Unity, without a window:
    ///   Unity -batchmode -projectPath &lt;project&gt; -executeMethod ClaudeHand.Editor.ChSelfTest.Run
    /// `claude_hand.py selftest --unity-project &lt;path&gt;` does that for a project that is closed, and hands in two
    /// versions of a model that a real Blender exported (CLAUDE_HAND_SELFTEST_MODEL_A / _B); without them two small OBJ
    /// files stand in. It works in a new scene that is never saved, and deletes the assets it made when it is done
    /// (what its undo checks take out of the project stays in Library/ClaudeHand/removed: a few small folders).
    /// The result goes to CLAUDE_HAND_SELFTEST_OUT (default Library/ClaudeHand/selftest.json); the exit code is 1 when
    /// a check failed.
    /// </summary>
    public static class ChSelfTest
    {
        private const string Name = "ch_selftest";
        private const string Work = "Assets/ClaudeHand/SelfTest";
        private const string Transfers = "Assets/ClaudeHand/Transfers";
        private const double PackageSeconds = 120;
        // The same two names in tools/handlib/checks.py, which makes the guard hook's copy of the file beforehand.
        private const string FilesRequest = "selftest-files";
        private const string FilesDirect = Work + "/ChSelfTestDirect.txt";

        private static readonly List<object> Checks = new List<object>();
        private static bool _failed;
        private static string _exported;

        // The package checks go on over editor ticks (the import is a job that ch_import_package is polled for).
        private static string _packageMaterial;
        private static byte[] _materialBefore;
        private static int _rootsBefore;
        private static double _packageDeadline;
        private static double _nextPoll;

        // ... and so do the scene checks after them.
        private const double SceneSeconds = 10;
        private static int _sceneStep;
        private static int _marksBefore;
        private static GameObject _sceneCube;
        private static double _sceneDeadline;

        // ... and the checks of models that follow their .blend end a few ticks later.
        private const string FollowRequest = "selftest-follow";
        private const string FollowSource = "selftest/ch_selftest_follow.blend";
        private static int _followTicks, _followStep;
        private static string _followPath, _followOne, _followTwo;

        // Serve: how long the editor waits for the Blender that is to send it a model.
        private const double ServeSeconds = 240;
        private static string _serveStop;
        private static double _serveDeadline;

        private static void Check(string name, bool ok, string detail = "")
        {
            Checks.Add(new { name, ok, detail });
            _failed |= !ok;
        }

        public static void Run()
        {
            Checks.Clear();
            _failed = false;
            bool waiting = false;
            try
            {
                ModelChecks();
                waiting = StartPackageChecks();
            }
            catch (Exception ex)
            {
                Check("ran to the end", false, ex.ToString());
            }
            if (!waiting) Finish();
        }

        private static void ModelChecks()
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
            // The model's own material: inside the file, or (from Blender, 0.17 and later) made next to it from the manifest.
            var ownMaterial = embedded != null ? AssetDatabase.GetAssetPath(embedded) : null;
            var madeHere = Path.GetDirectoryName(assetPath).Replace('\\', '/') + "/Materials/";
            bool hasMaterial = ownMaterial == assetPath || (ownMaterial != null && ownMaterial.StartsWith(madeHere));
            if (fromBlender)
            {
                var settings = AssetImporter.GetAtPath(assetPath) as ModelImporter;
                Check("from Blender: the axis conversion is baked in (no part gets a quarter turn)", settings != null && settings.bakeAxisConversion,
                    settings != null ? $"bakeAxisConversion {settings.bakeAxisConversion}" : "no model importer");
                Check("from Blender: its material is made as this project's kind, next to the model",
                    ownMaterial != null && ownMaterial.StartsWith(madeHere) && first["data"]["materials_made"].HasValues,
                    $"{ownMaterial}; {first["data"]["materials_made"]}");
                var measured = first["data"]["shape_check"];
                Check("from Blender: the model is where and as large as Blender measured it", measured != null && measured.HasValues && measured.Value<bool>("same"),
                    $"{measured}");
                // The same file with a wrong measure next to it: said, not passed over.
                var wrong = JObject.Parse(File.ReadAllText(manifestA));
                var part = (wrong["check"]?["parts"] as JObject)?.Properties().FirstOrDefault();
                if (part != null)
                {
                    part.Value["at"][0] = (float)part.Value["at"][0] + 0.5f;
                    var wrongManifest = Path.Combine(Path.GetDirectoryName(manifestA), "ch_selftest_off.claudehand.json");
                    File.WriteAllText(wrongManifest, wrong.ToString());
                    var off = Call(ChImportModel.HandleCommand, new { manifest = wrongManifest, new_copy = true, place = false, name = Name + "_off" });
                    var seen = off["data"]?["shape_check"];
                    Check("from Blender: a model that is not where Blender measured it is told of",
                        Ok(off) && seen != null && seen.HasValues && !seen.Value<bool>("same") && seen["differing"].HasValues &&
                        off["data"]["warnings"].Values<string>().Any(w => w.Contains("not what Blender sent")), Describe(off));
                    var offPath = off["data"]?.Value<string>("asset_path");
                    if (!string.IsNullOrEmpty(offPath)) AssetDatabase.DeleteAsset(offPath.Substring(0, offPath.LastIndexOf('/')));
                    File.Delete(wrongManifest);
                }
            }

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
                      AssetDatabase.GetAssetPath(instance.GetComponentInChildren<Renderer>().sharedMaterial) == ownMaterial,
                    AssetDatabase.GetAssetPath(instance.GetComponentInChildren<Renderer>().sharedMaterial));
                Check("materials=embedded: the answer says which links went", Ok(back) && back["data"]["materials"].HasValues &&
                      back["data"]["materials"].Value<string>("mode") == "embedded" &&
                      back["data"]["materials"]["unlinked"].Values<string>().Contains(used.name), Describe(back));
                // Replaced three times within the same second by now: each file it replaced is still there.
                var keptFiles = new[] { second, third, back }.Select(r => Ok(r) ? r["data"].Value<string>("previous_file") : null).ToList();
                Check("replaced again right away: every replaced file is kept under its own name",
                    keptFiles.All(f => !string.IsNullOrEmpty(f) && File.Exists(f)) && keptFiles.Distinct().Count() == keptFiles.Count,
                    string.Join(", ", keptFiles));
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
            _exported = sent["data"]?.Value<string>("manifest_path");
            Check("export: the model file goes to the exchange folder", Ok(sent) && sent["data"].Value<string>("via") == "source_file" &&
                  File.Exists(sent["data"].Value<string>("model_path")) && File.Exists(_exported), Describe(sent));

            var cube = GameObject.CreatePrimitive(PrimitiveType.Cube);
            builder = ChRefs.Begin();
            reference = builder.Add(cube, cube.name);
            builder.Save();
            var made = Call(ChExportModel.HandleCommand, new { refs = new[] { reference } });
            bool hasExporter = Type.GetType("UnityEditor.Formats.Fbx.Exporter.ModelExporter, Unity.Formats.Fbx.Editor") != null;
            Check(hasExporter ? "export: a mesh made in Unity is written by FBX Exporter" : "export: a mesh made in Unity says FBX Exporter is needed",
                hasExporter ? Ok(made) && File.Exists(made["data"].Value<string>("model_path"))
                            : !Ok(made) && (made["data"]?.Value<string>("hint") ?? "").Contains("com.unity.formats.fbx"), Describe(made));

            // 5. Undoing a request takes back what it did to files too, which Unity's own Undo knows nothing of.
            var modelFile = Path.Combine(ChPaths.ProjectRoot, assetPath);
            var bytesBefore = File.ReadAllBytes(modelFile);
            var recordBefore = AssetImporter.GetAtPath(assetPath).userData;
            int verticesNow = instance.GetComponentInChildren<MeshFilter>().sharedMesh.vertexCount;
            int rootsBefore = EditorSceneManager.GetActiveScene().rootCount;
            var marked = Call(ChCheckpoint.HandleCommand, new { action = "mark", label = "self-test" });
            var resent = Call(ChImportModel.HandleCommand, new { manifest = manifestA, materials = hasMaterial ? "project" : "embedded" });
            var extra = Call(ChImportModel.HandleCommand, new { manifest = manifestA, new_copy = true, name = Name + "_undo" });
            var extraPath = extra["data"]?.Value<string>("asset_path") ?? string.Empty;
            var extraFolder = extraPath.Length > 0 ? extraPath.Substring(0, extraPath.LastIndexOf('/')) : string.Empty;
            Check("undo: set up (one model replaced, another brought in and placed)",
                Ok(marked) && Ok(resent) && Ok(extra) && resent["data"].Value<bool>("updated") && !File.ReadAllBytes(modelFile).SequenceEqual(bytesBefore) &&
                AssetDatabase.IsValidFolder(extraFolder) && EditorSceneManager.GetActiveScene().rootCount == rootsBefore + 1 &&
                resent.Value<string>("message").Contains("puts the file it replaced back"), Describe(resent) + " / " + Describe(extra));
            var undone = Call(ChCheckpoint.HandleCommand, new { action = "undo" });
            var meshBack = instance != null ? instance.GetComponentInChildren<MeshFilter>()?.sharedMesh : null;
            Check("undo: the replaced model file is back, with the import settings it had",
                Ok(undone) && File.ReadAllBytes(modelFile).SequenceEqual(bytesBefore) && AssetDatabase.AssetPathToGUID(assetPath) == guid &&
                AssetImporter.GetAtPath(assetPath).userData == recordBefore && undone["data"]["restored"].Values<string>().Contains(assetPath) &&
                (!hasMaterial || AssetDatabase.GetAssetPath(instance.GetComponentInChildren<Renderer>().sharedMaterial) == ownMaterial),
                Describe(undone) + " / " + AssetImporter.GetAtPath(assetPath).userData);
            Check("undo: the scene object shows the earlier mesh again", meshBack != null && meshBack.vertexCount == verticesNow,
                $"{verticesNow} -> {(meshBack != null ? meshBack.vertexCount : 0)} vertices");
            Check("undo: the model that came in is out of the project, kept with its .meta, and its scene object is gone",
                Ok(undone) && !AssetDatabase.IsValidFolder(extraFolder) && !Directory.Exists(Path.Combine(ChPaths.ProjectRoot, extraFolder)) &&
                EditorSceneManager.GetActiveScene().rootCount == rootsBefore && undone["data"]["removed"].Values<string>().Contains(extraFolder) &&
                KeptOutside(extraFolder) && undone.Value<string>("message").Contains(ChCheckpoint.RemovedFolder),
                Describe(undone));
            Check("undo: nothing is left to undo, and a later import says so", ChCheckpoint.Marks.Count == 0 &&
                  Call(ChImportModel.HandleCommand, new { manifest = manifestB }).Value<string>("message").Contains("No undo point is set"));

            // 6. A request that only looked leaves a mark that nothing happened after. It has nothing to undo: it is
            // not offered, an undo passes it by, and the next request's mark takes its place.
            Call(ChCheckpoint.HandleCommand, new { action = "mark", label = "self-test changed" });
            var kept = Call(ChImportModel.HandleCommand, new { manifest = manifestA, new_copy = true, place = false, name = Name + "_looked" });
            var keptFolder = Ok(kept) ? kept["data"].Value<string>("asset_path") : string.Empty;
            keptFolder = keptFolder.Length > 0 ? keptFolder.Substring(0, keptFolder.LastIndexOf('/')) : keptFolder;
            Call(ChCheckpoint.HandleCommand, new { action = "mark", label = "self-test looked" });
            var listed = Call(ChCheckpoint.HandleCommand, new { action = "list" });
            Check("a mark nothing happened after: not offered", Ok(kept) && ChCheckpoint.Marks.Count == 2 && ChCheckpoint.Undoable.Count == 1 &&
                  listed["data"]["marks"].Count() == 1 && listed["data"]["marks"][0].Value<string>("label") == "self-test changed" &&
                  listed.Value<string>("message").Contains("skipped"), Describe(listed));
            var passed = Call(ChCheckpoint.HandleCommand, new { action = "undo" });
            Check("a mark nothing happened after: an undo passes it by", Ok(passed) && passed["data"].Value<string>("label") == "self-test changed" &&
                  ChCheckpoint.Marks.Count == 0 && !AssetDatabase.IsValidFolder(keptFolder), Describe(passed));
            Call(ChCheckpoint.HandleCommand, new { action = "mark", label = "self-test looked" });
            Call(ChCheckpoint.HandleCommand, new { action = "mark", label = "self-test looked again" });
            var none = Call(ChCheckpoint.HandleCommand, new { action = "undo" });
            Check("a mark nothing happened after: the next one takes its place", ChCheckpoint.Marks.Count == 1 &&
                  ChCheckpoint.Marks[0].label == "self-test looked again" && !Ok(none), Describe(none));
        }

        /// <summary>
        /// 6. Objects between projects, here out of this project and back in: the package brings a prefab this project
        /// does not have (the exporter removed its own copy) and a material it has, changed since. Returns whether the
        /// import started; PackageTick takes it from there.
        /// </summary>
        private static bool StartPackageChecks()
        {
            ChPaths.EnsureAssetFolder(Work);
            _packageMaterial = $"{Work}/ChSelfTestPackage.mat";
            var material = new Material(Shader.Find("Universal Render Pipeline/Lit") ?? Shader.Find("Standard"));
            AssetDatabase.CreateAsset(material, _packageMaterial);
            var cube = GameObject.CreatePrimitive(PrimitiveType.Cube);
            cube.name = "ChSelfTestPackage";
            cube.GetComponent<Renderer>().sharedMaterial = material;
            var builder = ChRefs.Begin();
            var reference = builder.Add(cube, cube.name);
            builder.Save();
            var sent = Call(ChExportObjects.HandleCommand, new { refs = new[] { reference }, name = Name + "_package" });
            Check("package: a scene object goes out with the material it uses", Ok(sent) && File.Exists(sent["data"].Value<string>("package_path")) &&
                  sent["data"].Value<int>("asset_count") >= 2, Describe(sent));
            UnityEngine.Object.DestroyImmediate(cube);
            if (!Ok(sent)) return false;

            // The project's material changes after the export, so the package would write the exported one over it.
            material.enableInstancing = !material.enableInstancing;
            EditorUtility.SetDirty(material);
            AssetDatabase.SaveAssets();
            _materialBefore = File.ReadAllBytes(Path.Combine(ChPaths.ProjectRoot, _packageMaterial));
            _rootsBefore = EditorSceneManager.GetActiveScene().rootCount;

            var manifest = sent["data"].Value<string>("manifest_path");
            Call(ChCheckpoint.HandleCommand, new { action = "mark", label = "self-test package" });
            var refused = Call(ChImportPackage.HandleCommand, new { manifest });
            Check("package: an asset already here is not written over unasked", !Ok(refused) && File.ReadAllBytes(
                      Path.Combine(ChPaths.ProjectRoot, _packageMaterial)).SequenceEqual(_materialBefore), Describe(refused));
            var started = Call(ChImportPackage.HandleCommand, new { manifest, overwrite = true });
            if (started.Value<string>("_mcp_status") != "pending")
            {
                Check("package: the import starts", false, Describe(started));
                return false;
            }
            _packageDeadline = EditorApplication.timeSinceStartup + PackageSeconds;
            EditorApplication.update += PackageTick;
            return true;
        }

        private static void PackageTick()
        {
            if (EditorApplication.timeSinceStartup < _nextPoll) return;
            _nextPoll = EditorApplication.timeSinceStartup + 0.5;
            try
            {
                var status = Call(ChImportPackage.HandleCommand, new { action = "status" });
                if (status.Value<string>("_mcp_status") == "pending" && EditorApplication.timeSinceStartup < _packageDeadline) return;
                EditorApplication.update -= PackageTick;
                FinishPackageChecks(status);
                _sceneStep = 0;
                EditorApplication.update += SceneTick;  // from the next tick on: what this one changed is told at its end
                return;
            }
            catch (Exception ex)
            {
                EditorApplication.update -= PackageTick;
                Check("package: ran to the end", false, ex.ToString());
            }
            Finish();
        }

        /// <summary>
        /// 8. A change in the scene alone, with no file touched: its mark has something to undo once the editor has
        /// told what changed (by the end of the tick it happened in, so this goes over ticks as well), and undoing
        /// the mark takes the change back.
        /// </summary>
        private static void SceneTick()
        {
            try
            {
                if (_sceneStep == 0)
                {
                    _rootsBefore = EditorSceneManager.GetActiveScene().rootCount;
                    _marksBefore = ChCheckpoint.Marks.Count;  // the one step 6 left may still be there: assets came in since
                    Call(ChCheckpoint.HandleCommand, new { action = "mark", label = "self-test scene" });
                    _sceneCube = GameObject.CreatePrimitive(PrimitiveType.Cube);
                    _sceneCube.name = "ChSelfTestScene";
                    Undo.RegisterCreatedObjectUndo(_sceneCube, "Create ChSelfTestScene");
                    _sceneDeadline = EditorApplication.timeSinceStartup + SceneSeconds;
                    _sceneStep = 1;
                    return;
                }
                bool told = ChCheckpoint.Undoable.Any(m => m.label == "self-test scene");
                if (!told && EditorApplication.timeSinceStartup < _sceneDeadline) return;
                EditorApplication.update -= SceneTick;
                Check("a change in the scene: its mark has something to undo", told,
                    $"{ChCheckpoint.Marks.Count} mark(s), {ChCheckpoint.Undoable.Count} with something to undo");
                var undone = Call(ChCheckpoint.HandleCommand, new { action = "undo" });
                Check("a change in the scene: undone with its mark", told && Ok(undone) && _sceneCube == null &&
                      EditorSceneManager.GetActiveScene().rootCount == _rootsBefore && ChCheckpoint.Marks.Count == _marksBefore, Describe(undone));
                if (_sceneCube != null) UnityEngine.Object.DestroyImmediate(_sceneCube);
                FileChecks();
                RequestChecks();
                if (FollowChecks())
                {
                    _followTicks = _followStep = 0;
                    EditorApplication.update += FollowTick;
                    return;
                }
            }
            catch (Exception ex)
            {
                EditorApplication.update -= SceneTick;
                Check("scene and files: ran to the end", false, ex.ToString());
            }
            Finish();
        }

        /// <summary>
        /// 9. What a request does to files with any tool, taken back with it: an asset made, one saved through Unity,
        /// a file written over directly, an asset deleted, one renamed. No guard hook runs here, so the test says
        /// itself that a request is at work (ChCheckpoint.SetWork), and the copy the hook makes of a file a call
        /// names was made by `claude_hand.py selftest` with the hook's own code, before Unity started. An asset made
        /// once the work is over is not the request's, and stays.
        /// </summary>
        private static void FileChecks()
        {
            ChPaths.EnsureAssetFolder(Work);
            var shader = Shader.Find("Universal Render Pipeline/Lit") ?? Shader.Find("Standard");
            string saved = $"{Work}/ChSelfTestSaved.mat", gone = $"{Work}/ChSelfTestDeleted.mat", renamed = $"{Work}/ChSelfTestRenamed.mat";
            string renamedTo = $"{Work}/ChSelfTestRenamedTo.mat", made = $"{Work}/ChSelfTestMade.mat", byHand = $"{Work}/ChSelfTestByHand.mat";
            foreach (var path in new[] { saved, gone, renamed }) AssetDatabase.CreateAsset(new Material(shader), path);
            AssetDatabase.SaveAssets();
            Func<string, string> file = path => Path.Combine(ChPaths.ProjectRoot, path);
            var savedBefore = File.ReadAllBytes(file(saved));
            string goneGuid = AssetDatabase.AssetPathToGUID(gone), renamedGuid = AssetDatabase.AssetPathToGUID(renamed);
            bool copied = File.Exists(file(FilesDirect));  // there only when the selftest command prepared it, with the hook's copy

            JObject marked, staged;
            bool deleted;
            string renameError;
            try
            {
                ChCheckpoint.SetWork(FilesRequest);
                marked = Call(ChCheckpoint.HandleCommand, new { action = "mark", label = "self-test files", request = FilesRequest });
                AssetDatabase.CreateAsset(new Material(shader), made);
                var material = AssetDatabase.LoadAssetAtPath<Material>(saved);
                material.enableInstancing = !material.enableInstancing;
                EditorUtility.SetDirty(material);
                AssetDatabase.SaveAssets();
                if (copied)
                {
                    File.WriteAllText(file(FilesDirect), "after");  // past Unity, as MCP for Unity writes a script
                    AssetDatabase.ImportAsset(FilesDirect, ImportAssetOptions.ForceSynchronousImport | ImportAssetOptions.ForceUpdate);
                }
                deleted = AssetDatabase.DeleteAsset(gone);
                renameError = AssetDatabase.RenameAsset(renamed, Path.GetFileNameWithoutExtension(renamedTo));
                // An export stages prefabs in the project and removes them again: this package's own business, not the request's.
                var cube = GameObject.CreatePrimitive(PrimitiveType.Cube);
                cube.name = "ChSelfTestStaged";
                var builder = ChRefs.Begin();
                var reference = builder.Add(cube, cube.name);
                builder.Save();
                staged = Call(ChExportObjects.HandleCommand, new { refs = new[] { reference }, name = Name + "_staged" });
                UnityEngine.Object.DestroyImmediate(cube);
            }
            finally
            {
                ChCheckpoint.SetWork(null);
            }
            AssetDatabase.CreateAsset(new Material(shader), byHand);  // the work is over: this one is the user's own

            Check("files: set up (an asset made, one saved, one deleted, one renamed while a request is at work)",
                Ok(marked) && File.Exists(file(made)) && !File.ReadAllBytes(file(saved)).SequenceEqual(savedBefore) && deleted &&
                !File.Exists(file(gone)) && string.IsNullOrEmpty(renameError) && File.Exists(file(renamedTo)) && !File.Exists(file(renamed)),
                $"{Describe(marked)} / deleted {deleted} / rename '{renameError}'");
            var undone = Call(ChCheckpoint.HandleCommand, new { action = "undo" });
            var data = undone["data"];
            Func<string, string, bool> told = (list, path) => Ok(undone) && data[list].Values<string>().Contains(path);
            Check("files: an asset the request made is out of the project, kept with its .meta",
                told("removed", made) && !File.Exists(file(made)) && AssetDatabase.LoadAssetAtPath<Material>(made) == null && KeptOutside(made),
                Describe(undone));
            Check("files: an asset saved through Unity is as it was before",
                told("restored", saved) && File.ReadAllBytes(file(saved)).SequenceEqual(savedBefore), Describe(undone));
            Check("files: a file written over directly is back from the copy the guard hook made",
                !copied || (told("restored", FilesDirect) && File.ReadAllText(file(FilesDirect)) == "before"),
                copied ? Describe(undone) : "skipped: `claude_hand.py selftest --unity-project` makes the copy before Unity starts");
            Check("files: a deleted asset is back with its GUID",
                told("returned", gone) && File.Exists(file(gone)) && AssetDatabase.AssetPathToGUID(gone) == goneGuid &&
                AssetDatabase.LoadAssetAtPath<Material>(gone) != null, Describe(undone));
            var named = AssetDatabase.LoadAssetAtPath<Material>(renamed);
            Check("files: a renamed asset has its name back, in its file too, with its GUID",
                told("moved", renamed) && File.Exists(file(renamed)) && !File.Exists(file(renamedTo)) &&
                AssetDatabase.AssetPathToGUID(renamed) == renamedGuid && named != null && named.name == Path.GetFileNameWithoutExtension(renamed),
                $"{(named != null ? named.name : "not loaded")} / {Describe(undone)}");
            Check("files: what an export staged in the project meanwhile is not the request's doing",
                Ok(staged) && Ok(undone) && !undone.ToString(Formatting.None).Contains("_staged") &&
                !AssetDatabase.IsValidFolder($"{Transfers}/{staged["data"].Value<string>("transfer_id")}"),
                Describe(staged) + " / " + Describe(undone));
            Check("files: an asset made after the work was over stays, and nothing is left unsaid",
                Ok(undone) && File.Exists(file(byHand)) && data["unkept"].Count() == 0 && data["problems"].Count() == 0,
                Ok(undone) ? $"unkept {data["unkept"].ToString(Formatting.None)} / problems {data["problems"].ToString(Formatting.None)}" : Describe(undone));
        }

        private static void FinishPackageChecks(JObject status)
        {
            var materialFile = Path.Combine(ChPaths.ProjectRoot, _packageMaterial);
            var placed = status["data"]?["placed"] as JArray;
            var prefab = placed != null && placed.Count > 0 ? placed[0].Value<string>("prefab") : null;
            Check("package: imported and placed, the asset already here written over",
                status.Value<string>("_mcp_status") == "complete" && prefab != null && AssetDatabase.LoadAssetAtPath<GameObject>(prefab) != null &&
                EditorSceneManager.GetActiveScene().rootCount == _rootsBefore + 1 && !File.ReadAllBytes(materialFile).SequenceEqual(_materialBefore),
                Describe(status));

            var undone = Call(ChCheckpoint.HandleCommand, new { action = "undo" });
            Check("undo: what the package wrote over is back as it was",
                Ok(undone) && File.ReadAllBytes(materialFile).SequenceEqual(_materialBefore) &&
                undone["data"]["restored"].Values<string>().Contains(_packageMaterial), Describe(undone));
            Check("undo: what the package added is out of the project, kept, and its scene object is gone",
                Ok(undone) && prefab != null && AssetDatabase.LoadAssetAtPath<GameObject>(prefab) == null &&
                !File.Exists(Path.Combine(ChPaths.ProjectRoot, prefab)) && EditorSceneManager.GetActiveScene().rootCount == _rootsBefore &&
                undone["data"]["removed"].HasValues && undone["data"]["removed"].Values<string>().All(KeptOutside) &&
                undone["data"]["problems"].Count() == 0, Describe(undone));
        }

        /// <summary>
        /// 10. A request is undone by its name, and only when its point is the newest one with something to undo. That
        /// is what the chat channel asks of every window a request changed (the 되돌리기 of the panel it was asked in):
        /// a window that a later request changed since says so instead of taking that later request back with it.
        /// </summary>
        private static void RequestChecks()
        {
            ChPaths.EnsureAssetFolder(Work);
            var shader = Shader.Find("Universal Render Pipeline/Lit") ?? Shader.Find("Standard");
            string first = $"{Work}/ChSelfTestFirst.mat", second = $"{Work}/ChSelfTestSecond.mat";
            Func<string, string> file = path => Path.Combine(ChPaths.ProjectRoot, path);
            Func<string, JObject> undo = request => Call(ChCheckpoint.HandleCommand, new { action = "undo_request", request });
            Func<JObject, string> state = result => result["data"]?.Value<string>("state");
            Action<string, string> change = (request, asset) =>
            {
                try
                {
                    ChCheckpoint.SetWork(request);
                    Call(ChCheckpoint.HandleCommand, new { action = "mark", label = request, request });
                    AssetDatabase.CreateAsset(new Material(shader), asset);  // noted with the mark: it has something to undo
                }
                finally
                {
                    ChCheckpoint.SetWork(null);
                }
            };

            var before = undo("selftest-first");
            change("selftest-first", first);
            change("selftest-second", second);
            var blocked = undo("selftest-first");
            Check("by name: a request that a later one came after is not undone, and the answer says what is on top",
                Ok(before) && state(before) == "nothing" && Ok(blocked) && state(blocked) == "blocked" &&
                (blocked["data"].Value<string>("top") ?? "").StartsWith("selftest-second") && File.Exists(file(first)) && File.Exists(file(second)),
                Describe(before) + " / " + Describe(blocked));
            var newest = undo("selftest-second");
            var again = undo("selftest-second");
            Check("by name: the newest request is undone, once",
                state(newest) == "undone" && !File.Exists(file(second)) && File.Exists(file(first)) && state(again) == "nothing" &&
                (newest["data"].Value<string>("summary") ?? "").Contains(ChCheckpoint.RemovedFolder), Describe(newest) + " / " + Describe(again));
            var then = undo("selftest-first");
            Check("by name: then the one before it", state(then) == "undone" && !File.Exists(file(first)), Describe(then));
            var unnamed = undo("");
            Check("by name: no name, nothing undone", !Ok(unnamed), Describe(unnamed));
        }

        /// <summary>
        /// 11. A model that follows its .blend (ch_model_follow). Told to follow, it is replaced in place by what a save
        /// in Blender sends, and that is the user's own doing: not the request's that is at work meanwhile, nothing
        /// placed, nothing made that is not here, nothing done when what comes is there already. Blender's panel
        /// stands in here as the calls it makes. This sets up; the saves come in ticks of their own (FollowTick), so
        /// that what a tick tells of them is told of them alone. False when there is nothing to go on with.
        /// </summary>
        private static bool FollowChecks()
        {
            _followOne = WriteObj("follow_a", 1, FollowSource, "ChSelfTestFollow");
            _followTwo = WriteObj("follow_b", 2, FollowSource, "ChSelfTestFollow");
            var came = Call(ChImportModel.HandleCommand, new { manifest = _followOne });
            _followPath = came["data"]?.Value<string>("asset_path");
            if (!Ok(came))
            {
                Check("follow: set up (a model that came from Blender)", false, Describe(came));
                return false;
            }

            var first = Listed(FollowSource);
            var early = Follow(new { action = "update", manifest = _followTwo });
            Check("follow: a model does not follow until it is told to",
                first != null && first.Value<bool>("here") && !first.Value<bool>("follow") && StateOf(early) == "off" &&
                ChCheckpoint.Same(Path.Combine(ChPaths.ProjectRoot, _followPath), ModelFile(_followOne)),
                $"{first?.ToString(Formatting.None)} / {Describe(early)}");

            var on = Follow(new { action = "set", asset_path = _followPath, follow = true });
            var resent = Call(ChImportModel.HandleCommand, new { manifest = _followOne });  // "send it again" through Claude leaves it on
            var told = Listed(FollowSource);
            Check("follow: told to, it stays so through a re-send, and the list gives what Blender compares its export with",
                Ok(on) && Ok(resent) && told != null && told.Value<bool>("follow") && (told.Value<string>("file_hash") ?? "").Length == 64 &&
                told.Value<int>("instances") == 1, $"{Describe(on)} / {told?.ToString(Formatting.None)}");
            return true;
        }

        private static JObject Follow(object parameters) => Call(ChModelFollow.HandleCommand, parameters);

        private static string StateOf(JObject result) => result["data"]?.Value<string>("state");

        /// <summary>The test's model as ch_model_follow lists it for a .blend.</summary>
        private static JObject Listed(string source)
        {
            return (Follow(new { action = "list", source })["data"]?["models"] as JArray)?.OfType<JObject>()
                .FirstOrDefault(m => m.Value<string>("asset_path") == _followPath);
        }

        /// <summary>The steps of 11, a few ticks apart: by then the editor has told of what the step before changed.</summary>
        private static void FollowTick()
        {
            if (++_followTicks < 3) return;
            _followTicks = 0;
            try
            {
                if (_followStep == 0)
                {
                    SavedWhileAtWork(_followTwo, true);
                    _followStep = 1;
                    return;
                }
                if (_followStep == 1)
                {
                    StillNothingToUndo("follow update: ticks later the request that was at work still has nothing to undo");
                    SavedWhileAtWork(WriteObj("follow_e", 1, FollowSource, "ChSelfTestFollow", 2), false);
                    _followStep = 2;
                    return;
                }
                EditorApplication.update -= FollowTick;
                StillNothingToUndo("follow update: nor after a model came with other parts than it had");
                FollowRest();
            }
            catch (Exception ex)
            {
                EditorApplication.update -= FollowTick;
                Check("follow: ran to the end", false, ex.ToString());
            }
            Finish();
        }

        /// <summary>The user saves in Blender while a request is at work here.</summary>
        private static void SavedWhileAtWork(string manifest, bool first)
        {
            var file = Path.Combine(ChPaths.ProjectRoot, _followPath);
            int roots = EditorSceneManager.GetActiveScene().rootCount, marks;
            JObject updated, again;
            try
            {
                ChCheckpoint.SetWork(FollowRequest);
                if (first) Call(ChCheckpoint.HandleCommand, new { action = "mark", label = FollowRequest, request = FollowRequest });
                marks = ChCheckpoint.Marks.Count;
                updated = Follow(new { action = "update", manifest });
                again = Follow(new { action = "update", manifest });
            }
            finally
            {
                ChCheckpoint.SetWork(null);
            }
            bool replaced = StateOf(updated) == "updated" && ChCheckpoint.Same(file, ModelFile(manifest)) &&
                            updated["data"].Value<int>("instance_count") == 1 && EditorSceneManager.GetActiveScene().rootCount == roots;
            if (!first)
            {
                Check("follow update: a model that comes with other parts than it had is replaced as well", replaced, Describe(updated));
                return;
            }
            var mark = ChCheckpoint.Marks.LastOrDefault();
            var kept = updated["data"]?.Value<string>("previous_file");
            Check("follow update: the model is replaced in place by what the save sent, and nothing is placed", replaced, Describe(updated));
            Check("follow update: not the doing of the request at work, and the file it replaced is kept apart from the requests' files",
                ChCheckpoint.Marks.Count == marks && mark != null && mark.request == FollowRequest && (mark.files == null || mark.files.Count == 0) &&
                kept != null && File.Exists(kept) && ChPaths.ToPosix(kept).Contains("/replaced/follow/"),
                $"{mark?.files?.Count} note(s) on '{mark?.request}', kept at {kept}");
            Check("follow update: what is there already is left alone", StateOf(again) == "unchanged", Describe(again));
        }

        private static void StillNothingToUndo(string name)
        {
            var mark = ChCheckpoint.Marks.LastOrDefault(m => m.request == FollowRequest);
            Check(name, mark != null && mark.untouched && (mark.files == null || mark.files.Count == 0),
                mark == null ? "its mark is gone" : $"untouched {mark.untouched}, {mark.files?.Count} note(s)");
        }

        /// <summary>What a save never does, a .blend that goes on under another name, and a model sent from Blender's panel.</summary>
        private static void FollowRest()
        {
            const string renamed = "selftest/ch_selftest_follow_v2.blend";
            int models = AssetDatabase.FindAssets("l:ClaudeHandImport").Length;
            var stranger = Follow(new { action = "update", manifest = WriteObj("follow_c", 1, "selftest/another.blend", "ChSelfTestFollow") });
            Check("follow update: a model this project does not have is not made",
                StateOf(stranger) == "missing" && AssetDatabase.FindAssets("l:ClaudeHandImport").Length == models, Describe(stranger));

            var movedList = Follow(new { action = "list", source = renamed, moved_from = FollowSource });
            var there = (movedList["data"]?["models"] as JArray)?.OfType<JObject>().FirstOrDefault(m => m.Value<string>("asset_path") == _followPath);
            var old = Follow(new { action = "update", manifest = _followOne });  // the file it was saved from no longer reaches the model
            var tied = Follow(new { action = "set", asset_path = _followPath, source = FollowSource });
            var back = Listed(FollowSource);
            Check("follow: a .blend saved under another name takes its models along, and a model can be tied to another file",
                Ok(movedList) && there != null && there.Value<bool>("here") && there.Value<bool>("follow") &&
                movedList["data"]["moved"].Values<string>().Contains(_followPath) && StateOf(old) == "missing" &&
                Ok(tied) && back != null && back.Value<bool>("here") && back.Value<bool>("follow"),
                $"{there?.ToString(Formatting.None)} / {Describe(old)} / {Describe(tied)}");

            var sent = WriteObj("follow_d", 1, "selftest/ch_selftest_sent.blend", "ChSelfTestSent");
            int roots = EditorSceneManager.GetActiveScene().rootCount;
            JObject refused;
            try
            {
                ChCheckpoint.SetWork(FollowRequest);
                refused = Follow(new { action = "receive", manifest = sent });
            }
            finally
            {
                ChCheckpoint.SetWork(null);
            }
            var received = Follow(new { action = "receive", manifest = sent });
            var path = received["data"]?.Value<string>("asset_path");
            bool offered = (ChCheckpoint.Undoable.LastOrDefault()?.label ?? "").StartsWith("Blender에서 보냄");
            bool placed = EditorSceneManager.GetActiveScene().rootCount == roots + 1;
            var undone = Call(ChCheckpoint.HandleCommand, new { action = "undo" });
            Check("receive: a model sent from Blender's panel comes in following, with an undo point of its own that takes it out again",
                Ok(received) && received["data"].Value<bool>("follow") && placed && offered && Ok(undone) && path != null &&
                !File.Exists(Path.Combine(ChPaths.ProjectRoot, path)) && EditorSceneManager.GetActiveScene().rootCount == roots,
                $"{Describe(received)} / {Describe(undone)}");
            Check("receive: not while a request is at work here", !Ok(refused) && refused.Value<string>("code") == ChErrors.Conflict, Describe(refused));
        }

        /// <summary>
        /// The other half of the self-test's round trip (`claude_hand.py selftest --unity-project`, after Run): this
        /// editor, without a window but with its bridge open (CLAUDE_HAND_BRIDGE_IN_BATCH=1), stays up while a real
        /// Blender sends it a model from its panel and then saves, over the same socket as in daily use. It ends when
        /// the file CLAUDE_HAND_SELFTEST_STOP names appears, or after ServeSeconds, and deletes what came in.
        ///   Unity -batchmode -projectPath &lt;project&gt; -executeMethod ClaudeHand.Editor.ChSelfTest.Serve
        /// </summary>
        public static void Serve()
        {
            Checks.Clear();
            _failed = false;
            EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
            _serveStop = Environment.GetEnvironmentVariable("CLAUDE_HAND_SELFTEST_STOP");
            _serveDeadline = EditorApplication.timeSinceStartup + ServeSeconds;
            EditorApplication.update += ServeTick;
        }

        private static void ServeTick()
        {
            bool told = !string.IsNullOrEmpty(_serveStop) && File.Exists(_serveStop);
            if (!told && EditorApplication.timeSinceStartup < _serveDeadline) return;
            EditorApplication.update -= ServeTick;
            Check("served until told to stop", told, told ? "" : $"no word within {ServeSeconds} seconds");
            Finish();
        }

        private static string ModelFile(string manifest)
        {
            return Path.Combine(Path.GetDirectoryName(manifest), JObject.Parse(File.ReadAllText(manifest)).Value<string>("model_file"));
        }

        /// <summary>An asset that an undo took out of the project lies in Library/ClaudeHand/removed, with its .meta.</summary>
        private static bool KeptOutside(string assetPath)
        {
            var root = Path.Combine(ChPaths.LibraryDir, "removed");
            var name = Path.GetFileName(assetPath.TrimEnd('/'));
            return Directory.Exists(root) && Directory.GetDirectories(root).Any(dir =>
                (Directory.Exists(Path.Combine(dir, name)) || File.Exists(Path.Combine(dir, name))) && File.Exists(Path.Combine(dir, name + ".meta")));
        }

        /// <summary>Deletes what the test made, writes the result and, without a window, ends Unity.</summary>
        private static void Finish()
        {
            try
            {
                foreach (var parent in new[] { "Assets/ClaudeHand/Imports", Transfers })
                {
                    if (!AssetDatabase.IsValidFolder(parent)) continue;
                    foreach (var folder in AssetDatabase.GetSubFolders(parent).Where(f => Path.GetFileName(f).StartsWith(Name)))
                    {
                        AssetDatabase.DeleteAsset(folder);
                    }
                }
                AssetDatabase.DeleteAsset(Work);
                ChPaths.DeleteAssetFolderIfEmpty("Assets/ClaudeHand/Imports");
                ChPaths.DeleteAssetFolderIfEmpty(Transfers);
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
                ok = !_failed,
                unity = Application.unityVersion,
                exported_manifest = _exported,
                checks = Checks,
            }, Formatting.Indented));
            if (Application.isBatchMode) EditorApplication.Exit(_failed ? 1 : 0);
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
        private static string WriteObj(string version, int quads, string source = "selftest/ch_selftest.blend", string root = "ChSelfTest",
                                       int parts = 1)
        {
            var lines = new List<string>();
            int vertex = 0;
            for (int part = 0; part < parts; part++)  // each part an object of its own in the model
            {
                lines.Add(part == 0 ? "o ChSelfTest" : $"o ChSelfTestPart{part + 1}");
                for (int q = 0; q < quads; q++)
                {
                    lines.AddRange(new[] { $"v {q * 2} {part * 2} 0", $"v {q * 2 + 1} {part * 2} 0", $"v {q * 2 + 1} {part * 2 + 1} 0", $"v {q * 2} {part * 2 + 1} 0" });
                }
                lines.Add("usemtl ChSelfTestMat");
                for (int q = 0; q < quads; q++, vertex += 4) lines.Add($"f {vertex + 1} {vertex + 2} {vertex + 3} {vertex + 4}");
            }
            var id = $"{Name}_{version}";
            File.WriteAllLines(Path.Combine(ChPaths.ExchangeDir, id + ".obj"), lines);
            var manifest = Path.Combine(ChPaths.ExchangeDir, id + ".claudehand.json");
            File.WriteAllText(manifest, JsonConvert.SerializeObject(new
            {
                format = "claude-hand/model@1",
                name = Name,
                transfer_id = id,
                model_file = id + ".obj",
                source,
                roots = new[] { root },
            }));
            return manifest;
        }
    }
}
