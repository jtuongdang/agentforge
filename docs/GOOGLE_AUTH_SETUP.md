# Cấu hình Google cho Hub và kiểm thử Desktop

Ngày: 12/11/2026. Broker source đang triển khai G2; chưa tích hợp Account/profile/
license gate G3 hoặc phát hành bộ cài mới. Image Docker `0.1.0-admin.1` chưa có
broker này. Không dùng trạng thái OIDC pass fixture để ghi nhận Google production
đã vận hành.

## Google Cloud

Project operator đang cấu hình: `aiagent-514511`. Consent app **AgentForge**,
External, **Testing**. Không publish production ở bước kiểm thử.

Client local: **AgentForge Hub - Local development**, loại **Web application**.
Callback đăng ký chính xác:

```text
http://127.0.0.1:8080/auth/google/callback
```

Client này chỉ dành cho Hub chạy trên máy phát triển. JavaScript origins để
trống vì browser không đổi Google code/token trực tiếp. Chỉ xin `openid email
profile`; không xin Gmail/Drive. Không bật lựa chọn delegated AI agent cho luồng
xác minh tài khoản người dùng thông thường này.

Trong Audience → Test users, thêm tài khoản thử nghiệm được operator cho phép.
Nếu secret có nguy cơ đã lộ, vô hiệu hóa nó và tạo secret thay thế trước khi dùng.
JSON tải từ Google chứa secret: lưu riêng, quyền 0600, không commit/GitHub/upload
image hay gửi nội dung vào chat.

## Cấu hình server source

Giữ các biến MongoDB, signing key, pepper, admin token hiện có. Thêm vào cấu hình
secret riêng của process server:

```dotenv
GOOGLE_AUTH_ENABLED=true
HUB_PUBLIC_ORIGIN=http://127.0.0.1:8080
GOOGLE_CLIENT_ID=replace-with-client-id.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=replace-with-current-private-secret
HUB_AUTH_KEYS_PATH=/absolute/private/path/agentforge-auth-keys
ASPNETCORE_ENVIRONMENT=Development
ASPNETCORE_URLS=http://127.0.0.1:8080
```

`.env` không được .NET tự nạp. Trình khởi chạy/secret manager cần truyền các biến
này vào process; không dùng `echo`, command-line arguments hay log để truyền
giá trị secret. `license-hub/.env.example` chỉ chứa placeholder. Thư mục Data
Protection keys phải riêng, writable và tồn tại bền qua restart (POSIX 0700).
Không dùng `/`, thư mục repo, thư mục dùng chung hoặc tmpfs cho keys. Bảo vệ và
sao lưu chúng như credential vì chúng bảo vệ state/nonce của giao dịch OAuth.

Broker mặc định tắt. Khi bật mà thiếu cấu hình, server từ chối khởi động; không
giả lập Google login. HTTP chỉ được chấp nhận với literal `127.0.0.1` trong
**Development**. Production bắt buộc HTTPS và client riêng; callback Google là
`HUB_PUBLIC_ORIGIN` + `/auth/google/callback`.

Không thêm domain HTTPS dự kiến khi chưa thực sự quản lý domain/service đó.
Docker Hub chỉ là registry, không phải endpoint OAuth. Khi deploy reverse proxy,
giữ Host/transport đúng origin, không log query callback/Authorization/body.
Broker không tin forwarded headers tùy ý để xây callback.

## Giao thức đang có trong source

1. Desktop mở listener loopback cổng tạm tại `/oauth/callback`, tạo state ngẫu
   nhiên và PKCE S256. Listener/UI tích hợp chính thức thuộc G3.
2. `POST /auth/desktop/start` nhận `device_hash`, `code_challenge`, `callback_uri`,
   `state`; trả `login_url` có TTL 5 phút. Callback chỉ chấp nhận literal
   `http://127.0.0.1:<port>/oauth/callback`, không nhận query/fragment/host khác.
3. Browser mở URL Hub. Middleware OIDC thực hiện code flow + PKCE với Google;
   kiểm chữ ký, issuer, audience, expiry, state/nonce. Chỉ email_verified=true.
4. Hub bàn giao code một lần, TTL 60 giây; không đặt session token vào URL.
   `POST /auth/desktop/exchange` đổi code bằng verifier và device hash.
5. Access token opaque 15 phút, refresh token tối đa 30 ngày, rotation, revoke
   khi phát hiện reuse trong lịch sử 100 lần rotation gần nhất. Server chỉ giữ
   hash token. Mã đã dùng/hết hạn, sai verifier/device bị từ chối.
6. `/auth/me`, `/auth/refresh`, `/auth/logout` dùng session AgentForge riêng.
   Admin token không phải user token; browser Google token không được lưu lại.

Identity theo Google sub, không theo email. Email trùng hồ sơ operator tạo trước
chỉ sinh yêu cầu approval có hạn. Admin xem `/api/v1/admin/identity-claims`, rồi
approve ID → user_id tại `/api/v1/admin/identity-claims/{id}/approve`; user phải
đăng nhập lại sau đó. Endpoint không chuyển identity đã gắn sang user khác.
`POST /api/v1/admin/users/{id}/revoke-sessions` thu hồi các phiên của user.
Admin web để xử lý các yêu cầu này đang được bổ sung; API đã có trong source.

## Cổng nghiệm thu còn mở

- Google login thật bằng client local/secret hiện hành đã pass ngày 12/11/2026:
  OIDC → PKCE handoff → session → refresh rotation → logout/revocation. Thử qua
  harness Development; chưa nghiệm thu Account UI/Desktop installer.
- Hub HTTPS thật và client production riêng.
- Desktop loopback/Account UI/keychain/profile isolation/legacy backup và binding
  license với user đã xác minh; trial theo thiết bị vẫn 14 ngày.
- Nghiệm thu CLI/provider thật, scoped permissions Copilot và bộ cài mới.

Hiện device/license endpoints cũ vẫn giữ contract tương thích; G2 identity broker
một mình chưa áp yêu cầu Google cho mọi đường kích hoạt/dispatch. Không phát hành
thương mại trước khi hoàn thành G3 và kiểm thử chống bypass.

Nguồn: [Google OIDC](https://developers.google.com/identity/openid-connect/openid-connect),
[ASP.NET OIDC](https://learn.microsoft.com/en-us/aspnet/core/security/authentication/configure-oidc-web-authentication?view=aspnetcore-8.0).
