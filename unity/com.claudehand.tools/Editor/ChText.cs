using System;
using System.Collections.Generic;

namespace ClaudeHand.Editor
{
    /// <summary>
    /// The language of what this package says: Korean as it stands in the code, or English from the table below.
    ///
    ///   T("이전 대화 {0}개 더 보기", first)
    ///
    /// The Korean text is the source and stays where it is said; when the settings say "en" (ChSettings.Language:
    /// config.json's "language", which the settings window writes) T looks it up here and fills the same {0} {1} in.
    /// So Korean comes out exactly as written, and a text with no English yet comes out in Korean rather than not at
    /// all (tools/tests/test_lang.py fails on one). N() marks a text that is not said where it is written (a table of
    /// words filled once): it goes through T where it is said, T(ModeWords[...]), since a table filled through T
    /// would keep the language of the moment it was filled.
    ///
    /// The Python tools (tools/handlib/english.py) and the Blender add-on have tables of their own, built the same
    /// way; that test holds a text two of them say to the same English. Use it with
    /// <c>using static ClaudeHand.Editor.ChText;</c>.
    /// </summary>
    internal static class ChText
    {
        /// <summary>"ko" or "en" (see <see cref="ChSettings.Language"/>); the self-test sets Forced for a moment.</summary>
        public static string Language => Forced ?? ChSettings.Language;

        internal static string Forced;

        /// <summary>The text in the language now in use.</summary>
        public static string T(string korean)
        {
            if (korean == null) return null;
            return Language == "en" && English.TryGetValue(korean, out var said) ? said : korean;
        }

        /// <summary>
        /// The text in the language now in use, its {0} {1} filled in. A text that counts something has a second English
        /// wording for when the count is one (<see cref="EnglishOne"/>): "1 rule", not "1 rules".
        /// </summary>
        public static string T(string korean, params object[] values)
        {
            if (Language == "en" && korean != null && EnglishOne.TryGetValue(korean, out var one) && one.Key < values.Length &&
                values[one.Key] is int count && count == 1)
            {
                return string.Format(one.Value, values);
            }
            return string.Format(T(korean), values);
        }

        /// <summary>Marks a Korean text that is said elsewhere, through T. Returns it as it is.</summary>
        public static string N(string korean) => korean;

        /// <summary>Whether a text has its English (the self-test asks).</summary>
        internal static bool Has(string korean) => English.ContainsKey(korean);

