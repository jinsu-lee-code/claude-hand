using System.Collections.Generic;
using System.Linq;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace ClaudeHand.Editor
{
    [ChTool("ch_scene_snapshot",
        Description = "Snapshot the open scene hierarchy as JSON. Every GameObject gets a ref like @s1a2b3c4:e5 that " +
                      "other ch_* tools accept; each node also carries instance_id and path for the built-in MCP for Unity tools. " +
                      "Refs belong to their snapshot: after the scene changes, take a new snapshot. Large scenes are cut off " +
                      "at depth/max_nodes (nodes then show child_count without children); drill in with root=<ref>.")]
    public static class ChSceneSnapshot
    {
        public class Parameters
        {
            [ChParameter("Ref of a GameObject to start from, taken from an earlier snapshot. Omit to cover all loaded scenes.", Required = false)]
            public string root { get; set; }

            [ChParameter("How many levels below the starting objects to include (default 3).", Required = false)]
            public int? depth { get; set; }

            [ChParameter("Stop after this many GameObjects (default 300).", Required = false)]
            public int? max_nodes { get; set; }

            [ChParameter("List component type names on each node (default true).", Required = false)]
            public bool? include_components { get; set; }

            [ChParameter("Add world position, rotation (euler) and local scale to each node (default false).", Required = false)]
            public bool? include_transforms { get; set; }
        }

        private sealed class WalkState
        {
            public ChRefs.Builder Builder;
            public int MaxNodes;
            public bool WithComponents;
            public bool WithTransforms;
            public int Count;
            public bool Truncated;
        }

        public static object HandleCommand(JObject @params)
        {
            var p = @params?.ToObject<Parameters>() ?? new Parameters();
            var state = new WalkState
            {
                Builder = ChRefs.Begin(),
                MaxNodes = Mathf.Clamp(p.max_nodes ?? 300, 1, 5000),
                WithComponents = p.include_components ?? true,
                WithTransforms = p.include_transforms ?? false,
            };
            int depth = Mathf.Clamp(p.depth ?? 3, 0, 64);
            var scenes = new List<object>();
            var tree = new List<object>();

            if (!string.IsNullOrWhiteSpace(p.root))
            {
                var rootGo = ChRefs.Resolve(p.root, out var error);
                if (rootGo == null) return error;
                tree.Add(Walk(rootGo, depth, state));
            }
            else
            {
                var stage = PrefabStageUtility.GetCurrentPrefabStage();
                if (stage != null)
                {
                    scenes.Add(new { name = stage.prefabContentsRoot.name, path = stage.assetPath, kind = "prefab_stage", is_dirty = stage.scene.isDirty });
                    tree.Add(Walk(stage.prefabContentsRoot, depth, state));
                }
                else
                {
                    var active = SceneManager.GetActiveScene();
                    for (int i = 0; i < SceneManager.sceneCount; i++)
                    {
                        var scene = SceneManager.GetSceneAt(i);
                        if (!scene.isLoaded) continue;
                        scenes.Add(new { name = scene.name, path = scene.path, kind = "scene", is_dirty = scene.isDirty, is_active = scene == active });
                        foreach (var go in scene.GetRootGameObjects())
                        {
                            if (state.Count >= state.MaxNodes)
                            {
                                state.Truncated = true;
                                break;
                            }
                            tree.Add(Walk(go, depth, state));
                        }
                    }
                }
            }

            state.Builder.Save();
            var summary = $"Snapshot {state.Builder.SnapshotId}: {state.Count} objects" + (state.Truncated ? " (truncated)" : "");
            return new ChSuccess(summary, new
            {
                snapshot_id = state.Builder.SnapshotId,
                project = ChPaths.ProjectName,
                scenes,
                node_count = state.Count,
                truncated = state.Truncated,
                tree,
            });
        }

        private static Dictionary<string, object> Walk(GameObject go, int depthLeft, WalkState s)
        {
            s.Count++;
            var t = go.transform;
            var path = ChRefs.HierarchyPath(t);
            var node = new Dictionary<string, object>
            {
                ["ref"] = s.Builder.Add(go, path),
                ["name"] = go.name,
                ["path"] = path,
                ["instance_id"] = ChCompat.InstanceIdForTools(go),
                ["active"] = go.activeSelf,
            };

            if (s.WithComponents)
            {
                node["components"] = go.GetComponents<Component>()
                    .Where(c => !(c is Transform) || c is RectTransform)
                    .Select(c => c == null ? "(Missing Script)" : c.GetType().Name)
                    .ToArray();
            }

            if (s.WithTransforms)
            {
                node["position"] = Round(t.position);
                node["rotation"] = Round(t.eulerAngles);
                node["scale"] = Round(t.localScale);
            }

            if (PrefabUtility.IsOutermostPrefabInstanceRoot(go))
            {
                node["prefab"] = PrefabUtility.GetPrefabAssetPathOfNearestInstanceRoot(go);
            }

            if (t.childCount > 0)
            {
                node["child_count"] = t.childCount;
                if (depthLeft > 0)
                {
                    var children = new List<object>();
                    for (int i = 0; i < t.childCount; i++)
                    {
                        if (s.Count >= s.MaxNodes)
                        {
                            s.Truncated = true;
                            break;
                        }
                        children.Add(Walk(t.GetChild(i).gameObject, depthLeft - 1, s));
                    }
                    node["children"] = children;
                }
            }

            return node;
        }

        private static float[] Round(Vector3 v)
        {
            return new[] { Mathf.Round(v.x * 1000f) / 1000f, Mathf.Round(v.y * 1000f) / 1000f, Mathf.Round(v.z * 1000f) / 1000f };
        }
    }
}
