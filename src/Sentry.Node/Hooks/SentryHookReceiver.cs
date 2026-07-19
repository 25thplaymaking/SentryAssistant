using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.Json.Serialization;

namespace Sentry.Node.Hooks;

/// <summary>
/// What Sentry records when a harness event fires.
///
/// Deliberately narrow. A hook payload carries the prompt, the tool input, and
/// whatever the person happened to be editing; none of that belongs in Sentry's
/// activity trail, and a field-by-field allowlist is the only version of this
/// that stays safe as upstream payloads grow.
/// </summary>
public sealed record HookObservation(
    [property: JsonPropertyName("event")] string Event,
    [property: JsonPropertyName("session_id")] string SessionId,
    [property: JsonPropertyName("tool")] string? Tool,
    [property: JsonPropertyName("workspace")] string? Workspace);

/// <summary>
/// Turns a Claude Code hook payload into the little that Sentry keeps.
/// </summary>
public static class SentryHookReceiver
{
    /// <summary>
    /// Fields copied through. Everything absent from this list is dropped,
    /// including anything added upstream later — the failure mode of an
    /// allowlist is losing a field, and of a denylist is leaking one.
    /// </summary>
    private static readonly string[] Allowed =
        ["hook_event_name", "session_id", "tool_name", "cwd"];

    /// <summary>
    /// Read a payload and keep only what is safe to record. Returns null when the
    /// payload is unusable, so a malformed event is dropped rather than guessed
    /// at.
    /// </summary>
    public static HookObservation? Observe(string payloadJson)
    {
        if (string.IsNullOrWhiteSpace(payloadJson)) return null;

        JsonObject? payload;
        try
        {
            payload = JsonNode.Parse(payloadJson) as JsonObject;
        }
        catch (JsonException)
        {
            return null;
        }

        if (payload is null) return null;

        var name = Text(payload, "hook_event_name");
        if (string.IsNullOrWhiteSpace(name)) return null;

        return new HookObservation(
            Event: name,
            SessionId: Text(payload, "session_id") ?? string.Empty,
            Tool: Text(payload, "tool_name"),

            // The directory, never the contents. Useful for attributing activity
            // to a workspace and harmless on its own.
            Workspace: Text(payload, "cwd"));
    }

    /// <summary>
    /// The allowlist, exposed so a test can assert that a field nobody
    /// anticipated does not quietly become readable.
    /// </summary>
    public static bool IsAllowed(string field) => Allowed.Contains(field);

    private static string? Text(JsonObject payload, string field)
    {
        if (!IsAllowed(field)) return null;

        return payload[field] is JsonValue value && value.TryGetValue<string>(out var text)
            ? text
            : null;
    }
}
