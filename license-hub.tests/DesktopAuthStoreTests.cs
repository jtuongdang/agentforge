using System.Text.Json;
using LicenseHub;
using MongoDB.Driver;
using Xunit;

namespace LicenseHub.Tests;

public sealed class LocalMongoFactAttribute : FactAttribute
{
    public LocalMongoFactAttribute()
    {
        if (string.IsNullOrEmpty(Environment.GetEnvironmentVariable("LICENSE_HUB_TEST_MONGO_URI")))
            Skip = "Set LICENSE_HUB_TEST_MONGO_URI to an isolated local test MongoDB.";
    }
}

public sealed class DesktopAuthStoreTests
{
    [LocalMongoFact]
    public async Task HandoffPkceReplaySessionRotationDisableAndLegacyClaimAreEnforced()
    {
        var client = new MongoClient(Environment.GetEnvironmentVariable("LICENSE_HUB_TEST_MONGO_URI"));
        var name = "AuthHubIT_" + Guid.NewGuid().ToString("N")[..24];
        var database = client.GetDatabase(name);
        try
        {
            var users = database.GetCollection<UserDocument>("users");
            await users.Indexes.CreateOneAsync(new CreateIndexModel<UserDocument>(Builders<UserDocument>.IndexKeys.Ascending(x => x.Email), new CreateIndexOptions { Unique = true }));
            var store = new DesktopAuthStore(database);
            await store.Initialize();
            const string device = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
            var verifier = DesktopAuthProtocol.Token();
            var account = new GoogleAccount("synthetic-google-subject", "fixture@example.invalid", "Fixture User");
            var ct = CancellationToken.None;

            async Task<(string Code, AuthTransactionDocument Transaction)> Login(GoogleAccount who)
            {
                var transaction = await store.Start(new(device, DesktopAuthProtocol.Challenge(verifier),
                    "http://127.0.0.1:49152/oauth/callback", DesktopAuthProtocol.Token()), ct);
                Assert.True(await store.BeginBrowser(transaction.Id, ct));
                Assert.False(await store.BeginBrowser(transaction.Id, ct));
                var redirect = await store.Complete(transaction.Id, who, ct);
                Assert.NotNull(redirect);
                Assert.Contains("state=" + transaction.State, redirect);
                Assert.DoesNotContain("access_token", redirect);
                Assert.Null(await store.Complete(transaction.Id, who, ct));
                return (redirect.Split("code=")[1].Split('&')[0], transaction);
            }

            var (code, transaction) = await Login(account);
            Assert.Null(await store.Exchange(new(code, DesktopAuthProtocol.Token(), device), ct));
            Assert.Null(await store.Exchange(new(code, verifier, new string('b', 64)), ct));
            var results = await Task.WhenAll(store.Exchange(new(code, verifier, device), ct), store.Exchange(new(code, verifier, device), ct));
            Assert.Single(results, x => x is not null);
            var tokens = JsonSerializer.SerializeToElement(results.Single(x => x is not null));
            var access = tokens.GetProperty("access_token").GetString()!;
            var refresh = tokens.GetProperty("refresh_token").GetString()!;
            var userId = tokens.GetProperty("user_id").GetString()!;
            Assert.NotNull(await store.Authorize(access, ct));
            Assert.Null(await store.Exchange(new(code, verifier, device), ct));
            var persisted = JsonSerializer.Serialize(await store.Sessions.Find(_ => true).FirstAsync());
            Assert.DoesNotContain(access, persisted); Assert.DoesNotContain(refresh, persisted);
            var persistedTransaction = JsonSerializer.Serialize(await store.Transactions.Find(x => x.Id == transaction.Id).FirstAsync());
            Assert.DoesNotContain(code, persistedTransaction); Assert.DoesNotContain(verifier, persistedTransaction);

            var rotated = JsonSerializer.SerializeToElement(await store.Refresh(new(refresh, device), ct));
            var newAccess = rotated.GetProperty("access_token").GetString()!;
            Assert.NotNull(await store.Authorize(newAccess, ct));
            Assert.Null(await store.Authorize(access, ct));
            Assert.Null(await store.Refresh(new(refresh, device), ct));
            Assert.Null(await store.Authorize(newAccess, ct)); // replay revokes rotated session

            var disabledLogin = await Login(account);
            await users.UpdateOneAsync(x => x.Id == userId, Builders<UserDocument>.Update.Set(x => x.IsEnabled, false));
            Assert.Null(await store.Exchange(new(disabledLogin.Code, verifier, device), ct));
            await users.UpdateOneAsync(x => x.Id == userId, Builders<UserDocument>.Update.Set(x => x.IsEnabled, true));
            var expiredLogin = await Login(account);
            await store.Transactions.UpdateOneAsync(x => x.Id == expiredLogin.Transaction.Id,
                Builders<AuthTransactionDocument>.Update.Set(x => x.ExpiresAt, DateTime.UtcNow.AddSeconds(-1)));
            Assert.Null(await store.Exchange(new(expiredLogin.Code, verifier, device), ct));

            var legacy = new UserDocument { Email = "legacy@example.invalid", DisplayName = "Existing operator profile" };
            await users.InsertOneAsync(legacy);
            var legacyAccount = new GoogleAccount("new-google-subject", legacy.Email, "Verified Google user");
            var pending = await store.Start(new(device, DesktopAuthProtocol.Challenge(verifier),
                "http://127.0.0.1:49152/oauth/callback", DesktopAuthProtocol.Token()), ct);
            Assert.True(await store.BeginBrowser(pending.Id, ct));
            Assert.Contains("error=approval_required", await store.Complete(pending.Id, legacyAccount, ct));
            Assert.Equal(1, await store.Identities.CountDocumentsAsync(_ => true)); // no email auto-merge
            var claim = await store.Claims.Find(_ => true).FirstAsync();
            Assert.False(await store.ApproveClaim(claim.Id, "nonexistent-user", ct));
            Assert.True(await store.ApproveClaim(claim.Id, legacy.Id, ct));
            Assert.False(await store.ApproveClaim(claim.Id, userId, ct)); // cannot reassign
            var claimedLogin = await Login(legacyAccount);
            var claimedTokens = JsonSerializer.SerializeToElement(await store.Exchange(new(claimedLogin.Code, verifier, device), ct));
            Assert.Equal(legacy.Id, claimedTokens.GetProperty("user_id").GetString());
            Assert.Equal(0, await database.GetCollection<DeviceDocument>("devices").CountDocumentsAsync(_ => true)); // no trial reset
        }
        finally { await client.DropDatabaseAsync(name); } // Only this uniquely named synthetic database.
    }
}
