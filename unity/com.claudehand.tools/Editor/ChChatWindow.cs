using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Net.Http;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace ClaudeHand.Editor
{
    /// <summary>
    /// Window > Claude Hand > Chat: talk to this project's claude-hand subagent from inside Unity.
    /// Messages go to the claude-hand-chat channel (tools/chat_channel.py), which the supervisor
    /// Claude Code session runs; the supervisor hands them to unity-&lt;project&gt; and answers here.
    /// </summary>
    public sealed class ChChatWindow : EditorWindow
    {
        private const double NoReplyHintSeconds = 60;
        private static readonly HttpClient Http = new HttpClient { Timeout = TimeSpan.FromSeconds(45) };

        private readonly List<Entry> _entries = new List<Entry>();
        private string _input = string.Empty;
        private Vector2 _scroll;
        private bool _includeSelection = true;
        private string _status = "연결 중...";
        private string _chatId;
        private string _agent;
        private long _after;
        private int _serverPid;
        private double _lastUserMessageTime = -1;
        private bool _sending;
        private bool _serverMissing;
        private double _startRequestedAt = -1;
        private CancellationTokenSource _polling;

        private sealed class Entry
        {
            public string Kind;
            public string Text;
            public string RequestId;
            public string ToolName;
            public string Description;
            public string Preview;
            public string Behavior;
        }

        private sealed class ServerInfo // filled from server.json by Json.NET
        {
            public int port { get; set; }
            public string token { get; set; }
            public int pid { get; set; }
        }

        [MenuItem("Window/Claude Hand/Chat")]
        private static void Open()
        {
            GetWindow<ChChatWindow>("Claude Hand");
        }

        private void OnEnable()
        {
            _entries.Clear();
            _after = 0;
            _chatId = null;
            _polling = new CancellationTokenSource();
            PollLoop(_polling.Token);
        }

        private void OnDisable()
        {
            _polling?.Cancel();
        }

        // ------------------------------------------------------------------ server

        private static string ServerFile
        {
            get
            {
                var home = Environment.GetEnvironmentVariable("CLAUDE_HAND_HOME");
                if (string.IsNullOrEmpty(home))
                {
                    home = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), ".claude-hand");
                }
                return Path.Combine(home, "chat", "server.json");
            }
        }

        private static ServerInfo ReadServer()
        {
            try
            {
                return File.Exists(ServerFile) ? JsonConvert.DeserializeObject<ServerInfo>(File.ReadAllText(ServerFile)) : null;
            }
            catch (Exception)
            {
                return null;
            }
        }

        /// <summary>Why there is no server.json, from the status.json the channel writes on every start.</summary>
        private static string DescribeMissingServer()
        {
            try
            {
                var config = Path.Combine(Path.GetDirectoryName(Path.GetDirectoryName(ServerFile)) ?? string.Empty, "config.json");
                if (File.Exists(config) && JObject.Parse(File.ReadAllText(config)).Value<string>("mode") == "desktop")
                {
                    return "데스크톱 앱 모드입니다. Claude 데스크톱 앱의 Code 탭에서 대화하세요. " +
                           "이 채팅창에서도 대화하려면 아래 버튼으로 Claude Hand를 켜세요.";
                }
            }
            catch (Exception)
            {
                // fall through to the server status
            }
            try
            {
                var path = Path.Combine(Path.GetDirectoryName(ServerFile) ?? string.Empty, "status.json");
                if (File.Exists(path))
                {
                    var status = JObject.Parse(File.ReadAllText(path));
                    var mode = status.Value<string>("mode");
                    if (mode == "passive")
                    {
                        return "채널 서버가 대기 중입니다: " + status.Value<string>("reason") +
                               ". 감독 세션을 claude-hand 폴더의 start-supervisor.cmd로 여세요.";
                    }
                    if (mode == "stopped") return "감독 세션이 종료됐습니다. start-supervisor.cmd로 다시 여세요.";
                }
            }
            catch (Exception)
            {
                // fall through to the generic message
            }
            return "감독 세션의 채널 서버가 시작되지 않았습니다. Claude Code에서 /mcp로 claude-hand-chat 상태를 보거나, " +
                   "claude-hand 폴더에서 uv run tools/claude_hand.py chat-status 를 실행하세요.";
        }

        private static async Task<JObject> Call(ServerInfo server, HttpMethod method, string path, object body, CancellationToken token)
        {
            using (var request = new HttpRequestMessage(method, $"http://127.0.0.1:{server.port}{path}"))
            {
                request.Headers.Add("X-Claude-Hand-Token", server.token);
                if (body != null)
                {
                    request.Content = new StringContent(JsonConvert.SerializeObject(body), Encoding.UTF8, "application/json");
                }
                using (var response = await Http.SendAsync(request, token))
                {
                    var text = await response.Content.ReadAsStringAsync();
                    return JObject.Parse(string.IsNullOrEmpty(text) ? "{}" : text);
                }
            }
        }

        private async void PollLoop(CancellationToken token)
        {
            while (!token.IsCancellationRequested)
            {
                var server = ReadServer();
                if (server == null)
                {
                    SetServerMissing(true);
                    SetStatus(DescribeMissingServer());
                    if (!await Delay(3000, token)) return;
                    continue;
                }

                if (server.pid != _serverPid)
                {
                    // A new supervisor session: its chat history starts empty.
                    _serverPid = server.pid;
                    _entries.Clear();
                    _after = 0;
                    _chatId = null;
                }

                try
                {
                    if (_chatId == null || _agent == null) // re-check until `sync` has made this project's subagent
                    {
                        var resolved = await Call(server, HttpMethod.Get,
                            $"/v1/resolve?app=unity&window={Uri.EscapeDataString(ChPaths.ProjectName)}", null, token);
                        _chatId = resolved.Value<string>("chat_id");
                        _agent = resolved.Value<string>("agent");
                    }

                    SetServerMissing(false);
                    SetStatus(_agent != null
                        ? $"담당: {_agent}"
                        : "이 프로젝트의 하위 세션이 없습니다. uv run tools/claude_hand.py sync 를 실행하세요.");

                    var updates = await Call(server, HttpMethod.Get,
                        $"/v1/updates?chat_id={Uri.EscapeDataString(_chatId)}&after={_after}&wait=20", null, token);
                    if (updates.Value<bool?>("ok") == false)
                    {
                        _serverPid = 0; // token changed or server restarted: read server.json again
                        if (!await Delay(1000, token)) return;
                        continue;
                    }
                    Apply(updates);
                }
                catch (OperationCanceledException) when (token.IsCancellationRequested)
                {
                    return;
                }
                catch (Exception ex)
                {
                    SetServerMissing(true);
                    SetStatus("감독 세션에 연결할 수 없습니다: " + ex.Message);
                    if (!await Delay(3000, token)) return;
                }
            }
        }

        private void Apply(JObject updates)
        {
            var items = updates["items"] as JArray;
            if (items == null) return;
            foreach (var item in items.OfType<JObject>())
            {
                var kind = item.Value<string>("kind");
                if (kind == "permission_resolved")
                {
                    var match = _entries.FirstOrDefault(e => e.Kind == "permission" && e.RequestId == item.Value<string>("request_id"));
                    if (match != null) match.Behavior = item.Value<string>("behavior");
                    continue;
                }
                if (kind == "reply") _lastUserMessageTime = -1;
                _entries.Add(new Entry
                {
                    Kind = kind,
                    Text = item.Value<string>("text"),
                    RequestId = item.Value<string>("request_id"),
                    ToolName = item.Value<string>("tool_name"),
                    Description = item.Value<string>("description"),
                    Preview = item.Value<string>("input_preview"),
                });
            }
            _after = updates.Value<long?>("next") ?? _after;
            _scroll.y = float.MaxValue;
            Repaint();
        }

        private async void Send()
        {
            var text = _input.Trim();
            var server = ReadServer();
            if (text.Length == 0 || server == null || _sending) return;
            _sending = true;
            try
            {
                var result = await Call(server, HttpMethod.Post, "/v1/messages", new
                {
                    app = "unity",
                    window = ChPaths.ProjectName,
                    text,
                    context = _includeSelection ? BuildContext() : string.Empty,
                }, CancellationToken.None);
                if (result.Value<bool?>("ok") == true)
                {
                    _input = string.Empty;
                    _lastUserMessageTime = EditorApplication.timeSinceStartup;
                    if (result.Value<bool?>("delivered") == false) SetStatus("Claude 세션이 아직 연결 중입니다. 잠시 뒤 답이 옵니다.");
                }
                else
                {
                    SetStatus("보내지 못했습니다: " + (result.Value<string>("error") ?? "알 수 없는 오류"));
                }
            }
            catch (Exception ex)
            {
                SetStatus("보내지 못했습니다: " + ex.Message);
            }
            finally
            {
                _sending = false;
                Repaint();
            }
        }

        private async void AnswerPermission(Entry entry, string behavior)
        {
            var server = ReadServer();
            if (server == null) return;
            try
            {
                await Call(server, HttpMethod.Post, "/v1/permission", new { request_id = entry.RequestId, behavior }, CancellationToken.None);
            }
            catch (Exception ex)
            {
                SetStatus("응답을 보내지 못했습니다: " + ex.Message);
            }
        }

        /// <summary>What the user is looking at, so "this object" in a message means something.</summary>
        private static string BuildContext()
        {
            var lines = new List<string> { "활성 씬: " + SceneManager.GetActiveScene().name };
            var selected = Selection.gameObjects;
            if (selected.Length > 0)
            {
                lines.Add("선택한 오브젝트: " + string.Join(", ", selected.Take(10).Select(go => ChRefs.HierarchyPath(go.transform))));
            }
            var assets = Selection.assetGUIDs.Take(10).Select(AssetDatabase.GUIDToAssetPath).Where(p => !string.IsNullOrEmpty(p)).ToList();
            if (assets.Count > 0) lines.Add("선택한 에셋: " + string.Join(", ", assets));
            return string.Join("\n", lines);
        }

        private static async Task<bool> Delay(int milliseconds, CancellationToken token)
        {
            try
            {
                await Task.Delay(milliseconds, token);
                return true;
            }
            catch (OperationCanceledException)
            {
                return false;
            }
        }

        private void SetServerMissing(bool missing)
        {
            if (_serverMissing == missing) return;
            _serverMissing = missing;
            if (!missing) _startRequestedAt = -1;
            Repaint();
        }

        /// <summary>start-supervisor next to this claude-hand: from ~/.claude-hand/config.json (written by setup),
        /// else from where this package is installed (claude-hand/unity/com.claudehand.tools).</summary>
        private static string FindStartScript()
        {
            try
            {
                var config = Path.Combine(Path.GetDirectoryName(Path.GetDirectoryName(ServerFile)) ?? string.Empty, "config.json");
                if (File.Exists(config))
                {
                    var script = JObject.Parse(File.ReadAllText(config)).Value<string>("start_script");
                    if (!string.IsNullOrEmpty(script) && File.Exists(script)) return script;
                }
            }
            catch (Exception)
            {
                // fall back to the package location
            }
            var repo = Path.GetFullPath(Path.Combine(Path.GetFullPath("Packages/com.claudehand.tools"), "..", ".."));
            var name = Application.platform == RuntimePlatform.WindowsEditor ? "start-supervisor.cmd" : "start-supervisor.sh";
            var candidate = Path.Combine(repo, name);
            return File.Exists(candidate) ? candidate : null;
        }

        private void StartSupervisor()
        {
            var script = FindStartScript();
            if (script == null)
            {
                SetStatus("start-supervisor를 찾지 못했습니다. 바탕화면의 Claude Hand 아이콘을 누르거나 claude-hand 폴더의 start-supervisor.cmd를 실행하세요.");
                return;
            }
            try
            {
                ProcessStartInfo info;
                if (Application.platform == RuntimePlatform.WindowsEditor)
                {
                    info = new ProcessStartInfo("cmd.exe", $"/c start \"Claude Hand\" \"{script}\"") { UseShellExecute = false, CreateNoWindow = true };
                }
                else if (Application.platform == RuntimePlatform.OSXEditor)
                {
                    info = new ProcessStartInfo("open", $"-a Terminal \"{script}\"") { UseShellExecute = false };
                }
                else
                {
                    info = new ProcessStartInfo("x-terminal-emulator", $"-e \"{script}\"") { UseShellExecute = false };
                }
                Process.Start(info);
                _startRequestedAt = EditorApplication.timeSinceStartup;
                SetStatus("Claude Hand 창을 여는 중입니다...");
            }
            catch (Exception ex)
            {
                SetStatus("Claude Hand를 시작하지 못했습니다: " + ex.Message);
            }
        }

        private void SetStatus(string status)
        {
            if (_status == status) return;
            _status = status;
            Repaint();
        }

        // ------------------------------------------------------------------ UI

        private void OnGUI()
        {
            EditorGUILayout.LabelField(_status, EditorStyles.wordWrappedMiniLabel);
            if (_serverMissing)
            {
                bool starting = _startRequestedAt > 0 && EditorApplication.timeSinceStartup - _startRequestedAt < 90;
                GUI.enabled = !starting;
                if (GUILayout.Button(starting ? "여는 중... (새로 뜬 Claude Hand 창을 확인하세요)" : "▶  Claude Hand 시작", GUILayout.Height(36)))
                {
                    StartSupervisor();
                }
                GUI.enabled = true;
                EditorGUILayout.HelpBox(starting
                    ? "새 창이 뜹니다. 처음 한 번은 그 창에서 확인 질문에 답해 주세요(화살표 키로 고르고 Enter). 그 창은 닫지 말고 최소화해 두세요."
                    : "Claude Hand(감독 세션)가 꺼져 있습니다. 버튼을 누르면 켜집니다.", MessageType.Info);
            }

            _scroll = GUILayout.BeginScrollView(_scroll);
            foreach (var entry in _entries)
            {
                switch (entry.Kind)
                {
                    case "user":
                        EditorGUILayout.LabelField("나", EditorStyles.boldLabel);
                        EditorGUILayout.LabelField(entry.Text, EditorStyles.wordWrappedLabel);
                        break;
                    case "reply":
                        EditorGUILayout.LabelField("Claude", EditorStyles.boldLabel);
                        EditorGUILayout.LabelField(entry.Text, EditorStyles.wordWrappedLabel);
                        break;
                    case "permission":
                        DrawPermission(entry);
                        break;
                }
                GUILayout.Space(6);
            }
            if (_lastUserMessageTime > 0 && EditorApplication.timeSinceStartup - _lastUserMessageTime > NoReplyHintSeconds)
            {
                EditorGUILayout.HelpBox("1분 넘게 답이 없습니다. 감독 세션을 start-supervisor.cmd(채널을 켜고 시작)로 열었는지, " +
                                        "Claude Code 창에 확인을 기다리는 질문이 없는지 봐 주세요.", MessageType.Info);
            }
            GUILayout.EndScrollView();

            var e = Event.current;
            bool submit = e.type == EventType.KeyDown && e.keyCode == KeyCode.Return && (e.control || e.command);
            _input = EditorGUILayout.TextArea(_input, GUILayout.MinHeight(48));
            GUILayout.BeginHorizontal();
            _includeSelection = EditorGUILayout.ToggleLeft("선택 항목 함께 보내기", _includeSelection);
            GUILayout.FlexibleSpace();
            GUI.enabled = !_sending && _input.Trim().Length > 0;
            if (GUILayout.Button("보내기 (Ctrl+Enter)", GUILayout.Width(140)) || (submit && GUI.enabled))
            {
                Send();
                if (submit) e.Use();
            }
            GUI.enabled = true;
            GUILayout.EndHorizontal();
        }

        private void DrawPermission(Entry entry)
        {
            var body = $"Claude가 '{entry.ToolName}' 실행 허락을 요청합니다.\n{entry.Description}";
            if (!string.IsNullOrEmpty(entry.Preview)) body += "\n" + entry.Preview;
            EditorGUILayout.HelpBox(body, MessageType.Warning);
            if (entry.Behavior != null)
            {
                EditorGUILayout.LabelField(entry.Behavior == "allow" ? "→ 허용됨" : "→ 거부됨", EditorStyles.miniLabel);
                return;
            }
            GUILayout.BeginHorizontal();
            if (GUILayout.Button("허용")) AnswerPermission(entry, "allow");
            if (GUILayout.Button("거부")) AnswerPermission(entry, "deny");
            GUILayout.EndHorizontal();
        }
    }
}
