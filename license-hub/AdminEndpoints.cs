using System.Net.Mail;
using MongoDB.Driver;

namespace LicenseHub;

public static class AdminEndpoints
{
    public static void Map(WebApplication app, IMongoDatabase database, Func<HttpRequest, bool> authorized)
    {
        var users = database.GetCollection<UserDocument>("users");
        var licenses = database.GetCollection<LicenseDocument>("licenses");
        var devices = database.GetCollection<DeviceDocument>("devices");
        var identities = database.GetCollection<GoogleIdentityDocument>("auth_identities");
        var sessions = database.GetCollection<AuthSessionDocument>("auth_sessions");
        var directory = new AccountDirectory(database);
        app.MapGet("/api/v1/admin/summary", async (HttpRequest request, CancellationToken ct) =>
        {
            if (!authorized(request)) return Results.Unauthorized();
            return Results.Ok(new {
                users = await users.CountDocumentsAsync(FilterDefinition<UserDocument>.Empty, cancellationToken: ct),
                enabled_users = await users.CountDocumentsAsync(item => item.IsEnabled, cancellationToken: ct),
                licenses = await licenses.CountDocumentsAsync(FilterDefinition<LicenseDocument>.Empty, cancellationToken: ct),
                devices = await devices.CountDocumentsAsync(FilterDefinition<DeviceDocument>.Empty, cancellationToken: ct),
                google_identities = await identities.CountDocumentsAsync(FilterDefinition<GoogleIdentityDocument>.Empty, cancellationToken: ct),
                account_devices = await directory.Devices.CountDocumentsAsync(FilterDefinition<AccountDeviceDocument>.Empty, cancellationToken: ct),
                active_sessions = await sessions.CountDocumentsAsync(x => !x.Revoked && x.ExpiresAt > DateTime.UtcNow, cancellationToken: ct)
            });
        });
        app.MapGet("/api/v1/admin/users", async (HttpRequest request, CancellationToken ct, int offset = 0, int limit = 50) =>
        {
            if (!authorized(request)) return Results.Unauthorized();
            if (!ValidPage(offset, limit)) return Results.BadRequest(new { error = "Invalid page limits." });
            var rows = await users.Find(FilterDefinition<UserDocument>.Empty)
                .SortByDescending(item => item.CreatedAt).ThenByDescending(item => item.Id).Skip(offset).Limit(limit).ToListAsync(ct);
            var ids = rows.Select(x => x.Id).ToList();
            var linked = await identities.Find(Builders<GoogleIdentityDocument>.Filter.In(x => x.UserId, ids)).ToListAsync(ct);
            var lookup = linked.ToLookup(x => x.UserId);
            return Results.Ok(new { offset, limit, items = rows.Select(x => UserView(x, lookup[x.Id].OrderByDescending(i => i.LastLoginAt).FirstOrDefault())) });
        });
        app.MapPost("/api/v1/admin/users", async (AdminUserRequest body, HttpRequest request, CancellationToken ct) =>
        {
            if (!authorized(request)) return Results.Unauthorized();
            if (!UserFields(body, out var name, out var email)) return Results.BadRequest(new { error = "Provide a name (1–120 characters) and valid email (at most 254 characters)." });
            var user = new UserDocument { DisplayName = name, Email = email, IsEnabled = body.IsEnabled,
                CreatedAt = DateTime.UtcNow, UpdatedAt = DateTime.UtcNow };
            try { await users.InsertOneAsync(user, cancellationToken: ct); }
            catch (MongoWriteException ex) when (ex.WriteError?.Code == 11000)
            { return Results.Conflict(new { error = "A user with this email already exists." }); }
            await directory.Record(user.Id, null, "profile_created", "admin", ct);
            return Results.Created($"/api/v1/admin/users/{user.Id}", UserView(user));
        });
        app.MapPatch("/api/v1/admin/users/{id}", async (string id, AdminUserRequest body, HttpRequest request, CancellationToken ct) =>
        {
            if (!authorized(request)) return Results.Unauthorized();
            if (!UserFields(body, out var name, out var email)) return Results.BadRequest(new { error = "Invalid name or email." });
            UpdateResult result;
            try
            {
                result = await users.UpdateOneAsync(item => item.Id == id, Builders<UserDocument>.Update
                    .Set(item => item.DisplayName, name).Set(item => item.Email, email)
                    .Set(item => item.IsEnabled, body.IsEnabled).Set(item => item.UpdatedAt, DateTime.UtcNow), cancellationToken: ct);
            }
            catch (MongoWriteException ex) when (ex.WriteError?.Code == 11000)
            { return Results.Conflict(new { error = "A user with this email already exists." }); }
            if (result.MatchedCount == 0) return Results.NotFound();
            if (!body.IsEnabled)
                await sessions.UpdateManyAsync(x => x.UserId == id && !x.Revoked,
                    Builders<AuthSessionDocument>.Update.Set(x => x.Revoked, true), cancellationToken: ct);
            await directory.Record(id, null, body.IsEnabled ? "profile_updated" : "account_disabled", "admin", ct);
            return Results.Ok(new { updated = true });
        });
        app.MapGet("/api/v1/admin/licenses", async (HttpRequest request, CancellationToken ct, int offset = 0, int limit = 50) =>
        {
            if (!authorized(request)) return Results.Unauthorized();
            if (!ValidPage(offset, limit)) return Results.BadRequest(new { error = "Invalid page limits." });
            var rows = await licenses.Find(FilterDefinition<LicenseDocument>.Empty)
                .SortByDescending(item => item.CreatedAt).ThenByDescending(item => item.Id).Skip(offset).Limit(limit).ToListAsync(ct);
            var instant = DateTime.UtcNow;
            return Results.Ok(new { offset, limit, items = rows.Select(item => new {
                id = item.Id, tier = item.Tier, user_id = item.UserId, max_devices = item.MaxDevices,
                activated_devices = item.ActivatedDeviceHashes.Count, created_at = item.CreatedAt, expires_at = item.ExpiresAt,
                status = !item.IsActive ? "revoked" : item.ExpiresAt <= instant ? "expired" : "active"
            }) });
        });
        app.MapGet("/api/v1/admin/devices", async (HttpRequest request, CancellationToken ct, int offset = 0, int limit = 50) =>
        {
            if (!authorized(request)) return Results.Unauthorized();
            if (!ValidPage(offset, limit)) return Results.BadRequest(new { error = "Invalid page limits." });
            var rows = await devices.Find(FilterDefinition<DeviceDocument>.Empty)
                .SortByDescending(item => item.LastSeenAt).ThenByDescending(item => item.Id).Skip(offset).Limit(limit).ToListAsync(ct);
            return Results.Ok(new { offset, limit, items = rows.Select(item => new {
                id = item.Id, device_hash = item.DeviceHash, last_known_status = item.Status,
                license_id = item.LicenseId, first_seen_at = item.FirstSeenAt,
                last_seen_at = item.LastSeenAt, trial_expires_at = item.TrialExpiresAt
            }) });
        });
    }

    private static object UserView(UserDocument item, GoogleIdentityDocument? identity = null) => new {
        id = item.Id, display_name = item.DisplayName, email = item.Email,
        is_enabled = item.IsEnabled, created_at = item.CreatedAt, updated_at = item.UpdatedAt,
        google_linked = identity is not null, google_email = identity?.VerifiedEmail, last_google_verified_at = identity?.LastLoginAt
    };

    private static bool ValidPage(int offset, int limit) => offset is >= 0 and <= 100_000 && limit is >= 1 and <= 100;

    private static bool UserFields(AdminUserRequest body, out string name, out string email)
    {
        name = (body.DisplayName ?? "").Trim();
        email = (body.Email ?? "").Trim().ToLowerInvariant();
        return name.Length is >= 1 and <= 120 && !name.Any(char.IsControl) && email.Length is >= 3 and <= 254
            && MailAddress.TryCreate(email, out var parsed) && parsed.DisplayName.Length == 0 && parsed.Address == email;
    }
}
