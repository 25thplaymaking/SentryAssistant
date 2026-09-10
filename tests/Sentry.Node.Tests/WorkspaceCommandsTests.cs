using Sentry.Node.Workspaces;

namespace Sentry.Node.Tests;

public class WorkspaceCommandsTests
{
    [Fact]
    public void ReplaceRegistrationsUpdatesRegisteredIdsAndResolves()
    {
        var registry = new WorkspaceRegistry(
        [
            new WorkspaceRegistration(
                "ws-init",
                @"C:\Users\Bryce\Documents\Init",
                new HashSet<string> { "shell" },
                new HashSet<string> { "readOnly" })
        ]);

        Assert.Single(registry.RegisteredIds);
        Assert.Equal("ws-init", registry.RegisteredIds.First());

        registry.ReplaceRegistrations(
        [
            new WorkspaceRegistration(
                "ws-new-1",
                @"C:\Users\Bryce\Documents\New1",
                new HashSet<string> { "shell", "codex" },
                new HashSet<string> { "readOnly", "workspaceWrite" }),
            new WorkspaceRegistration(
                "ws-new-2",
                @"C:\Users\Bryce\Documents\New2",
                new HashSet<string> { "shell" },
                new HashSet<string> { "readOnly" })
        ]);

        Assert.Equal(2, registry.RegisteredIds.Count);
        Assert.Contains("ws-new-1", registry.RegisteredIds);
        Assert.Contains("ws-new-2", registry.RegisteredIds);
        Assert.DoesNotContain("ws-init", registry.RegisteredIds);

        var reg = registry.Resolve("ws-new-1", "codex", "workspaceWrite");
        Assert.Equal(@"C:\Users\Bryce\Documents\New1", reg.RootPath);
    }

    [Fact]
    public void SlugifyHandlesSpecialCharactersAndSpaces()
    {
        Assert.Equal("blender-addons", WorkspaceCommands.Slugify("Blender Addons"));
        Assert.Equal("my-cool-project-123", WorkspaceCommands.Slugify("My_Cool!Project#123"));
        Assert.Equal("arma-reforger", WorkspaceCommands.Slugify("Arma Reforger"));
    }

    [Fact]
    public void WorkspaceCommandsAddAndListWorkCorrectly()
    {
        var tempDir = Path.Combine(Path.GetTempPath(), "SentryTest_" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(tempDir);
        var targetFolder = Path.Combine(tempDir, "ProjectX");
        Directory.CreateDirectory(targetFolder);

        var configPath = Path.Combine(tempDir, "appsettings.node.json");
        File.WriteAllText(configPath, "{\n  \"workspaces\": []\n}");

        var outWriter = new StringWriter();
        var errWriter = new StringWriter();

        var code = WorkspaceCommands.Run(new[] { "workspace", "add", targetFolder, "--id", "project-x" }, configPath, outWriter, errWriter);
        Assert.Equal(0, code);
        Assert.Contains("Successfully added workspace 'project-x'", outWriter.ToString());

        // Verify listed
        outWriter = new StringWriter();
        var listCode = WorkspaceCommands.Run(new[] { "workspace", "list" }, configPath, outWriter, errWriter);
        Assert.Equal(0, listCode);
        Assert.Contains("project-x", outWriter.ToString());

        // Clean up
        Directory.Delete(tempDir, recursive: true);
    }
}
