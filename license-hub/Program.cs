using System.Globalization;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json.Serialization;
using LicenseHub;
using MongoDB.Driver;

if (args is ["--healthcheck"])
{
    try
    {
        using var handler = new HttpClientHandler { AllowAutoRedirect = false, UseProxy = false };
        using var client = new HttpClient(handler) { Timeout = TimeSpan.FromSeconds(4) };
        using var response = await client.GetAsync("http://127.0.0.1:8080/ready");
        Environment.ExitCode = response.IsSuccessStatusCode ? 0 : 1;
    }
    catch (Exception ex) when (ex is HttpRequestException or OperationCanceledException)
    { Environment.ExitCode = 1; }
    return;
}

var builder = WebApplication.CreateBuilder(new WebApplicationOptions
{
    Args = args, ContentRootPath = AppContext.BaseDirectory,
    WebRootPath = Path.Combine(AppContext.BaseDirectory, "wwwroot")
});
builder.WebHost.ConfigureKestrel(options => options.Limits.MaxRequestBodySize = 4096);
builder.Services.ConfigureHttpJsonOptions(options =>
    options.SerializerOptions.UnmappedMemberHandling = JsonUnmappedMemberHandling.Disallow);
var mongoUri = Required("MONGODB_URI");
var databaseName = Environment.GetEnvironmentVariable("LICENSE_DATABASE") ?? "LicenseHubDb";
var signingKeyPath = Required("LICENSE_SIGNING_PRIVATE_KEY_PATH");
var pepperValue = Required("LICENSE_KEY_PEPPER");
var adminToken = Required("LICENSE_ADMIN_TOKEN");
var pepper = Convert.FromBase64String(pepperValue);
if (pepper.Length < 32 || adminToken.Length < 32)
    throw new InvalidOperationException("License pepper and admin token must each have at least 32 bytes.");

var mongoClient = new MongoClient(mongoUri);
var database = mongoClient.GetDatabase(databaseName);
var googleAuth = GoogleAuth.Configure(builder, database);
var devices = database.GetCollection<DeviceDocument>("devices");
var licenses = database.GetCollection<LicenseDocument>("licenses");
var users = database.GetCollection<UserDocument>("users");
var keyHasher = new LicenseKeyHasher(pepper);
var signingPem = await File.ReadAllTextAsync(signingKeyPath);
var signer = new EntitlementSigner(signingPem);
CryptographicOperations.ZeroMemory(pepper);
var now = () => DateTime.UtcNow;

var app = builder.Build();
app.Use(async (context, next) =>
{
    if (context.Request.Path.StartsWithSegments("/admin") || context.Request.Path.StartsWithSegments("/api/v1/admin"))
    {
        context.Response.Headers.CacheControl = "no-store";
        context.Response.Headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'";
        context.Response.Headers["Referrer-Policy"] = "no-referrer";
        context.Response.Headers["X-Content-Type-Options"] = "nosniff";
    }
    await next(context);
});
app.UseDefaultFiles();
app.UseStaticFiles();
app.MapGet("/", () => Results.Redirect("/admin/"));
app.MapGet("/admin", () => Results.File(Path.Combine(AppContext.BaseDirectory, "wwwroot", "admin", "index.html"), "text/html; charset=utf-8"));

var deviceHashIndex = new CreateIndexModel<DeviceDocument>(
    Builders<DeviceDocument>.IndexKeys.Ascending(item => item.DeviceHash),
    new CreateIndexOptions { Unique = true, Name = "device_hash_unique" });
var licenseHashIndex = new CreateIndexModel<LicenseDocument>(
    Builders<LicenseDocument>.IndexKeys.Ascending(item => item.LicenseKeyHash),
    new CreateIndexOptions { Unique = true, Name = "license_key_hash_unique" });
await devices.Indexes.CreateOneAsync(deviceHashIndex);
await licenses.Indexes.CreateOneAsync(licenseHashIndex);
await users.Indexes.CreateOneAsync(new CreateIndexModel<UserDocument>(
    Builders<UserDocument>.IndexKeys.Ascending(item => item.Email),
    new CreateIndexOptions { Unique = true, Name = "user_email_unique" }));
await mongoClient.GetDatabase("admin").RunCommandAsync<MongoDB.Bson.BsonDocument>(new MongoDB.Bson.BsonDocument("ping", 1));

