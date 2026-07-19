using System.Text.Json;
using System.Text.Json.Serialization;

namespace Sentry.Contracts;

public sealed record HarnessEvent(
    Guid WorkOrderId,
    string Harness,
    string Type,
    DateTimeOffset OccurredAt,
    string Summary,
    JsonElement Evidence);

public sealed record NotificationEnvelope(
    Guid Id,
    string EventType,
    NotificationSeverity Severity,
    string Title,
    string RedactedSummary,
    Uri DeepLink,
    IReadOnlyList<string> AllowedChannels,
    DateTimeOffset ExpiresAt,
    string CorrelationId);

public enum NotificationSeverity
{
    Routine,
    Important,
    Urgent,
    Security
}

public static class SentryContractJson
{
    public static JsonSerializerOptions Options { get; } = CreateOptions();

    private static JsonSerializerOptions CreateOptions()
    {
        var options = new JsonSerializerOptions(JsonSerializerDefaults.Web)
        {
            PropertyNameCaseInsensitive = false,
            WriteIndented = false
        };

        options.Converters.Add(new JsonStringEnumConverter(JsonNamingPolicy.CamelCase, false));
        return options;
    }
}
