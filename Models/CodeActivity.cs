namespace SentryAssistant.Models;

public sealed class CodeActivity
{
    public CodeActivity() { }
    public CodeActivity(string kind, string path, DateTimeOffset time) => (Kind, Path, Time) = (kind, path, time);

    public string Kind { get; set; } = string.Empty;
    public string Path { get; set; } = string.Empty;
    public DateTimeOffset Time { get; set; }
    public string DisplayTime => Time.ToLocalTime().ToString("h:mm:ss tt");
}
