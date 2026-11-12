using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;

namespace LicenseHub;

public sealed class LicenseKeyHasher(byte[] pepper)
{
    private readonly byte[] _pepper = pepper.Length >= 32
        ? pepper.ToArray()
        : throw new ArgumentException("License key pepper must contain at least 32 bytes.", nameof(pepper));

    public string Hash(string licenseKey)
    {
        var normalized = licenseKey.Trim().ToUpperInvariant();
        if (normalized.Length is < 16 or > 256)
            throw new ArgumentException("License key is invalid.", nameof(licenseKey));
        return Convert.ToHexString(HMACSHA256.HashData(_pepper, Encoding.UTF8.GetBytes(normalized))).ToLowerInvariant();
    }
}

public sealed class EntitlementSigner(string privateKeyPem)
{
    private readonly RSA _rsa = Load(privateKeyPem);

    private static RSA Load(string pem)
    {
        var rsa = RSA.Create();
        rsa.ImportFromPem(pem);
        if (rsa.KeySize < 2048)
        {
            rsa.Dispose();
            throw new InvalidOperationException("License signing key must be at least 2048 bits.");
        }
        return rsa;
    }

    public SignedEntitlement Sign(EntitlementClaims claims)
    {
        var payload = JsonSerializer.SerializeToUtf8Bytes(claims);
        var signature = _rsa.SignData(payload, HashAlgorithmName.SHA256, RSASignaturePadding.Pss);
        return new SignedEntitlement(Base64Url(payload), Base64Url(signature));
    }

    public static string CreateLicenseKey() => "CO-" + Convert.ToHexString(RandomNumberGenerator.GetBytes(24)).ToLowerInvariant();

    public static bool IsDeviceHash(string value) => Regex.IsMatch(value, "^[a-f0-9]{64}$", RegexOptions.CultureInvariant);

    private static string Base64Url(byte[] value) => Convert.ToBase64String(value).TrimEnd('=').Replace('+', '-').Replace('/', '_');
}