        private static readonly Dictionary<string, string> English = new Dictionary<string, string>(StringComparer.Ordinal)
        {
            // ChChatWindow.cs
            { "{0} · {1}번째 ({2:HH:mm})", "{0} · #{1} ({2:HH:mm})" },
            {
                "채널 서버가 대기 중입니다: {0}. 아래 버튼으로 Claude Hand를 켜거나, 메시지를 보내면 자동으로 켜집니다.",
                "The channel server is waiting: {0}. Turn Claude Hand on with the button below, or send a message and it turns on by " +
                "itself."
            },
            { "Claude Hand가 꺼져 있습니다. 메시지를 보내면 자동으로 켜집니다.", "Claude Hand is off. Send a message and it turns on by itself." },
            {
                "Claude Hand가 꺼져 있습니다. 메시지를 보내면 자동으로 켜집니다. 켜도 연결되지 않으면 claude-hand 폴더에서 uv run tools/claude_hand.py chat-status 를 실행하세요.",
                "Claude Hand is off. Send a message and it turns on by itself. If it still does not connect once on, run uv run " +
                "tools/claude_hand.py chat-status in the claude-hand folder."
            },
            { "담당: {0}", "Handled by: {0}" },
            {
                "이 프로젝트의 하위 세션이 없습니다. uv run tools/claude_hand.py sync 를 실행하세요.",
                "This project has no subagent. Run uv run tools/claude_hand.py sync."
            },
            { "감독 세션에 연결할 수 없습니다: ", "Cannot connect to the supervisor session: " },
            {
                "Claude Hand가 아직 연결 중입니다. 연결되면 바로 전달됩니다.",
                "Claude Hand is still connecting. The message is delivered as soon as it is connected."
            },
            { "보내지 못했습니다: ", "Could not send: " },
            { "알 수 없는 오류", "unknown error" },
            { "응답을 보내지 못했습니다: ", "The answer could not be sent: " },
            { "활성 씬: ", "active scene: " },
            { "선택한 오브젝트: ", "selected objects: " },
            { "선택한 에셋: ", "selected assets: " },
            {
                "Claude Hand 창을 여는 중입니다. 그 창에 확인 질문이 보이면 화살표 키로 고르고 Enter를 눌러 주세요 (개발용 채널 경고는 I am using this for local development). 연결되면 " +
                "이 채팅창이 이어서 진행합니다.",
                "Opening the Claude Hand window. If it shows a question, choose with the arrow keys and press Enter (for the development " +
                "channel warning: I am using this for local development). Once connected, this chat window carries on."
            },
            {
                "start-supervisor를 찾지 못했습니다. 바탕화면의 Claude Hand 아이콘을 누르거나 claude-hand 폴더의 start-supervisor.cmd를 실행하세요.",
                "start-supervisor was not found. Click the Claude Hand icon on the Desktop, or run start-supervisor.cmd in the claude-hand " +
                "folder."
            },
            { "Claude Hand를 시작하지 못했습니다: ", "Claude Hand could not be started: " },
            { "연결 중...", "Connecting..." },
            { "여는 중... (새로 뜬 Claude Hand 창을 확인하세요)", "Opening... (check the new Claude Hand window)" },
            { "▶  Claude Hand 시작", "▶  Start Claude Hand" },
            {
                "Claude Hand 창에서 확인을 눌러 주세요. 세션이 일을 시작하면 그 창은 저절로 최소화됩니다. 닫지는 마세요.",
                "Confirm in the Claude Hand window. Once the session starts working, that window minimizes by itself. Do not close it."
            },
            {
                "Claude Hand(감독 세션)가 꺼져 있습니다. 버튼을 누르거나 메시지를 보내면 켜집니다.",
                "Claude Hand (the supervisor session) is off. Press the button or send a message to turn it on."
            },
            { "이전 대화 {0}개 더 보기", "Show {0} earlier messages" },
            { "나", "Me" },
            { "데스크톱 앱에서", "From the desktop app" },
            { "나 (Claude Hand가 켜지면 보냅니다)", "Me (sent once Claude Hand is on)" },
            { "보내지 않고 지우기", "Discard without sending" },
            {
                "1분 넘게 답이 없습니다. 감독 세션을 start-supervisor.cmd(채널을 켜고 시작)로 열었는지, Claude Code 창에 확인을 기다리는 질문이 없는지 봐 주세요.",
                "No answer for over a minute. See whether the supervisor session was opened with start-supervisor.cmd (which starts it " +
                "with the channel on), and whether the Claude Code window has a question waiting for your confirmation."
            },
            { "선택 항목 함께 보내기", "Send the selection along" },
            { "보내기 (Ctrl+Enter)", "Send (Ctrl+Enter)" },
            { "되돌리기: {0} ({1})", "Undo: {0} ({1})" },
            { "Claude Hand 되돌리기", "Claude Hand: Undo" },
            {
                "'{0}' 요청({1}) 이후 이 Unity에서 바뀐 것을 모두 되돌립니다.\n",
                "Everything changed in this Unity since the request '{0}' ({1}) is undone.\n"
            },
            {
                "그 뒤에 직접 고친 것도 함께 되돌아갑니다(파일은 이 요청이 일하는 동안 바뀐 것만). 저장해 둔 씬 파일은 다시 저장해야 바뀝니다.",
                "What you changed by hand after it goes back too (of files, only what changed while this request was at work). A scene " +
                "file that was saved changes only when it is saved again."
            },
            { "되돌리기", "Undo" },
            { "취소", "Cancel" },
            { "이 창 + ", "this window + " },
            { "'{0}' 요청이 바꾼 것을 되돌립니다: {1}.\n", "What the request '{0}' changed is undone: {1}.\n" },
            {
                "이 Unity에서는 그 요청 이후 바뀐 것을 모두 되돌립니다. 그 뒤에 직접 고친 것도 함께 되돌아갑니다(파일은 이 요청이 일하는 동안 바뀐 것만). 저장해 둔 씬 파일은 다시 저장해야 바뀝니다.\n",
                "In this Unity everything changed since that request is undone. What you changed by hand after it goes back too (of files, " +
                "only what changed while this request was at work). A scene file that was saved changes only when it is saved again.\n"
            },
            {
                "다른 창({0})은 그 창에 더 새 요청이 있거나 작업 중이면 건너뛰고, 그렇게 알려 줍니다.",
                "The other windows ({0}) are skipped when they have a newer request or are at work, and you are told so."
            },
            { "다른 창을 되돌리지 못했습니다", "The other windows could not be undone" },
            { "\n이 요청이 바꾼 파일 {0}개는 이전 것으로 돌아갑니다.", "\n{0} files this request changed go back to what they were." },
            { "\n이 요청으로 생긴 에셋 {0}개는 프로젝트에서 빠집니다({1}에 보관).", "\n{0} assets this request made leave the project (kept in {1})." },
            { "\n이 요청이 지운 에셋 {0}개는 되살아납니다.", "\n{0} assets this request deleted come back." },
            { "\n이 요청이 옮기거나 이름을 바꾼 에셋 {0}개는 원래 자리로 갑니다.", "\n{0} assets this request moved or renamed go back to their places." },
            { "\n이전 내용이 보관되지 않은 파일 {0}개는 그대로 남습니다({1}).", "\n{0} files whose earlier content was not kept stay as they are ({1})." },
            { "되돌렸어요: '{0}' ({1}) 이후의 변경.", "Undone: the changes since '{0}' ({1})." },
            {
                "[사용자가 채팅창에서 '{0}' ({1}) 요청 이후의 Unity 변경을 Undo로 되돌렸음",
                "[The user undid, with Undo in the chat panel, the changes made in Unity since the request '{0}' ({1})"
            },
            { "Claude Hand가 받지 않았습니다", "Claude Hand did not take it" },
            { "다른 창 ({0})", "Other windows ({0})" },
            { "{0} · {1}초", "{0} · {1}s" },
            { "중지하는 중", "Stopping" },
            { "작업 중", "Working" },
            { "{0}번째 동작", "action {0}" },
            { "중지", "Stop" },
            { "중지를 보내지 못했습니다: ", "Stop could not be sent: " },
            { "결과 미리보기", "Result preview" },
            { "(그림 파일이 지워졌습니다)", "(the picture file was deleted)" },
            { "누르면 크게 봅니다", "Click to view it larger" },
            { "…{0}자 더 있음", "…{0} more characters" },
            { "전체 복사", "Copy all" },
            { "Claude가 이 프로젝트를 바꿀 수 있습니다. 아래 규칙이 가리키는 것만 빼고요.", "Claude can change this project, except what the rules below cover." },
            { "보기만: Claude는 이 프로젝트를 읽기만 합니다.", "Look only: Claude only reads this project." },
            { "잠금: Claude는 이 프로젝트에서 아무 일도 하지 않습니다.", "Locked: Claude does nothing in this project." },
            { "보호 [열림] 규칙 없음", "Protect [open] no rules" },
            { "보호 [잠김] {0}규칙 {1}개", "Protect [locked] {0}{1} rules" },
            { " · 읽지 못한 파일 있음", " · a file could not be read" },
            { "풀기", "Release" },
            { "보호 규칙 풀기", "Release a protection rule" },
            { "'{0}' 규칙을 풉니다. Claude가 다시 바꿀 수 있게 됩니다.", "The rule '{0}' is released. Claude can change it again." },
            {
                "읽지 못한 규칙 파일(고칠 때까지 Claude의 바꾸는 호출을 막습니다): ",
                "A rules file that could not be read (until it is fixed, Claude's calls that change anything are refused): "
            },
            { "이번만 (Claude Hand를 끌 때까지)", "This run only (until Claude Hand is closed)" },
            { "선택한 것 보호", "Protect the selection" },
            { "이 창 보기만", "Look only" },
            { "이 창 잠금", "Lock this window" },
            { "동작 금지", "Forbid an action" },
            { "{0} 금지", "Forbid: {0}" },
            { "보호할 오브젝트(Hierarchy)나 에셋(Project)을 먼저 고르세요.", "Select the objects (Hierarchy) or assets (Project) to protect first." },
            { "Claude가 제안한 보호 규칙\n", "A protection rule Claude proposes\n" },
            { "→ 규칙에 더함", "→ Added to the rules" },
            { "→ 더하지 않음", "→ Not added" },
            { "추가", "Add" },
            { "Claude가 '{0}' 실행 허락을 요청합니다.\n{1}", "Claude asks permission to run '{0}'.\n{1}" },
            { "→ 허용됨", "→ Allowed" },
            { "→ 거부됨", "→ Denied" },
            { "허용", "Allow" },
            { "거부", "Deny" },
            // ChCheckpoint.cs
            { "파일 {0}개를 이전 것으로 되돌렸어요", "{0} files put back as they were" },
            { "이 요청으로 생긴 에셋 {0}개를 프로젝트에서 뺐어요({1}에 보관)", "{0} assets this request made taken out of the project (kept in {1})" },
            { "지워졌던 에셋 {0}개를 되살렸어요", "{0} deleted assets brought back" },
            { "옮겨졌던 에셋 {0}개를 제자리로 돌렸어요", "{0} moved assets put back in place" },
            { "이전 내용이 보관되지 않은 파일 {0}개는 그대로예요({1})", "{0} files whose earlier content was not kept stay as they are ({1})" },
            { "{0}개는 되돌리지 못했어요({1})", "{0} could not be undone ({1})" },
            { " 등", " and more" },
            { "Claude 요청", "Claude's request" },
            // ChImportModel.cs
            {
                " The chat window's 되돌리기 (ch_checkpoint undo) puts the file it replaced back; it is kept at {0}.",
                " The chat window's Undo (ch_checkpoint undo) puts the file it replaced back; it is kept at {0}."
            },
            {
                " No undo point is set, so 되돌리기 will not put the file it replaced back; it is kept at {0}.",
                " No undo point is set, so Undo will not put the file it replaced back; it is kept at {0}."
            },
            // ChModelFollow.cs
            { "저장", "Save" },
            { "{0} {1} → {2} 반영", "{0} {1} → {2} updated" },
            { " (씬 오브젝트 {0}개)", " ({0} scene objects)" },
            {
                "[이 프로젝트의 모델 {0}이(가) Blender의 {1} 저장을 따라 바뀌었음 ({2}, Claude를 거치지 않음)]",
                "[This project's model {0} was replaced following a save of Blender's {1} ({2}, not through Claude)]"
            },
            { "Blender에서 보냄: ", "Sent from Blender: " },
            {
                "Blender({0})에서 {1}을(를) 보냈어요. 저장할 때마다 다시 옵니다. 되돌리기로 뺄 수 있어요.",
                "Blender ({0}) sent {1}. It comes again at every save. Undo takes it out."
            },
            {
                "[사용자가 Blender 패널에서 {0}의 모델을 이 프로젝트에 직접 보냈음: {1} (Claude를 거치지 않음, 저장할 때마다 다시 옴)]",
                "[The user sent a model of {0} to this project straight from Blender's panel: {1} (not through Claude; it comes again at " +
                "every save)]"
            },
            // ChProtect.cs
            { "열림", "Open" },
            { "보기만", "Look only" },
            { "잠금", "Locked" },
            { "씬 저장", "Save scene" },
            { "에셋 삭제", "Delete asset" },
            { "플레이 모드", "Play mode" },
            { "이 창: {0}", "This window: {0}" },
            { "금지: {0}", "Forbidden: {0}" },
            { "경로 {0}", "path {0}" },
            { "오브젝트 {0}", "object {0}" },
            { " (이번만)", " (this run only)" },
            { "규칙을 적지 못했습니다: ", "The rule could not be written: " },
            { "'{0}'은(는) 창 규칙이 아닙니다.", "'{0}' is not a rule for a window." },
            { "'{0}'은(는) Unity에서 막을 수 있는 동작이 아닙니다.", "'{0}' is not an action that can be stopped in Unity." },
            { "더할 규칙이 없습니다.", "There is no rule to add." },
            { "{0} (저장)", "{0} (save)" },
            { "에셋 삭제 ({0})", "Delete asset ({0})" },
            { "{0} (지우기)", "{0} (delete)" },
            { "{0} (옮기기 · 이름 바꾸기)", "{0} (move · rename)" },
            { "{0} (새로 만듦)", "{0} (newly made)" },
            { "{0} (파일이 바뀜)", "{0} (file changed)" },
            { "{0} (지워짐)", "{0} (deleted)" },
            { "속성이 바뀜", "properties changed" },
            { "안의 것이 지워짐", "something in it was deleted" },
            { "이 요청의 되돌리기 지점이 없음", "this request has no undo point" },
            { "Undo를 거치지 않은 변경이 남음: ", "changes that did not go through Undo are left: " },
            { "되돌리지 못한 파일: ", "files that could not be put back: " },
            // ChSettings.cs
            { "설정을 적지 못했습니다: {0}", "The settings could not be written: {0}" },
            // ChSettingsWindow.cs
            { "설정", "Settings" },
            { "Claude Hand 설정", "Claude Hand settings" },
            { "기능 켜고 끄기", "Features" },
            { "실험실", "Lab" },
            {
                "설정 목록을 읽지 못했습니다. claude-hand 폴더에서 uv run tools/claude_hand.py chat-setup 을 한 번 실행하세요.",
                "The list of settings could not be read. Run uv run tools/claude_hand.py chat-setup once in the claude-hand folder."
            },
            { "아직 다듬는 중인 기능입니다. 켜야 쓰입니다.", "Features still being refined. In use only once turned on." },
            { "지금 실험실에 있는 기능이 없습니다.", "The lab holds no feature right now." },
            { "* 다음에 Claude Hand를 켤 때부터 적용됩니다.", "* Applies from the next start of Claude Hand." },
            { "켜짐", "On" },
            { "꺼짐", "Off" },
            { "{0}일", "{0} days" },
            { "지우지 않음", "never removed" },
            { "{0}부터", "since {0}" },
            // ChMcpLink: the lines it leaves in the Console
            { "MCP for Unity의 연결이 {0}초 동안 꺼져 있어 다시 켰습니다 (포트 {1}).", "MCP for Unity's connection was off for {0} seconds, so it was turned back on (port {1})." },
            { "MCP for Unity의 연결이 꺼져 있는데 다시 켜지 못했습니다: {0}  Window > MCP for Unity에서 직접 연결해 주세요.", "MCP for Unity's connection is off and could not be turned back on: {0}  Connect it yourself under Window > MCP for Unity." },
            { "MCP for Unity의 연결을 켜는 길을 찾지 못했습니다.", "Found no way to turn MCP for Unity's connection on." },
        };