app.MapGet("/health", () => Results.Ok(new { ok = true }));
app.MapGet("/ready", async (CancellationToken cancellationToken) =>
{
    using var timeout = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
    timeout.CancelAfter(TimeSpan.FromSeconds(3));
    try
    {
        await database.RunCommandAsync<MongoDB.Bson.BsonDocument>(new MongoDB.Bson.BsonDocument("ping", 1), cancellationToken: timeout.Token);
        return Results.Ok(new { ok = true });
    }
    catch (Exception ex) when (ex is MongoException or OperationCanceledException)
    { return Results.StatusCode(503); }
});

app.MapPost("/api/v1/devices/verify", async (VerifyRequest request, CancellationToken cancellationToken) =>
{
    if (!EntitlementSigner.IsDeviceHash(request.DeviceHash))
        return Results.BadRequest(new { error = "Invalid device identifier." });

    var instant = now();
    var filter = Builders<DeviceDocument>.Filter.Eq(item => item.DeviceHash, request.DeviceHash);
    var update = Builders<DeviceDocument>.Update
        .SetOnInsert(item => item.Id, Guid.NewGuid().ToString("N"))
        .SetOnInsert(item => item.FirstSeenAt, instant)
        .SetOnInsert(item => item.TrialExpiresAt, instant.AddDays(14))
        .SetOnInsert(item => item.Status, "trial")
        .Set(item => item.LastSeenAt, instant);
    try
    {
        await devices.UpdateOneAsync(filter, update, new UpdateOptions { IsUpsert = true }, cancellationToken);
    }
    catch (MongoWriteException exception) when (exception.WriteError?.Code == 11000)
    {
        await devices.UpdateOneAsync(filter, Builders<DeviceDocument>.Update.Set(item => item.LastSeenAt, instant),
            cancellationToken: cancellationToken);
    }

    var device = await devices.Find(filter).FirstAsync(cancellationToken);
    if (device.Status == "trial" && instant >= device.TrialExpiresAt)
    {
        await devices.UpdateOneAsync(filter & Builders<DeviceDocument>.Filter.Eq(item => item.Status, "trial"),
            Builders<DeviceDocument>.Update.Set(item => item.Status, "expired"), cancellationToken: cancellationToken);
        device.Status = "expired";
    }
    if (device.Status == "active" && device.LicenseId is not null)
    {
        var linked = await licenses.Find(item => item.Id == device.LicenseId).FirstOrDefaultAsync(cancellationToken);
        if (linked is null || !linked.IsActive || (linked.ExpiresAt is not null && instant >= linked.ExpiresAt))
        {
            device.Status = "revoked";
            await devices.UpdateOneAsync(filter, Builders<DeviceDocument>.Update.Set(item => item.Status, device.Status),
                cancellationToken: cancellationToken);
        }
        else if (!await UserEnabled(linked.UserId, cancellationToken))
            return Results.Ok(Entitle(request.DeviceHash, "banned", instant, instant.AddMinutes(1), linked.Id, linked.Tier));
    }
    var (expiry, licenseId, tier) = device.Status switch
    {
        "trial" => (device.TrialExpiresAt, (string?)null, (string?)"trial"),
        "active" when device.LicenseId is not null => await ActiveEntitlementDetails(device.LicenseId, instant, cancellationToken),
        _ => (instant.AddMinutes(1), (string?)null, (string?)null)
    };
    return Results.Ok(Entitle(request.DeviceHash, device.Status, instant, expiry, licenseId, tier));
});

