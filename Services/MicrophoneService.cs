using System.Runtime.InteropServices;

namespace SentryAssistant.Services;

public sealed class MicrophoneService
{
    [DllImport("winmm.dll", CharSet = CharSet.Auto)]
    private static extern int mciSendString(string command, System.Text.StringBuilder? buffer, int bufferSize, nint callback);

    private readonly string _path = Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
        "SentryAssistant", "voice-reply.wav");

    public bool IsRecording { get; private set; }

    public void Start()
    {
        Directory.CreateDirectory(Path.GetDirectoryName(_path)!);
        mciSendString("close sentrymic", null, 0, 0);
        Ensure(mciSendString("open new type waveaudio alias sentrymic", null, 0, 0), "open the microphone");
        Ensure(mciSendString("set sentrymic time format ms bitspersample 16 channels 1 samplespersec 16000 bytespersec 32000 alignment 2", null, 0, 0), "configure the microphone");
        Ensure(mciSendString("record sentrymic", null, 0, 0), "start recording");
        IsRecording = true;
    }

    public string Stop()
    {
        if (!IsRecording) return _path;
        mciSendString("stop sentrymic", null, 0, 0);
        Ensure(mciSendString($"save sentrymic \"{_path}\"", null, 0, 0), "save the recording");
        mciSendString("close sentrymic", null, 0, 0);
        IsRecording = false;
        return _path;
    }

    private static void Ensure(int code, string action)
    {
        if (code != 0) throw new InvalidOperationException($"Sentry could not {action} (Windows audio error {code}).");
    }
}
