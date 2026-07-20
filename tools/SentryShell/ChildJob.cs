using System.Runtime.InteropServices;

namespace SentryShell;

/// <summary>
/// A Windows job object that kills its members when the last handle closes —
/// i.e. when this process dies, for ANY reason.
///
/// Graceful shutdown already kills ssh in Tunnel.Dispose(), but that only runs
/// on a clean exit. Task Manager "End task", a crash, or a debugger stop all
/// skip it and strand the ssh child holding the forwarded ports. That orphan
/// class is exactly what left runaway processes chewing CPU on this machine
/// before, so the OS — not a finally block — is what guarantees cleanup here.
/// </summary>
internal sealed class ChildJob : IDisposable
{
    private const int JobObjectExtendedLimitInformation = 9;
    private const uint LimitKillOnJobClose = 0x2000;

    [StructLayout(LayoutKind.Sequential)]
    private struct JobBasicLimitInformation
    {
        public long PerProcessUserTimeLimit, PerJobUserTimeLimit;
        public uint LimitFlags;
        public nuint MinimumWorkingSetSize, MaximumWorkingSetSize;
        public uint ActiveProcessLimit;
        public nuint Affinity;
        public uint PriorityClass, SchedulingClass;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct IoCounters
    {
        public ulong ReadOperationCount, WriteOperationCount, OtherOperationCount;
        public ulong ReadTransferCount, WriteTransferCount, OtherTransferCount;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct JobExtendedLimitInformation
    {
        public JobBasicLimitInformation BasicLimitInformation;
        public IoCounters IoInfo;
        public nuint ProcessMemoryLimit, JobMemoryLimit, PeakProcessMemoryUsed, PeakJobMemoryUsed;
    }

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern IntPtr CreateJobObjectW(IntPtr attributes, string? name);

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool SetInformationJobObject(IntPtr job, int infoClass, IntPtr info, uint length);

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool CloseHandle(IntPtr handle);

    private IntPtr _handle;

    internal bool Ready => _handle != IntPtr.Zero;

    internal ChildJob()
    {
        _handle = CreateJobObjectW(IntPtr.Zero, null);
        if (_handle == IntPtr.Zero) return;

        var info = new JobExtendedLimitInformation();
        info.BasicLimitInformation.LimitFlags = LimitKillOnJobClose;

        var size = Marshal.SizeOf<JobExtendedLimitInformation>();
        var ptr = Marshal.AllocHGlobal(size);
        try
        {
            Marshal.StructureToPtr(info, ptr, false);
            if (!SetInformationJobObject(_handle, JobObjectExtendedLimitInformation, ptr, (uint)size))
            {
                // Without the limit set, membership would NOT imply cleanup —
                // better to fall back to Dispose()-only than to believe a
                // guarantee we do not have.
                CloseHandle(_handle);
                _handle = IntPtr.Zero;
            }
        }
        finally { Marshal.FreeHGlobal(ptr); }
    }

    internal bool Add(System.Diagnostics.Process process)
    {
        if (_handle == IntPtr.Zero) return false;
        try { return AssignProcessToJobObject(_handle, process.Handle); }
        catch { return false; }
    }

    public void Dispose()
    {
        if (_handle == IntPtr.Zero) return;
        CloseHandle(_handle);   // closing the last handle kills every member
        _handle = IntPtr.Zero;
    }
}
