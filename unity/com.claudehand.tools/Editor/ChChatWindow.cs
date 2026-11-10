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
using UnityEditorInternal;
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
        private const double StartWaitSeconds = 120;
        private const int KeptEntries = 100;  // recent history only: the conversation itself lives in Claude Code
        private const int ShownStep = 40;     // entries drawn per page: IMGUI lays out every drawn label each frame
        private const int ShownChars = 1500;  // longer messages show their start; "전체 복사" has the rest
        private const double ProgressStaleSeconds = 300;  // as the server's PROGRESS_TTL
        private const double AlertFreshSeconds = 30;      // older items (history shown again after a restart) never alert
        private static readonly HttpClient Http = new HttpClient { Timeout = TimeSpan.FromSeconds(45) };

        private readonly List<Entry> _entries = new List<Entry>();
        private string _input = string.Empty;
        private Vector2 _scroll;
        private int _shownEntries = ShownStep;
        private JObject _progress;          // what this project's subagent is doing now; null when idle
        private long _progressRev = -1;     // the server's revision of it, so polling wakes on changes
        private double _progressReceivedAt;
        private double _lastBusyRepaint;
        private int _unread;                // replies and permission prompts that came while this window had no focus
        [SerializeField] private string _undoNote;  // goes along with the next message after "되돌리기"
        private bool _followNewest = true;  // scrolled to the bottom: new messages keep it there; reading older ones: stay put
        private float _contentHeight;
        private bool _includeSelection = true;
        private string _status = "연결 중...";
        private string _chatId;
        private string _agent;
        private long _after;
        private int _serverPid;
        private double _lastUserMessageTime = -1;
        private bool _sending;
        private bool _serverMissing;
        // Kept through script reloads (EditorWindow fields with SerializeField survive them).
        [SerializeField] private double _startRequestedAt = -1;
        [SerializeField] private string _pendingText;    // typed while Claude Hand was off: sent once it answers
        [SerializeField] private string _pendingContext;
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
            public string Origin;
            public string Request;
            public string ImagePath;  // "image": a result preview of another window, made by the chat channel
            public string Caption;
        }

        private readonly Dictionary<string, Texture2D> _images = new Dictionary<string, Texture2D>();  // by file; null = gone

        internal sealed class ServerInfo // filled from server.json by Json.NET
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
            ChCheckpoint.Changed += Repaint;
        }

        private void OnDisable()
        {
            _polling?.Cancel();
            EditorApplication.update -= RepaintWhileBusy;
            ChCheckpoint.Changed -= Repaint;
            foreach (var texture in _images.Values.Where(t => t != null)) DestroyImmediate(texture);
            _images.Clear();
        }

        // ------------------------------------------------------------------ server

        internal static string ServerFile
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

        internal static ServerInfo ReadServer()
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
                var path = Path.Combine(Path.GetDirectoryName(ServerFile) ?? string.Empty, "status.json");
                if (File.Exists(path))
                {
                    var status = JObject.Parse(File.ReadAllText(path));
                    var mode = status.Value<string>("mode");
                    if (mode == "passive")
                    {
                        return "채널 서버가 대기 중입니다: " + status.Value<string>("reason") +
                               ". 아래 버튼으로 Claude Hand를 켜거나, 메시지를 보내면 자동으로 켜집니다.";
                    }
                    if (mode == "stopped") return "Claude Hand가 꺼져 있습니다. 메시지를 보내면 자동으로 켜집니다.";
                }
            }
            catch (Exception)
            {
                // fall through to the generic message
            }
            return "Claude Hand가 꺼져 있습니다. 메시지를 보내면 자동으로 켜집니다. 켜도 연결되지 않으면 " +
                   "claude-hand 폴더에서 uv run tools/claude_hand.py chat-status 를 실행하세요.";
        }

        internal static async Task<JObject> Call(ServerInfo server, HttpMethod method, string path, object body, CancellationToken token)
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
                    SetStatus(Starting ? StartingMessage : DescribeMissingServer());
                    if (!await Delay(Starting ? 1000 : 3000, token)) return;
                    continue;
                }

                if (server.pid != _serverPid)
                {
                    // A new supervisor session: its chat history starts empty.
                    _serverPid = server.pid;
                    _entries.Clear();
                    _shownEntries = ShownStep;
                    SetProgress(null, -1);
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
                    if (_pendingText != null) SendPending();

                    var updates = await Call(server, HttpMethod.Get,
                        $"/v1/updates?chat_id={Uri.EscapeDataString(_chatId)}&after={_after}&prev={_progressRev}&wait=20", null, token);
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
            var rev = updates.Value<long?>("progress_rev");
            if (rev.HasValue && rev.Value != _progressRev) SetProgress(updates["progress"] as JObject, rev.Value);
            var items = updates["items"] as JArray;
            if (items == null) return;
            int alerts = items.OfType<JObject>().Count(WantsAttention);
            foreach (var item in items.OfType<JObject>())
            {
                var kind = item.Value<string>("kind");
                if (kind == "permission_resolved")
                {
                    var match = _entries.FirstOrDefault(e => e.Kind == "permission" && e.RequestId == item.Value<string>("request_id"));
                    if (match != null) match.Behavior = item.Value<string>("behavior");
                    continue;
                }
                if (kind == "reply" && item.Value<string>("origin") != "desktop") _lastUserMessageTime = -1;
                _entries.Add(new Entry
                {
                    Kind = kind,
                    Text = item.Value<string>("text"),
                    RequestId = item.Value<string>("request_id"),
                    ToolName = item.Value<string>("tool_name"),
                    Description = item.Value<string>("description"),
                    Preview = item.Value<string>("input_preview"),
                    Origin = item.Value<string>("origin"),
                    Request = item.Value<string>("request"),
                    ImagePath = item.Value<string>("path"),
                    Caption = item.Value<string>("caption"),
                });
            }
            if (_entries.Count > KeptEntries) _entries.RemoveRange(0, _entries.Count - KeptEntries);
            _after = updates.Value<long?>("next") ?? _after;
            if (_followNewest) _scroll.y = float.MaxValue;
            if (alerts > 0) Alert(alerts);
            Repaint();
        }

        /// <summary>
        /// A fresh reply to a request made here, or a permission prompt: worth calling the user back for. Not history
        /// shown again after a restart, and not replies to work asked in the Claude app (the user is there).
        /// </summary>
        private static bool WantsAttention(JObject item)
        {
            var now = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() / 1000.0;
            if (now - (item.Value<double?>("ts") ?? 0) > AlertFreshSeconds) return false;
            var kind = item.Value<string>("kind");
            return kind == "permission" || (kind == "reply" && item.Value<string>("origin") != "desktop");
        }

        /// <summary>The tab says how many came while it had no focus; Unity's taskbar button blinks while it is behind.</summary>
        private void Alert(int count)
        {
            if (!hasFocus)
            {
                _unread += count;
                UpdateTitle();
            }
            if (!InternalEditorUtility.isApplicationActive) ChTaskbar.Flash();
        }

        private void OnFocus()
        {
            if (_unread == 0) return;
            _unread = 0;
            UpdateTitle();
        }

        private void UpdateTitle()
        {
            titleContent = new GUIContent(_unread > 0 ? $"Claude Hand ({_unread})" : "Claude Hand");
        }

        private void Send()
        {
            var text = _input.Trim();
            if (text.Length == 0 || _sending || _pendingText != null) return;
            _followNewest = true;  // your own message and its answer come into view even if you had scrolled up
            var context = _includeSelection ? BuildContext() : string.Empty;
            if (!string.IsNullOrEmpty(_undoNote))  // so Claude does not assume its last changes are still there
            {
                context = string.IsNullOrEmpty(context) ? _undoNote : _undoNote + "\n" + context;
                _undoNote = null;
            }
            if (ReadServer() == null || _serverMissing)
            {
                // Claude Hand is off (or its server.json is left over from a crash): keep the message,
                // start it, and send once it answers.
                _pendingText = text;
                _pendingContext = context;
                _input = string.Empty;
                if (!Starting) StartSupervisor();
                Repaint();
                return;
            }
            Post(text, context, () => _input = string.Empty);
        }

        private void SendPending()
        {
            if (_sending) return;
            var text = _pendingText;
            var context = _pendingContext;
            Post(text, context, () =>
            {
                if (_pendingText == text) _pendingText = _pendingContext = null;
            });
        }

        private async void Post(string text, string context, Action onSent)
        {
            var server = ReadServer();
            if (server == null || _sending) return;
            _sending = true;
            try
            {
                var result = await Call(server, HttpMethod.Post, "/v1/messages", new
                {
                    app = "unity",
                    window = ChPaths.ProjectName,
                    text,
                    context,
                }, CancellationToken.None);
                if (result.Value<bool?>("ok") == true)
                {
                    onSent();
                    _lastUserMessageTime = EditorApplication.timeSinceStartup;
                    if (result.Value<bool?>("delivered") == false) SetStatus("Claude Hand가 아직 연결 중입니다. 연결되면 바로 전달됩니다.");
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
        internal static string BuildContext()
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

        private bool Starting => _startRequestedAt > 0 && EditorApplication.timeSinceStartup - _startRequestedAt < StartWaitSeconds;

        private const string StartingMessage =
            "Claude Hand 창을 여는 중입니다. 그 창에 확인 질문이 보이면 화살표 키로 고르고 Enter를 눌러 주세요 " +
            "(개발용 채널 경고는 I am using this for local development). 연결되면 이 채팅창이 이어서 진행합니다.";

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
                SetStatus(StartingMessage);
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
                bool starting = Starting;
                GUI.enabled = !starting;
                if (GUILayout.Button(starting ? "여는 중... (새로 뜬 Claude Hand 창을 확인하세요)" : "▶  Claude Hand 시작", GUILayout.Height(36)))
                {
                    StartSupervisor();
                }
                GUI.enabled = true;
                EditorGUILayout.HelpBox(starting
                    ? "Claude Hand 창에서 확인을 눌러 주세요. 세션이 일을 시작하면 그 창은 저절로 최소화됩니다. 닫지는 마세요."
                    : "Claude Hand(감독 세션)가 꺼져 있습니다. 버튼을 누르거나 메시지를 보내면 켜집니다.", MessageType.Info);
            }

            _scroll = GUILayout.BeginScrollView(_scroll);
            GUILayout.BeginVertical();
            int first = Math.Max(0, _entries.Count - _shownEntries);
            if (first > 0 && GUILayout.Button($"이전 대화 {first}개 더 보기")) _shownEntries += ShownStep;
            for (int i = first; i < _entries.Count; i++)
            {
                var entry = _entries[i];
                switch (entry.Kind)
                {
                    case "user":
                        EditorGUILayout.LabelField("나", EditorStyles.boldLabel);
                        DrawText(entry.Text, entry.Text);
                        break;
                    case "reply":
                        if (entry.Origin == "desktop")
                        {
                            EditorGUILayout.LabelField("데스크톱 앱에서", EditorStyles.boldLabel);
                            if (!string.IsNullOrEmpty(entry.Request)) EditorGUILayout.LabelField(Shorten(entry.Request, 300), EditorStyles.wordWrappedMiniLabel);
                        }
                        EditorGUILayout.LabelField("Claude", EditorStyles.boldLabel);
                        DrawText(entry.Text, string.IsNullOrEmpty(entry.Request) ? entry.Text : entry.Request + "\n\n" + entry.Text);
                        break;
                    case "permission":
                        DrawPermission(entry);
                        break;
                    case "notice":  // shown only here, not sent anywhere
                        EditorGUILayout.HelpBox(entry.Text, MessageType.Info);
                        break;
                    case "image":
                        DrawImage(entry);
                        break;
                }
                GUILayout.Space(6);
            }
            if (_pendingText != null)
            {
                EditorGUILayout.LabelField("나 (Claude Hand가 켜지면 보냅니다)", EditorStyles.boldLabel);
                EditorGUILayout.LabelField(_pendingText, EditorStyles.wordWrappedLabel);
                if (!Starting && GUILayout.Button("보내지 않고 지우기", GUILayout.Width(140))) _pendingText = _pendingContext = null;
            }
            if (_lastUserMessageTime > 0 && EditorApplication.timeSinceStartup - _lastUserMessageTime > NoReplyHintSeconds)
            {
                EditorGUILayout.HelpBox("1분 넘게 답이 없습니다. 감독 세션을 start-supervisor.cmd(채널을 켜고 시작)로 열었는지, " +
                                        "Claude Code 창에 확인을 기다리는 질문이 없는지 봐 주세요.", MessageType.Info);
            }
            GUILayout.EndVertical();
            if (Event.current.type == EventType.Repaint) _contentHeight = GUILayoutUtility.GetLastRect().height;
            GUILayout.EndScrollView();
            if (Event.current.type == EventType.Repaint)
            {
                _followNewest = _scroll.y + GUILayoutUtility.GetLastRect().height >= _contentHeight - 30f;
            }
            DrawProgress();
            DrawUndo();

            var e = Event.current;
            bool submit = e.type == EventType.KeyDown && e.keyCode == KeyCode.Return && (e.control || e.command);
            _input = EditorGUILayout.TextArea(_input, GUILayout.MinHeight(48));
            GUILayout.BeginHorizontal();
            _includeSelection = EditorGUILayout.ToggleLeft("선택 항목 함께 보내기", _includeSelection);
            GUILayout.FlexibleSpace();
            GUI.enabled = !_sending && _pendingText == null && _input.Trim().Length > 0;
            if (GUILayout.Button("보내기 (Ctrl+Enter)", GUILayout.Width(140)) || (submit && GUI.enabled))
            {
                Send();
                if (submit) e.Use();
            }
            GUI.enabled = true;
            GUILayout.EndHorizontal();
        }

        /// <summary>
        /// "Undo the last request": back to the undo point the subagent's guard set before its first action. Not while
        /// it is still working, and only after a confirmation that says edits made by hand since then go too.
        /// </summary>
        private void DrawUndo()
        {
            var marks = ChCheckpoint.Marks;
            if (marks.Count == 0 || _progress != null) return;
            var mark = marks[marks.Count - 1];
            if (!GUILayout.Button($"되돌리기: {mark.label} ({mark.time})", EditorStyles.miniButton)) return;
            var ok = EditorUtility.DisplayDialog("Claude Hand 되돌리기",
                $"'{mark.label}' 요청({mark.time}) 이후 이 Unity에서 바뀐 것을 모두 되돌립니다.\n" +
                "그 뒤에 직접 고친 것도 함께 되돌아갑니다. 저장해 둔 씬 파일은 다시 저장해야 바뀝니다.", "되돌리기", "취소");
            if (!ok) return;
            var undone = ChCheckpoint.UndoLast();
            if (undone == null) return;
            _entries.Add(new Entry { Kind = "notice", Text = $"되돌렸어요: '{undone.label}' ({undone.time}) 이후의 변경" });
            _undoNote = $"[사용자가 채팅창에서 '{undone.label}' ({undone.time}) 요청 이후의 Unity 변경을 Undo로 되돌렸음]";
            _followNewest = true;
            _scroll.y = float.MaxValue;
            GUIUtility.ExitGUI();  // the dialog and the undo changed things mid-frame
        }

        private void SetProgress(JObject progress, long rev)
        {
            _progress = progress;
            _progressRev = rev;
            _progressReceivedAt = EditorApplication.timeSinceStartup;
            EditorApplication.update -= RepaintWhileBusy;
            if (progress != null) EditorApplication.update += RepaintWhileBusy;  // the seconds count up
        }

        private void RepaintWhileBusy()
        {
            if (EditorApplication.timeSinceStartup - _lastBusyRepaint < 0.5) return;
            _lastBusyRepaint = EditorApplication.timeSinceStartup;
            Repaint();
        }

        /// <summary>
        /// One line just above the input while this project's subagent works, saying what is being done (no bar).
        /// The reply removes it.
        /// </summary>
        private void DrawProgress()
        {
            if (_progress == null) return;
            if (EditorApplication.timeSinceStartup - _progressReceivedAt > ProgressStaleSeconds)
            {
                SetProgress(null, _progressRev);  // no news for a long while: the supervisor never answered
                return;
            }
            int actions = _progress.Value<int?>("actions") ?? 0;
            double elapsed = (_progress.Value<double?>("elapsed") ?? 0) + EditorApplication.timeSinceStartup - _progressReceivedAt;
            var parts = new List<string> { $"작업 중 · {(int)elapsed}초" };
            var tool = _progress.Value<string>("tool");
            if (!string.IsNullOrEmpty(tool)) parts.Add($"{tool} {_progress.Value<string>("detail")}".Trim());
            if (actions > 0) parts.Add($"{actions}번째 동작");
            EditorGUILayout.LabelField(string.Join(" · ", parts), EditorStyles.wordWrappedMiniLabel);
        }

        /// <summary>
        /// A picture of another window that a request made here changed (Blender's viewport, say), attached by the chat
        /// channel when the reply came; clicking it opens the file full size.
        /// </summary>
        private void DrawImage(Entry entry)
        {
            EditorGUILayout.LabelField(entry.Caption ?? "결과 미리보기", EditorStyles.boldLabel);
            var texture = LoadImage(entry.ImagePath);
            if (texture == null)
            {
                EditorGUILayout.LabelField("(그림 파일이 지워졌습니다)", EditorStyles.miniLabel);
                return;
            }
            float width = Mathf.Min(texture.width, position.width - 30);
            float height = width * texture.height / texture.width;
            if (height > 360)
            {
                width *= 360 / height;
                height = 360;
            }
            var rect = GUILayoutUtility.GetRect(width, height, GUILayout.Width(width), GUILayout.Height(height));
            GUI.DrawTexture(rect, texture, ScaleMode.ScaleToFit);
            EditorGUIUtility.AddCursorRect(rect, MouseCursor.Zoom);
            if (Event.current.type == EventType.MouseDown && rect.Contains(Event.current.mousePosition))
            {
                EditorUtility.OpenWithDefaultApp(entry.ImagePath);
                Event.current.Use();
            }
            EditorGUILayout.LabelField("누르면 크게 봅니다", EditorStyles.miniLabel);
        }

        private Texture2D LoadImage(string path)
        {
            if (string.IsNullOrEmpty(path)) return null;
            if (_images.TryGetValue(path, out var cached)) return cached;
            Texture2D texture = null;
            if (File.Exists(path))
            {
                texture = new Texture2D(2, 2) { hideFlags = HideFlags.HideAndDontSave };
                if (!texture.LoadImage(File.ReadAllBytes(path)))
                {
                    DestroyImmediate(texture);
                    texture = null;
                }
            }
            _images[path] = texture;
            return texture;
        }

        /// <summary>A message, or its first ShownChars characters and a button that copies all of it.</summary>
        private static void DrawText(string text, string copy)
        {
            text = text ?? string.Empty;
            EditorGUILayout.LabelField(Shorten(text, ShownChars), EditorStyles.wordWrappedLabel);
            if (text.Length <= ShownChars) return;
            GUILayout.BeginHorizontal();
            EditorGUILayout.LabelField($"…{text.Length - ShownChars}자 더 있음", EditorStyles.miniLabel);
            if (GUILayout.Button("전체 복사", EditorStyles.miniButton, GUILayout.Width(70))) GUIUtility.systemCopyBuffer = copy;
            GUILayout.EndHorizontal();
        }

        private static string Shorten(string text, int max) =>
            string.IsNullOrEmpty(text) || text.Length <= max ? text ?? string.Empty : text.Substring(0, max) + "…";

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
