using System.Text.Json.Serialization;
using System.Text.RegularExpressions;
using MongoDB.Bson.Serialization.Attributes;
using MongoDB.Driver;

namespace LicenseHub;

public sealed class AccountDeviceDocument
{
    [BsonId] public string Id { get; set; } = "";
    public string UserId { get; set; } = "";
    public string DeviceHash { get; set; } = "";
    public string Platform { get; set; } = "unknown";
    public string AppVersion { get; set; } = "unknown";
    public DateTime FirstSeenAt { get; set; }
    public DateTime LastSeenAt { get; set; }
    public DateTime? ConnectionsReportedAt { get; set; }
    public List<ConnectionReport> Connections { get; set; } = [];
}

public sealed class AccountAuditDocument
{
    [BsonId] public string Id { get; set; } = Guid.NewGuid().ToString("N");
    public string UserId { get; set; } = "";
    public string? DeviceHash { get; set; }
    public string Kind { get; set; } = "";
    public string Actor { get; set; } = "user";
    public DateTime CreatedAt { get; set; } = DateTime.UtcNow;
    public DateTime KeepUntil { get; set; } = DateTime.UtcNow.AddDays(90);
}

public sealed record ConnectionReport(
    [property: JsonPropertyName("connection_id")] string ConnectionId,
    [property: JsonPropertyName("provider")] string Provider,
    [property: JsonPropertyName("status")] string Status,
    [property: JsonPropertyName("account_label")] string? AccountLabel = null,
    [property: JsonPropertyName("checked_at")] DateTime? CheckedAt = null);
public sealed record DesktopConnectionReport(
    [property: JsonPropertyName("platform")] string Platform,
    [property: JsonPropertyName("app_version")] string AppVersion,
    [property: JsonPropertyName("connections")] List<ConnectionReport> Connections);

public sealed partial class AccountDirectory(IMongoDatabase database)
{
    public IMongoCollection<AccountDeviceDocument> Devices { get; } = database.GetCollection<AccountDeviceDocument>("account_devices");
    public IMongoCollection<AccountAuditDocument> Audit { get; } = database.GetCollection<AccountAuditDocument>("account_audit");

    public async Task Initialize(CancellationToken ct = default)
    {
        await Devices.Indexes.CreateOneAsync(new CreateIndexModel<AccountDeviceDocument>(
            Builders<AccountDeviceDocument>.IndexKeys.Ascending(x => x.UserId).Ascending(x => x.DeviceHash),
            new CreateIndexOptions { Unique = true }), cancellationToken: ct);
        await Audit.Indexes.CreateManyAsync([
            new(Builders<AccountAuditDocument>.IndexKeys.Ascending(x => x.KeepUntil), new CreateIndexOptions { ExpireAfter = TimeSpan.Zero }),
            new(Builders<AccountAuditDocument>.IndexKeys.Ascending(x => x.UserId).Descending(x => x.CreatedAt))
        ], ct);
    }

    public Task Record(string userId, string? deviceHash, string kind, string actor = "user", CancellationToken ct = default) =>
        Audit.InsertOneAsync(new AccountAuditDocument { UserId = userId, DeviceHash = deviceHash, Kind = kind, Actor = actor }, cancellationToken: ct);

    public Task RegisterDevice(string userId, string deviceHash, CancellationToken ct) =>
        Devices.UpdateOneAsync(x => x.Id == DeviceId(userId, deviceHash), Builders<AccountDeviceDocument>.Update
            .SetOnInsert(x => x.UserId, userId).SetOnInsert(x => x.DeviceHash, deviceHash)
            .SetOnInsert(x => x.FirstSeenAt, DateTime.UtcNow).Set(x => x.LastSeenAt, DateTime.UtcNow),
            new UpdateOptions { IsUpsert = true }, ct);

    public async Task Report(AuthSessionDocument session, DesktopConnectionReport body, CancellationToken ct)
    {
        if (!ValidReport(body)) throw new ArgumentException("Invalid bounded connection metadata.");
        await Devices.UpdateOneAsync(x => x.Id == DeviceId(session.UserId, session.DeviceHash), Builders<AccountDeviceDocument>.Update
            .SetOnInsert(x => x.UserId, session.UserId).SetOnInsert(x => x.DeviceHash, session.DeviceHash)
            .SetOnInsert(x => x.FirstSeenAt, DateTime.UtcNow).Set(x => x.LastSeenAt, DateTime.UtcNow)
            .Set(x => x.Platform, body.Platform).Set(x => x.AppVersion, body.AppVersion)
            .Set(x => x.Connections, body.Connections).Set(x => x.ConnectionsReportedAt, DateTime.UtcNow),
            new UpdateOptions { IsUpsert = true }, ct);
        await Record(session.UserId, session.DeviceHash, "connections_reported", ct: ct);
    }

    public static bool ValidReport(DesktopConnectionReport body) => body is not null
        && body.Platform is "macos" or "windows" or "linux" or "unknown"
        && body.AppVersion is not null && VersionPattern().IsMatch(body.AppVersion)
        && body.Connections is { Count: <= 20 }
        && body.Connections.Select(x => x?.ConnectionId).Distinct(StringComparer.Ordinal).Count() == body.Connections.Count
        && body.Connections.All(x => x is not null && x.ConnectionId is not null && IdentifierPattern().IsMatch(x.ConnectionId)
            && x.Provider is "codex" or "copilot" or "antigravity" or "github" or "openai" or "anthropic" or "gemini" or "ollama" or "custom" or "handoff"
            && x.Status is "unchecked" or "valid" or "invalid" or "error" or "unsupported" or "not_applicable" or "unknown" or "ready" or "needs_key"
            && (x.AccountLabel is null || (x.AccountLabel.Length <= 254 && LabelPattern().IsMatch(x.AccountLabel)
                && !CredentialPattern().IsMatch(x.AccountLabel)))
            && (x.CheckedAt is null || x.CheckedAt <= DateTime.UtcNow.AddMinutes(5)));

    private static string DeviceId(string userId, string deviceHash) => DesktopAuthProtocol.Hash(userId + ":" + deviceHash);
    [GeneratedRegex(@"^[A-Za-z0-9._-]{1,80}\z", RegexOptions.CultureInvariant)]
    private static partial Regex IdentifierPattern();
    [GeneratedRegex(@"^[A-Za-z0-9._+-]{1,40}\z", RegexOptions.CultureInvariant)]
    private static partial Regex VersionPattern();
    [GeneratedRegex(@"^[\p{L}\p{N} ._@+-]{1,254}\z", RegexOptions.CultureInvariant)]
    private static partial Regex LabelPattern();
    [GeneratedRegex(@"^(?:GOCSPX-|gh[pors]_|github_pat_|sk-|eyJ)", RegexOptions.CultureInvariant | RegexOptions.IgnoreCase)]
    private static partial Regex CredentialPattern();
}
