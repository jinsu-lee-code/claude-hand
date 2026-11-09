using System;
using System.IO;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

namespace ClaudeHand.Editor
{
    /// <summary>
    /// A picture of the Scene view as it is now, for the chat panels' result previews: the chat channel asks for one
    /// when a request made in another window changed this project. A copy of the Scene view camera renders into a
    /// texture, so it works while Unity is in the background (no screen grab) and draws no gizmos.
    /// </summary>
    [McpForUnityTool("ch_capture",
        Description = "Save a PNG of the Scene view as it is now (no gizmos) and return its path. Changes nothing in the scene. " +
                      "The chat panels use it for result previews.")]
    public static class ChCapture
    {
        public class Parameters
        {
            [ToolParameter("Where to write the PNG (default: the exchange folder)", Required = false)]
            public string path { get; set; }

            [ToolParameter("Longest side in pixels (default 640)", Required = false)]
            public int? max_size { get; set; }
        }

        public static object HandleCommand(JObject @params)
        {
            var p = @params?.ToObject<Parameters>() ?? new Parameters();
            var view = SceneView.lastActiveSceneView;
            if (view == null || view.camera == null)
                return ChErrors.Make(ChErrors.NotFound, "No Scene view is open in this project.", "after_fix");
            var path = string.IsNullOrWhiteSpace(p.path)
                ? Path.Combine(ChPaths.ExchangeDir, $"capture-{DateTime.Now:yyyyMMdd-HHmmss}.png")
                : p.path;
            Directory.CreateDirectory(Path.GetDirectoryName(Path.GetFullPath(path)) ?? ".");
            var (width, height) = Fit(view.camera.pixelWidth, view.camera.pixelHeight, Mathf.Clamp(p.max_size ?? 640, 64, 2048));
            File.WriteAllBytes(path, Render(view.camera, width, height));
            return new SuccessResponse("Scene view captured", new
            {
                path = ChPaths.ToPosix(Path.GetFullPath(path)),
                width,
                height,
                scene = UnityEngine.SceneManagement.SceneManager.GetActiveScene().name,
            });
        }

        private static (int, int) Fit(int width, int height, int longest)
        {
            width = Math.Max(1, width);
            height = Math.Max(1, height);
            var scale = Math.Min(1f, (float)longest / Math.Max(width, height));
            return (Math.Max(1, Mathf.RoundToInt(width * scale)), Math.Max(1, Mathf.RoundToInt(height * scale)));
        }

        private static byte[] Render(Camera source, int width, int height)
        {
            var go = new GameObject("Claude Hand capture") { hideFlags = HideFlags.HideAndDontSave };
            var target = RenderTexture.GetTemporary(width, height, 24, RenderTextureFormat.ARGB32);
            var texture = new Texture2D(width, height, TextureFormat.RGB24, false);
            var active = RenderTexture.active;
            try
            {
                var camera = go.AddComponent<Camera>();
                camera.CopyFrom(source);
                camera.cameraType = CameraType.Game;  // a plain render: no grid, gizmos or selection outline
                camera.targetTexture = target;
                camera.Render();
                RenderTexture.active = target;
                texture.ReadPixels(new Rect(0, 0, width, height), 0, 0);
                texture.Apply();
                return texture.EncodeToPNG();
            }
            finally
            {
                RenderTexture.active = active;
                RenderTexture.ReleaseTemporary(target);
                UnityEngine.Object.DestroyImmediate(texture);
                UnityEngine.Object.DestroyImmediate(go);
            }
        }
    }
}
