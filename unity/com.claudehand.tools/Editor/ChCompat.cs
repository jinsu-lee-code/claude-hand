using UnityEditor;
using UnityEngine;

namespace ClaudeHand.Editor
{
    /// <summary>
    /// Object ids across Unity versions. Unity 6.3 marks the int instance-ID APIs obsolete (warning) and the
    /// Unity 7 reference source makes them errors (GetInstanceID, EditorUtility.InstanceIDToObject), in favour
    /// of EntityId. Refs are resolved through GlobalObjectId anyway; the id here is only a fast path and the
    /// "instance_id" handed to MCP for Unity's own tools.
    /// </summary>
    internal static class ChCompat
    {
        public static string ObjectId(Object obj)
        {
#if UNITY_7000_0_OR_NEWER
            return obj.GetEntityId().ToString();
#else
#pragma warning disable CS0618
            return obj.GetInstanceID().ToString();
#pragma warning restore CS0618
#endif
        }

        /// <summary>Snapshot value for MCP for Unity tools: a number on Unity 6, the EntityId text on Unity 7.</summary>
        public static object InstanceIdForTools(Object obj)
        {
#if UNITY_7000_0_OR_NEWER
            return obj.GetEntityId().ToString();
#else
#pragma warning disable CS0618
            return obj.GetInstanceID();
#pragma warning restore CS0618
#endif
        }

        /// <summary>Fast lookup by the id from <see cref="ObjectId"/>; null when it cannot be used (callers fall back to GlobalObjectId).</summary>
        public static GameObject FindGameObject(string id)
        {
#if UNITY_7000_0_OR_NEWER
            return null;
#else
            if (!int.TryParse(id, out var instanceId)) return null;
#pragma warning disable CS0618
            return EditorUtility.InstanceIDToObject(instanceId) as GameObject;
#pragma warning restore CS0618
#endif
        }
    }
}
