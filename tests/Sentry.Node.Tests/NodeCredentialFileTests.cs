using Sentry.Node.Gateway;

namespace Sentry.Node.Tests;

public class NodeCredentialFileTests
{
    [Fact]
    public async Task DpapiFileRoundTripsWithoutPlaintextSecrets()
    {
        Assert.SkipUnless(OperatingSystem.IsWindows(), "Windows DPAPI is required.");
        var root = Directory.CreateTempSubdirectory("sentry-node-credentials");
        try
        {
            var path = Path.Combine(root.FullName, "node.credentials.json");
            var file = new NodeCredentialFile(path);
            var expected = new NodeCredentialSecrets(
                "access-secret-value", "refresh-secret-value", "signing-secret-value");

            await file.SaveAsync(expected, TestContext.Current.CancellationToken);
            var raw = await File.ReadAllTextAsync(path, TestContext.Current.CancellationToken);
            Assert.DoesNotContain(expected.AccessToken, raw);
            Assert.DoesNotContain(expected.RefreshToken, raw);
            Assert.DoesNotContain(expected.SigningKey, raw);
            Assert.Equal(expected, file.Load());
        }
        finally
        {
            root.Delete(recursive: true);
        }
    }
}
