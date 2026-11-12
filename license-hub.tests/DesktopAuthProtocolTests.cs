using System.Security.Claims;
using LicenseHub;
using Xunit;

namespace LicenseHub.Tests;

public sealed class DesktopAuthProtocolTests
{
    [Fact]
    public void PkceUsesS256AndRejectsWrongVerifierAndNonCanonicalChallenge()
    {
        const string verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk";
        const string challenge = "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM";
        Assert.Equal(challenge, DesktopAuthProtocol.Challenge(verifier));
        Assert.True(DesktopAuthProtocol.MatchesChallenge(challenge, verifier));
        Assert.False(DesktopAuthProtocol.MatchesChallenge(challenge, new string('a', 43)));
        Assert.False(DesktopAuthProtocol.MatchesChallenge(challenge, "short"));
        Assert.False(DesktopAuthProtocol.ValidChallenge(challenge[..^1] + "N"));
        Assert.False(DesktopAuthProtocol.ValidChallenge(challenge + "="));
        Assert.False(DesktopAuthProtocol.ValidToken(new string('a', 43) + "\n"));
        Assert.False(DesktopAuthProtocol.ValidVerifier(new string('a', 43) + "\n"));
        var token = DesktopAuthProtocol.Token();
        Assert.True(DesktopAuthProtocol.ValidToken(token));
        Assert.NotEqual(token, DesktopAuthProtocol.Hash(token));
        Assert.Equal(64, DesktopAuthProtocol.Hash(token).Length);
    }

    [Theory]
    [InlineData("http://127.0.0.1:49152/oauth/callback", true)]
    [InlineData("http://127.0.0.1:65535/oauth/callback", true)]
    [InlineData("http://127.0.0.1:80/oauth/callback", false)]
    [InlineData("http://localhost:49152/oauth/callback", false)]
    [InlineData("http://127.0.0.1.evil.test:49152/oauth/callback", false)]
    [InlineData("http://127.0.0.1:49152@evil.test/oauth/callback", false)]
    [InlineData("http://127.0.0.1:49152/oauth/callback?redirect=evil", false)]
    [InlineData("http://127.0.0.1:49152/oauth/callback#fragment", false)]
    [InlineData("http://127.0.0.1:65536/oauth/callback", false)]
    [InlineData("http://2130706433:49152/oauth/callback", false)]
    [InlineData("https://evil.test/oauth/callback", false)]
    public void CallbackIsStrictLiteralLoopback(string callback, bool valid) =>
        Assert.Equal(valid, DesktopAuthProtocol.ValidDesktopCallback(callback));

    [Fact]
    public void ProductionOriginRequiresHttpsAndCannotContainPathOrCredentials()
    {
        Assert.Equal("https://hub.example.invalid", DesktopAuthProtocol.PublicOrigin("https://hub.example.invalid/", false));
        Assert.Throws<InvalidOperationException>(() => DesktopAuthProtocol.PublicOrigin("http://127.0.0.1:8080", false));
        Assert.Equal("http://127.0.0.1:8080", DesktopAuthProtocol.PublicOrigin("http://127.0.0.1:8080", true));
        foreach (var origin in new[] { "http://example.invalid", "http://2130706433:8080", "http://127.1:8080", "https://user:pass@example.invalid", "https://example.invalid/path", "https://example.invalid/?secret=value" })
            Assert.Throws<InvalidOperationException>(() => DesktopAuthProtocol.PublicOrigin(origin, true));
    }

    [Fact]
    public void VerifiedEmailAndStableSubjectAreRequiredAfterOidcValidation()
    {
        var principal = new ClaimsPrincipal(new ClaimsIdentity([
            new Claim("sub", "google-fixture-subject"), new Claim("email", "fixture@example.invalid"),
            new Claim("email_verified", "false")], "oidc"));
        Assert.Throws<InvalidOperationException>(() => DesktopAuthProtocol.VerifiedGoogleAccount(principal));
        var identity = (ClaimsIdentity)principal.Identity!;
        identity.RemoveClaim(identity.FindFirst("email_verified")!);
        identity.AddClaim(new Claim("email_verified", "true"));
        Assert.Equal("google-fixture-subject", DesktopAuthProtocol.VerifiedGoogleAccount(principal).Subject);
        identity.RemoveClaim(identity.FindFirst("sub")!);
        Assert.Throws<InvalidOperationException>(() => DesktopAuthProtocol.VerifiedGoogleAccount(principal));
    }
}
