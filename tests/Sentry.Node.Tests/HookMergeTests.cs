using System.Text.Json;
using System.Text.Json.Nodes;
using Sentry.Node.Hooks;

namespace Sentry.Node.Tests;

public class HookMergeTests
{
    private const string Command = "sentry-node hook";

    /// <summary>
    /// This machine's actual settings file, trimmed to the parts that matter.
    /// claude-presence runs on all six events.
    /// </summary>
    private const string ExistingSettings = """
    {
      "permissions": { "defaultMode": "acceptEdits" },
      "model": "opus[1m]",
      "hooks": {
        "SessionStart": [
          { "matcher": "", "hooks": [ { "type": "command", "command": "claude-presence hook", "timeout": 5 } ] }
        ],
        "UserPromptSubmit": [
          { "hooks": [ { "type": "command", "command": "claude-presence hook", "timeout": 5, "async": true } ] }
        ],
        "PreToolUse": [
          { "matcher": "Write|Edit|Bash|Read|Grep|Glob|WebSearch|WebFetch|Task",
            "hooks": [ { "type": "command", "command": "claude-presence hook", "timeout": 5, "async": true } ] }
        ],
        "Stop": [
          { "hooks": [ { "type": "command", "command": "claude-presence hook", "timeout": 5, "async": true } ] }
        ],
        "Notification": [
          { "hooks": [ { "type": "command", "command": "claude-presence hook", "timeout": 5, "async": true } ] }
        ],
        "SessionEnd": [
          { "hooks": [ { "type": "command", "command": "claude-presence hook", "timeout": 5, "async": true } ] }
        ]
      },
      "voiceEnabled": true
    }
    """;

    private static JsonObject Hooks(string json) =>
        (JsonObject)JsonNode.Parse(json)!["hooks"]!;

    private static List<JsonNode> PresenceEntries(string json)
    {
        var found = new List<JsonNode>();
        foreach (var (name, _) in SentryHookInstaller.Events)
        {
            if (Hooks(json)[name] is not JsonArray groups) continue;
            foreach (var group in groups)
            {
                if (group?["hooks"] is not JsonArray entries) continue;
                foreach (var entry in entries)
                {
                    var command = entry?["command"]?.GetValue<string>() ?? "";
                    if (command.Contains("claude-presence")) found.Add(entry!);
                }
            }
        }
        return found;
    }

    // The requirement that matters most: installing must not cost the person a
    // tool they already rely on.
    [Fact]
    public void ExistingPresenceHooksSurviveInstallationUnchanged()
    {
        var before = PresenceEntries(ExistingSettings);
        var after = PresenceEntries(SentryHookInstaller.Install(ExistingSettings, Command));

        Assert.Equal(6, before.Count);
        Assert.Equal(before.Count, after.Count);

        for (var i = 0; i < before.Count; i++)
        {
            Assert.Equal(
                before[i].ToJsonString(),
                after[i].ToJsonString());
        }
    }

    [Fact]
    public void SentryHooksAreAddedToEveryObservedEvent()
    {
        var merged = SentryHookInstaller.Install(ExistingSettings, Command);
        var hooks = Hooks(merged);

        foreach (var (name, _) in SentryHookInstaller.Events)
        {
            var groups = Assert.IsType<JsonArray>(hooks[name]);
            var commands = groups
                .SelectMany(g => (JsonArray)g!["hooks"]!)
                .Select(e => e!["command"]!.GetValue<string>())
                .ToList();

            Assert.Contains(Command, commands);
            Assert.Contains("claude-presence hook", commands);
        }
    }

    [Fact]
    public void InstallingTwiceDoesNotDuplicateEntries()
    {
        var once = SentryHookInstaller.Install(ExistingSettings, Command);
        var twice = SentryHookInstaller.Install(once, Command);

        Assert.Equal(once, twice);

        foreach (var (name, _) in SentryHookInstaller.Events)
        {
            var count = ((JsonArray)Hooks(twice)[name]!)
                .SelectMany(g => (JsonArray)g!["hooks"]!)
                .Count(e => e!["command"]!.GetValue<string>() == Command);

            Assert.Equal(1, count);
        }
    }

    [Fact]
    public void UninstallRemovesOnlySentryEntries()
    {
        var merged = SentryHookInstaller.Install(ExistingSettings, Command);
        var reverted = SentryHookInstaller.Uninstall(merged);

        Assert.False(SentryHookInstaller.IsInstalled(reverted));
        Assert.Equal(6, PresenceEntries(reverted).Count);

        var presenceBefore = PresenceEntries(ExistingSettings);
        var presenceAfter = PresenceEntries(reverted);
        for (var i = 0; i < presenceBefore.Count; i++)
        {
            Assert.Equal(presenceBefore[i].ToJsonString(), presenceAfter[i].ToJsonString());
        }
    }

