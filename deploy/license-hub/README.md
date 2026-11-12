# Tự deploy AgentForge License Admin

Image dự kiến: `jtuongdev/agentforge-admin:0.1.0-admin.1` (Linux amd64 và arm64).
Chỉ dùng link/tag sau khi push và xác minh registry thành công được ghi trong
báo cáo bàn giao. Đây là container License Hub + admin web, không chứa Desktop
worker, credential MongoDB, admin token hoặc private signing key.

## Chức năng admin

Mở `/admin/`, nhập giá trị `LICENSE_ADMIN_TOKEN` để truy cập:

- Xem tổng số người dùng, license và thiết bị; danh sách có phân trang.
- Tạo/sửa hồ sơ tên/email; khóa/mở người dùng, giữ nguyên lịch sử.
- Tạo license gắn user ID hoặc license độc lập; xem raw key đúng một lần.
- Thu hồi license; xem thiết bị và trạng thái server ghi nhận gần nhất.

Hồ sơ người dùng này phục vụ quản trị license. Chưa có đăng nhập email/mật khẩu
cho người dùng Desktop. Admin token chỉ giữ trong RAM của trang, không ghi vào
browser storage, cookie hoặc URL. Device APIs vẫn không nhận email/tên.

Khóa user chặn kích hoạt và trả entitlement `banned` ở lần verify online tiếp
theo. Mở lại user cho phép verify license còn hợp lệ. Thu hồi license trả
`revoked` ở lần verify tiếp theo. Thiết bị offline vẫn theo entitlement đã ký,
bao gồm thời hạn/grace; đây không phải công tắc cắt quyền offline tức thì.

## 1. Chuẩn bị máy chủ

Cần Docker Engine + Docker Compose v2, kết nối đến MongoDB Atlas hoặc MongoDB
riêng, và reverse proxy HTTPS nếu phục vụ client bên ngoài.

Tạo thư mục `/opt/agentforge`, lưu `compose.yaml` và bản sao `.env.example`
thành `.env`. Điền URI database chuyên dụng cùng các secret của bạn. File
`.env` phải có quyền `0600` và không được commit hoặc gửi vào chat.

`LICENSE_SIGNING_PRIVATE_KEY_PATH` trong `.env` là đường dẫn trên **máy chủ**,
không phải đường dẫn trên Mac phát triển. Container mount file đó read-only
tại `/run/secrets/license_private_key` và chạy bằng UID/GID `1654`.

Nếu dùng bộ ký và pepper đã cấp license trước đó, chuyển chúng qua kênh riêng
và giữ nguyên. Đổi pepper làm các key cũ không còn xác thực được; đổi signing
key đòi hỏi cập nhật public-key pin trên Desktop. Không tự tạo lại key/pepper
mỗi lần container khởi động.

Nếu chưa có RSA key, tạo ngoài source repo bằng OpenSSL:

```sh
sudo install -d -m 0700 /opt/agentforge/secrets
sudo openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:3072 \
  -out /opt/agentforge/secrets/license-private.pem
sudo openssl pkey -in /opt/agentforge/secrets/license-private.pem -pubout \
  -out /opt/agentforge/secrets/license-public.pem
sudo chown 1654:1654 /opt/agentforge/secrets/license-private.pem
sudo chmod 0400 /opt/agentforge/secrets/license-private.pem
```

Chỉ public key được cấp cho Desktop. Với local Docker Compose, secret file là
bind mount nên quyền đọc của file trên host phải phù hợp UID 1654; không dựa
vào trường `uid/mode` của Compose để thay quyền cho file có sẵn.

Pepper phải là base64 của ít nhất 32 byte ngẫu nhiên; admin token tối thiểu 32
ký tự ngẫu nhiên. Tạo/lưu qua secret manager hoặc file cấu hình riêng, không
đưa vào Dockerfile, build args, image layer hay command line `docker run -e`.

## 2. Chạy container

Từ thư mục chứa `compose.yaml` và `.env`:

```sh
chmod 600 .env
docker compose --env-file .env config --quiet
docker compose --env-file .env pull
docker compose --env-file .env up -d
docker compose ps
curl --fail http://127.0.0.1:8080/health
curl --fail http://127.0.0.1:8080/ready
```

`/health` kiểm tra process đang chạy; `/ready` ping database với timeout.
Image có healthcheck gọi `/ready`. Compose mặc định bind cổng vào loopback,
giới hạn 1 CPU/512 MiB, read-only root filesystem, không thêm Linux capability,
và mount signing key read-only. Có thể điều chỉnh tài nguyên theo tải thực tế.

Để quản trị từ máy cá nhân mà chưa public admin, dùng SSH tunnel:

```sh
ssh -L 8080:127.0.0.1:8080 your-user@your-server
```

Sau đó mở `http://127.0.0.1:8080/admin/` tại máy cá nhân. Admin token nằm trong
file/secret của máy chủ; trang web không tự tiết lộ token.

## 3. HTTPS và cấu hình Desktop

Reverse proxy HTTPS vào `127.0.0.1:8080`; chỉ cho mạng quản trị/VPN truy cập
`/admin/*` và `/api/v1/admin/*`. Device APIs `/api/v1/devices/verify` và
`/api/v1/licenses/activate`, `/deactivate` phục vụ Desktop qua HTTPS. Tắt log
request body và Authorization header tại proxy. Chỉ allowlist IP máy chủ Hub
đến Atlas, không đưa MongoDB URI vào Desktop.

Desktop cần URL HTTPS của Hub và public PEM tương ứng. Bản cài alpha đang có
không tự đọc `.env` của server; deploy Docker không tự cấu hình public-key pin
hay tài khoản đăng nhập trên Desktop.

## 4. Sao lưu và cập nhật

Sao lưu database, private signing key, pepper và cấu hình admin qua kênh riêng.
Image là stateless; hồ sơ nằm trong MongoDB, secret ở host/secret manager.
Database mới sẽ có indexes `device_hash_unique`, `license_key_hash_unique` và
`user_email_unique`. License cũ không có `user_id` vẫn được giữ là license độc lập.

Để cập nhật, thay tag/digest trong `LICENSE_HUB_IMAGE`, rồi `docker compose pull`
và `docker compose up -d`. Kiểm tra `/ready`, admin login và một license staging
trước khi áp dụng cho khách hàng. Không xóa database/secret để khắc phục lỗi image.
