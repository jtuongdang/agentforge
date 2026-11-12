using MongoDB.Bson.Serialization.Attributes;

namespace LicenseHub;

public sealed class DeviceDocument
{
    [BsonId]
    public string Id { get; set; } = Guid.NewGuid().ToString("N");
    [BsonElement("device_hash")]
    public string DeviceHash { get; set; } = "";
    [BsonElement("first_seen_at")]
    public DateTime FirstSeenAt { get; set; }
    [BsonElement("trial_expires_at")]
    public DateTime TrialExpiresAt { get; set; }
    [BsonElement("last_seen_at")]
    public DateTime LastSeenAt { get; set; }
    [BsonElement("status")]
    public string Status { get; set; } = "trial";
    [BsonElement("license_id")]
    public string? LicenseId { get; set; }
}

public sealed class LicenseDocument
{
    [BsonId]
    public string Id { get; set; } = Guid.NewGuid().ToString("N");
    [BsonElement("license_key_hash")]
    public string LicenseKeyHash { get; set; } = "";
    [BsonElement("tier")]
    public string Tier { get; set; } = "monthly";
    [BsonElement("max_devices")]
    public int MaxDevices { get; set; } = 1;
    [BsonElement("created_at")]
    public DateTime CreatedAt { get; set; }
    [BsonElement("expires_at")]
    public DateTime? ExpiresAt { get; set; }
    [BsonElement("is_active")]
    public bool IsActive { get; set; } = true;
    [BsonElement("activated_device_hashes")]
    public List<string> ActivatedDeviceHashes { get; set; } = [];
    [BsonElement("user_id")]
    public string? UserId { get; set; }
}

public sealed class UserDocument
{
    [BsonId]
    public string Id { get; set; } = Guid.NewGuid().ToString("N");
    [BsonElement("display_name")]
    public string DisplayName { get; set; } = "";
    [BsonElement("email")]
    public string Email { get; set; } = "";
    [BsonElement("is_enabled")]
    public bool IsEnabled { get; set; } = true;
    [BsonElement("created_at")]
    public DateTime CreatedAt { get; set; }
    [BsonElement("updated_at")]
    public DateTime UpdatedAt { get; set; }
}

public sealed record AdminUserRequest(
    [property: System.Text.Json.Serialization.JsonPropertyName("display_name")] string DisplayName,
    [property: System.Text.Json.Serialization.JsonPropertyName("email")] string Email,
    [property: System.Text.Json.Serialization.JsonPropertyName("is_enabled")] bool IsEnabled = true);

public sealed record VerifyRequest(
    [property: System.Text.Json.Serialization.JsonPropertyName("device_hash")] string DeviceHash);
public sealed record ActivateRequest(
    [property: System.Text.Json.Serialization.JsonPropertyName("device_hash")] string DeviceHash,
    [property: System.Text.Json.Serialization.JsonPropertyName("license_key")] string LicenseKey);
public sealed record DeactivateRequest(
    [property: System.Text.Json.Serialization.JsonPropertyName("device_hash")] string DeviceHash,
    [property: System.Text.Json.Serialization.JsonPropertyName("license_key")] string LicenseKey);
public sealed record CreateLicenseRequest(
    [property: System.Text.Json.Serialization.JsonPropertyName("tier")] string Tier,
    [property: System.Text.Json.Serialization.JsonPropertyName("max_devices")] int MaxDevices,
    [property: System.Text.Json.Serialization.JsonPropertyName("expires_at")] DateTime? ExpiresAt,
    [property: System.Text.Json.Serialization.JsonPropertyName("user_id")] string? UserId = null);
public sealed record EntitlementClaims(
    [property: System.Text.Json.Serialization.JsonPropertyName("version")] int Version,
    [property: System.Text.Json.Serialization.JsonPropertyName("device_hash")] string DeviceHash,
    [property: System.Text.Json.Serialization.JsonPropertyName("status")] string Status,
    [property: System.Text.Json.Serialization.JsonPropertyName("issued_at")] string IssuedAt,
    [property: System.Text.Json.Serialization.JsonPropertyName("not_before")] string NotBefore,
    [property: System.Text.Json.Serialization.JsonPropertyName("expires_at")] string ExpiresAt,
    [property: System.Text.Json.Serialization.JsonPropertyName("grace_until")] string GraceUntil,
    [property: System.Text.Json.Serialization.JsonPropertyName("features")] string[] Features,
    [property: System.Text.Json.Serialization.JsonPropertyName("license_id")] string? LicenseId,
    [property: System.Text.Json.Serialization.JsonPropertyName("tier")] string? Tier);

public sealed record SignedEntitlement(string Payload, string Signature);
