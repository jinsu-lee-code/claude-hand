using System.Reflection;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

namespace ClaudeHand.Editor
{
    /// <summary>
    /// The Editor setting claude-hand needs: Preferences > General > Interaction Mode = No Throttling,
    /// so an unfocused Editor keeps processing MCP commands. Unity stores it in two EditorPrefs keys
    /// (see UnityCsReference, PreferencesSettingsProviders.DrawInteractionModeOptions). EditorPrefs are
    /// per user and per Unity major version, so applying it once covers every project on that version.
    /// The previous values are kept so "revert" can put them back.
    /// </summary>
    [McpForUnityTool("ch_apply_settings",
        Description = "Show, apply or revert the Editor setting claude-hand recommends: Preferences > General > Interaction Mode = " +
                      "No Throttling, so Unity keeps answering MCP commands while it is unfocused. It is a per-user Editor " +
                      "preference (all projects on this Unity version). action: show (default), apply, revert.")]
    public static class ChApplySettings
    {
        private const string IdleTimeKey = "ApplicationIdleTime";
        private const string InteractionModeKey = "InteractionMode";
        private const int NoThrottling = 1;
        private const string BackupSavedKey = "ClaudeHand.Backup.Saved";
        private const string BackupIdleKey = "ClaudeHand.Backup.ApplicationIdleTime";
        private const string BackupModeKey = "ClaudeHand.Backup.InteractionMode";
        private static readonly string[] ModeNames = { "Default", "No Throttling", "Monitor Refresh Rate", "Custom" };

        public class Parameters
        {
            [ToolParameter("show (default), apply or revert.", Required = false)]
            public string action { get; set; }
        }

        public static object HandleCommand(JObject @params)
        {
            var action = (@params?.ToObject<Parameters>()?.action ?? "show").Trim().ToLowerInvariant();
            switch (action)
            {
                case "show":
                    return new SuccessResponse("Current Editor settings", Describe());
                case "apply":
                    var applied = Apply(out bool live);
                    return new SuccessResponse(applied
                        ? "Interaction Mode set to No Throttling" + (live ? "." : "; it takes effect after restarting Unity.")
                        : "Interaction Mode was already No Throttling.", Describe(live));
                case "revert":
                    var reverted = Revert(out bool liveRevert);
                    return new SuccessResponse(reverted ? "Restored the Interaction Mode from before claude-hand changed it." : "Nothing to revert.",
                        Describe(liveRevert));
                default:
                    return ChErrors.Make(ChErrors.InvalidArgs, $"Unknown action '{action}'.", "after_fix", "Use show, apply or revert.");
            }
        }

        [MenuItem("Tools/Claude Hand/Apply Recommended Settings")]
        private static void ApplyFromMenu()
        {
            var applied = Apply(out bool live);
            Debug.Log(applied
                ? "[Claude Hand] Interaction Mode set to No Throttling" + (live ? "." : " (restart Unity to apply).")
                : "[Claude Hand] Interaction Mode is already No Throttling.");
        }

        [MenuItem("Tools/Claude Hand/Revert Recommended Settings")]
        private static void RevertFromMenu()
        {
            Debug.Log(Revert(out _) ? "[Claude Hand] Interaction Mode restored." : "[Claude Hand] Nothing to revert.");
        }

        internal static bool Apply(out bool appliedLive)
        {
            appliedLive = false;
            if (EditorPrefs.GetInt(InteractionModeKey, 0) == NoThrottling && EditorPrefs.GetInt(IdleTimeKey, 4) == 0) return false;

            if (!EditorPrefs.GetBool(BackupSavedKey, false))
            {
                EditorPrefs.SetInt(BackupModeKey, EditorPrefs.HasKey(InteractionModeKey) ? EditorPrefs.GetInt(InteractionModeKey, 0) : -1);
                EditorPrefs.SetInt(BackupIdleKey, EditorPrefs.HasKey(IdleTimeKey) ? EditorPrefs.GetInt(IdleTimeKey, 4) : -1);
                EditorPrefs.SetBool(BackupSavedKey, true);
            }

            EditorPrefs.SetInt(IdleTimeKey, 0);
            EditorPrefs.SetInt(InteractionModeKey, NoThrottling);
            appliedLive = RefreshInteractionMode();
            return true;
        }

        internal static bool Revert(out bool appliedLive)
        {
            appliedLive = false;
            if (!EditorPrefs.GetBool(BackupSavedKey, false)) return false;

            RestoreInt(InteractionModeKey, EditorPrefs.GetInt(BackupModeKey, -1));
            RestoreInt(IdleTimeKey, EditorPrefs.GetInt(BackupIdleKey, -1));
            EditorPrefs.DeleteKey(BackupModeKey);
            EditorPrefs.DeleteKey(BackupIdleKey);
            EditorPrefs.DeleteKey(BackupSavedKey);
            appliedLive = RefreshInteractionMode();
            return true;
        }

        private static void RestoreInt(string key, int value)
        {
            if (value < 0) EditorPrefs.DeleteKey(key);
            else EditorPrefs.SetInt(key, value);
        }

        /// <summary>EditorApplication.UpdateInteractionModeSettings is internal; without it the change applies after a restart.</summary>
        private static bool RefreshInteractionMode()
        {
            var method = typeof(EditorApplication).GetMethod("UpdateInteractionModeSettings",
                BindingFlags.Static | BindingFlags.NonPublic | BindingFlags.Public);
            if (method == null) return false;
            try
            {
                method.Invoke(null, null);
                return true;
            }
            catch (TargetInvocationException)
            {
                return false;
            }
        }

        private static object Describe(bool? appliedLive = null)
        {
            int mode = EditorPrefs.GetInt(InteractionModeKey, 0);
            return new
            {
                interaction_mode = mode >= 0 && mode < ModeNames.Length ? ModeNames[mode] : mode.ToString(),
                idle_time_ms = EditorPrefs.GetInt(IdleTimeKey, 4),
                recommended = mode == NoThrottling,
                can_revert = EditorPrefs.GetBool(BackupSavedKey, false),
                applied_live = appliedLive,
                scope = "Per-user Editor preference for this Unity version (all projects).",
            };
        }
    }
}