        /// <summary>
        /// What a text that counts something says in English when the count is one: (which value holds the count, the
        /// text). Korean has no plural, so one Korean text stands for both.
        /// </summary>
        private static readonly Dictionary<string, KeyValuePair<int, string>> EnglishOne = new Dictionary<string, KeyValuePair<int, string>>(StringComparer.Ordinal)
        {
            { "{0}일", new KeyValuePair<int, string>(0, "{0} day") },
            { "보호 [잠김] {0}규칙 {1}개", new KeyValuePair<int, string>(1, "Protect [locked] {0}{1} rule") },
            { "\n이 요청이 바꾼 파일 {0}개는 이전 것으로 돌아갑니다.", new KeyValuePair<int, string>(0, "\n{0} file this request changed goes back to what it was.") },
            { "\n이 요청으로 생긴 에셋 {0}개는 프로젝트에서 빠집니다({1}에 보관).", new KeyValuePair<int, string>(0, "\n{0} asset this request made leaves the project (kept in {1}).") },
            { "\n이 요청이 지운 에셋 {0}개는 되살아납니다.", new KeyValuePair<int, string>(0, "\n{0} asset this request deleted comes back.") },
            { "\n이 요청이 옮기거나 이름을 바꾼 에셋 {0}개는 원래 자리로 갑니다.", new KeyValuePair<int, string>(0, "\n{0} asset this request moved or renamed goes back to its place.") },
            { "\n이전 내용이 보관되지 않은 파일 {0}개는 그대로 남습니다({1}).", new KeyValuePair<int, string>(0, "\n{0} file whose earlier content was not kept stays as it is ({1}).") },
            { "파일 {0}개를 이전 것으로 되돌렸어요", new KeyValuePair<int, string>(0, "{0} file put back as it was") },
            { "이 요청으로 생긴 에셋 {0}개를 프로젝트에서 뺐어요({1}에 보관)", new KeyValuePair<int, string>(0, "{0} asset this request made taken out of the project (kept in {1})") },
            { "이전 내용이 보관되지 않은 파일 {0}개는 그대로예요({1})", new KeyValuePair<int, string>(0, "{0} file whose earlier content was not kept stays as it is ({1})") },
        };
    }
}
