namespace SentryAssistant.Models;

public sealed class AssistantSettings
{
    public string AssistantName { get; set; } = "Sentry";
    public string AssistantModel { get; set; } = "gpt-5-mini";
    public string SummaryModel { get; set; } = "gpt-5-mini";
    public string SpeechModel { get; set; } = "gpt-4o-mini-tts";
    public string SpeechVoice { get; set; } = "coral";
    public string TranscriptionModel { get; set; } = "gpt-4o-mini-transcribe";
    public string VoiceStyle { get; set; } = "Calm, concise, capable Jarvis-style operations assistant.";
    public int SummaryWordLimit { get; set; } = 22;
    public bool SpeakResolutions { get; set; } = true;
    public bool BriefResolutionSummaries { get; set; } = true;
    public bool StartWithWindows { get; set; }
    public bool NotifyOnCodeChanges { get; set; } = true;
    public string WatchedFolder { get; set; } = @"C:\Users\Bryce\Desktop\25thVID-Website";

    /// <summary>
    /// Sentry Gateway base address. Empty means the desktop is running
    /// standalone, which the shell reports as "not set up" rather than as a
    /// failure. Not a secret: the gateway is authenticated separately.
    /// </summary>
    public string GatewayUrl { get; set; } = string.Empty;

    /// <summary>
    /// The <c>user@host</c> the gateway is reached through when its address is a
    /// loopback one, so the connections page can print the exact forward command
    /// instead of a placeholder.
    ///
    /// Not a secret — an SSH destination, never a key. Authentication is the
    /// agent's business.
    /// </summary>
    public string GatewayTunnelTarget { get; set; } = string.Empty;
}
