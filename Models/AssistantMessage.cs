namespace SentryAssistant.Models;

public sealed class AssistantMessage
{
    public AssistantMessage() { }
    public AssistantMessage(string speaker, string text, DateTimeOffset time) => (Speaker, Text, Time) = (speaker, text, time);

    public string Speaker { get; set; } = string.Empty;
    public string Text { get; set; } = string.Empty;
    public DateTimeOffset Time { get; set; }
    public string DisplayTime => Time.ToLocalTime().ToString("h:mm tt");
}