app.MapPost("/api/v1/licenses/activate", async (ActivateRequest request, CancellationToken cancellationToken) =>
{
    if (!EntitlementSigner.IsDeviceHash(request.DeviceHash))
        return Results.BadRequest(new { error = "Invalid device identifier." });
    string keyHash;
    try { keyHash = keyHasher.Hash(request.LicenseKey); }
    catch (ArgumentException) { return Results.BadRequest(new { error = "License key is invalid." }); }

    var instant = now();
    var license = await licenses.Find(item => item.LicenseKeyHash == keyHash).FirstOrDefaultAsync(cancellationToken);
    if (license is null || !license.IsActive || (license.ExpiresAt is not null && instant >= license.ExpiresAt))
        return Results.BadRequest(new { error = "License key is invalid or inactive." });
    if (!await UserEnabled(license.UserId, cancellationToken))
        return Results.Json(new { error = "The account linked to this license is disabled." }, statusCode: 403);
    var deviceFilter = Builders<DeviceDocument>.Filter.Eq(item => item.DeviceHash, request.DeviceHash);
    var device = await devices.Find(deviceFilter).FirstOrDefaultAsync(cancellationToken);
    if (device is null)
        return Results.Conflict(new { error = "Start the device trial before activating a license." });

    var licenseFilter = Builders<LicenseDocument>.Filter.Eq(item => item.Id, license.Id) &
        Builders<LicenseDocument>.Filter.Eq(item => item.LicenseKeyHash, keyHash) &
        Builders<LicenseDocument>.Filter.Eq(item => item.IsActive, true) &
        Builders<LicenseDocument>.Filter.Eq(item => item.MaxDevices, license.MaxDevices) &
        (Builders<LicenseDocument>.Filter.Eq(item => item.ExpiresAt, null) |
         Builders<LicenseDocument>.Filter.Gt(item => item.ExpiresAt, instant)) &
        (Builders<LicenseDocument>.Filter.AnyEq(item => item.ActivatedDeviceHashes, request.DeviceHash) |
         Builders<LicenseDocument>.Filter.SizeLt(item => item.ActivatedDeviceHashes, license.MaxDevices));
    var binding = await licenses.UpdateOneAsync(licenseFilter,
        Builders<LicenseDocument>.Update.AddToSet(item => item.ActivatedDeviceHashes, request.DeviceHash),
        cancellationToken: cancellationToken);
    if (binding.MatchedCount != 1)
        return Results.Conflict(new { error = "License key has reached its device limit." });

    await devices.UpdateOneAsync(deviceFilter, Builders<DeviceDocument>.Update
        .Set(item => item.Status, "active")
        .Set(item => item.LicenseId, license.Id)
        .Set(item => item.LastSeenAt, instant), cancellationToken: cancellationToken);
    var maxExpiry = instant.AddDays(30);
    var tokenExpiry = license.ExpiresAt is null || license.ExpiresAt > maxExpiry ? maxExpiry : license.ExpiresAt.Value;
    if (instant >= tokenExpiry)
        return Results.BadRequest(new { error = "License key has expired." });
    return Results.Ok(Entitle(request.DeviceHash, "active", instant, tokenExpiry, license.Id, license.Tier));
});

app.MapPost("/api/v1/licenses/deactivate", async (DeactivateRequest request, CancellationToken cancellationToken) =>
{
    if (!EntitlementSigner.IsDeviceHash(request.DeviceHash))
        return Results.BadRequest(new { error = "Invalid device identifier." });
    string keyHash;
    try { keyHash = keyHasher.Hash(request.LicenseKey); }
    catch (ArgumentException) { return Results.BadRequest(new { error = "License key is invalid." }); }
    var license = await licenses.Find(item => item.LicenseKeyHash == keyHash && item.ActivatedDeviceHashes.Contains(request.DeviceHash))
        .FirstOrDefaultAsync(cancellationToken);
    if (license is null)
        return Results.NotFound(new { error = "This device is not activated by that license." });
    await licenses.UpdateOneAsync(item => item.Id == license.Id,
        Builders<LicenseDocument>.Update.Pull(item => item.ActivatedDeviceHashes, request.DeviceHash),
        cancellationToken: cancellationToken);
    await devices.UpdateOneAsync(item => item.DeviceHash == request.DeviceHash && item.LicenseId == license.Id,
        Builders<DeviceDocument>.Update.Set(item => item.Status, "expired").Unset(item => item.LicenseId),
        cancellationToken: cancellationToken);
    return Results.Ok(new { deactivated = true });
});

