using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.Animations;
using UnityEngine;
using UnityEngine.Rendering;

namespace ClaudeHand.Editor
{
    /// <summary>
    /// What a model from Blender needs besides its file, as the manifest next to it says (0.17 and later): how the
    /// file's axes lie, what its materials are made of, whether its rig is a person's. An FBX carries none of that
    /// to Unity in a usable way: its images stay inside the file, metallic and roughness are not read, and every part
    /// arrives with a quarter turn. Used by ch_import_model, and by ch_model_follow for a model that follows its .blend.
    /// </summary>
    internal static class ChModelLook
    {
        private const string TexturesFolder = "Textures";
        private const string MaterialsFolder = "Materials";

        // ------------------------------------------------------------------ import settings

        /// <summary>
        /// The import settings the manifest asks for. Returns whether any changed (one more import applies them).
        ///  - "axes": "blender": the file is in Blender's own axes and Unity bakes the conversion into the data, so
        ///    that no part has a turn it does not have in Blender.
        ///  - "rig": "humanoid" / "generic".
        ///  - Clips are named after their action ("Wave"), not "Armature|Wave", unless two would get the same name.
        ///    A clip whose settings were edited in the Inspector is kept as it is.
        /// </summary>
        internal static bool Settings(ModelImporter importer, ChImportModel.Incoming incoming)
        {
            if (importer == null) return false;
            bool dirty = false;
            if (incoming.Axes == "blender" && !importer.bakeAxisConversion)
            {
                importer.bakeAxisConversion = true;
                dirty = true;
            }
            var rig = incoming.Rig == "humanoid" ? ModelImporterAnimationType.Human
                : incoming.Rig == "generic" ? ModelImporterAnimationType.Generic : importer.animationType;
            if (importer.animationType != rig)
            {
                importer.animationType = rig;
                dirty = true;
            }
            return NameClips(importer, incoming.Clips) || dirty;
        }

        private static string Plain(string take)
        {
            int bar = take.LastIndexOf('|');
            return bar >= 0 && bar < take.Length - 1 ? take.Substring(bar + 1) : take;
        }

        /// <summary>
        /// `own`: the takes that are this model's (take name -> clip name), when the manifest says. Blender's exporter
        /// also writes every other action of the file as a take of a rig; those are left out. Null: all takes.
        /// </summary>
        private static bool NameClips(ModelImporter importer, Dictionary<string, string> own)
        {
            var takes = importer.defaultClipAnimations;
            var existing = importer.clipAnimations;
            if (takes.Length == 0) return false;
            if (own != null && !takes.Any(t => own.ContainsKey(t.takeName))) own = null;  // none of them came: better all than none
            if (own != null) takes = takes.Where(t => own.ContainsKey(t.takeName)).ToArray();
            var wanted = new List<ModelImporterClipAnimation>();
            bool renamed = own != null && takes.Length != importer.defaultClipAnimations.Length && existing.Length == 0;
            foreach (var take in takes)
            {
                var kept = existing.FirstOrDefault(c => c.takeName == take.takeName);
                if (kept != null)
                {
                    wanted.Add(kept);
                    continue;
                }
                var plain = own != null ? own[take.takeName] : Plain(take.name);
                if (plain != take.name && takes.Count(other => (own != null ? own[other.takeName] : Plain(other.name)) == plain) == 1)
                {
                    take.name = plain;
                    renamed = true;
                }
                wanted.Add(take);
            }
            // A list that was set before hides the takes a newer file has, and still names the ones it lost.
            bool stale = existing.Length > 0 && (existing.Length != wanted.Count || existing.Any(c => takes.All(t => t.takeName != c.takeName)));
            if (!renamed && !stale) return false;
            importer.clipAnimations = wanted.ToArray();
            return true;
        }

        /// <summary>
        /// After the import: a rig set to Humanoid that Unity could not make a person of goes back to Generic (a broken
        /// Humanoid plays nothing). Returns whether it did, and one more import is needed.
        /// </summary>
        internal static bool RigFailed(ModelImporter importer, string assetPath, List<string> warnings)
        {
            if (importer == null || importer.animationType != ModelImporterAnimationType.Human) return false;
            var avatar = AssetDatabase.LoadAllAssetsAtPath(assetPath).OfType<Avatar>().FirstOrDefault();
            if (avatar != null && avatar.isValid && avatar.isHuman) return false;
            importer.animationType = ModelImporterAnimationType.Generic;
            warnings.Add("Unity could not set this rig up as Humanoid (it found no match for the bones a person needs: hips, spine, head, " +
                         "upper and lower arms and legs, hands, feet; the rig must stand in T-pose), so it was imported as Generic.");
            return true;
        }

