using MongoDB.Bson.Serialization.Attributes;
using System.Text.Json.Serialization;

namespace LicenseHub;

public sealed class AuthTransactionDocument
{
    [BsonId] public string Id { get; set; } = DesktopAuthProtocol.Token();
    public string DeviceHash { get; set; } = "";
    public string Challenge { get; set; } = "";
    public string Callback { get; set; } = "";
    public string State { get; set; } = "";
    public string Status { get; set; } = "pending";
    public DateTime ExpiresAt { get; set; }
    public string? CodeHash { get; set; }
    public string? UserId { get; set; }
}

public sealed class GoogleIdentityDocument
{
    [BsonId] public string Id { get; set; } = "";
    public string Provider { get; set; } = "google";
    public string Subject { get; set; } = "";
    public string UserId { get; set; } = "";
    public string VerifiedEmail { get; set; } = "";
    public DateTime LastLoginAt { get; set; }
}

public sealed class IdentityClaimDocument
{
    [BsonId] public string Id { get; set; } = "";
    public string Subject { get; set; } = "";
    public string VerifiedEmail { get; set; } = "";
    public DateTime RequestedAt { get; set; }
    public DateTime ExpiresAt { get; set; }
}

public sealed class AuthSessionDocument
{
    [BsonId] public string Id { get; set; } = Guid.NewGuid().ToString("N");
    public string UserId { get; set; } = "";
    public string DeviceHash { get; set; } = "";
    public string AccessHash { get; set; } = "";
    public string RefreshHash { get; set; } = "";
    public List<string> PreviousRefreshHashes { get; set; } = [];
    public DateTime AccessExpiresAt { get; set; }
    public DateTime ExpiresAt { get; set; }
    public bool Revoked { get; set; }
}

public sealed record DesktopLoginStart(
    [property: JsonPropertyName("device_hash")] string DeviceHash,
    [property: JsonPropertyName("code_challenge")] string CodeChallenge,
    [property: JsonPropertyName("callback_uri")] string CallbackUri,
    [property: JsonPropertyName("state")] string State);
public sealed record DesktopLoginExchange(
    [property: JsonPropertyName("code")] string Code,
    [property: JsonPropertyName("code_verifier")] string CodeVerifier,
    [property: JsonPropertyName("device_hash")] string DeviceHash);
public sealed record DesktopRefresh(
    [property: JsonPropertyName("refresh_token")] string RefreshToken,
    [property: JsonPropertyName("device_hash")] string DeviceHash);
public sealed record ApproveIdentityClaim([property: JsonPropertyName("user_id")] string UserId);
