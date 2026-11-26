using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

using static ClaudeHand.Editor.ChText;

namespace ClaudeHand.Editor
{
    /// <summary>
    /// Models that follow their .blend. A model that came from Blender (ch_import_model) can be told to follow: from
    /// then on Blender's Claude Hand panel sends it again by itself whenever that .blend is saved, straight to this
    /// editor's bridge, and it is replaced here in place. No Claude takes part, so it costs neither tokens nor a
    /// minute of waiting, and it works with Claude Hand closed.
    /// Whether a model follows is kept here, in the model's own record (ChImportModel.Record.follow): this project is
    /// what gets changed, so this is where the say is, and a model that is deleted takes it along.
    /// What a save sends is the user's own doing, not a request's: it sets no undo point, is noted for none, places
    /// nothing in the scene, and never makes a model that is not here.
    /// </summary>
    [ChTool("ch_model_follow",
        Description = "Models that follow their .blend: once a model that came from Blender (ch_import_model) is set to follow, " +
                      "Blender's Claude Hand panel sends it again by itself whenever that .blend is saved, and this project " +
                      "replaces it in place, without Claude. list: the models this project got from Blender and whether each " +
                      "follows. set: turn following on or off for one (asset_path, follow); use it when the user wants a model " +
                      "to keep up with what they save in Blender. It starts with the next save there. update and receive are " +
                      "what Blender's panel calls when the user saves or sends from it: do not call them.")]
    public static class ChModelFollow
    {
        public class Parameters
        {
            [ChParameter("list (default), set, or (Blender's panel only) update, receive", Required = false)]
            public string action { get; set; }

            [ChParameter("For set: the model, as list names it (Assets/ClaudeHand/Imports/...)", Required = false)]
            public string asset_path { get; set; }

            [ChParameter("For set: true to follow the .blend it came from, false to stop", Required = false)]
            public bool? follow { get; set; }

            [ChParameter("For list: the .blend whose models are asked for (those are marked `here`). For set: tie the model to this .blend instead of the one it came from (the file was moved or renamed)", Required = false)]
            public string source { get; set; }

            [ChParameter("Blender's panel only, for list: the .blend was saved under the name `source`; before that it was this one", Required = false)]
            public string moved_from { get; set; }

            [ChParameter("Blender's panel only, for update and receive: the manifest of what it exported", Required = false)]
            public string manifest { get; set; }

            [ChParameter("Blender's panel only: the call is the user's own doing there, not part of a request", Required = false)]
            public bool? from_panel { get; set; }

            [ChParameter("Blender's panel only, for update: what made it send, in the user's words (저장, 되돌리기, ...)", Required = false)]
            public string why { get; set; }
        }

        public static object HandleCommand(JObject @params)
        {
            var p = @params?.ToObject<Parameters>() ?? new Parameters();
            switch ((p.action ?? "list").Trim().ToLowerInvariant())
            {
                case "list": return List(p);
                case "set": return Set(p);
                case "update": return Update(p);
                case "receive": return Receive(p);
                default:
                    return ChErrors.Make(ChErrors.InvalidArgs, "action must be list or set.", "after_fix");
            }
        }

        private static string Posix(string path) => string.IsNullOrWhiteSpace(path) ? null : ChPaths.ToPosix(path.Trim());

        private static bool SameFile(string one, string other) => one != null && string.Equals(one, other, StringComparison.OrdinalIgnoreCase);

        private static string FileHash(string assetPath)
        {
            using (var sha = SHA256.Create())
            using (var stream = File.OpenRead(Path.Combine(ChPaths.ProjectRoot, assetPath)))
            {
                return string.Concat(sha.ComputeHash(stream).Select(b => b.ToString("x2")));
            }
        }

        /// <summary>Writes the record into the model's .meta. usersOwn: done from Blender's panel, so no request's doing.</summary>
        private static bool Write(string assetPath, ChImportModel.Record record, bool usersOwn, List<string> warnings)
        {
            var importer = AssetImporter.GetAtPath(assetPath);
            var model = AssetDatabase.LoadAssetAtPath<GameObject>(assetPath);
            using (usersOwn ? ChCheckpoint.UsersOwn(assetPath) : null)
            {
                if (!ChImportModel.WriteRecord(importer, model, record, warnings)) return false;
                importer.SaveAndReimport();
                return true;
            }
        }

