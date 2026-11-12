using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using LicenseHub;
using Xunit;

namespace LicenseHub.Tests;

public sealed class SecurityTests
{
    [Fact]
    public void LicenseKeyHashIsStableNormalizedAndPepperBound()
    {
        var key = "CO-0123456789abcdef0123456789abcdef0123456789abcdef";
        var first = new LicenseKeyHasher(Enumerable.Repeat((byte)0x21, 32).ToArray());
        var second = new LicenseKeyHasher(Enumerable.Repeat((byte)0x22, 32).ToArray());

        var hash = first.Hash(key);
        Assert.Equal(hash, first.Hash($"  {key.ToLowerInvariant()}  "));
        Assert.NotEqual(hash, second.Hash(key));
        Assert.DoesNotContain(key, hash, StringComparison.OrdinalIgnoreCase);
        Assert.Throws<ArgumentException>(() => new LicenseKeyHasher(new byte[31]));
    }

    [Fact]
    public void SignedEntitlementUsesClientContractAndRsaPssSha256()
    {
        using var rsa = RSA.Create(2048);
        var signer = new EntitlementSigner(rsa.ExportPkcs8PrivateKeyPem());
        var claims = new EntitlementClaims(1, new string('a', 64), "trial",
            "2026-11-12T12:00:00.0000000Z", "2026-11-12T12:00:00.0000000Z",
            "2026-11-26T12:00:00.0000000Z", "2026-12-03T12:00:00.0000000Z",
            ["ai_execution"], null, "trial");

        var signed = signer.Sign(claims);
        var payload = Decode(signed.Payload);
        var signature = Decode(signed.Signature);
        Assert.True(rsa.VerifyData(payload, signature, HashAlgorithmName.SHA256, RSASignaturePadding.Pss));
        using var json = JsonDocument.Parse(payload);
        Assert.Equal(new[] { "version", "device_hash", "status", "issued_at", "not_before", "expires_at",
            "grace_until", "features", "license_id", "tier" },
            json.RootElement.EnumerateObject().Select(property => property.Name));
        Assert.Equal("trial", json.RootElement.GetProperty("status").GetString());
        Assert.Equal("ai_execution", json.RootElement.GetProperty("features")[0].GetString());
        Assert.DoesNotContain("license_key", Encoding.UTF8.GetString(payload));
    }

    [Fact]
    public void GeneratedLicenseKeyHasHighEntropyAndDeviceHashIsStrict()
    {
        var key = EntitlementSigner.CreateLicenseKey();
        Assert.Matches("^CO-[a-f0-9]{48}$", key);
        Assert.True(EntitlementSigner.IsDeviceHash(new string('f', 64)));
        Assert.False(EntitlementSigner.IsDeviceHash("abc"));
        Assert.False(EntitlementSigner.IsDeviceHash(new string('F', 64)));
    }

    [Fact]
    public void PublicDeviceEndpointAcceptsOnlyTheClientDeviceHashContract()
    {
        var options = new JsonSerializerOptions(JsonSerializerDefaults.Web)
        {
            UnmappedMemberHandling = System.Text.Json.Serialization.JsonUnmappedMemberHandling.Disallow
        };
        var request = JsonSerializer.Deserialize<VerifyRequest>(
            "{\"device_hash\":\"" + new string('a', 64) + "\"}", options);
        Assert.NotNull(request);
        Assert.Equal(new string('a', 64), request.DeviceHash);
        Assert.Throws<JsonException>(() => JsonSerializer.Deserialize<VerifyRequest>(
            "{\"device_hash\":\"" + new string('a', 64) + "\",\"source_code\":\"must not be accepted\"}", options));
    }

    private static byte[] Decode(string value) => Convert.FromBase64String(
        value.Replace('-', '+').Replace('_', '/') + new string('=', (4 - value.Length % 4) % 4));
}