        /// <summary>
        /// An Animator Controller next to the model with a state for each of its clips (the first one plays), so that
        /// an object placed from it moves when the scene runs. Null: the model has no clips.
        /// </summary>
        internal static RuntimeAnimatorController Controller(string assetPath)
        {
            var clips = AssetDatabase.LoadAllAssetsAtPath(assetPath).OfType<AnimationClip>()
                .Where(c => !c.name.StartsWith("__preview__", StringComparison.Ordinal)).OrderBy(c => c.name, StringComparer.Ordinal).ToList();
            if (clips.Count == 0) return null;
            var path = Path.ChangeExtension(assetPath, ".controller");
            var controller = AssetDatabase.LoadAssetAtPath<AnimatorController>(path) ?? AnimatorController.CreateAnimatorControllerAtPath(path);
            var have = controller.layers[0].stateMachine.states.Select(s => s.state.motion).ToList();
            foreach (var clip in clips.Where(c => !have.Contains(c))) controller.AddMotion(clip);
            return controller;
        }

        // ------------------------------------------------------------------ is it what was sent

        /// <summary>
        /// Holds the imported model against what Blender measured before it wrote the file (the manifest's "check":
        /// where each object is, and how far each mesh's bounding box reaches, in Unity's axes). A model that arrives
        /// turned, moved, scaled or short of a part is told of in the answer instead of being found by eye. A mesh
        /// that a rig or a shape key bends is held to its place only. Null: the manifest brought nothing to check.
        /// </summary>
        internal static object ShapeCheck(ChImportModel.Incoming incoming, string assetPath, List<string> warnings)
        {
            var parts = incoming.Check?["parts"] as JObject;
            var model = AssetDatabase.LoadAssetAtPath<GameObject>(assetPath);
            if (parts == null || model == null) return null;
            var byName = new Dictionary<string, Transform>(StringComparer.Ordinal);
            foreach (var transform in model.GetComponentsInChildren<Transform>(true))
            {
                if (!byName.ContainsKey(transform.name)) byName[transform.name] = transform;
            }
            // When the file has one root, Unity makes it the model's root, under the file's name: the part that is not
            // found by name (of several, the one that is where the root is) is that one.
            var unnamed = parts.Properties().Where(p => !byName.ContainsKey(p.Name)).Select(p => p.Name).ToList();
            var root = unnamed.Count == 1 ? unnamed[0]
                : unnamed.FirstOrDefault(name => Vector3.Distance(Point(parts[name]["at"]), model.transform.position) < 0.002f);
            if (root != null) byName[root] = model.transform;

            var differing = new List<object>();
            var missing = new List<string>();
            float worst = 0f;
            int compared = 0;
            foreach (var part in parts.Properties())
            {
                if (!byName.TryGetValue(part.Name, out var transform))
                {
                    missing.Add(part.Name);
                    continue;
                }
                var at = Point(part.Value["at"]);
                var low = part.Value["low"] != null ? Point(part.Value["low"]) : (Vector3?)null;
                var high = part.Value["high"] != null ? Point(part.Value["high"]) : (Vector3?)null;
                float off = Vector3.Distance(transform.position, at);
                float size = 1f;
                if (low.HasValue && high.HasValue)
                {
                    size = Mathf.Max(1f, (high.Value - low.Value).magnitude);
                    var mesh = transform.GetComponent<MeshFilter>()?.sharedMesh ?? transform.GetComponent<SkinnedMeshRenderer>()?.sharedMesh;
                    if (mesh == null || mesh.vertexCount == 0)
                    {
                        off = float.PositiveInfinity;  // a mesh went, none came
                    }
                    else
                    {
                        var bounds = mesh.bounds;
                        Vector3 min = Vector3.positiveInfinity, max = Vector3.negativeInfinity;
                        for (int corner = 0; corner < 8; corner++)
                        {
                            var point = transform.TransformPoint(bounds.center + Vector3.Scale(bounds.extents,
                                new Vector3((corner & 1) == 0 ? -1 : 1, (corner & 2) == 0 ? -1 : 1, (corner & 4) == 0 ? -1 : 1)));
                            min = Vector3.Min(min, point);
                            max = Vector3.Max(max, point);
                        }
                        off = Mathf.Max(off, Mathf.Max(Largest(min - low.Value), Largest(max - high.Value)));
                    }
                }
                compared++;
                if (off > 0.002f * size + 0.001f)
                {
                    differing.Add(new { name = part.Name, off = float.IsInfinity(off) ? -1f : (float)Math.Round(off, 4) });
                }
                else
                {
                    worst = Mathf.Max(worst, off);
                }
            }
            bool same = differing.Count == 0 && missing.Count == 0;
            if (!same)
            {
                warnings.Add("The model in Unity is not what Blender sent: " +
                             (differing.Count > 0 ? $"{differing.Count} part(s) sit or reach differently (shape_check.differing; off is in metres, -1: the mesh is empty)" : string.Empty) +
                             (differing.Count > 0 && missing.Count > 0 ? "; " : string.Empty) +
                             (missing.Count > 0 ? $"{missing.Count} part(s) did not arrive (shape_check.missing)" : string.Empty) + ". Tell the user.");
            }
            return new { same, parts = compared, worst_off = (float)Math.Round(worst, 5), differing, missing };
        }

