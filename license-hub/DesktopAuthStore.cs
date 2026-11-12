using MongoDB.Driver;

namespace LicenseHub;

public sealed class DesktopAuthStore(IMongoDatabase database)
{
    public IMongoCollection<AuthTransactionDocument> Transactions { get; } = database.GetCollection<AuthTransactionDocument>("auth_transactions");
    public IMongoCollection<GoogleIdentityDocument> Identities { get; } = database.GetCollection<GoogleIdentityDocument>("auth_identities");
    public IMongoCollection<IdentityClaimDocument> Claims { get; } = database.GetCollection<IdentityClaimDocument>("auth_identity_claims");
    public IMongoCollection<AuthSessionDocument> Sessions { get; } = database.GetCollection<AuthSessionDocument>("auth_sessions");
    private IMongoCollection<UserDocument> Users { get; } = database.GetCollection<UserDocument>("users");

    public async Task Initialize(CancellationToken ct = default)
    {
        await Transactions.Indexes.CreateOneAsync(new CreateIndexModel<AuthTransactionDocument>(Builders<AuthTransactionDocument>.IndexKeys.Ascending(x => x.ExpiresAt),
            new CreateIndexOptions { ExpireAfter = TimeSpan.Zero }), cancellationToken: ct);
        await Sessions.Indexes.CreateManyAsync([
            new(Builders<AuthSessionDocument>.IndexKeys.Ascending(x => x.ExpiresAt), new CreateIndexOptions { ExpireAfter = TimeSpan.Zero }),
            new(Builders<AuthSessionDocument>.IndexKeys.Ascending(x => x.AccessHash), new CreateIndexOptions { Unique = true }),
            new(Builders<AuthSessionDocument>.IndexKeys.Ascending(x => x.RefreshHash), new CreateIndexOptions { Unique = true })
        ], ct);
        await Identities.Indexes.CreateOneAsync(new CreateIndexModel<GoogleIdentityDocument>(Builders<GoogleIdentityDocument>.IndexKeys.Ascending(x => x.Provider).Ascending(x => x.Subject),
            new CreateIndexOptions { Unique = true }), cancellationToken: ct);
        await Claims.Indexes.CreateOneAsync(new CreateIndexModel<IdentityClaimDocument>(Builders<IdentityClaimDocument>.IndexKeys.Ascending(x => x.ExpiresAt),
            new CreateIndexOptions { ExpireAfter = TimeSpan.Zero }), cancellationToken: ct);
    }

    public async Task<AuthTransactionDocument> Start(DesktopLoginStart request, CancellationToken ct)
    {
        if (!EntitlementSigner.IsDeviceHash(request.DeviceHash ?? "") || !DesktopAuthProtocol.ValidChallenge(request.CodeChallenge)
            || !DesktopAuthProtocol.ValidDesktopCallback(request.CallbackUri) || !DesktopAuthProtocol.ValidToken(request.State))
            throw new ArgumentException("Invalid device, S256 challenge, loopback callback or state.");
        var transaction = new AuthTransactionDocument { DeviceHash = request.DeviceHash!, Challenge = request.CodeChallenge,
            Callback = request.CallbackUri, State = request.State, ExpiresAt = DateTime.UtcNow.AddMinutes(5) };
        await Transactions.InsertOneAsync(transaction, cancellationToken: ct);
        return transaction;
    }

    public async Task<bool> BeginBrowser(string id, CancellationToken ct) => (await Transactions.UpdateOneAsync(
        x => x.Id == id && x.Status == "pending" && x.ExpiresAt > DateTime.UtcNow,
        Builders<AuthTransactionDocument>.Update.Set(x => x.Status, "authorizing"), cancellationToken: ct)).ModifiedCount == 1;

