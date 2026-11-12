using LicenseHub;
using Xunit;

namespace LicenseHub.Tests;

public sealed class AccountDirectoryTests
{
    [Fact]
    public void ClientMetadataIsBoundedAndRejectsSecretsUntrustedFieldsAndDuplicateIds()
    {
        var report = new DesktopConnectionReport("macos", "0.1.0-alpha.2", [new("github", "github", "valid", "fixture-user")]);
        Assert.True(AccountDirectory.ValidReport(report));
        Assert.False(AccountDirectory.ValidReport(report with { Platform = "arbitrary-os" }));
        Assert.False(AccountDirectory.ValidReport(report with { AppVersion = "secret\nvalue" }));
        Assert.False(AccountDirectory.ValidReport(report with { Connections = [new("github", "github", "valid"), new("github", "github", "invalid")] }));
        Assert.False(AccountDirectory.ValidReport(report with { Connections = Enumerable.Range(0, 21).Select(i => new ConnectionReport("fixture" + i, "github", "valid")).ToList() }));
        foreach (var label in new[] { "ghp_synthetic-token", "github_pat_synthetic-token", "sk-synthetic-token", "GOCSPX-synthetic-token", "<script>alert(1)</script>", "user\nname" })
            Assert.False(AccountDirectory.ValidReport(report with { Connections = [new("github", "github", "valid", label)] }));
        Assert.False(AccountDirectory.ValidReport(report with { Connections = [new("github", "github", "verified_by_hub")] }));
        Assert.False(AccountDirectory.ValidReport(report with { Connections = [new("github", "github", "valid", "fixture", DateTime.UtcNow.AddHours(1))] }));
    }
}
