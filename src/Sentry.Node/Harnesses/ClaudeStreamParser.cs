using System.Text.Json;

namespace Sentry.Node.Harnesses;

/// <summary>
/// One normalized event from a harness stream.
/// </summary>
public sealed record HarnessStreamEvent(
    string Type,
    string Summary,
    bool IsTerminal,
    bool IsError);

/// <summary>
/// Parses Claude Code's <c>--output-format stream-json</c> output.
///
/// Every line is newline-delimited JSON. Unrecognized shapes normalize to
/// non-terminal progress rather than being dropped or mistaken for a result:
/// only an explicit <c>result</c> event may end a run, so a truncated or
/// malformed stream can never be reported as success.
/// </summary>
public static class ClaudeStreamParser
{
    public static HarnessStreamEvent? ParseLine(string line)
    {
        line = line.Trim();
        if (line.Length == 0) return null;

        JsonDocument document;
        try
        {
            document = JsonDocument.Parse(line);
        }
        catch (JsonException)
        {
            // A malformed frame is surfaced, not swallowed, but it is never
            // terminal — a broken stream must not look like a finished run.
            return new HarnessStreamEvent(
                "error",
                $"Malformed harness frame: {Truncate(line, 200)}",
                IsTerminal: false,
                IsError: true);
        }

        using (document)
        {
            var root = document.RootElement;
            if (root.ValueKind != JsonValueKind.Object) return null;

            var type = root.TryGetProperty("type", out var typeElement)
                ? typeElement.GetString() ?? ""
                : "";

            return type switch
            {
                "system" => new HarnessStreamEvent(
                    "session.started",
                    Subtype(root) == "init" ? "Harness session started." : "Harness system event.",
                    IsTerminal: false,
                    IsError: false),

                "assistant" => new HarnessStreamEvent(
                    "message",
                    Truncate(ExtractText(root), 2000),
                    IsTerminal: false,
                    IsError: false),

                "user" => new HarnessStreamEvent(
                    "tool.progress",
                    "Tool result returned to the harness.",
                    IsTerminal: false,
                    IsError: false),

                "result" => ParseResult(root),

                // Unknown event types are progress. They are never terminal and
                // never speakable, so a new upstream event cannot be mistaken
                // for a completed run.
                _ => new HarnessStreamEvent(
                    "tool.progress",
                    string.IsNullOrEmpty(type) ? "Harness progress." : $"Harness event '{type}'.",
                    IsTerminal: false,
                    IsError: false)
            };
        }
    }

    private static HarnessStreamEvent ParseResult(JsonElement root)
    {
        var isError = root.TryGetProperty("is_error", out var errorElement)
                      && errorElement.ValueKind == JsonValueKind.True;

        var subtype = Subtype(root);
        // Anything other than an explicit success subtype counts as failure,
        // so an unfamiliar terminal subtype fails closed.
        var failed = isError || (subtype.Length > 0 && subtype != "success");

        var text = root.TryGetProperty("result", out var resultElement)
            ? resultElement.GetString() ?? ""
            : "";

        if (text.Length == 0 && failed)
        {
            text = subtype.Length > 0 ? $"Harness ended with '{subtype}'." : "Harness reported an error.";
        }

        return new HarnessStreamEvent(
            failed ? "turn.failed" : "turn.completed",
            Truncate(text, 4000),
            IsTerminal: true,
            IsError: failed);
    }

    private static string Subtype(JsonElement root) =>
        root.TryGetProperty("subtype", out var element) ? element.GetString() ?? "" : "";

    /// <summary>Pulls readable text out of an assistant message's content blocks.</summary>
    private static string ExtractText(JsonElement root)
    {
        if (!root.TryGetProperty("message", out var message)) return "Assistant message.";
        if (!message.TryGetProperty("content", out var content)) return "Assistant message.";

        if (content.ValueKind == JsonValueKind.String)
        {
            return content.GetString() ?? "Assistant message.";
        }

        if (content.ValueKind != JsonValueKind.Array) return "Assistant message.";

        var parts = new List<string>();
        foreach (var block in content.EnumerateArray())
        {
            if (block.ValueKind != JsonValueKind.Object) continue;

            var blockType = block.TryGetProperty("type", out var t) ? t.GetString() : null;
            if (blockType == "text" && block.TryGetProperty("text", out var textElement))
            {
                var value = textElement.GetString();
                if (!string.IsNullOrWhiteSpace(value)) parts.Add(value);
            }
            else if (blockType == "tool_use" && block.TryGetProperty("name", out var nameElement))
            {
                // Tool names are useful evidence; their inputs are not recorded
                // here because they can carry file contents and secrets.
                parts.Add($"[tool: {nameElement.GetString()}]");
            }
        }

        return parts.Count > 0 ? string.Join("\n", parts) : "Assistant message.";
    }

    private static string Truncate(string value, int limit) =>
        value.Length <= limit ? value : value[..limit] + "...";
}