    public async Task<string?> Complete(string id, GoogleAccount account, CancellationToken ct)
    {
        // Claim the fully validated OIDC result once before resolving any identity.
        var transaction = await Transactions.FindOneAndUpdateAsync(
            x => x.Id == id && x.Status == "authorizing" && x.ExpiresAt > DateTime.UtcNow,
            Builders<AuthTransactionDocument>.Update.Set(x => x.Status, "resolving"), cancellationToken: ct);
        if (transaction is null) return null;
        var userId = await ResolveIdentity(account, ct);
        if (userId is null)
            return transaction.Callback + "?error=approval_required&state=" + Uri.EscapeDataString(transaction.State);
        if (!await Enabled(userId, ct))
            return transaction.Callback + "?error=account_disabled&state=" + Uri.EscapeDataString(transaction.State);
        var code = DesktopAuthProtocol.Token();
        await Transactions.UpdateOneAsync(x => x.Id == id, Builders<AuthTransactionDocument>.Update
            .Set(x => x.CodeHash, DesktopAuthProtocol.Hash(code)).Set(x => x.UserId, userId)
            .Set(x => x.Status, "handoff").Set(x => x.ExpiresAt, DateTime.UtcNow.AddSeconds(60)), cancellationToken: ct);
        return transaction.Callback + "?code=" + Uri.EscapeDataString(code) + "&state=" + Uri.EscapeDataString(transaction.State);
    }

    private async Task<string?> ResolveIdentity(GoogleAccount account, CancellationToken ct)
    {
        var identityId = DesktopAuthProtocol.Hash("google:" + account.Subject);
        var identity = await Identities.Find(x => x.Id == identityId).FirstOrDefaultAsync(ct);
        if (identity is not null)
        {
            await Identities.UpdateOneAsync(x => x.Id == identityId, Builders<GoogleIdentityDocument>.Update
                .Set(x => x.VerifiedEmail, account.Email).Set(x => x.LastLoginAt, DateTime.UtcNow), cancellationToken: ct);
            return identity.UserId;
        }
        // Stable, disjoint ID makes retries/concurrent first login safe without requiring a replica set.
        var userId = "google_" + identityId;
        var user = await Users.Find(x => x.Id == userId).FirstOrDefaultAsync(ct);
        if (user is null)
        {
            user = new UserDocument { Id = userId, Email = account.Email, DisplayName = account.DisplayName,
                CreatedAt = DateTime.UtcNow, UpdatedAt = DateTime.UtcNow };
            try { await Users.InsertOneAsync(user, cancellationToken: ct); }
            catch (MongoWriteException ex) when (ex.WriteError?.Code == 11000)
            {
                if (await Users.Find(x => x.Id == userId).FirstOrDefaultAsync(ct) is null)
                {
                    // Email matching alone never claims an existing operator-created profile.
                    await Claims.UpdateOneAsync(x => x.Id == identityId, Builders<IdentityClaimDocument>.Update
                        .SetOnInsert(x => x.Subject, account.Subject).Set(x => x.VerifiedEmail, account.Email)
                        .Set(x => x.RequestedAt, DateTime.UtcNow).Set(x => x.ExpiresAt, DateTime.UtcNow.AddDays(1)),
                        new UpdateOptions { IsUpsert = true }, ct);
                    return null;
                }
            }
        }
        await Identities.UpdateOneAsync(x => x.Id == identityId, Builders<GoogleIdentityDocument>.Update
            .SetOnInsert(x => x.Subject, account.Subject).SetOnInsert(x => x.UserId, userId)
            .SetOnInsert(x => x.Provider, "google").Set(x => x.VerifiedEmail, account.Email)
            .Set(x => x.LastLoginAt, DateTime.UtcNow), new UpdateOptions { IsUpsert = true }, ct);
        // A concurrent administrator approval may have established a different owner; never overwrite it.
        return (await Identities.Find(x => x.Id == identityId).FirstAsync(ct)).UserId;
    }

    public async Task<object?> Exchange(DesktopLoginExchange body, CancellationToken ct)
    {
        if (!DesktopAuthProtocol.ValidToken(body.Code) || !DesktopAuthProtocol.ValidVerifier(body.CodeVerifier)
            || !EntitlementSigner.IsDeviceHash(body.DeviceHash ?? "")) return null;
        var hash = DesktopAuthProtocol.Hash(body.Code);
        var challenge = DesktopAuthProtocol.Challenge(body.CodeVerifier);
        var transaction = await Transactions.FindOneAndUpdateAsync(
            x => x.CodeHash == hash && x.Challenge == challenge && x.DeviceHash == body.DeviceHash
                && x.Status == "handoff" && x.ExpiresAt > DateTime.UtcNow,
            Builders<AuthTransactionDocument>.Update.Set(x => x.Status, "consumed").Unset(x => x.CodeHash), cancellationToken: ct);
        if (transaction?.UserId is null || !await Enabled(transaction.UserId, ct)) return null;
        var access = DesktopAuthProtocol.Token(); var refresh = DesktopAuthProtocol.Token();
        var session = new AuthSessionDocument { UserId = transaction.UserId, DeviceHash = transaction.DeviceHash,
            AccessHash = DesktopAuthProtocol.Hash(access), RefreshHash = DesktopAuthProtocol.Hash(refresh),
            AccessExpiresAt = DateTime.UtcNow.AddMinutes(15), ExpiresAt = DateTime.UtcNow.AddDays(30) };
        await Sessions.InsertOneAsync(session, cancellationToken: ct);
        return Tokens(session, access, refresh);
    }

