using System.Diagnostics;
using System.Runtime.InteropServices;

namespace WordLyricsForNoctis;

/// <summary>
/// Ties a started program, and everything it starts in turn, to this one: disposing the job
/// stops them all, and so does Windows when Noctis itself ends for any reason. Without it the
/// program would go on working with nobody watching (stopping only the command window that
/// started it leaves the rest running).
/// </summary>
internal sealed class Job : IDisposable
{
    private IntPtr _handle;

    private Job(IntPtr handle) => _handle = handle;

    /// <summary>Null when Windows refuses; the caller then stops the process itself.</summary>
    public static Job? Around(Process process)
    {
        var handle = CreateJobObjectW(IntPtr.Zero, null);
        if (handle == IntPtr.Zero) return null;
        var limits = new ExtendedLimits();
        limits.Basic.LimitFlags = KillOnJobClose;
        if (!SetInformationJobObject(handle, ExtendedLimitInformation, ref limits, (uint)Marshal.SizeOf<ExtendedLimits>())
            || !AssignProcessToJobObject(handle, process.Handle))
        {
            CloseHandle(handle);
            return null;
        }
        return new Job(handle);
    }

    /// <summary>Stops whatever of the program is still running.</summary>
    public void Dispose()
    {
        var handle = Interlocked.Exchange(ref _handle, IntPtr.Zero);
        if (handle == IntPtr.Zero) return;
        TerminateJobObject(handle, 1);
        CloseHandle(handle);
    }

    private const int ExtendedLimitInformation = 9;
    private const uint KillOnJobClose = 0x2000;

    [StructLayout(LayoutKind.Sequential)]
    private struct BasicLimits
    {
        public long PerProcessUserTimeLimit;
        public long PerJobUserTimeLimit;
        public uint LimitFlags;
        public UIntPtr MinimumWorkingSetSize;
        public UIntPtr MaximumWorkingSetSize;
        public uint ActiveProcessLimit;
        public UIntPtr Affinity;
        public uint PriorityClass;
        public uint SchedulingClass;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct IoCounters
    {
        public ulong ReadOperations, WriteOperations, OtherOperations, ReadBytes, WriteBytes, OtherBytes;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct ExtendedLimits
    {
        public BasicLimits Basic;
        public IoCounters Io;
        public UIntPtr ProcessMemoryLimit;
        public UIntPtr JobMemoryLimit;
        public UIntPtr PeakProcessMemoryUsed;
        public UIntPtr PeakJobMemoryUsed;
    }

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern IntPtr CreateJobObjectW(IntPtr attributes, string? name);

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool SetInformationJobObject(IntPtr job, int kind, ref ExtendedLimits info, uint size);

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool TerminateJobObject(IntPtr job, uint exitCode);

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool CloseHandle(IntPtr handle);
}
