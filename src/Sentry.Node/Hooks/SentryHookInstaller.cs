using System.Text.Json;
using System.Text.Json.Nodes;

namespace Sentry.Node.Hooks;

/// <summary>
/// Adds Sentry's hooks to a Claude Code settings file without disturbing what is
/// already there.
///
/// This machine already runs <c>claude-presence</c> on every event. Installing
/// over the top of someone else's hooks — or replacing the hooks block wholesale,
/// which is the obvious implementation — would silently remove a tool the person
/// depends on. Every operation here is additive and reversible.
/// </summary>
public static class SentryHookInstaller
{
    /// <summary>
    /// Identifies Sentry's own entries. Uninstall matches on this, so it can
    /// never remove a hook Sentry did not add.
    /// </summary>
    public const string Marker = "sentry-node hook";

    /// <summary>
    /// The events Sentry observes. PreToolUse carries a matcher because Sentry
    /// only cares about tools that change something; the rest apply broadly.
    /// </summary>
    public static readonly IReadOnlyList<(string Event, string? Matcher)> Events =
    [
        ("SessionStart", null),
        ("UserPromptSubmit", null),
        ("PreToolUse", "Write|Edit|Bash|Task"),
        ("Stop", null),
        ("Notification", null),
        ("SessionEnd", null),
    ];

    private static readonly JsonSerializerOptions Formatting = new() { WriteIndented = true };

    /// <summary>
    /// Add Sentry's hooks, leaving every existing entry in place.
    ///
    /// Running twice is the same as running once: an event that already carries a
    /// Sentry hook is skipped rather than gaining a duplicate.
    /// </summary>
    public static string Install(string settingsJson, string command)
    {
        var root = Parse(settingsJson);

        if (root["hooks"] is not JsonObject hooks)
        {
            hooks = new JsonObject();
            root["hooks"] = hooks;
        }

        foreach (var (name, matcher) in Events)
        {
            if (hooks[name] is not JsonArray groups)
            {
                groups = new JsonArray();
                hooks[name] = groups;
            }

            if (ContainsSentryHook(groups)) continue;

            var entry = new JsonObject
            {
                ["type"] = "command",
                ["command"] = command,
                ["timeout"] = 5,

                // Never block the person's session on Sentry's bookkeeping.
                ["async"] = true,
            };

            var group = new JsonObject { ["hooks"] = new JsonArray(entry) };
            if (matcher is not null) group["matcher"] = matcher;

            // Appended, so anything already registered still runs first.
            groups.Add(group);
        }

        return root.ToJsonString(Formatting);
    }

    /// <summary>
    /// Remove only Sentry's entries. Groups that held other hooks keep them, and
    /// a group emptied by the removal is dropped rather than left as a husk.
    /// </summary>
    public static string Uninstall(string settingsJson)
    {
        var root = Parse(settingsJson);
        if (root["hooks"] is not JsonObject hooks) return root.ToJsonString(Formatting);

        foreach (var (name, _) in Events)
        {
            if (hooks[name] is not JsonArray groups) continue;

            for (var g = groups.Count - 1; g >= 0; g--)
            {
                if (groups[g] is not JsonObject group ||
                    group["hooks"] is not JsonArray entries)
                {
                    continue;
                }

                for (var e = entries.Count - 1; e >= 0; e--)
                {
                    if (IsSentryHook(entries[e])) entries.RemoveAt(e);
                }

                if (entries.Count == 0) groups.RemoveAt(g);
            }

            if (groups.Count == 0) hooks.Remove(name);
        }

        return root.ToJsonString(Formatting);
    }

    /// <summary>Whether a settings file already carries Sentry's hooks.</summary>
    public static bool IsInstalled(string settingsJson)
    {
        var root = Parse(settingsJson);
        if (root["hooks"] is not JsonObject hooks) return false;

        return Events.Any(e =>
            hooks[e.Event] is JsonArray groups && ContainsSentryHook(groups));
    }

    private static JsonObject Parse(string json)
    {
        if (string.IsNullOrWhiteSpace(json)) return new JsonObject();

        var node = JsonNode.Parse(json)
            ?? throw new InvalidOperationException("Settings file is empty.");

        return node as JsonObject
            ?? throw new InvalidOperationException("Settings file is not a JSON object.");
    }

    private static bool ContainsSentryHook(JsonArray groups) =>
        groups.Any(group =>
            group is JsonObject obj &&
            obj["hooks"] is JsonArray entries &&
            entries.Any(IsSentryHook));

    private static bool IsSentryHook(JsonNode? entry) =>
        entry is JsonObject obj &&
        obj["command"]?.GetValue<string>() is { } command &&
        command.Contains(Marker, StringComparison.OrdinalIgnoreCase);
}
