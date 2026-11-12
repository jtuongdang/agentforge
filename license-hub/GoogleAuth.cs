using Microsoft.AspNetCore.Authentication;
using Microsoft.AspNetCore.DataProtection;
using Microsoft.AspNetCore.RateLimiting;
using Microsoft.IdentityModel.Protocols.OpenIdConnect;
using MongoDB.Driver;
using System.Threading.RateLimiting;

namespace LicenseHub;

public sealed record GoogleAuthConfiguration(bool Enabled, string? PublicOrigin, string? KeysPath);

public static class GoogleAuth
{
    public static GoogleAuthConfiguration Configure(WebApplicationBuilder builder, IMongoDatabase database)
    {
        var enabledValue = Environment.GetEnvironmentVariable("GOOGLE_AUTH_ENABLED") ?? "false";
        if (enabledValue != "true" && enabledValue != "false")
            throw new InvalidOperationException("GOOGLE_AUTH_ENABLED must be true or false.");
        if (enabledValue != "true") return new(false, null, null);
        var development = builder.Environment.IsDevelopment();
        var origin = DesktopAuthProtocol.PublicOrigin(Required("HUB_PUBLIC_ORIGIN"), development);
        var clientId = Required("GOOGLE_CLIENT_ID");
        var clientSecret = Required("GOOGLE_CLIENT_SECRET");
        var keysPath = Required("HUB_AUTH_KEYS_PATH");
        if (!Path.IsPathFullyQualified(keysPath) || Path.GetFullPath(keysPath) == Path.GetPathRoot(keysPath))
            throw new InvalidOperationException("HUB_AUTH_KEYS_PATH must name a dedicated absolute directory.");
        Directory.CreateDirectory(keysPath);
        if (!OperatingSystem.IsWindows())
            File.SetUnixFileMode(keysPath, UnixFileMode.UserRead | UnixFileMode.UserWrite | UnixFileMode.UserExecute);
        builder.Services.AddDataProtection().SetApplicationName("AgentForge.GoogleBroker")
            .PersistKeysToFileSystem(new DirectoryInfo(keysPath));
        builder.Services.AddSingleton(new DesktopAuthStore(database));
        // Default request diagnostics include query strings, including OAuth authorization codes.
        builder.Logging.AddFilter("Microsoft.AspNetCore.Hosting.Diagnostics", LogLevel.Warning);
        builder.Logging.AddFilter("Microsoft.AspNetCore.Authentication", LogLevel.None);
        builder.Services.AddRateLimiter(options => {
            options.RejectionStatusCode = 429;
            options.AddPolicy("auth-start", context =>
                RateLimitPartition.GetFixedWindowLimiter(context.Connection.RemoteIpAddress?.ToString() ?? "unknown", _ =>
                    new FixedWindowRateLimiterOptions { PermitLimit = 10, Window = TimeSpan.FromMinutes(1), QueueLimit = 0 }));
        });
        builder.Services.AddAuthentication("broker-temporary")
            .AddCookie("broker-temporary", options => {
                options.Cookie.Name = "agentforge-unused-broker-session";
                options.Cookie.HttpOnly = true;
                options.ExpireTimeSpan = TimeSpan.FromMinutes(5);
            })
            .AddOpenIdConnect("google", options => {
                options.Authority = "https://accounts.google.com";
                options.ClientId = clientId;
                options.ClientSecret = clientSecret;
                options.CallbackPath = "/auth/google/callback";
                options.ResponseType = OpenIdConnectResponseType.Code;
                options.ResponseMode = OpenIdConnectResponseMode.Query;
                options.UsePkce = true;
                options.MapInboundClaims = false;
                options.SaveTokens = false;
                options.GetClaimsFromUserInfoEndpoint = false;
                options.Scope.Clear();
                options.Scope.Add("openid"); options.Scope.Add("email"); options.Scope.Add("profile");
                options.TokenValidationParameters.ValidateIssuer = true;
                options.TokenValidationParameters.ValidateAudience = true;
                options.TokenValidationParameters.ValidateLifetime = true;
                options.TokenValidationParameters.RequireSignedTokens = true;
                options.TokenValidationParameters.ClockSkew = TimeSpan.FromSeconds(60);
                options.ProtocolValidator.RequireNonce = true;
                options.RemoteAuthenticationTimeout = TimeSpan.FromMinutes(5);
                options.NonceCookie.SameSite = SameSiteMode.Lax;
                options.CorrelationCookie.SameSite = SameSiteMode.Lax;
                options.NonceCookie.SecurePolicy = development ? CookieSecurePolicy.SameAsRequest : CookieSecurePolicy.Always;
                options.CorrelationCookie.SecurePolicy = development ? CookieSecurePolicy.SameAsRequest : CookieSecurePolicy.Always;
                options.Events.OnRedirectToIdentityProvider = context => {
                    context.ProtocolMessage.RedirectUri = origin + "/auth/google/callback";
                    return Task.CompletedTask;
                };
                options.Events.OnAuthorizationCodeReceived = context => {
                    if (context.TokenEndpointRequest is not null)
                        context.TokenEndpointRequest.RedirectUri = origin + "/auth/google/callback";
                    return Task.CompletedTask;
                };
                // TicketReceived runs after all middleware protocol validations, including nonce.
                options.Events.OnTicketReceived = async context => {
                    context.HandleResponse(); // Never persist a browser login cookie or Google tokens.
                    try
                    {
                        if (context.Principal is null || context.Properties is null || !context.Properties.Items.TryGetValue("transaction_id", out var id)
                            || !DesktopAuthProtocol.ValidToken(id)) throw new InvalidOperationException();
                        var store = context.HttpContext.RequestServices.GetRequiredService<DesktopAuthStore>();
                        var account = DesktopAuthProtocol.VerifiedGoogleAccount(context.Principal);
                        var redirect = await store.Complete(id!, account, context.HttpContext.RequestAborted);
                        if (redirect is null) throw new InvalidOperationException();
                        context.Response.Redirect(redirect);
                    }
                    catch (Exception ex) when (ex is InvalidOperationException or MongoException)
                    {
                        context.Response.StatusCode = 400;
                        context.Response.ContentType = "text/plain; charset=utf-8";
                        await context.Response.WriteAsync("Sign-in could not be completed. Return to Desktop and start again.");
                    }
                };
                options.Events.OnRemoteFailure = async context => {
                    context.HandleResponse();
                    context.Response.StatusCode = 400;
                    context.Response.ContentType = "text/plain; charset=utf-8";
                    await context.Response.WriteAsync("Google sign-in was rejected or expired. Return to Desktop and start again.");
                };
            });
        return new(true, origin, keysPath);
    }

