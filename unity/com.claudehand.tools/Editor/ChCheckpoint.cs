using System;
using System.Collections.Generic;
using System.Linq;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;

namespace ClaudeHand.Editor
{
    /// <summary>
    /// Undo points for Claude's requests. Just before a subagent's first Unity action for a request, its guard hook
    /// asks for a mark (a new Undo group, named after the request); "되돌리기" in the chat window reverts everything
    /// done in this editor since the newest mark. That covers what went through Unity's Undo: MCP for Unity's tools,
    /// ch_import_*, and execute_code that registers its changes with Undo.
    /// Kept in SessionState: survives script reloads and ends with the editor, like the Undo history itself.
    /// </summary>
    [McpForUnityTool("ch_checkpoint",
        Description = "Undo points for requests. mark (default): set one before changing anything for a new request (the " +
                      "guard hook already does this before your first action). undo: revert everything since the newest point " +
                      "(use it when the user asks to cancel what you just did). list: show the points.")]
    public static class ChCheckpoint
    {
        private const string Key = "ClaudeHand.Checkpoints";
        private const int Kept = 20;
        private static List<Mark> _marks;

        public sealed class Mark
        {
            public int group;
            public string label;
            public string time;
        }

        public class Parameters
        {
            [ToolParameter("mark (default), undo or list", Required = false)]
            public string action { get; set; }

            [ToolParameter("For mark: the request in a few words, shown on the chat window's undo button", Required = false)]
            public string label { get; set; }
        }

        /// <summary>Raised on the main thread whenever a mark is added or undone (the chat window repaints).</summary>
        public static event Action Changed;

        public static IReadOnlyList<Mark> Marks => _marks ??= JsonConvert.DeserializeObject<List<Mark>>(SessionState.GetString(Key, "[]")) ?? new List<Mark>();

        public static Mark Add(string label)
        {
            Undo.IncrementCurrentGroup();
            var mark = new Mark
            {
                group = Undo.GetCurrentGroup(),
                label = string.IsNullOrWhiteSpace(label) ? "Claude 요청" : label.Trim(),
                time = DateTime.Now.ToString("HH:mm"),
            };
            Undo.SetCurrentGroupName("Claude Hand: " + mark.label);
            Save(Marks.Append(mark).ToList());
            return mark;
        }

        /// <summary>Reverts everything since the newest mark (also edits made by hand after it) and drops the mark.</summary>
        public static Mark UndoLast()
        {
            if (Marks.Count == 0) return null;
            var mark = Marks[Marks.Count - 1];
            Undo.RevertAllDownToGroup(mark.group);
            Save(Marks.Take(Marks.Count - 1).ToList());
            return mark;
        }

        private static void Save(List<Mark> marks)
        {
            _marks = marks.Skip(Math.Max(0, marks.Count - Kept)).ToList();
            SessionState.SetString(Key, JsonConvert.SerializeObject(_marks));
            Changed?.Invoke();
        }

        public static object HandleCommand(JObject @params)
        {
            var p = @params?.ToObject<Parameters>() ?? new Parameters();
            switch ((p.action ?? "mark").Trim().ToLowerInvariant())
            {
                case "mark":
                    var busy = ChErrors.BusyIfCompiling();
                    if (busy != null) return busy;
                    var mark = Add(p.label);
                    return new SuccessResponse($"Undo point {mark.group} set", new { mark.group, mark.label, mark.time });
                case "undo":
                    var undone = UndoLast();
                    return undone == null
                        ? (object)ChErrors.Make(ChErrors.NotFound, "There is no undo point to go back to.", "after_fix")
                        : new SuccessResponse($"Reverted everything since '{undone.label}' ({undone.time})", new { undone.label, undone.time });
                case "list":
                    return new SuccessResponse($"{Marks.Count} undo point(s)", new { marks = Marks });
                default:
                    return ChErrors.Make(ChErrors.InvalidArgs, "action must be mark, undo or list.", "after_fix");
            }
        }
    }
}
