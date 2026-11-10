using System;
using System.IO;
using Newtonsoft.Json;

namespace ClaudeHand.Editor
{
    // What a ch_* tool is made of: the attributes ChBridge finds it by, what it returns, and where a long-running
    // one keeps its state. These are claude-hand's own types on purpose. The package used to borrow MCP for
    // Unity's (McpForUnityTool, ToolParameter, SuccessResponse, ErrorResponse, PendingResponse, McpJobStateStore),
    // so a rename there would have stopped this whole assembly from compiling: the bridge, the chat window, undo
    // points and captures all at once. The JSON the tools return is the same as before.

    /// <summary>
    /// Marks a class as a ch_* tool. ChBridge lists every such class of this assembly in the registry file and runs
    /// its public static HandleCommand(JObject). Its parameters are the properties of a nested public class
    /// Parameters that carry <see cref="ChParameterAttribute"/>.
    /// </summary>
    [AttributeUsage(AttributeTargets.Class, AllowMultiple = false)]
    public sealed class ChToolAttribute : Attribute
    {
        public string Name { get; }

        /// <summary>What the tool does, as the subagent reads it.</summary>
        public string Description { get; set; }

        /// <summary>
        /// The tool answers with <see cref="ChPending"/> while it works; tools/unity_bridge.py then keeps calling it
        /// with action=<see cref="PollAction"/> until the answer is no longer pending.
        /// </summary>
        public bool RequiresPolling { get; set; }

        public string PollAction { get; set; } = "status";

        /// <summary>How long the bridge keeps polling. 0: the bridge's default.</summary>
        public int MaxPollSeconds { get; set; }

        public ChToolAttribute(string name)
        {
            Name = name;
        }
    }

    /// <summary>Describes one property of a tool's Parameters class.</summary>
    [AttributeUsage(AttributeTargets.Property, AllowMultiple = false)]
    public sealed class ChParameterAttribute : Attribute
    {
        public string Description { get; }

        public bool Required { get; set; } = true;

        public ChParameterAttribute(string description)
        {
            Description = description;
        }
    }

    /// <summary>The tool worked: {"success": true, "message": ..., "data": ...}.</summary>
    public sealed class ChSuccess
    {
        [JsonProperty("success")]
        public bool Success => true;

        [JsonProperty("message")]
        public string Message { get; }

        [JsonProperty("data", NullValueHandling = NullValueHandling.Ignore)]
        public object Data { get; }

        public ChSuccess(string message, object data = null)
        {
            Message = message;
            Data = data;
        }
    }

    /// <summary>
    /// The tool did not work: {"success": false, "code": ..., "error": ..., "data": ...}. Make these with
    /// <see cref="ChErrors.Make"/>, which puts the message and the retry advice into data.
    /// </summary>
    public sealed class ChError
    {
        [JsonProperty("success")]
        public bool Success => false;

        [JsonProperty("code")]
        public string Code { get; }

        [JsonProperty("error")]
        public string Error => Code;

        [JsonProperty("data", NullValueHandling = NullValueHandling.Ignore)]
        public object Data { get; }

        public ChError(string code, object data = null)
        {
            Code = code;
            Data = data;
        }
    }

    /// <summary>
    /// The tool is still working: {"success": true, "_mcp_status": "pending", "_mcp_poll_interval": seconds, ...}.
    /// The field names are the ones tools/unity_bridge.py polls on (kept from MCP for Unity's polling convention).
    /// </summary>
    public sealed class ChPending
    {
        [JsonProperty("success")]
        public bool Success => true;

        [JsonProperty("_mcp_status")]
        public string Status => "pending";

        [JsonProperty("_mcp_poll_interval")]
        public double PollIntervalSeconds { get; }

        [JsonProperty("message", NullValueHandling = NullValueHandling.Ignore)]
        public string Message { get; }

        [JsonProperty("data", NullValueHandling = NullValueHandling.Ignore)]
        public object Data { get; }

        public ChPending(string message = "", double pollIntervalSeconds = 1.0, object data = null)
        {
            Message = string.IsNullOrEmpty(message) ? null : message;
            PollIntervalSeconds = pollIntervalSeconds;
            Data = data;
        }
    }

    /// <summary>
    /// State of a long-running tool, one file per tool in Library/ClaudeHand/jobs. Library is local to the project
    /// and survives the script reloads an import can cause.
    /// </summary>
    internal static class ChJobState
    {
        private static string PathOf(string tool)
        {
            return Path.Combine(ChPaths.LibraryDir, "jobs", ChPaths.Sanitize(tool) + ".json");
        }

        public static void Save<T>(string tool, T state)
        {
            var path = PathOf(tool);
            Directory.CreateDirectory(Path.GetDirectoryName(path));
            File.WriteAllText(path, JsonConvert.SerializeObject(state));
        }

        /// <summary>The saved state, or null when there is none or it cannot be read.</summary>
        public static T Load<T>(string tool) where T : class
        {
            var path = PathOf(tool);
            if (!File.Exists(path)) return null;
            try
            {
                return JsonConvert.DeserializeObject<T>(File.ReadAllText(path));
            }
            catch (Exception)
            {
                return null;
            }
        }
    }
}