    public static async Task Map(WebApplication app, GoogleAuthConfiguration configuration, Func<HttpRequest, bool> admin)
    {
        app.MapGet("/auth/config", () => Results.Ok(new { google_enabled = configuration.Enabled }));
        if (!configuration.Enabled) return;
        var store = app.Services.GetRequiredService<DesktopAuthStore>();
        await store.Initialize();
        app.Use(async (context, next) => {
            if (context.Request.Path.StartsWithSegments("/auth"))
            {
                context.Response.Headers.CacheControl = "no-store";
                context.Response.Headers["Referrer-Policy"] = "no-referrer";
                context.Response.Headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'";
                context.Response.Headers["X-Content-Type-Options"] = "nosniff";
            }
            await next(context);
        });
        app.UseRateLimiter();
        app.UseAuthentication();
        app.MapPost("/auth/desktop/start", async (DesktopLoginStart body, CancellationToken ct) => {
            try
            {
                var transaction = await store.Start(body, ct);
                return Results.Ok(new { login_url = configuration.PublicOrigin + "/auth/desktop/login?transaction_id=" + transaction.Id,
                    transaction_id = transaction.Id, expires_in = 300 });
            }
            catch (ArgumentException) { return Results.BadRequest(new { error = "Invalid login transaction." }); }
        }).RequireRateLimiting("auth-start");
        app.MapGet("/auth/desktop/login", async (string transaction_id, CancellationToken ct) => {
            if (!DesktopAuthProtocol.ValidToken(transaction_id) || !await store.BeginBrowser(transaction_id, ct))
                return Results.BadRequest(new { error = "Login transaction expired or was already used." });
            var properties = new AuthenticationProperties();
            properties.Items["transaction_id"] = transaction_id;
            return Results.Challenge(properties, ["google"]);
        });
        app.MapPost("/auth/desktop/exchange", async (DesktopLoginExchange body, CancellationToken ct) => {
            var result = await store.Exchange(body, ct);
            return result is null ? Results.Unauthorized() : Results.Ok(result);
        }).RequireRateLimiting("auth-start");
        app.MapPost("/auth/refresh", async (DesktopRefresh body, CancellationToken ct) => {
            var result = await store.Refresh(body, ct);
            return result is null ? Results.Unauthorized() : Results.Ok(result);
        }).RequireRateLimiting("auth-start");
        app.MapGet("/auth/me", async (HttpRequest request, CancellationToken ct) => {
            var session = await store.Authorize(Bearer(request), ct);
            return session is null ? Results.Unauthorized() : Results.Ok(new { user_id = session.UserId, device_hash = session.DeviceHash });
        });
        app.MapPost("/auth/logout", async (HttpRequest request, CancellationToken ct) => {
            var session = await store.Authorize(Bearer(request), ct);
            if (session is null) return Results.Unauthorized();
            await store.Sessions.UpdateOneAsync(x => x.Id == session.Id, Builders<AuthSessionDocument>.Update.Set(x => x.Revoked, true), cancellationToken: ct);
            return Results.Ok(new { revoked = true });
        });
        app.MapGet("/api/v1/admin/identity-claims", async (HttpRequest request, CancellationToken ct) => {
            if (!admin(request)) return Results.Unauthorized();
            var claims = await store.Claims.Find(x => x.ExpiresAt > DateTime.UtcNow).SortByDescending(x => x.RequestedAt).Limit(100).ToListAsync(ct);
            return Results.Ok(new { items = claims.Select(x => new { id = x.Id, verified_email = x.VerifiedEmail, requested_at = x.RequestedAt, expires_at = x.ExpiresAt }) });
        });
        app.MapPost("/api/v1/admin/identity-claims/{id}/approve", async (string id, ApproveIdentityClaim body, HttpRequest request, CancellationToken ct) => {
            if (!admin(request)) return Results.Unauthorized();
            return await store.ApproveClaim(id, body.UserId, ct) ? Results.Ok(new { approved = true, sign_in_again = true }) : Results.Conflict();
        });
        app.MapPost("/api/v1/admin/users/{id}/revoke-sessions", async (string id, HttpRequest request, CancellationToken ct) => {
            if (!admin(request)) return Results.Unauthorized();
            await store.Sessions.UpdateManyAsync(x => x.UserId == id && !x.Revoked,
                Builders<AuthSessionDocument>.Update.Set(x => x.Revoked, true), cancellationToken: ct);
            return Results.Ok(new { revoked = true });
        });
    }

    private static string Bearer(HttpRequest request) => request.Headers.Authorization.ToString() is var value
        && value.StartsWith("Bearer ", StringComparison.Ordinal) ? value[7..] : "";
    private static string Required(string name) => Environment.GetEnvironmentVariable(name) is { Length: > 0 } value
        ? value : throw new InvalidOperationException($"Required authentication setting is missing: {name}");
}
