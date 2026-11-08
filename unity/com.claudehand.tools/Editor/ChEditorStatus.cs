using System;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditorInternal;
using UnityEngine;

namespace ClaudeHand.Editor
{
    /// <summary>
    /// Cheap probe for the background-operation test: call it while Unity is focused, unfocused and
    /// minimized and compare how long each round trip takes. It changes nothing.
    /// </summary>
    [McpForUnityTool("ch_editor_status",
        Description = "Report this Unity Editor's state (project, focus, compiling, play mode, editor time). Changes nothing. " +
                      "Use it to check which project you are connected to and whether the editor answers while unfocused or minimized.")]
    public static class ChEditorStatus
    {
        public class Parameters
        {
            [ToolParameter("Optional text echoed back, handy for matching requests to responses when timing calls.", Required = false)]
            public string echo { get; set; }
        }

        public static object HandleCommand(JObject @params)
        {
            var p = @params?.ToObject<Parameters>() ?? new Parameters();
            return new SuccessResponse("Editor status", new
            {
                project = ChPaths.ProjectName,
                project_path = ChPaths.ToPosix(ChPaths.ProjectRoot),
                unity_version = Application.unityVersion,
                focused = InternalEditorUtility.isApplicationActive,
                is_compiling = EditorApplication.isCompiling,
                is_updating = EditorApplication.isUpdating,
                is_playing = EditorApplication.isPlaying,
                editor_time_seconds = Math.Round(EditorApplication.timeSinceStartup, 3),
                utc_now = DateTime.UtcNow.ToString("o"),
                exchange_dir = ChPaths.ToPosix(ChPaths.ExchangeDir),
                echo = p.echo,
            });
        }
    }
}