app.MapPost("/api/v1/admin/licenses", async (CreateLicenseRequest request, HttpRequest httpRequest,
    CancellationToken cancellationToken) =>
{
    if (!AdminAuthorized(httpRequest, adminToken)) return Results.Unauthorized();
    if (request.Tier is not ("monthly" or "yearly" or "lifetime") || request.MaxDevices is < 1 or > 25 ||
        (request.Tier == "lifetime" && request.ExpiresAt is not null) ||
        (request.Tier != "lifetime" && (request.ExpiresAt is null || request.ExpiresAt <= now())))
        return Results.BadRequest(new { error = "Invalid license configuration." });
    if (request.UserId is not null && !await UserEnabled(request.UserId, cancellationToken))
        return Results.BadRequest(new { error = "Linked user is missing or disabled." });
    var rawKey = EntitlementSigner.CreateLicenseKey();
    var document = new LicenseDocument
    {
        LicenseKeyHash = keyHasher.Hash(rawKey), Tier = request.Tier, MaxDevices = request.MaxDevices,
        CreatedAt = now(), ExpiresAt = request.ExpiresAt?.ToUniversalTime(), IsActive = true, UserId = request.UserId
    };
    await licenses.InsertOneAsync(document, cancellationToken: cancellationToken);
    return Results.Created($"/api/v1/admin/licenses/{document.Id}",
        new { id = document.Id, license_key = rawKey, tier = document.Tier, max_devices = document.MaxDevices,
              expires_at = document.ExpiresAt });
});

app.MapPost("/api/v1/admin/licenses/{id}/revoke", async (string id, HttpRequest httpRequest,
    CancellationToken cancellationToken) =>
{
    if (!AdminAuthorized(httpRequest, adminToken)) return Results.Unauthorized();
    var result = await licenses.UpdateOneAsync(item => item.Id == id,
        Builders<LicenseDocument>.Update.Set(item => item.IsActive, false), cancellationToken: cancellationToken);
    if (result.MatchedCount == 0) return Results.NotFound();
    return Results.Ok(new { revoked = true });
});

AdminEndpoints.Map(app, database, request => AdminAuthorized(request, adminToken));
await AccountAdminEndpoints.Map(app, database, request => AdminAuthorized(request, adminToken));
await GoogleAuth.Map(app, googleAuth, request => AdminAuthorized(request, adminToken));
app.Run();

async Task<bool> UserEnabled(string? userId, CancellationToken cancellationToken)
{
    if (userId is null) return true;
    return await users.Find(item => item.Id == userId && item.IsEnabled).AnyAsync(cancellationToken);
}

static string Required(string name) => Environment.GetEnvironmentVariable(name) is { Length: > 0 } value
    ? value
    : throw new InvalidOperationException($"Required environment variable {name} is missing.");

static bool AdminAuthorized(HttpRequest request, string expectedToken)
{
    var supplied = request.Headers.Authorization.ToString();
    if (!supplied.StartsWith("Bearer ", StringComparison.Ordinal)) return false;
    var actualHash = SHA256.HashData(Encoding.UTF8.GetBytes(supplied[7..]));
    var expectedHash = SHA256.HashData(Encoding.UTF8.GetBytes(expectedToken));
    return CryptographicOperations.FixedTimeEquals(actualHash, expectedHash);
}

SignedEntitlement Entitle(string deviceHash, string status, DateTime issued, DateTime expires,
    string? licenseId, string? tier)
{
    var grace = expires.AddDays(7);
    var claims = new EntitlementClaims(1, deviceHash, status,
        issued.ToUniversalTime().ToString("O", CultureInfo.InvariantCulture),
        issued.ToUniversalTime().ToString("O", CultureInfo.InvariantCulture),
        expires.ToUniversalTime().ToString("O", CultureInfo.InvariantCulture),
        grace.ToUniversalTime().ToString("O", CultureInfo.InvariantCulture),
        status is "trial" or "active" ? ["ai_execution"] : [], licenseId, tier);
    return signer.Sign(claims);
}

async Task<(DateTime Expiry, string? LicenseId, string? Tier)> ActiveEntitlementDetails(
    string licenseId, DateTime instant, CancellationToken cancellationToken)
{
    var license = await licenses.Find(item => item.Id == licenseId).FirstOrDefaultAsync(cancellationToken);
    if (license is null) return (instant.AddMinutes(1), null, null);
    var rollingExpiry = instant.AddDays(30);
    var expiry = license.ExpiresAt is null || license.ExpiresAt > rollingExpiry ? rollingExpiry : license.ExpiresAt.Value;
    return (expiry, license.Id, license.Tier);
}

public partial class Program;
