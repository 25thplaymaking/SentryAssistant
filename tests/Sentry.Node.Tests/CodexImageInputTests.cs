using Sentry.Node.Gateway;
using Sentry.Node.Harnesses;

namespace Sentry.Node.Tests;

public sealed class CodexImageInputTests
{
    private const string Png = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=";

    [Fact]
    public void NativeTurnInputContainsTextAndValidatedImage()
    {
        var directory = Path.Combine(Path.GetTempPath(), $"sentry-image-test-{Guid.NewGuid():N}");
        try
        {
            var input = CodexAppServerAdapter.BuildTurnInput(
                "Describe this image.", [new RuntimeImageInput(Png)], directory);

            Assert.Equal(2, input.Length);
            Assert.Equal("text", input[0]["type"]);
            Assert.Equal("Describe this image.", input[0]["text"]);
            Assert.Equal("localImage", input[1]["type"]);
            var path = Assert.IsType<string>(input[1]["path"]);
            Assert.True(File.Exists(path));
            Assert.Equal(0x89, File.ReadAllBytes(path)[0]);
        }
        finally
        {
            if (Directory.Exists(directory)) Directory.Delete(directory, recursive: true);
        }
    }

    [Fact]
    public void NativeTurnInputRefusesAClaimedImageWithNonImageBytes()
    {
        var bad = "data:image/png;base64,aGVsbG8=";

        var error = Assert.Throws<ArgumentException>(() =>
            CodexAppServerAdapter.BuildTurnInput(
                "Read it", [new RuntimeImageInput(bad)], Path.GetTempPath()));

        Assert.Contains("image", error.Message, StringComparison.OrdinalIgnoreCase);
    }
}