        private static object List(Parameters p)
        {
            string source = Posix(p.source), movedFrom = Posix(p.moved_from);
            var moved = new List<string>();
            if (source != null && movedFrom != null && !SameFile(source, movedFrom))
            {
                // The .blend goes on under another name (Save As): its models are that file's from now on, whether
                // they follow or not. The file it was saved from stays behind, and no longer reaches them.
                var busy = ChErrors.BusyIfCompiling();
                if (busy != null) return busy;
                foreach (var found in ChImportModel.Recorded().Where(m => SameFile(m.Value.source, movedFrom)).ToList())
                {
                    found.Value.source = source;
                    if (Write(found.Key, found.Value, true, new List<string>())) moved.Add(found.Key);
                }
            }

            var models = new List<object>();
            int following = 0;
            foreach (var found in ChImportModel.Recorded())
            {
                bool here = SameFile(found.Value.source, source);
                if (found.Value.follow) following++;
                models.Add(new
                {
                    asset_path = found.Key,
                    name = Path.GetFileNameWithoutExtension(found.Key),
                    source = found.Value.source,
                    roots = found.Value.roots ?? new List<string>(),
                    follow = found.Value.follow,
                    here,
                    // What Blender compares its own export with, to know whether there is anything to send.
                    file_hash = here && found.Value.follow ? FileHash(found.Key) : null,
                    materials_hash = here && found.Value.follow ? found.Value.materials_hash ?? string.Empty : null,
                    instances = here ? ChImportModel.InstancesInOpenScenes(AssetDatabase.LoadAssetAtPath<GameObject>(found.Key)).Count : 0,
                });
            }
            return new ChSuccess($"{models.Count} model(s) from Blender in this project, {following} of them following their .blend.",
                new { project = ChPaths.ProjectName, models, moved });
        }

        private static object Set(Parameters p)
        {
            var path = (p.asset_path ?? string.Empty).Trim();
            var source = Posix(p.source);
            if (path.Length == 0 || (p.follow == null && source == null))
            {
                return ChErrors.Make(ChErrors.InvalidArgs, "set needs asset_path and follow (true or false).", "after_fix",
                    "list names the models that came from Blender.");
            }
            var record = ChImportModel.ReadRecord(AssetImporter.GetAtPath(path));
            if (record == null)
            {
                return ChErrors.Make(ChErrors.NotFound, $"'{path}' is not a model that came from Blender through ch_import_model.", "after_fix",
                    "list names the ones that did. A model sent before it was ever saved in Blender has no record of where it came from.");
            }
            var busy = ChErrors.BusyIfCompiling();
            if (busy != null) return busy;

            if (p.follow != null) record.follow = p.follow.Value;
            if (source != null) record.source = source;
            var warnings = new List<string>();
            bool changed = Write(path, record, p.from_panel == true, warnings);
            var blend = Path.GetFileName(record.source ?? string.Empty);
            return new ChSuccess(record.follow
                    ? $"{path} follows {blend} from now on: whenever that file is saved in Blender (with the Claude Hand add-on there, and " +
                      "this editor open), the model is replaced here in place. Nothing is sent until the next save."
                    : $"{path} does not follow {blend}: it changes only when it is sent again.",
                new { asset_path = path, follow = record.follow, source = record.source, changed, warnings });
        }

        private static object Update(Parameters p)
        {
            if (string.IsNullOrWhiteSpace(p.manifest)) return ChErrors.Make(ChErrors.InvalidArgs, "manifest is required.", "after_fix");
            if (!ChImportModel.TryResolveModel(p.manifest, out var incoming, out var error)) return error;
            var busy = ChErrors.BusyIfCompiling();
            if (busy != null) return busy;

