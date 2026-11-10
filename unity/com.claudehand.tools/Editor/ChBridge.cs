using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Net;
using System.Net.Sockets;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

namespace ClaudeHand.Editor
{
    /// <summary>
    /// claude-hand's own way into this Editor: a loopback socket that runs the ch_* tools ([ChTool] classes of
    /// this assembly), so they do not depend on MCP for Unity: neither on whether it exposes custom tools (its
    /// stdio server in 10.3.0 does not) nor on its types to compile.
    ///
    /// Protocol: one JSON object per line in, one per line out.
    ///   {"token": "...", "tool": "ch_scene_snapshot", "params": {...}}  ->  {"ok": true, "result": {...}}
    ///   tool "__ping__" answers without touching the scene; "__list__" returns the tool metadata.
    /// Requests are read on background threads and run on the main thread (EditorApplication.update).
    /// Where to connect is written to ~/.claude-hand/unity/&lt;hash&gt;.json (port, token, tools), with the same
    /// hash MCP for Unity uses for this project (first 8 hex of SHA1(Application.dataPath)).
    /// </summary>
    [InitializeOnLoad]
    internal static class ChBridge
    {
        private const string TokenKey = "ClaudeHand.Bridge.Token";
        private const string PortKey = "ClaudeHand.Bridge.Port";
        private const double HeartbeatSeconds = 30;
        private static readonly ConcurrentQueue<Pending> Queue = new ConcurrentQueue<Pending>();
        private static TcpListener _listener;
        private static volatile bool _running;
        private static string _token;
        private static int _port;
        private static double _lastHeartbeat;

        private sealed class Pending
        {
            public JObject Request;
            public string Response;
            public readonly ManualResetEventSlim Done = new ManualResetEventSlim(false);
        }

        static ChBridge()
        {
            if (Application.isBatchMode) return;
            try
            {
                Start();
            }
            catch (Exception ex)
            {
                Debug.LogWarning("[Claude Hand] Could not open the claude-hand bridge: " + ex.Message);
                return;
            }
            EditorApplication.update += Drain;
            AssemblyReloadEvents.beforeAssemblyReload += Stop; // InitializeOnLoad starts it again afterwards
            EditorApplication.quitting += () =>
            {
                Stop();
                try { File.Delete(RegistryFile); } catch (Exception) { /* best effort */ }
            };
        }

        // ------------------------------------------------------------------ identity and registry

        internal static string ProjectHash(int length = 8)
        {
            using (var sha1 = SHA1.Create())
            {
                var bytes = sha1.ComputeHash(Encoding.UTF8.GetBytes(Application.dataPath ?? string.Empty));
                var hex = string.Concat(bytes.Select(b => b.ToString("x2")));
                return hex.Substring(0, Math.Min(length, hex.Length));
            }
        }

        private static string RegistryFile
        {
            get
            {
                var dir = Path.Combine(ChPaths.ClaudeHandHome, "unity");
                Directory.CreateDirectory(dir);
                return Path.Combine(dir, ProjectHash() + ".json");
            }
        }

        /// <summary>Claude Hand Tools' version (package.json), so selftest can tell an old compile from this checkout.</summary>
        private static string PackageVersion()
        {
            var info = UnityEditor.PackageManager.PackageInfo.FindForAssembly(typeof(ChBridge).Assembly);
            return info != null ? info.version : string.Empty;
        }

        private static void WriteRegistry()
        {
            var data = new
            {
                format = "claude-hand/unity-bridge@1",
                name = ChPaths.ProjectName,
                project_path = ChPaths.ToPosix(ChPaths.ProjectRoot),
                data_path = ChPaths.ToPosix(Application.dataPath),
                hash = ProjectHash(),
                hash16 = ProjectHash(16),
                port = _port,
                token = _token,
                pid = System.Diagnostics.Process.GetCurrentProcess().Id,
                unity_version = Application.unityVersion,
                version = PackageVersion(),
                heartbeat_utc = DateTime.UtcNow.ToString("o"),
                tools = DescribeTools(),
            };
            var path = RegistryFile;
            var tmp = path + ".tmp";
            File.WriteAllText(tmp, JsonConvert.SerializeObject(data, Formatting.Indented));
            if (File.Exists(path)) File.Delete(path);
            File.Move(tmp, path);
        }

        // ------------------------------------------------------------------ socket