    public async Task<object?> Refresh(DesktopRefresh body, CancellationToken ct)
    {
        if (!DesktopAuthProtocol.ValidToken(body.RefreshToken) || !EntitlementSigner.IsDeviceHash(body.DeviceHash ?? "")) return null;
        var hash = DesktopAuthProtocol.Hash(body.RefreshToken);
        var session = await Sessions.Find(x => x.RefreshHash == hash && x.DeviceHash == body.DeviceHash
            && !x.Revoked && x.ExpiresAt > DateTime.UtcNow).FirstOrDefaultAsync(ct);
        if (session is null)
        {
            // Reuse of a rotated credential revokes its session. History is bounded per session.
            await Sessions.UpdateManyAsync(x => x.PreviousRefreshHashes.Contains(hash) && x.DeviceHash == body.DeviceHash,
                Builders<AuthSessionDocument>.Update.Set(x => x.Revoked, true), cancellationToken: ct);
            return null;
        }
        if (!await Enabled(session.UserId, ct)) return null;
        var access = DesktopAuthProtocol.Token(); var refresh = DesktopAuthProtocol.Token();
        var updated = await Sessions.FindOneAndUpdateAsync(x => x.Id == session.Id && x.RefreshHash == hash && !x.Revoked,
            Builders<AuthSessionDocument>.Update.Set(x => x.AccessHash, DesktopAuthProtocol.Hash(access))
                .Set(x => x.RefreshHash, DesktopAuthProtocol.Hash(refresh)).Set(x => x.AccessExpiresAt, DateTime.UtcNow.AddMinutes(15))
                .PushEach(x => x.PreviousRefreshHashes, [hash], slice: -100),
            new FindOneAndUpdateOptions<AuthSessionDocument> { ReturnDocument = ReturnDocument.After }, ct);
        return updated is null ? null : Tokens(updated, access, refresh);
    }

    public async Task<AuthSessionDocument?> Authorize(string token, CancellationToken ct)
    {
        if (!DesktopAuthProtocol.ValidToken(token)) return null;
        var hash = DesktopAuthProtocol.Hash(token);
        var session = await Sessions.Find(x => x.AccessHash == hash && !x.Revoked && x.ExpiresAt > DateTime.UtcNow
            && x.AccessExpiresAt > DateTime.UtcNow).FirstOrDefaultAsync(ct);
        return session is not null && await Enabled(session.UserId, ct) ? session : null;
    }

    public async Task<bool> ApproveClaim(string id, string userId, CancellationToken ct)
    {
        if (!await Enabled(userId, ct)) return false;
        var claim = await Claims.Find(x => x.Id == id && x.ExpiresAt > DateTime.UtcNow).FirstOrDefaultAsync(ct);
        if (claim is null) return false;
        // Insert only. An established identity can never be reassigned with this endpoint.
        try { await Identities.InsertOneAsync(new GoogleIdentityDocument { Id = id, Subject = claim.Subject,
            UserId = userId, VerifiedEmail = claim.VerifiedEmail, LastLoginAt = claim.RequestedAt }, cancellationToken: ct); }
        catch (MongoWriteException ex) when (ex.WriteError?.Code == 11000) { return false; }
        await Claims.DeleteOneAsync(x => x.Id == id, ct);
        return true;
    }

    private async Task<bool> Enabled(string userId, CancellationToken ct) =>
        await Users.Find(x => x.Id == userId && x.IsEnabled).AnyAsync(ct);

    private static object Tokens(AuthSessionDocument session, string access, string refresh) => new {
        access_token = access, refresh_token = refresh, token_type = "Bearer", expires_in = 900,
        refresh_expires_at = session.ExpiresAt, user_id = session.UserId, device_hash = session.DeviceHash
    };
}