        private static Vector3 Point(JToken numbers) => new Vector3((float)numbers[0], (float)numbers[1], (float)numbers[2]);

        private static float Largest(Vector3 v) => Mathf.Max(Mathf.Abs(v.x), Mathf.Max(Mathf.Abs(v.y), Mathf.Abs(v.z)));

        // ------------------------------------------------------------------ materials

        /// <summary>
        /// Makes this project's own kind of material of each one the manifest describes (URP Lit, or Standard without a
        /// render pipeline), puts the images next to the model and links the model's material slots to them.
        /// Returns what was made: material name -> hash of the .mat as it was written. `before` is that from the last
        /// time: a material whose file is not what was written then has been edited here, and is left as it is (so is
        /// one that was not made by this at all). `wrote`: told of every asset written, when the caller needs to know.
        /// </summary>
        internal static Dictionary<string, string> Materials(ModelImporter importer, ChImportModel.Incoming incoming, string assetPath,
            Dictionary<string, string> before, List<string> warnings, ref bool dirty, out object report, Action<string> wrote = null)
        {
            report = null;
            if (importer == null || incoming.Materials == null || incoming.Materials.Count == 0) return before;
            var pipeline = GraphicsSettings.currentRenderPipeline;
            var kind = pipeline == null ? "builtin" : pipeline.GetType().Name.Contains("Universal") ? "urp" : "other";
            var shader = kind == "urp" ? Shader.Find("Universal Render Pipeline/Lit") : kind == "builtin" ? Shader.Find("Standard") : null;
            if (shader == null)
            {
                warnings.Add($"Materials are made for URP and for projects without a render pipeline; this project uses {pipeline.GetType().Name}, " +
                             "so the model keeps the plain materials Unity made for it.");
                return before;
            }

            var folder = Path.GetDirectoryName(assetPath)?.Replace('\\', '/') ?? "Assets";
            var textures = $"{folder}/{TexturesFolder}";
            var made = new List<string>();
            var kept = new List<string>();
            var after = new Dictionary<string, string>(StringComparer.Ordinal);
            int images = BringTextures(incoming, textures, warnings, wrote);

            ChPaths.EnsureAssetFolder($"{folder}/{MaterialsFolder}");
            wrote?.Invoke($"{folder}/{MaterialsFolder}");
            var written = new List<KeyValuePair<string, string>>();
            foreach (var described in incoming.Materials.OfType<JObject>())
            {
                var name = described.Value<string>("name");
                if (string.IsNullOrEmpty(name)) continue;
                var path = $"{folder}/{MaterialsFolder}/{ChPaths.Sanitize(name)}.mat";
                var material = AssetDatabase.LoadAssetAtPath<Material>(path);
                string was = null;
                bool theirs = material != null && (before == null || !before.TryGetValue(name, out was) || was != FileHash(path));
                if (theirs)
                {
                    kept.Add(name);
                    if (was != null) after[name] = was;  // still not what was written: stays theirs next time too
                }
                else
                {
                    wrote?.Invoke(path);
                    if (material == null)
                    {
                        material = new Material(shader) { name = name };
                        AssetDatabase.CreateAsset(material, path);
                    }
                    else if (material.shader != shader)
                    {
                        material.shader = shader;
                    }
                    Fill(material, described, kind == "urp", token => Texture(token, textures));
                    EditorUtility.SetDirty(material);
                    made.Add(name);
                    written.Add(new KeyValuePair<string, string>(name, path));
                }
                var id = new AssetImporter.SourceAssetIdentifier(typeof(Material), name);
                if (!importer.GetExternalObjectMap().TryGetValue(id, out var linked) || linked != material)
                {
                    importer.AddRemap(id, material);
                    dirty = true;
                }
            }
            if (written.Count > 0) AssetDatabase.SaveAssets();
            foreach (var one in written) after[one.Key] = FileHash(one.Value);
            if (kept.Count > 0)
            {
                warnings.Add($"{kept.Count} material(s) were edited in this project (or not made by Claude Hand) and were left as they are: " +
                             string.Join(", ", kept.Take(5)) + (kept.Count > 5 ? ", ..." : string.Empty) + ". Delete the .mat to have it made anew.");
            }
            report = new { made, kept, images, shader = shader.name, folder = $"{folder}/{MaterialsFolder}" };
            return after;
        }

