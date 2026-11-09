using System;
using System.Collections.Generic;
using System.Linq;
using System.Net.Http;
using System.Threading;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEditorInternal;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace ClaudeHand.Editor
{
    /// <summary>
    /// Tells the Claude Hand supervisor what is selected and open in this project, without the chat window
    /// being open, so a request typed in the Claude desktop app ("move the selected one to Blender") can find
    /// its target (the supervisor's current_context tool). Only runs while Claude Hand is on (server.json
    /// exists); sends at most twice a second, and only when something changed, plus a refresh every minute.
    /// Nothing is sent anywhere but the local Claude Hand channel (127.0.0.1, with its token).
    /// </summary>
    [InitializeOnLoad]
    internal static class ChContextReporter
    {
        private const double MinIntervalSeconds = 0.5;
        private const double RefreshSeconds = 60;
        private const int MaxItems = 20;

        private static bool _dirty = true;
        private static double _lastSent;
        private static string _lastSignature;
        private static int _lastServerPid;
        private static int _sending; // 0 or 1, touched with Interlocked

        static ChContextReporter()
        {
            if (Application.isBatchMode) return;
            Selection.selectionChanged += MarkDirty;
            EditorApplication.focusChanged += _ => MarkDirty();
            EditorSceneManager.activeSceneChangedInEditMode += (_, __) => MarkDirty();
            EditorApplication.update += Update;
        }

        private static void MarkDirty()
        {
            _dirty = true;
        }

        private static void Update()
        {
            var now = EditorApplication.timeSinceStartup;
            if (now - _lastSent < MinIntervalSeconds) return;
            if (!_dirty && now - _lastSent < RefreshSeconds) return;
            if (Interlocked.CompareExchange(ref _sending, 0, 0) == 1) return;

            var server = ChChatWindow.ReadServer();
            if (server == null)
            {
                _lastSent = now; // Claude Hand is off: nothing to tell; look again in a moment, not every frame
                return;
            }
            var context = Collect();
            var signature = string.Join("|", context.Select(kv => kv.Key + "=" + Convert.ToString(kv.Value is IEnumerable<string> list ? string.Join(",", list) : kv.Value)));
            if (signature == _lastSignature && server.pid == _lastServerPid && now - _lastSent < RefreshSeconds)
            {
                _dirty = false;
                return;
            }

            _dirty = false;
            _lastSent = now;
            _lastSignature = signature;
            _lastServerPid = server.pid;
            Send(server, context);
        }

        private static Dictionary<string, object> Collect()
        {
            var scene = SceneManager.GetActiveScene();
            return new Dictionary<string, object>
            {
                ["scene"] = string.IsNullOrEmpty(scene.path) ? scene.name : scene.path,
                ["selection"] = Selection.gameObjects.Take(MaxItems).Select(go => ChRefs.HierarchyPath(go.transform)).ToList(),
                ["assets"] = Selection.assetGUIDs.Take(MaxItems).Select(AssetDatabase.GUIDToAssetPath).Where(p => !string.IsNullOrEmpty(p)).ToList(),
                ["focused"] = InternalEditorUtility.isApplicationActive,
            };
        }

        private static async void Send(ChChatWindow.ServerInfo server, Dictionary<string, object> context)
        {
            Interlocked.Exchange(ref _sending, 1);
            try
            {
                await ChChatWindow.Call(server, HttpMethod.Post, "/v1/context",
                    new { app = "unity", window = ChPaths.ProjectName, context }, CancellationToken.None);
            }
            catch (Exception)
            {
                _lastServerPid = 0; // try again on the next change or refresh
            }
            finally
            {
                Interlocked.Exchange(ref _sending, 0);
            }
        }
    }
}
