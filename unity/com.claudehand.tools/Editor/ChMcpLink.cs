using System;
using System.Reflection;
using UnityEditor;
using UnityEngine;

namespace ClaudeHand.Editor
{
    /// <summary>
    /// Keeps this editor's MCP for Unity connection (its stdio bridge) up.
    ///
    /// MCP for Unity starts the bridge again after every script reload, but gives up after six tries (about 49
    /// seconds, StdioBridgeReloadHandler), for example while its port stays taken. The window then says it is not
    /// connected until someone presses the button, and everything the subagent of this window calls fails.
    /// So: when the bridge has been down for a while with nothing to wait for (no compiling, no importing), it is
    /// started here the way MCP for Unity starts it itself. Looked up by name, so this package compiles without
    /// MCP for Unity and does nothing where its layout differs.
    /// EditorPrefs "ClaudeHand.KeepMcpForUnity" = false turns this off.
    /// </summary>
    [InitializeOnLoad]
    internal static class ChMcpLink
    {
        private const string HostType = "MCPForUnity.Editor.Services.Transport.Transports.StdioBridgeHost";
        private const string LocatorType = "MCPForUnity.Editor.Services.MCPServiceLocator";
        private const string ModeType = "MCPForUnity.Editor.Services.Transport.TransportMode";
        private const string KeepKey = "ClaudeHand.KeepMcpForUnity";
        private const string HttpKey = "MCPForUnity.UseHttpTransport"; // MCP for Unity's own setting; unset means HTTP
        private const BindingFlags Any = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static | BindingFlags.Instance;
        private const double LookEvery = 2;  // seconds
        private const double Patience = 20;  // down this long before stepping in: MCP for Unity's own quick tries come first
        private const double TryEvery = 20;

        private static Type _host;
        private static bool _searched;
        private static double _nextLook, _downSince = -1, _nextTry;
        private static int _tries;

        static ChMcpLink()
        {
            if (Application.isBatchMode) return;
            EditorApplication.update += Watch;
        }

        private static Type Find(string name)
        {
            foreach (var assembly in AppDomain.CurrentDomain.GetAssemblies())
            {
                var type = assembly.GetType(name, false);
                if (type != null) return type;
            }
            return null;
        }

        private static Type Host
        {
            get
            {
                if (!_searched)
                {
                    _searched = true;
                    _host = Find(HostType);
                }
                return _host;
            }
        }

        /// <summary>Whether the bridge listens. null: MCP for Unity is not in this project, or is laid out differently.</summary>
        internal static bool? Running()
        {
            try
            {
                var property = Host?.GetProperty("IsRunning", Any);
                return property == null ? (bool?)null : (bool)property.GetValue(null);
            }
            catch (Exception)
            {
                return null;
            }
        }

        internal static int Port()
        {
            try
            {
                var method = Host?.GetMethod("GetCurrentPort", Any);
                return method == null ? 0 : (int)method.Invoke(null, null);
            }
            catch (Exception)
            {
                return 0;
            }
        }

        private static bool Keeping => EditorPrefs.GetBool(KeepKey, true);
        private static bool OnHttp => EditorPrefs.GetBool(HttpKey, true);

        /// <summary>For ch_editor_status: what a subagent whose MCP for Unity calls fail can look at.</summary>
        internal static object Describe()
        {
            return new { running = Running(), port = Port(), transport = OnHttp ? "http" : "stdio", kept_up = Keeping };
        }

        private static void Watch()
        {
            double now = EditorApplication.timeSinceStartup;
            if (now < _nextLook) return;
            _nextLook = now + LookEvery;

            bool? running = Running();
            if (running == true && _tries > 0 && _downSince >= 0)
            {
                Debug.Log("[Claude Hand] " + ChText.T("MCP for Unity의 연결이 {0}초 동안 꺼져 있어 다시 켰습니다 (포트 {1}).", Math.Round(now - _downSince), Port()));
            }
            if (running != false || EditorApplication.isCompiling || EditorApplication.isUpdating || OnHttp || !Keeping)
            {
                _downSince = -1;
                _tries = 0;
                return;
            }
            if (_downSince < 0)
            {
                _downSince = now;
                return;
            }
            if (now - _downSince < Patience || now < _nextTry) return;

            _nextTry = now + TryEvery;
            _tries++;
            try
            {
                Start();
            }
            catch (Exception ex)
            {
                if (_tries == 1)
                {
                    Debug.LogWarning("[Claude Hand] " + ChText.T("MCP for Unity의 연결이 꺼져 있는데 다시 켜지 못했습니다: {0}  Window > MCP for Unity에서 직접 연결해 주세요.",
                                                              (ex.InnerException ?? ex).Message));
                }
            }
        }

        /// <summary>As MCP for Unity does after a reload: through its transport manager, so its window shows the
        /// connection too. Straight to the bridge when the manager is not where it was.</summary>
        private static void Start()
        {
            var manager = Find(LocatorType)?.GetProperty("TransportManager", Any)?.GetValue(null);
            var mode = Find(ModeType);
            var start = manager != null && mode != null ? manager.GetType().GetMethod("StartAsync", Any, null, new[] { mode }, null) : null;
            if (start != null)
            {
                start.Invoke(manager, new[] { Enum.Parse(mode, "Stdio") });
                return;
            }
            var direct = Host?.GetMethod("StartAutoConnect", Any);
            if (direct == null) throw new InvalidOperationException(ChText.T("MCP for Unity의 연결을 켜는 길을 찾지 못했습니다."));
            direct.Invoke(null, null);
        }
    }
}