        /// <summary>Copies the images the materials name to Textures/ next to the model, each imported as what it is used for.</summary>
        private static int BringTextures(ChImportModel.Incoming incoming, string textures, List<string> warnings, Action<string> wrote)
        {
            var roles = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);  // file -> colour | normal | data
            var squares = new HashSet<string>(StringComparer.OrdinalIgnoreCase);  // files whose pixels are to stay squares (Blender's "Closest")
            foreach (var described in incoming.Materials.OfType<JObject>())
            {
                foreach (var use in new[] { ("base_color_texture", "colour"), ("emission_texture", "colour"), ("normal_texture", "normal"), ("metallic_smoothness_texture", "data") })
                {
                    var file = described[use.Item1]?.Value<string>("file");
                    if (!string.IsNullOrEmpty(file) && !roles.ContainsKey(file)) roles[file] = use.Item2;
                    if (!string.IsNullOrEmpty(file) && described[use.Item1].Value<string>("filter") == "point") squares.Add(file);
                }
            }
            if (roles.Count == 0) return 0;
            ChPaths.EnsureAssetFolder(textures);
            wrote?.Invoke(textures);
            int brought = 0;
            foreach (var role in roles)
            {
                var from = Path.Combine(incoming.TexturesDir ?? string.Empty, role.Key);
                var path = $"{textures}/{Path.GetFileName(role.Key)}";
                var to = Path.Combine(ChPaths.ProjectRoot, path);
                if (!File.Exists(from))
                {
                    if (!File.Exists(to)) warnings.Add($"The image {role.Key} did not come with the model, so the material that uses it has none.");
                    continue;
                }
                if (!File.Exists(to) || !ChCheckpoint.Same(from, to))
                {
                    wrote?.Invoke(path);
                    File.Copy(from, to, true);
                    AssetDatabase.ImportAsset(path, ImportAssetOptions.ForceSynchronousImport | ImportAssetOptions.ForceUpdate);
                    brought++;
                }
                if (!(AssetImporter.GetAtPath(path) is TextureImporter importer)) continue;
                var type = role.Value == "normal" ? TextureImporterType.NormalMap : TextureImporterType.Default;
                bool colour = role.Value == "colour";
                bool square = squares.Contains(role.Key) && importer.filterMode != FilterMode.Point;
                if (importer.textureType == type && (type == TextureImporterType.NormalMap || importer.sRGBTexture == colour) && !square) continue;
                wrote?.Invoke(path);
                importer.textureType = type;
                if (type != TextureImporterType.NormalMap) importer.sRGBTexture = colour;
                if (square) importer.filterMode = FilterMode.Point;
                importer.SaveAndReimport();
            }
            return brought;
        }

        private static Texture2D Texture(JToken described, string textures)
        {
            var file = described?.Value<string>("file");
            return string.IsNullOrEmpty(file) ? null : AssetDatabase.LoadAssetAtPath<Texture2D>($"{textures}/{Path.GetFileName(file)}");
        }

        /// <summary>Blender's numbers are light as it adds up; a material's colour property is how it looks on screen.</summary>
        private static Color Colour(JToken numbers, float scale = 1f)
        {
            var c = (numbers as JArray)?.Select(n => (float)n).ToArray() ?? new float[0];
            if (c.Length < 3) return Color.white;
            var colour = new Color(c[0] * scale, c[1] * scale, c[2] * scale, 1f).gamma;
            colour.a = c.Length > 3 ? c[3] : 1f;
            return colour;
        }

