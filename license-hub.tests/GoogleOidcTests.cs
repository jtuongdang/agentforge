using System.IdentityModel.Tokens.Jwt;
using System.Net;
using System.Net.Http.Json;
using System.Security.Claims;
using System.Security.Cryptography;
using System.Text.Json;
using LicenseHub;
using Microsoft.AspNetCore.Authentication.OpenIdConnect;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.IdentityModel.Protocols;
using Microsoft.IdentityModel.Protocols.OpenIdConnect;
using Microsoft.IdentityModel.Tokens;
using MongoDB.Driver;
using Xunit;

namespace LicenseHub.Tests;

public sealed class GoogleOidcTests
{
    [LocalMongoFact]
    public async Task RealMiddlewareRejectsInvalidTokensStateAndNonceAndCompletesPkceHandoff()
    {
        var directory = Path.Combine(Path.GetTempPath(), "agentforge-oidc-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(directory);
        var databaseName = "OidcHubIT_" + Guid.NewGuid().ToString("N")[..24];
        using var rsa = RSA.Create(2048);
        using var wrongRsa = RSA.Create(2048);
        var privatePath = Path.Combine(directory, "signer.pem");
        await File.WriteAllTextAsync(privatePath, rsa.ExportPkcs8PrivateKeyPem());
        var values = new Dictionary<string, string> {
            ["MONGODB_URI"] = Environment.GetEnvironmentVariable("LICENSE_HUB_TEST_MONGO_URI")!,
            ["LICENSE_DATABASE"] = databaseName, ["LICENSE_SIGNING_PRIVATE_KEY_PATH"] = privatePath,
            ["LICENSE_KEY_PEPPER"] = Convert.ToBase64String(RandomNumberGenerator.GetBytes(32)),
            ["LICENSE_ADMIN_TOKEN"] = new string('a', 40), ["GOOGLE_AUTH_ENABLED"] = "true",
            ["HUB_PUBLIC_ORIGIN"] = "https://hub.example.invalid", ["GOOGLE_CLIENT_ID"] = "fixture-client-id",
            ["GOOGLE_CLIENT_SECRET"] = "fixture-client-secret", ["HUB_AUTH_KEYS_PATH"] = Path.Combine(directory, "keys")
        };
        var previous = values.Keys.ToDictionary(key => key, Environment.GetEnvironmentVariable);
        foreach (var item in values) Environment.SetEnvironmentVariable(item.Key, item.Value);
        var mongo = new MongoClient(values["MONGODB_URI"]);
        try
        {
            var mode = "valid"; var nonce = "";
            var backchannel = new FakeBackchannel(async request => {
                Assert.Equal(HttpMethod.Post, request.Method);
                Assert.Equal("https://accounts.google.com/o/oauth2/token", request.RequestUri!.AbsoluteUri);
                var form = await request.Content!.ReadAsStringAsync();
                Assert.Contains("code_verifier=", form);
                Assert.Contains("grant_type=authorization_code", form);
                Assert.Contains("redirect_uri=https%3A%2F%2Fhub.example.invalid%2Fauth%2Fgoogle%2Fcallback", form);
                var claims = new[] { new Claim("sub", "fixture-google-subject"), new Claim("email", "oidc@example.invalid"),
                    new Claim("email_verified", mode == "unverified" ? "false" : "true", ClaimValueTypes.Boolean),
                    new Claim("nonce", mode == "nonce" ? "wrong-nonce" : nonce) };
                var instant = DateTime.UtcNow;
                var token = new JwtSecurityToken(mode == "issuer" ? "https://evil.example.invalid" : "https://accounts.google.com",
                    mode == "audience" ? "wrong-client" : "fixture-client-id", claims,
                    instant.AddMinutes(-10), mode == "expired" ? instant.AddMinutes(-5) : instant.AddMinutes(5),
                    new SigningCredentials(new RsaSecurityKey(mode == "signature" ? wrongRsa : rsa), SecurityAlgorithms.RsaSha256));
                token.Payload["iat"] = new DateTimeOffset(instant).ToUnixTimeSeconds();
                return new HttpResponseMessage(HttpStatusCode.OK) { Content = JsonContent.Create(new {
                    access_token = "synthetic-google-token-not-retained", token_type = "Bearer", expires_in = 300,
                    id_token = new JwtSecurityTokenHandler().WriteToken(token)
                }) };
            });
            using var factory = new BrokerFactory(rsa, backchannel);
            using var client = factory.CreateClient(new WebApplicationFactoryClientOptions {
                BaseAddress = new Uri("https://hub.example.invalid"), AllowAutoRedirect = false, HandleCookies = true
            });
            const string device = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
            var verifier = DesktopAuthProtocol.Token();
            var desktopState = DesktopAuthProtocol.Token();
            async Task<HttpResponseMessage> Login(string variant)
            {
                mode = variant;
                var start = await client.PostAsJsonAsync("/auth/desktop/start", new DesktopLoginStart(device,
                    DesktopAuthProtocol.Challenge(verifier), "http://127.0.0.1:49152/oauth/callback", desktopState));
                Assert.Equal(HttpStatusCode.OK, start.StatusCode);
                var json = await start.Content.ReadFromJsonAsync<JsonElement>();
                var challenge = await client.GetAsync(json.GetProperty("login_url").GetString());
                Assert.Equal(HttpStatusCode.Redirect, challenge.StatusCode);
                var parameters = Query(challenge.Headers.Location!.Query);
                nonce = parameters["nonce"];
                Assert.Equal("S256", parameters["code_challenge_method"]);
                Assert.Equal("https://hub.example.invalid/auth/google/callback", parameters["redirect_uri"]);
                return await client.GetAsync("/auth/google/callback?code=fixture-code&state=" + Uri.EscapeDataString(
                    variant == "state" ? DesktopAuthProtocol.Token() : parameters["state"]));
            }
            foreach (var variant in new[] { "nonce", "audience", "issuer", "signature", "expired", "unverified", "state" })
            {
                using var failed = await Login(variant);
                Assert.Equal(HttpStatusCode.BadRequest, failed.StatusCode);
                Assert.Equal("text/plain", failed.Content.Headers.ContentType!.MediaType);
                Assert.Equal("no-store", failed.Headers.CacheControl!.ToString());
                Assert.Null(failed.Headers.Location);
            }
            using var completed = await Login("valid");
            Assert.Equal(HttpStatusCode.Redirect, completed.StatusCode);
            Assert.Equal("127.0.0.1", completed.Headers.Location!.Host);
            var handoff = Query(completed.Headers.Location.Query);
            Assert.Equal(desktopState, handoff["state"]);
            Assert.False(handoff.ContainsKey("access_token"));
            var exchange = await client.PostAsJsonAsync("/auth/desktop/exchange", new DesktopLoginExchange(handoff["code"], verifier, device));
            Assert.Equal(HttpStatusCode.OK, exchange.StatusCode);
            var tokens = await exchange.Content.ReadFromJsonAsync<JsonElement>();
            Assert.False(tokens.TryGetProperty("google_access_token", out _));
            var replay = await client.PostAsJsonAsync("/auth/desktop/exchange", new DesktopLoginExchange(handoff["code"], verifier, device));
            Assert.Equal(HttpStatusCode.Unauthorized, replay.StatusCode);
            client.DefaultRequestHeaders.Authorization = new("Bearer", tokens.GetProperty("access_token").GetString());
            Assert.Equal(HttpStatusCode.OK, (await client.GetAsync("/auth/me")).StatusCode);
            var report = new DesktopConnectionReport("macos", "0.1.0-test", [
                new("codex-local", "codex", "valid"), new("github", "github", "valid", "fixture-account", DateTime.UtcNow)
            ]);
            var reportResponse = await client.PostAsJsonAsync("/auth/connections", report);
            Assert.Equal(HttpStatusCode.OK, reportResponse.StatusCode);
            Assert.Equal(HttpStatusCode.Unauthorized, (await client.GetAsync("/api/v1/admin/summary")).StatusCode);
            using var admin = factory.CreateClient(new WebApplicationFactoryClientOptions { BaseAddress = new Uri("https://hub.example.invalid") });
            admin.DefaultRequestHeaders.Authorization = new("Bearer", new string('a', 40));
            var userId = tokens.GetProperty("user_id").GetString();
            Assert.Equal(HttpStatusCode.Unauthorized, (await admin.PostAsJsonAsync("/auth/connections", report)).StatusCode);
            var details = await admin.GetAsync("/api/v1/admin/users/" + userId + "/accounts");
            Assert.Equal(HttpStatusCode.OK, details.StatusCode);
            var text = await details.Content.ReadAsStringAsync();
            Assert.DoesNotContain("AccessHash", text); Assert.DoesNotContain("RefreshHash", text);
            Assert.DoesNotContain("access_token", text); Assert.DoesNotContain("refresh_token", text);
            var account = JsonDocument.Parse(text).RootElement;
            Assert.Equal("oidc_verified", account.GetProperty("identities")[0].GetProperty("source").GetString());
            Assert.Equal("desktop_reported", account.GetProperty("devices")[0].GetProperty("connections")[0].GetProperty("source").GetString());
            Assert.Equal("fixture-account", account.GetProperty("devices")[0].GetProperty("connections")[1].GetProperty("account_label").GetString());
            Assert.Single(account.GetProperty("sessions").EnumerateArray());
            Assert.True(account.GetProperty("audit").GetArrayLength() >= 2);
            Assert.Equal(HttpStatusCode.BadRequest, (await admin.GetAsync("/api/v1/admin/users/" + userId + "/accounts?limit=101")).StatusCode);
            Assert.Equal(HttpStatusCode.NotFound, (await admin.GetAsync("/api/v1/admin/users/missing/accounts")).StatusCode);
            var badReport = await client.PostAsJsonAsync("/auth/connections", new {
                platform = "macos", app_version = "0.1", connections = new[] { new { connection_id = "github", provider = "github", status = "valid", access_token = "synthetic-must-reject" } }
            });
            Assert.Equal(HttpStatusCode.BadRequest, badReport.StatusCode);
            var summary = await admin.GetFromJsonAsync<JsonElement>("/api/v1/admin/summary");
            Assert.Equal(1, summary.GetProperty("google_identities").GetInt32());
            Assert.Equal(1, summary.GetProperty("account_devices").GetInt32());
            Assert.Equal(HttpStatusCode.OK, (await client.PostAsync("/auth/logout", null)).StatusCode);
            Assert.Equal(HttpStatusCode.Unauthorized, (await client.GetAsync("/auth/me")).StatusCode);
        }
        finally
        {
            foreach (var item in previous) Environment.SetEnvironmentVariable(item.Key, item.Value);
            await mongo.DropDatabaseAsync(databaseName);
            Directory.Delete(directory, recursive: true);
        }
    }

    private static Dictionary<string, string> Query(string value) => value.TrimStart('?').Split('&')
        .Select(part => part.Split('=', 2)).ToDictionary(part => Uri.UnescapeDataString(part[0]), part => Uri.UnescapeDataString(part[1]));

    private sealed class FakeBackchannel(Func<HttpRequestMessage, Task<HttpResponseMessage>> respond) : HttpMessageHandler
    {
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken) => respond(request);
    }

    private sealed class BrokerFactory(RSA rsa, HttpMessageHandler handler) : WebApplicationFactory<Program>
    {
        protected override void ConfigureWebHost(IWebHostBuilder builder)
        {
            builder.UseEnvironment("Production");
            builder.ConfigureServices(services => services.PostConfigure<OpenIdConnectOptions>("google", options => {
                var configuration = new OpenIdConnectConfiguration { Issuer = "https://accounts.google.com",
                    AuthorizationEndpoint = "https://accounts.google.com/o/oauth2/v2/auth", TokenEndpoint = "https://accounts.google.com/o/oauth2/token" };
                configuration.SigningKeys.Add(new RsaSecurityKey(rsa));
                options.ConfigurationManager = new StaticConfigurationManager<OpenIdConnectConfiguration>(configuration);
                options.Backchannel = new HttpClient(handler);
            }));
        }
    }
}
