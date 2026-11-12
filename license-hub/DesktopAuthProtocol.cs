using System.Security.Claims;
using System.Net.Mail;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;

namespace LicenseHub;

public static partial class DesktopAuthProtocol
{
    public static string Token() => Base64Url(RandomNumberGenerator.GetBytes(32));
    public static string Hash(string value) => Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(value))).ToLowerInvariant();
    public static string Challenge(string verifier) => Base64Url(SHA256.HashData(Encoding.ASCII.GetBytes(verifier)));
    public static bool ValidToken(string? value) => value is not null && TokenPattern().IsMatch(value);
    public static bool ValidVerifier(string? value) => value is not null && VerifierPattern().IsMatch(value);
    public static bool ValidChallenge(string? value)
    {
        if (!ValidToken(value)) return false;
        try { return Base64Url(Convert.FromBase64String(value!.Replace('-', '+').Replace('_', '/') + "=")) == value; }
        catch (FormatException) { return false; }
    }

    public static bool MatchesChallenge(string challenge, string verifier) => ValidChallenge(challenge)
        && ValidVerifier(verifier) && CryptographicOperations.FixedTimeEquals(
            Encoding.ASCII.GetBytes(challenge), Encoding.ASCII.GetBytes(Challenge(verifier)));

    public static bool ValidDesktopCallback(string? value) => value is not null
        && CallbackPattern().IsMatch(value) && Uri.TryCreate(value, UriKind.Absolute, out var uri)
        && uri.Host == "127.0.0.1" && uri.Port is >= 1024 and <= 65535;

    public static string PublicOrigin(string value, bool development)
    {
        if (!Uri.TryCreate(value, UriKind.Absolute, out var uri) || uri.UserInfo.Length != 0
            || uri.Query.Length != 0 || uri.Fragment.Length != 0 || uri.AbsolutePath != "/")
            throw new InvalidOperationException("HUB_PUBLIC_ORIGIN must be an origin without path or credentials.");
        if (uri.Scheme != "https" && !(development && uri.Scheme == "http" && uri.Host == "127.0.0.1"
            && LocalOriginPattern().IsMatch(value)))
            throw new InvalidOperationException("Hub authentication requires HTTPS; only Development may use literal loopback HTTP.");
        return uri.GetLeftPart(UriPartial.Authority);
    }

    // Call only after the OIDC middleware has validated signature, issuer, audience, expiry and nonce.
    public static GoogleAccount VerifiedGoogleAccount(ClaimsPrincipal principal)
    {
        var sub = principal.FindFirstValue("sub");
        var email = principal.FindFirstValue("email");
        var verified = principal.FindFirstValue("email_verified");
        if (string.IsNullOrWhiteSpace(sub) || sub.Length > 255 || sub.Any(char.IsControl)
            || email is null || email.Length is < 3 or > 254 || email.Any(char.IsControl)
            || !MailAddress.TryCreate(email, out var parsed) || parsed.Address != email || parsed.DisplayName.Length != 0
            || !string.Equals(verified, "true", StringComparison.OrdinalIgnoreCase))
            throw new InvalidOperationException("Google did not provide a verified account.");
        var name = principal.FindFirstValue("name") ?? "Google user";
        name = new string(name.Where(c => !char.IsControl(c)).Take(120).ToArray());
        return new GoogleAccount(sub, email.Trim().ToLowerInvariant(), string.IsNullOrWhiteSpace(name) ? "Google user" : name);
    }

    private static string Base64Url(byte[] value) => Convert.ToBase64String(value).TrimEnd('=').Replace('+', '-').Replace('/', '_');
    [GeneratedRegex("^[A-Za-z0-9_-]{43}\\z", RegexOptions.CultureInvariant)]
    private static partial Regex TokenPattern();
    [GeneratedRegex("^[A-Za-z0-9._~-]{43,128}\\z", RegexOptions.CultureInvariant)]
    private static partial Regex VerifierPattern();
    [GeneratedRegex(@"^http://127\.0\.0\.1:[0-9]{4,5}/oauth/callback\z", RegexOptions.CultureInvariant)]
    private static partial Regex CallbackPattern();
    [GeneratedRegex(@"^http://127\.0\.0\.1(?::[0-9]{1,5})?/?\z", RegexOptions.CultureInvariant)]
    private static partial Regex LocalOriginPattern();
}

public sealed record GoogleAccount(string Subject, string Email, string DisplayName);
