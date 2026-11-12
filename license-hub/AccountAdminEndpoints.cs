using MongoDB.Driver;

namespace LicenseHub;

public static class AccountAdminEndpoints
{
    public static async Task Map(WebApplication app, IMongoDatabase database, Func<HttpRequest, bool> admin)
    {
        var store = new DesktopAuthStore(database);
        await store.Directory.Initialize();
        var users = database.GetCollection<UserDocument>("users");
        var licenses = database.GetCollection<LicenseDocument>("licenses");
        app.MapGet("/api/v1/admin/users/{id}/accounts", async (string id, HttpRequest request, CancellationToken ct, int offset = 0, int limit = 50) => {
            if (!admin(request)) return Results.Unauthorized();
            if (!ValidPage(offset, limit)) return Results.BadRequest(new { error = "Invalid page limits." });
            var user = await users.Find(x => x.Id == id).FirstOrDefaultAsync(ct);
            if (user is null) return Results.NotFound();
            var identities = await store.Identities.Find(x => x.UserId == id).SortBy(x => x.Provider).ToListAsync(ct);
            var devices = await store.Directory.Devices.Find(x => x.UserId == id).SortByDescending(x => x.LastSeenAt).ThenBy(x => x.Id)
                .Skip(offset).Limit(limit + 1).ToListAsync(ct);
            var sessions = await store.Sessions.Find(x => x.UserId == id).SortByDescending(x => x.CreatedAt).ThenBy(x => x.Id)
                .Skip(offset).Limit(limit + 1).ToListAsync(ct);
            var keys = await licenses.Find(x => x.UserId == id).SortByDescending(x => x.CreatedAt).ThenBy(x => x.Id)
                .Skip(offset).Limit(limit + 1).ToListAsync(ct);
            var audit = await store.Directory.Audit.Find(x => x.UserId == id).SortByDescending(x => x.CreatedAt).ThenBy(x => x.Id)
                .Skip(offset).Limit(limit + 1).ToListAsync(ct);
            var instant = DateTime.UtcNow;
            return Results.Ok(new {
                user = new { id = user.Id, display_name = user.DisplayName, email = user.Email, is_enabled = user.IsEnabled },
                identities = identities.Select(x => new { provider = x.Provider, subject = x.Subject, verified_email = x.VerifiedEmail,
                    display_name = x.DisplayName, last_verified_at = x.LastLoginAt, source = "oidc_verified" }),
                devices = devices.Take(limit).Select(x => new { device_hash = x.DeviceHash, platform = x.Platform, app_version = x.AppVersion,
                    first_seen_at = x.FirstSeenAt, last_seen_at = x.LastSeenAt, connections_reported_at = x.ConnectionsReportedAt,
                    connections = x.Connections.Select(c => new { connection_id = c.ConnectionId, provider = c.Provider,
                        status = c.Status, account_label = c.AccountLabel, checked_at = c.CheckedAt, source = "desktop_reported" }) }),
                sessions = sessions.Take(limit).Select(x => new { id = x.Id, device_hash = x.DeviceHash, created_at = x.CreatedAt,
                    last_used_at = x.LastUsedAt, expires_at = x.ExpiresAt, status = x.Revoked ? "revoked" : x.ExpiresAt <= instant ? "expired" : "active" }),
                licenses = keys.Take(limit).Select(x => new { id = x.Id, tier = x.Tier, max_devices = x.MaxDevices,
                    activated_devices = x.ActivatedDeviceHashes.Count, expires_at = x.ExpiresAt,
                    status = !x.IsActive ? "revoked" : x.ExpiresAt <= instant ? "expired" : "active" }),
                audit = audit.Take(limit).Select(x => new { kind = x.Kind, actor = x.Actor, device_hash = x.DeviceHash, created_at = x.CreatedAt }),
                offset, limit, audit_retention_days = 90,
                has_more = devices.Count > limit || sessions.Count > limit || keys.Count > limit || audit.Count > limit
            });
        });
        app.MapGet("/api/v1/admin/identity-claims", async (HttpRequest request, CancellationToken ct, int offset = 0, int limit = 50) => {
            if (!admin(request)) return Results.Unauthorized();
            if (!ValidPage(offset, limit)) return Results.BadRequest(new { error = "Invalid page limits." });
            var claims = await store.Claims.Find(x => x.ExpiresAt > DateTime.UtcNow).SortByDescending(x => x.RequestedAt).ThenBy(x => x.Id)
                .Skip(offset).Limit(limit).ToListAsync(ct);
            return Results.Ok(new { offset, limit, items = claims.Select(x => new { id = x.Id, verified_email = x.VerifiedEmail, requested_at = x.RequestedAt, expires_at = x.ExpiresAt }) });
        });
        app.MapPost("/api/v1/admin/identity-claims/{id}/approve", async (string id, ApproveIdentityClaim body, HttpRequest request, CancellationToken ct) => {
            if (!admin(request)) return Results.Unauthorized();
            return await store.ApproveClaim(id, body.UserId, ct) ? Results.Ok(new { approved = true, sign_in_again = true }) : Results.Conflict();
        });
        app.MapPost("/api/v1/admin/users/{id}/revoke-sessions", async (string id, HttpRequest request, CancellationToken ct) => {
            if (!admin(request)) return Results.Unauthorized();
            if (!await users.Find(x => x.Id == id).AnyAsync(ct)) return Results.NotFound();
            await store.Sessions.UpdateManyAsync(x => x.UserId == id && !x.Revoked,
                Builders<AuthSessionDocument>.Update.Set(x => x.Revoked, true), cancellationToken: ct);
            await store.Directory.Record(id, null, "sessions_revoked", "admin", ct);
            return Results.Ok(new { revoked = true });
        });
    }

    private static bool ValidPage(int offset, int limit) => offset is >= 0 and <= 100_000 && limit is >= 1 and <= 100;
}