        private static void Start()
        {
            _token = SessionState.GetString(TokenKey, string.Empty);
            if (string.IsNullOrEmpty(_token))
            {
                var bytes = new byte[24];
                using (var rng = RandomNumberGenerator.Create()) rng.GetBytes(bytes);
                _token = Convert.ToBase64String(bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_');
                SessionState.SetString(TokenKey, _token);
            }

            // Keep the port across domain reloads so a client that read the registry a moment ago still connects.
            int preferred = SessionState.GetInt(PortKey, 0);
            try
            {
                _listener = new TcpListener(IPAddress.Loopback, preferred);
                _listener.Start();
            }
            catch (SocketException)
            {
                _listener = new TcpListener(IPAddress.Loopback, 0);
                _listener.Start();
            }
            _port = ((IPEndPoint)_listener.LocalEndpoint).Port;
            SessionState.SetInt(PortKey, _port);
            _running = true;
            new Thread(AcceptLoop) { IsBackground = true, Name = "Claude Hand bridge" }.Start();
            WriteRegistry();
            _lastHeartbeat = EditorApplication.timeSinceStartup;
        }

        private static void Stop()
        {
            _running = false;
            try { _listener?.Stop(); } catch (Exception) { /* already closed */ }
            while (Queue.TryDequeue(out var pending))
            {
                pending.Response = Error("EDITOR_BUSY", "Unity is reloading scripts.", "safe");
                pending.Done.Set();
            }
        }

        private static void AcceptLoop()
        {
            while (_running)
            {
                TcpClient client;
                try
                {
                    client = _listener.AcceptTcpClient();
                }
                catch (Exception)
                {
                    if (!_running) return;
                    continue;
                }
                ThreadPool.QueueUserWorkItem(_ => Serve(client));
            }
        }

        private static void Serve(TcpClient client)
        {
            using (client)
            {
                try
                {
                    client.ReceiveTimeout = 15000;
                    var stream = client.GetStream();
                    var reader = new StreamReader(stream, new UTF8Encoding(false));
                    var writer = new StreamWriter(stream, new UTF8Encoding(false)) { AutoFlush = true, NewLine = "\n" };
                    var line = reader.ReadLine();
                    if (string.IsNullOrEmpty(line)) return;
                    var request = JObject.Parse(line);
                    if (!string.Equals(request.Value<string>("token"), _token, StringComparison.Ordinal))
                    {
                        writer.WriteLine(Error("UNAUTHORIZED", "Wrong token; read the registry file again.", "after_fix"));
                        return;
                    }
                    if (request.Value<string>("tool") == "__ping__")
                    {
                        writer.WriteLine(JsonConvert.SerializeObject(new { ok = true, result = new { project = ChPaths.ProjectName, hash = ProjectHash() } }));
                        return;
                    }

                    var pending = new Pending { Request = request };
                    Queue.Enqueue(pending);
                    var timeout = TimeSpan.FromSeconds(Math.Max(5, request.Value<int?>("timeout") ?? 120));
                    writer.WriteLine(pending.Done.Wait(timeout)
                        ? pending.Response
                        : Error("TIMEOUT", "Unity did not run the request in time (busy, or blocked by a dialog).", "check_first"));
                }
                catch (Exception)
                {
                    // the client went away or sent garbage; nothing to answer
                }
            }
        }

        // ------------------------------------------------------------------ main thread

        private static void Drain()
        {
            while (Queue.TryDequeue(out var pending))
            {
                try
                {
                    pending.Response = Dispatch(pending.Request);
                }
                catch (Exception ex)
                {
                    pending.Response = Error("FAILED", (ex.InnerException ?? ex).Message, "check_first");
                }
                finally
                {
                    pending.Done.Set();
                }
            }

            if (EditorApplication.timeSinceStartup - _lastHeartbeat > HeartbeatSeconds)
            {
                _lastHeartbeat = EditorApplication.timeSinceStartup;
                try { WriteRegistry(); } catch (Exception) { /* try again next heartbeat */ }
            }
        }

        private static string Dispatch(JObject request)
        {
            var name = request.Value<string>("tool");
            if (name == "__list__") return JsonConvert.SerializeObject(new { ok = true, result = DescribeTools() });

            var type = ToolTypes().FirstOrDefault(t => t.GetCustomAttribute<ChToolAttribute>().Name == name);
            if (type == null) return Error("NOT_FOUND", $"No claude-hand tool named '{name}'.", "after_fix");
            var method = type.GetMethod("HandleCommand", BindingFlags.Public | BindingFlags.Static, null, new[] { typeof(JObject) }, null);
            if (method == null) return Error("FAILED", $"{type.Name} has no HandleCommand(JObject).", "after_fix");
            var result = method.Invoke(null, new object[] { request["params"] as JObject ?? new JObject() });
            return JsonConvert.SerializeObject(new { ok = true, result });
        }

        private static IEnumerable<Type> ToolTypes()
        {
            return typeof(ChBridge).Assembly.GetTypes().Where(t => t.GetCustomAttribute<ChToolAttribute>() != null);
        }

        /// <summary>Name, description, polling and parameters of every ch_* tool, read from its attributes.</summary>
        private static List<object> DescribeTools()
        {
            var tools = new List<object>();
            foreach (var type in ToolTypes().OrderBy(t => t.Name))
            {
                var attribute = type.GetCustomAttribute<ChToolAttribute>();
                var parameters = new List<object>();
                var parametersType = type.GetNestedType("Parameters");
                if (parametersType != null)
                {
                    foreach (var property in parametersType.GetProperties(BindingFlags.Public | BindingFlags.Instance))
                    {
                        var info = property.GetCustomAttribute<ChParameterAttribute>();
                        parameters.Add(new
                        {
                            name = property.Name,
                            type = JsonType(property.PropertyType),
                            description = info?.Description ?? string.Empty,
                            required = info?.Required ?? false,
                        });
                    }
                }
                tools.Add(new
                {
                    name = attribute.Name,
                    description = attribute.Description,
                    requires_polling = attribute.RequiresPolling,
                    poll_action = attribute.PollAction,
                    max_poll_seconds = attribute.MaxPollSeconds,
                    parameters,
                });
            }
            return tools;
        }

        private static string JsonType(Type type)
        {
            type = Nullable.GetUnderlyingType(type) ?? type;
            if (type == typeof(string)) return "string";
            if (type == typeof(int) || type == typeof(long)) return "integer";
            if (type == typeof(float) || type == typeof(double)) return "number";
            if (type == typeof(bool)) return "boolean";
            if (type.IsArray || (type.IsGenericType && type.GetGenericTypeDefinition() == typeof(List<>))) return "array";
            return "object";
        }

        private static string Error(string code, string message, string retry)
        {
            return JsonConvert.SerializeObject(new { ok = false, error = new { code, message, retry } });
        }
    }
}
