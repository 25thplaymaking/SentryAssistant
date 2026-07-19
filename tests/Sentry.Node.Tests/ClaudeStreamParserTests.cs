using Sentry.Node.Harnesses;

namespace Sentry.Node.Tests;

public class ClaudeStreamParserTests
{
    [Fact]
    public void BlankLinesAreIgnored()
    {
        Assert.Null(ClaudeStreamParser.ParseLine(""));
        Assert.Null(ClaudeStreamParser.ParseLine("   "));
    }

    [Fact]
    public void SystemInitStartsTheSession()
    {
        var e = ClaudeStreamParser.ParseLine("""{"type":"system","subtype":"init"}""")!;
        Assert.Equal("session.started", e.Type);
        Assert.False(e.IsTerminal);
    }

    [Fact]
    public void AssistantTextIsExtracted()
    {
        var line = """
        {"type":"assistant","message":{"content":[{"type":"text","text":"All tests pass."}]}}
        """;
        var e = ClaudeStreamParser.ParseLine(line)!;
        Assert.Equal("message", e.Type);
        Assert.Contains("All tests pass.", e.Summary);
        Assert.False(e.IsTerminal);
    }

    [Fact]
    public void ToolUseIsRecordedByNameWithoutItsInput()
    {
        var line = """
        {"type":"assistant","message":{"content":[
          {"type":"tool_use","name":"Read","input":{"file_path":"C:/secrets/key.pem"}}]}}
        """;
        var e = ClaudeStreamParser.ParseLine(line)!;
        Assert.Contains("[tool: Read]", e.Summary);
        // Tool inputs can carry file contents and secrets, so they stay out.
        Assert.DoesNotContain("key.pem", e.Summary);
    }

    [Fact]
    public void SuccessResultIsTerminalAndNotAnError()
    {
        var line = """
        {"type":"result","subtype":"success","is_error":false,"result":"Done."}
        """;
        var e = ClaudeStreamParser.ParseLine(line)!;
        Assert.Equal("turn.completed", e.Type);
        Assert.True(e.IsTerminal);
        Assert.False(e.IsError);
        Assert.Equal("Done.", e.Summary);
    }

    [Fact]
    public void ErrorResultIsTerminalFailure()
    {
        var line = """
        {"type":"result","subtype":"error_during_execution","is_error":true,"result":"boom"}
        """;
        var e = ClaudeStreamParser.ParseLine(line)!;
        Assert.Equal("turn.failed", e.Type);
        Assert.True(e.IsTerminal);
        Assert.True(e.IsError);
    }

    // An unfamiliar terminal subtype must fail closed rather than read as success.
    [Fact]
    public void UnknownResultSubtypeFailsClosed()
    {
        var line = """
        {"type":"result","subtype":"some_future_outcome","result":"?"}
        """;
        var e = ClaudeStreamParser.ParseLine(line)!;
        Assert.Equal("turn.failed", e.Type);
        Assert.True(e.IsError);
    }

    // A truncated or malformed stream must never look like a finished run.
    [Fact]
    public void MalformedFrameIsSurfacedButNeverTerminal()
    {
        var e = ClaudeStreamParser.ParseLine("{not json")!;
        Assert.Equal("error", e.Type);
        Assert.True(e.IsError);
        Assert.False(e.IsTerminal);
    }

    [Fact]
    public void UnknownEventTypeIsNonTerminalProgress()
    {
        var e = ClaudeStreamParser.ParseLine("""{"type":"some_new_event"}""")!;
        Assert.Equal("tool.progress", e.Type);
        Assert.False(e.IsTerminal);
        Assert.False(e.IsError);
    }

    [Fact]
    public void OnlyAResultEventCanEndARun()
    {
        string[] nonTerminal =
        [
            """{"type":"system","subtype":"init"}""",
            """{"type":"assistant","message":{"content":[{"type":"text","text":"working"}]}}""",
            """{"type":"user"}""",
            """{"type":"anything_else"}""",
            "{malformed"
        ];

        foreach (var line in nonTerminal)
        {
            Assert.False(ClaudeStreamParser.ParseLine(line)!.IsTerminal, line);
        }

        Assert.True(ClaudeStreamParser.ParseLine(
            """{"type":"result","subtype":"success","result":"ok"}""")!.IsTerminal);
    }

    [Fact]
    public void LongOutputIsTruncated()
    {
        var huge = new string('x', 10_000);
        var line = $$"""{"type":"result","subtype":"success","result":"{{huge}}"}""";
        var e = ClaudeStreamParser.ParseLine(line)!;
        Assert.True(e.Summary.Length <= 4010, $"length was {e.Summary.Length}");
    }
}
