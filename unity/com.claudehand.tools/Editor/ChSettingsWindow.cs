using System.Linq;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;
using static ClaudeHand.Editor.ChText;

namespace ClaudeHand.Editor
{
    /// <summary>
    /// The settings window: what the gear button of the chat window opens. Two tabs: "기능 켜고 끄기" with a row for
    /// each setting of the catalog, and "실험실" with the features still in the lab (see <see cref="ChSettings"/>).
    /// A change is written to config.json at once, which every part of claude-hand reads anew; what holds only from
    /// the next start of Claude Hand is marked with a star and said so at the bottom. What a setting is, is said in
    /// words beside it, never by a colour alone.
    /// </summary>
    internal sealed class ChSettingsWindow : EditorWindow
    {
        private int _tab;
        private Vector2 _scroll;
        private string _problem;

        /// <summary>The gear of the chat window: the Unity icon where this editor has it, else the character.</summary>
        internal static GUIContent Gear()
        {
            var icon = EditorGUIUtility.FindTexture(EditorGUIUtility.isProSkin ? "d_Settings" : "Settings");
            return icon != null ? new GUIContent(icon, T("설정")) : new GUIContent("⚙", T("설정"));
        }

        internal static void Open()
        {
            var window = GetWindow<ChSettingsWindow>(true, T("Claude Hand 설정"), true);  // a floating window of its own
            window.minSize = new Vector2(400, 320);
            window.Show();
        }

        private void OnEnable()
        {
            ChSettings.Changed += Repaint;
        }

        private void OnDisable()
        {
            ChSettings.Changed -= Repaint;
        }

        private void OnGUI()
        {
            titleContent = new GUIContent(T("Claude Hand 설정"));  // follows the language when it is changed here
            var tabs = new[] { T("기능 켜고 끄기"), T("실험실") };
            _tab = GUILayout.Toolbar(Mathf.Clamp(_tab, 0, 1), tabs);
            // As wide as the longest name of this language needs: names are not cut off, in Korean or in English.
            float names = ChSettings.Settings.Select(e => EditorStyles.label.CalcSize(new GUIContent(ChSettings.Pick(e.name))).x)
                .Concat(ChSettings.Lab.Select(e => EditorStyles.boldLabel.CalcSize(new GUIContent(ChSettings.Pick(e.name))).x))
                .DefaultIfEmpty(200).Max();
            EditorGUIUtility.labelWidth = names + 8;
            float wide = Mathf.Max(400, names + 190);
            if (!Mathf.Approximately(minSize.x, wide)) minSize = new Vector2(wide, 320);
            EditorGUILayout.LabelField(tabs[_tab], EditorStyles.boldLabel);  // which tab this is, in words
            if (!string.IsNullOrEmpty(_problem)) EditorGUILayout.HelpBox(_problem, MessageType.Warning);
            bool starred = false;
            _scroll = EditorGUILayout.BeginScrollView(_scroll);
            if (_tab == 0)
            {
                foreach (var entry in ChSettings.Settings) starred |= DrawSetting(entry);
                if (!ChSettings.CatalogFound)
                {
                    EditorGUILayout.HelpBox(T("설정 목록을 읽지 못했습니다. claude-hand 폴더에서 uv run tools/claude_hand.py chat-setup 을 한 번 실행하세요."),
                        MessageType.Info);
                }
            }
            else
            {
                EditorGUILayout.LabelField(T("아직 다듬는 중인 기능입니다. 켜야 쓰입니다."), EditorStyles.wordWrappedMiniLabel);
                if (ChSettings.Lab.Count == 0) EditorGUILayout.LabelField(T("지금 실험실에 있는 기능이 없습니다."));
                foreach (var entry in ChSettings.Lab) starred |= DrawLab(entry);
            }
            EditorGUILayout.EndScrollView();
            if (starred) EditorGUILayout.LabelField(T("* 다음에 Claude Hand를 켤 때부터 적용됩니다."), EditorStyles.wordWrappedMiniLabel);
        }

        /// <summary>One setting: its control, and beside it what it is now in words. Returns whether it holds only from the next start.</summary>
        private bool DrawSetting(ChSettings.Entry entry)
        {
            bool later = entry.applies != "now";
            var label = new GUIContent(ChSettings.Pick(entry.name), ChSettings.Pick(entry.hint));
            GUILayout.BeginHorizontal();
            if (entry.kind == "switch")
            {
                bool now = ChSettings.Switch(entry);
                bool next = EditorGUILayout.ToggleLeft(label, now);
                Words((now ? T("켜짐") : T("꺼짐")) + (later ? " *" : ""));
                if (next != now) _problem = ChSettings.Set(entry.key, next);
            }
            else if (entry.kind == "choice")
            {
                var values = entry.choices.Select(c => c.Key).ToList();
                int now = Mathf.Max(0, values.IndexOf(ChSettings.Choice(entry)));
                int next = EditorGUILayout.Popup(label, now, entry.choices.Select(c => c.Value).ToArray());
                if (later) Words("*");
                if (next != now && next >= 0 && next < values.Count) _problem = ChSettings.Set(entry.key, values[next]);
            }
            else if (entry.kind == "days")
            {
                int now = ChSettings.Days(entry);
                int next = Mathf.Clamp(EditorGUILayout.DelayedIntField(label, now), 0, 3650);
                Words((now > 0 ? T("{0}일", now) : T("지우지 않음")) + (later ? " *" : ""));
                if (next != now) _problem = ChSettings.Set(entry.key, next);
            }
            else
            {
                EditorGUILayout.LabelField(label);  // a kind from a newer catalog than this package
            }
            GUILayout.EndHorizontal();
            return later;
        }

        /// <summary>One feature of the lab: its switch, its state in words, since when it is there and what it does.</summary>
        private bool DrawLab(ChSettings.Entry entry)
        {
            bool later = entry.applies != "now";
            bool now = ChSettings.LabOn(entry);
            GUILayout.BeginHorizontal();
            bool next = EditorGUILayout.ToggleLeft(new GUIContent(ChSettings.Pick(entry.name), ChSettings.Pick(entry.hint)), now, EditorStyles.boldLabel);
            Words((now ? T("켜짐") : T("꺼짐")) + (later ? " *" : ""));
            GUILayout.EndHorizontal();
            EditorGUILayout.LabelField(T("{0}부터", entry.since ?? "?") + " · " + ChSettings.Pick(entry.hint), EditorStyles.wordWrappedMiniLabel);
            GUILayout.Space(4);
            if (next != now) _problem = ChSettings.SetLab(entry.key, next);
            return later;
        }

        private static void Words(string words)
        {
            GUILayout.Label(words, EditorStyles.miniLabel, GUILayout.Width(96));
        }
    }
}
