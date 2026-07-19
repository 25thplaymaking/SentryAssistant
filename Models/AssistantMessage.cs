namespace SentryAssistant.Models;

/// <summary>
/// Who produced a conversation message. The shell styles user and Sentry turns
/// differently, so this is an explicit role rather than a display-name comparison.
/// </summary>
public enum MessageAuthor
{
    User,
    Sentry,
    System
}

public sealed class AssistantMessage
{
    public AssistantMessage() { }

    public AssistantMessage(MessageAuthor author, string speaker, string text, DateTimeOffset time) =>
        (Author, Speaker, Text, Time) = (author, speaker, text, time);

    public MessageAuthor Author { get; set; } = MessageAuthor.Sentry;
    public string Speaker { get; set; } = string.Empty;
    public string Text { get; set; } = string.Empty;
    public DateTimeOffset Time { get; set; }
    public string DisplayTime => Time.ToLocalTime().ToString("h:mm tt");
}