            var existing = ChImportModel.FindExisting(incoming);
            if (existing == null)
            {
                return new ChSuccess("This project has no model from that source any more; nothing was made.", new { state = "missing" });
            }
            if (ChImportModel.ReadRecord(AssetImporter.GetAtPath(existing))?.follow != true)
            {
                return new ChSuccess($"{existing} does not follow its .blend; nothing was changed.", new { state = "off", asset_path = existing });
            }
            var had = ChImportModel.ReadRecord(AssetImporter.GetAtPath(existing));
            if (ChCheckpoint.Same(incoming.ModelPath, Path.Combine(ChPaths.ProjectRoot, existing)) &&
                (incoming.MaterialsHash == null || incoming.MaterialsHash == had?.materials_hash))
            {
                return new ChSuccess($"{existing} is what was sent already.", new { state = "unchanged", asset_path = existing });
            }

            var warnings = new List<string>();
            string previous;
            object made;
            try
            {
                previous = ChImportModel.ReplaceFollowed(incoming, existing, warnings, out made);
            }
            catch (Exception ex)
            {
                return ChErrors.Make(ChErrors.Failed, "Could not replace the model: " + ex.Message, "check_first",
                    $"Look at {existing} before trying again.");
            }
            var shape = ChModelLook.ShapeCheck(incoming, existing, warnings);
            int uses = ChImportModel.InstancesInOpenScenes(AssetDatabase.LoadAssetAtPath<GameObject>(existing)).Count;
            var blend = Path.GetFileName(incoming.Source ?? string.Empty);
            // Mostly a save; also the state after a 되돌리기 in Blender, or what was saved while this editor was closed.
            var why = string.IsNullOrWhiteSpace(p.why) ? T("저장") : p.why.Trim();
            if (why.Length > 20) why = why.Substring(0, 20);
            ChChatWindow.Tell("follow:" + existing,
                T("{0} {1} → {2} 반영", blend, why, Path.GetFileName(existing)) + (uses > 0 ? T(" (씬 오브젝트 {0}개)", uses) : string.Empty),
                T("[이 프로젝트의 모델 {0}이(가) Blender의 {1} 저장을 따라 바뀌었음 ({2}, Claude를 거치지 않음)]", existing, blend, why));
            return new ChSuccess($"Replaced {existing} with what {blend} holds now.",
                new { state = "updated", asset_path = existing, instance_count = uses, previous_file = previous, materials_made = made,
                      shape_check = shape, warnings });
        }

        private static object Receive(Parameters p)
        {
            if (string.IsNullOrWhiteSpace(p.manifest)) return ChErrors.Make(ChErrors.InvalidArgs, "manifest is required.", "after_fix");
            if (ChCheckpoint.SomebodyAtWork)
            {
                // A new model sets an undo point of its own; on top of a request's point, what that request still
                // does would go under the wrong one.
                return ChErrors.Make(ChErrors.Conflict, "A request is at work in this project.", "safe",
                    "Send again once Claude has finished here.");
            }
            if (!ChImportModel.TryResolveModel(p.manifest, out var incoming, out var error)) return error;
            var busy = ChErrors.BusyIfCompiling();
            if (busy != null) return busy;

            // So that the chat window's 되돌리기 can take it out again, as it can a model Claude brought in.
            ChCheckpoint.Add(T("Blender에서 보냄: ") + incoming.Name);
            var result = ChImportModel.Import(new ChImportModel.Parameters { manifest = p.manifest }, true);
            if (!(result is ChSuccess done)) return result;
            var data = JObject.FromObject(done.Data);
            var path = data.Value<string>("asset_path");
            data["state"] = "received";
            data["follow"] = ChImportModel.ReadRecord(AssetImporter.GetAtPath(path))?.follow == true;
            var blend = Path.GetFileName(incoming.Source ?? string.Empty);
            ChChatWindow.Tell("sent:" + path,
                T("Blender({0})에서 {1}을(를) 보냈어요. 저장할 때마다 다시 옵니다. 되돌리기로 뺄 수 있어요.", blend, Path.GetFileName(path)),
                T("[사용자가 Blender 패널에서 {0}의 모델을 이 프로젝트에 직접 보냈음: {1} (Claude를 거치지 않음, 저장할 때마다 다시 옴)]", blend, path));
            return new ChSuccess(done.Message, data);
        }
    }
}