        private static void Keyword(Material material, string keyword, bool on)
        {
            if (on) material.EnableKeyword(keyword);
            else material.DisableKeyword(keyword);
        }

        private static void Fill(Material material, JObject d, bool urp, Func<JToken, Texture2D> texture)
        {
            string map = urp ? "_BaseMap" : "_MainTex", tint = urp ? "_BaseColor" : "_Color", gloss = urp ? "_Smoothness" : "_Glossiness";
            var colour = Colour(d["base_color"]);
            colour.a = d.Value<float?>("alpha") ?? (d["base_color_texture"] != null ? 1f : colour.a);
            var baseMap = texture(d["base_color_texture"]);
            var packed = texture(d["metallic_smoothness_texture"]);
            var normal = texture(d["normal_texture"]);
            var glow = texture(d["emission_texture"]);

            material.SetColor(tint, colour);
            material.SetTexture(map, baseMap);
            var uv = d["base_color_texture"]?["uv"];
            material.SetTextureScale(map, uv != null ? new Vector2((float)uv["scale"][0], (float)uv["scale"][1]) : Vector2.one);
            material.SetTextureOffset(map, uv != null ? new Vector2((float)uv["offset"][0], (float)uv["offset"][1]) : Vector2.zero);

            // With the packed image the two numbers scale what it holds; without, they are the values themselves.
            material.SetFloat("_Metallic", packed != null ? 1f : d.Value<float?>("metallic") ?? 0f);
            material.SetFloat(gloss, packed != null ? 1f : 1f - (d.Value<float?>("roughness") ?? 0.5f));
            if (!urp) material.SetFloat("_GlossMapScale", 1f);
            material.SetTexture("_MetallicGlossMap", packed);
            Keyword(material, urp ? "_METALLICSPECGLOSSMAP" : "_METALLICGLOSSMAP", packed != null);

            material.SetTexture("_BumpMap", normal);
            material.SetFloat("_BumpScale", d.Value<float?>("normal_strength") ?? 1f);
            Keyword(material, "_NORMALMAP", normal != null);

            float strength = d.Value<float?>("emission_strength") ?? 0f;
            bool glows = strength > 0f && d["emission_color"] != null;
            material.SetColor("_EmissionColor", glows ? Colour(d["emission_color"], strength) : Color.black);
            material.SetTexture("_EmissionMap", glows ? glow : null);
            Keyword(material, "_EMISSION", glows);
            material.globalIlluminationFlags = glows ? MaterialGlobalIlluminationFlags.BakedEmissive : MaterialGlobalIlluminationFlags.EmissiveIsBlack;

            var blend = d.Value<string>("blend");
            bool clip = blend == "clip", see_through = blend == "blend";
            material.SetFloat("_Cutoff", d.Value<float?>("alpha_clip") ?? 0.5f);
            material.SetFloat("_SrcBlend", see_through ? (float)BlendMode.SrcAlpha : (float)BlendMode.One);
            material.SetFloat("_DstBlend", see_through ? (float)BlendMode.OneMinusSrcAlpha : (float)BlendMode.Zero);
            material.SetFloat("_ZWrite", see_through ? 0f : 1f);
            Keyword(material, "_ALPHATEST_ON", clip);
            material.SetOverrideTag("RenderType", see_through ? "Transparent" : clip ? "TransparentCutout" : "Opaque");
            material.renderQueue = see_through ? (int)RenderQueue.Transparent : clip ? (int)RenderQueue.AlphaTest : -1;
            if (urp)
            {
                material.SetFloat("_AlphaClip", clip ? 1f : 0f);
                material.SetFloat("_Surface", see_through ? 1f : 0f);
                material.SetFloat("_Cull", d.Value<bool?>("two_sided") == true ? (float)CullMode.Off : (float)CullMode.Back);
                Keyword(material, "_SURFACE_TYPE_TRANSPARENT", see_through);
            }
            else
            {
                material.SetFloat("_Mode", see_through ? 2f : clip ? 1f : 0f);
                Keyword(material, "_ALPHABLEND_ON", see_through);
            }
        }

        private static string FileHash(string assetPath)
        {
            var path = Path.Combine(ChPaths.ProjectRoot, assetPath);
            if (!File.Exists(path)) return null;
            using (var sha = SHA256.Create())
            using (var stream = File.OpenRead(path))
            {
                return string.Concat(sha.ComputeHash(stream).Select(b => b.ToString("x2")));
            }
        }
    }
}
