using System;
using System.Runtime.InteropServices;
using UnityEngine;

namespace ClaudeHand.Editor
{
    /// <summary>Windows: blink the editor's taskbar button until it comes to the front (the chat's "done" alert).</summary>
    internal static class ChTaskbar
    {
        private const uint FlashAll = 0x3;          // caption and taskbar button
        private const uint FlashUntilFront = 0xC;   // FLASHW_TIMERNOFG

        [StructLayout(LayoutKind.Sequential)]
        private struct FlashInfo
        {
            public uint cbSize;
            public IntPtr hwnd;
            public uint dwFlags;
            public uint uCount;
            public uint dwTimeout;
        }

        [DllImport("user32.dll")]
        private static extern bool FlashWindowEx(ref FlashInfo info);

        /// <summary>Returns whether it blinked: only on Windows, and only when the main window was found.</summary>
        public static bool Flash()
        {
            if (Application.platform != RuntimePlatform.WindowsEditor) return false;
            var hwnd = System.Diagnostics.Process.GetCurrentProcess().MainWindowHandle;
            if (hwnd == IntPtr.Zero) return false;
            var info = new FlashInfo
            {
                cbSize = (uint)Marshal.SizeOf<FlashInfo>(),
                hwnd = hwnd,
                dwFlags = FlashAll | FlashUntilFront,
            };
            FlashWindowEx(ref info);
            return true;
        }
    }
}
