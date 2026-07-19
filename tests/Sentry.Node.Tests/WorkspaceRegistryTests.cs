using Sentry.Node.Workspaces;

namespace Sentry.Node.Tests;

public class WorkspaceRegistryTests
{
    private static WorkspaceRegistry Registry() => new(
    [
        new WorkspaceRegistration(
            "ws-sentry",
            @"C:\Users\Bryce\Desktop\SentryAssistant",
            new HashSet<string> { "codex", "claude" },
            new HashSet<string> { "readOnly", "workspaceWrite" }),
        new WorkspaceRegistration(
            "ws-readonly",
            @"C:\Users\Bryce\Desktop\Reference",
            new HashSet<string> { "codex" },
            new HashSet<string> { "readOnly" })
    ]);

    [Fact]
    public void ResolvesARegisteredWorkspace()
    {
        var registration = Registry().Resolve("ws-sentry", "codex", "workspaceWrite");
        Assert.Equal(@"C:\Users\Bryce\Desktop\SentryAssistant", registration.RootPath);
    }

    [Fact]
    public void UnregisteredWorkspaceIsRefused()
    {
        var exception = Assert.Throws<WorkspaceResolutionException>(
            () => Registry().Resolve("ws-unknown", "codex", "readOnly"));
        Assert.Contains("not registered", exception.Message);
    }

    // The Gateway sends identifiers, never paths. A path-shaped value must not
    // be treated as a location just because it looks like one.
    [Theory]
    [InlineData(@"C:\Windows\System32")]
    [InlineData(@"\\attacker\share")]
    [InlineData("/etc/passwd")]
    [InlineData(@"..\..\Secrets")]
    public void PathShapedWorkspaceIdentifiersAreRefused(string candidate)
    {
        var exception = Assert.Throws<WorkspaceResolutionException>(
            () => Registry().Resolve(candidate, "codex", "readOnly"));
        Assert.Contains("not registered", exception.Message);
    }

    [Fact]
    public void RefusalDoesNotEchoTheRequestedValue()
    {
        var exception = Assert.Throws<WorkspaceResolutionException>(
            () => Registry().Resolve(@"C:\Users\Bryce\.ssh", "codex", "readOnly"));
        Assert.DoesNotContain(".ssh", exception.Message);
    }

    [Fact]
    public void DisabledHarnessIsRefused()
    {
        var exception = Assert.Throws<WorkspaceResolutionException>(
            () => Registry().Resolve("ws-readonly", "claude", "readOnly"));
        Assert.Contains("not enabled", exception.Message);
    }

    [Fact]
    public void ModeOutsideTheWorkspaceGrantIsRefused()
    {
        var exception = Assert.Throws<WorkspaceResolutionException>(
            () => Registry().Resolve("ws-readonly", "codex", "workspaceWrite"));
        Assert.Contains("not permitted", exception.Message);
    }

    [Fact]
    public void ElevatedModeIsNeverImplicitlyGranted()
    {
        Assert.Throws<WorkspaceResolutionException>(
            () => Registry().Resolve("ws-sentry", "codex", "approvedElevated"));
    }

    [Fact]
    public void EmptyWorkspaceIdentifierIsRefused()
    {
        Assert.Throws<WorkspaceResolutionException>(
            () => Registry().Resolve("", "codex", "readOnly"));
    }

    [Fact]
    public void RelativeRegistrationRootIsRejectedAtConstruction()
    {
        Assert.Throws<ArgumentException>(() => new WorkspaceRegistry(
        [
            new WorkspaceRegistration(
                "ws-relative", @"relative\path",
                new HashSet<string> { "codex" }, new HashSet<string> { "readOnly" })
        ]));
    }

    public class PathContainment
    {
        private static (WorkspaceRegistry Registry, WorkspaceRegistration Registration) Setup()
        {
            var registry = Registry();
            return (registry, registry.Resolve("ws-sentry", "codex", "workspaceWrite"));
        }

        private static WorkspaceRegistry Registry() => new(
        [
            new WorkspaceRegistration(
                "ws-sentry",
                @"C:\Users\Bryce\Desktop\SentryAssistant",
                new HashSet<string> { "codex" },
                new HashSet<string> { "readOnly", "workspaceWrite" })
        ]);

        [Fact]
        public void ResolvesAChildPath()
        {
            var (registry, registration) = Setup();
            var resolved = registry.ResolvePathWithin(registration, @"src\Sentry.Node");
            Assert.StartsWith(registration.RootPath, resolved);
        }

        [Fact]
        public void EmptyRelativePathReturnsTheRoot()
        {
            var (registry, registration) = Setup();
            Assert.Equal(registration.RootPath, registry.ResolvePathWithin(registration, ""));
        }

        [Theory]
        [InlineData(@"..\..\..\Windows\System32")]
        [InlineData(@"..\.ssh\id_ed25519")]
        [InlineData(@"src\..\..\..\secrets")]
        public void TraversalOutOfTheRootIsRefused(string relative)
        {
            var (registry, registration) = Setup();
            var exception = Assert.Throws<WorkspaceResolutionException>(
                () => registry.ResolvePathWithin(registration, relative));
            Assert.Contains("escapes", exception.Message);
        }

        [Theory]
        [InlineData(@"C:\Windows\System32")]
        [InlineData(@"\\attacker\share\payload")]
        public void AbsolutePathIsRefused(string absolute)
        {
            var (registry, registration) = Setup();
            var exception = Assert.Throws<WorkspaceResolutionException>(
                () => registry.ResolvePathWithin(registration, absolute));
            Assert.Contains("Absolute paths are refused", exception.Message);
        }

        // "C:\...\SentryAssistantEvil" must not pass as a child of
        // "C:\...\SentryAssistant" through a bare prefix comparison.
        [Fact]
        public void SiblingDirectorySharingAPrefixIsRefused()
        {
            var (registry, registration) = Setup();
            Assert.Throws<WorkspaceResolutionException>(
                () => registry.ResolvePathWithin(registration, @"..\SentryAssistantEvil\payload"));
        }
    }
}