    // A group that held nothing but a Sentry hook should disappear rather than
    // linger as an empty shell that later reads as a configuration error.
    [Fact]
    public void UninstallDropsGroupsItEmpties()
    {
        var fresh = SentryHookInstaller.Install("{}", Command);
        var reverted = SentryHookInstaller.Uninstall(fresh);

        var hooks = JsonNode.Parse(reverted)!["hooks"] as JsonObject;
        Assert.True(hooks is null || hooks.Count == 0);
    }

    [Fact]
    public void UnrelatedSettingsAreUntouched()
    {
        var merged = SentryHookInstaller.Install(ExistingSettings, Command);
        var root = JsonNode.Parse(merged)!;

        Assert.Equal("opus[1m]", root["model"]!.GetValue<string>());
        Assert.True(root["voiceEnabled"]!.GetValue<bool>());
        Assert.Equal("acceptEdits", root["permissions"]!["defaultMode"]!.GetValue<string>());
    }

    [Fact]
    public void InstallingIntoAFileWithNoHooksBlockWorks()
    {
        var merged = SentryHookInstaller.Install("""{ "model": "opus" }""", Command);

        Assert.True(SentryHookInstaller.IsInstalled(merged));
        Assert.Equal("opus", JsonNode.Parse(merged)!["model"]!.GetValue<string>());
    }

    [Fact]
    public void SentryHooksNeverBlockTheSession()
    {
        var merged = SentryHookInstaller.Install(ExistingSettings, Command);

        foreach (var (name, _) in SentryHookInstaller.Events)
        {
            var sentry = ((JsonArray)Hooks(merged)[name]!)
                .SelectMany(g => (JsonArray)g!["hooks"]!)
                .Single(e => e!["command"]!.GetValue<string>() == Command);

            Assert.True(sentry!["async"]!.GetValue<bool>());
            Assert.Equal(5, sentry["timeout"]!.GetValue<int>());
        }
    }

    [Fact]
    public void AMalformedSettingsFileIsRefusedRatherThanOverwritten()
    {
        Assert.Throws<InvalidOperationException>(
            () => SentryHookInstaller.Install("[1,2,3]", Command));
        Assert.ThrowsAny<Exception>(
            () => SentryHookInstaller.Install("{ not json", Command));
    }

    // --- payload sanitisation ------------------------------------------------

    [Fact]
    public void APayloadIsReducedToTheFewFieldsSentryKeeps()
    {
        var payload = """
        {
          "hook_event_name": "PreToolUse",
          "session_id": "abc-123",
          "tool_name": "Edit",
          "cwd": "C:/work/repo",
          "prompt": "the user's private question",
          "tool_input": { "file_path": "C:/secrets/keys.txt", "new_string": "sk-live-1234" },
          "transcript_path": "C:/Users/x/.claude/transcript.jsonl"
        }
        """;

        var observed = SentryHookReceiver.Observe(payload);

        Assert.NotNull(observed);
        Assert.Equal("PreToolUse", observed.Event);
        Assert.Equal("abc-123", observed.SessionId);
        Assert.Equal("Edit", observed.Tool);
        Assert.Equal("C:/work/repo", observed.Workspace);

        // Nothing sensitive survives into what gets recorded.
        var serialized = JsonSerializer.Serialize(observed);
        Assert.DoesNotContain("private question", serialized);
        Assert.DoesNotContain("sk-live-1234", serialized);
        Assert.DoesNotContain("keys.txt", serialized);
        Assert.DoesNotContain("transcript", serialized);
    }

    [Fact]
    public void FieldsOutsideTheAllowlistAreNotReadable()
    {
        Assert.True(SentryHookReceiver.IsAllowed("tool_name"));
        Assert.False(SentryHookReceiver.IsAllowed("prompt"));
        Assert.False(SentryHookReceiver.IsAllowed("tool_input"));
        Assert.False(SentryHookReceiver.IsAllowed("transcript_path"));
    }

    [Fact]
    public void AnUnusablePayloadIsDroppedRatherThanGuessedAt()
    {
        Assert.Null(SentryHookReceiver.Observe(""));
        Assert.Null(SentryHookReceiver.Observe("{ not json"));
        Assert.Null(SentryHookReceiver.Observe("""{ "session_id": "abc" }"""));
    }
}
