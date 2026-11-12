# Bản Đặc Tả Sản Phẩm: Desktop AI Orchestrator (Product Specification)

> **Tài liệu chiến lược**: Định hướng đóng gói và phát triển hệ thống điều phối đa AI Agent thành ứng dụng máy tính độc lập (Standalone Desktop Application).
> **Ngày cập nhật**: 06/11/2026

---

## 1. Tầm Nhìn Sản Phẩm & Định Vị (Product Vision)

- **Tên sản phẩm (Dự kiến)**: **DevAgent Orchestrator Desktop** (hoặc *ClinicOrchestrator Desktop*).
- **Định vị**: Phần mềm máy tính điều phối đa AI Agent cục bộ (Local-First Multi-Agent Platform) dành cho lập trình viên solo và nhóm phát triển phần mềm.
- **Mô hình**: Ứng dụng Desktop chạy native trên macOS (`.dmg`) và Windows (`.exe`), hoạt động theo triết lý **Local-First & Safety-First**.
- **Lợi thế cạnh tranh cốt lõi**:
  1. **Chi phí tác giả = 0 Đồng**: Toàn bộ quá trình biên dịch (Maven/Gradle), chạy test và chạy AI đều sử dụng CPU/RAM và tài khoản cá nhân của người dùng. Tác giả phần mềm không phải gánh chi phí server cloud đắt đỏ.
  2. **Bảo mật tuyệt đối (100% On-Premise)**: Mã nguồn của người dùng và các khóa bảo mật không bao giờ bị gửi lên server của bên thứ ba.
  3. **Cô lập an toàn (Git Worktree)**: Không bao giờ làm hỏng hay ghi đè thư mục làm việc chính của lập trình viên.
  4. **Kiểm thử tự động trước khi nộp code**: Bắt buộc phải vượt qua toàn bộ unit test và build đỗ thì mới cho phép tạo Pull Request.

---

## 2. Tính Năng Cốt Lõi: Đăng Nhập Một Chạm (1-Click Local Auth & BYOK)

Đây là tính năng trải nghiệm người dùng (UX) quan trọng nhất giúp biến công cụ thành một sản phẩm thương mại mượt mà.

### 2.1. Cơ chế nhận diện tự động (Auto-Detect Local Session)
Phần lớn các lập trình viên đều đã đăng nhập ít nhất một dịch vụ AI trên máy tính cá nhân của họ:
- **GitHub Copilot CLI**: Đã đăng nhập tài khoản GitHub có gói Copilot.
- **OpenAI Codex CLI**: Đã đăng nhập phiên ChatGPT Plus / Pro / Team.
- **Google Antigravity CLI**: Đã đăng nhập tài khoản Google Workspace / Personal.

**Dưới nền hệ thống xử lý**:
- Khi người dùng bấm **`[Kết nối]`** trên giao diện:
  - App chạy ngầm các lệnh kiểm tra: `copilot --prompt "Reply TOKEN_CHECK_OK"`, `agy --print /usage`, `codex login status`.
  - Hệ điều hành (macOS Keychain / Windows Credential Manager) tự động cấp quyền truy cập phiên đã lưu.
  - Giao diện ngay lập tức hiển thị trạng thái xanh:  
    `🟢 Đã kết nối GitHub: [username] - Gói: Active`  
    `🟢 Đã kết nối Google: Hạn mức tuần còn lại [87%]`
- **Giá trị**: Người dùng **không cần gõ lại mật khẩu**, không cần tìm API Key, tận dụng 100% gói thuê bao tháng họ đã mua sẵn.

### 2.2. Cơ chế dự phòng: Đăng nhập Device Flow & Nhập API Key riêng (BYOK)
- **Nếu máy tính chưa đăng nhập**:
  - App hỗ trợ luồng **OAuth Device Flow**: Hiện mã 8 chữ số và tự mở trình duyệt `github.com/login/device` để người dùng bấm Authorize.
- **Nếu người dùng muốn dùng API Key riêng**:
  - Hỗ trợ form nhập API Key cá nhân: OpenAI (`sk-proj-...`), Anthropic Claude (`sk-ant-...`), Google Gemini (`AIzaSy...`), DeepSeek.
  - Hỗ trợ mô hình chạy Offline 100% miễn phí: Kết nối Ollama Local (`http://localhost:11434`).
  - Toàn bộ Key được mã hóa lưu trữ trong Keychain của máy người dùng, **tuyệt đối không gửi về máy chủ tác giả**.

---

## 3. Hành Trình Người Dùng (End-to-End User Experience)

```text
[Tải file .dmg / .exe]
         │
         ▼
[Mở App lần đầu - Wizard 3 bước (60 giây)]
   ├── 1. Tự động kiểm tra môi trường: Git, JDK 17, Maven, AI CLI
   ├── 2. Bấm [📁 Chọn thư mục dự án] qua cửa sổ Finder/Explorer
   └── 3. Bấm [Kết nối AI] nhận diện tài khoản 1-chạm
         │
         ▼
[Giao diện chính (Dashboard)]
   ├── Giao việc thủ công (+ Tạo Task) HOẶC Bật Codex Tech Lead Auto-Pilot
   ├── Theo dõi log terminal chạy thời gian thực qua WebSocket
   └── Tự động chạy 'mvn clean verify' & Bounded Recovery (tự sửa lỗi nếu fail)
         │
         ▼
[Xem kết quả Git Diff trực quan] ──► Bấm [Duyệt & Tạo Draft PR] ──► Hoàn tất!
```

### Điểm nhấn: Điều khiển từ xa qua Điện thoại (Remote Dashboard)
- Remote pairing dùng VPN riêng với HTTPS ingress do operator quản lý; app chỉ khởi động gateway loopback khi được cấu hình rõ ràng.
- Desktop App hiển thị QR/mã mời một lần; owner phải duyệt tên thiết bị trước khi thiết bị đọc được task summary.
- Browser remote mặc định chỉ đọc. Full diff cần owner cấp riêng theo task và SHA validation. Duyệt review, tạo task, publish PR, truy cập log và API owner không được hỗ trợ từ xa.

---

## 4. Kiến Trúc Kỹ Thuật & Đóng Gói (Architecture & Packaging)

### 4.1. Ngăn xếp công nghệ đề xuất
| Thành phần | Công nghệ | Vai trò |
|---|---|---|
| **Vỏ Desktop (Shell)** | **Tauri (Rust)** *(Khuyên dùng)* hoặc Electron | Tạo cửa sổ phần mềm native, nhẹ (<30MB), tốn ít RAM (<50MB) |
| **Giao diện (Frontend)** | React 19 + TypeScript + Vite | Dashboard hiển thị danh sách task, diff viewer, log streaming |
| **Lõi xử lý (Backend)** | Python 3.12+ (FastAPI) | Bộ lập lịch Scheduler, Git Worktree Manager, CLI Adapters |
| **Cơ sở dữ liệu** | SQLite3 (`orchestrator.sqlite3`) | Lưu trữ trạng thái task, events, contracts, append-only logs |
| **Hệ thống tệp** | Git CLI & Git Worktree | Tạo môi trường nhánh cách ly cho từng tác vụ |

### 4.2. Bảo vệ mã nguồn & Chống dịch ngược (Anti-Theft)
- **Biên dịch mã máy nhị phân C**: Dùng **Nuitka** hoặc **Cython** để dịch toàn bộ mã Python (`.py`) sang file nhị phân C (`.so` trên Mac, `.pyd` / `.dll` trên Windows). Kẻ xấu không thể dịch ngược lại code Python gốc.
- **Obfuscation Frontend**: Làm rối mã JavaScript/React trước khi đóng gói vào Tauri.
- **Cơ chế Bản quyền (License Server & Remote Kill-Switch)**:
  - App tạo mã nhận diện phần cứng (Hardware ID).
  - Định kỳ gửi heartbeat kiểm tra bản quyền.
  - Tác giả có thể thu hồi (Revoke/Ban) bản quyền từ xa qua trang quản trị bất cứ lúc nào.

---

## 5. Lộ Trình Triển Khai (Implementation Roadmap)

### Giai đoạn 1: Tinh chỉnh lõi & Vá lỗi hệ thống (Hoàn thành)
- [x] Vá lỗi Antigravity Sandbox (ép model `gemini-3.8-flash-medium`, mở quyền lệnh Maven headless).
- [x] Đưa toàn bộ bộ test suite Orchestrator lên 27/27 passed 100%.
- [x] Chuẩn hóa cơ chế Bounded Recovery (tự sửa lỗi 2 lượt khi test fail).

### Giai đoạn 2: Khử phụ thuộc cấu hình cứng (De-hardcoding & Generalization)
- [ ] Bỏ đường dẫn mặc định `~/Documents/desktop_clinic/desktop`.
- [ ] Tích hợp hộp thoại chọn thư mục native (Native Folder Picker API).
- [ ] Hỗ trợ đa ngôn ngữ/framework ngoài Java (Node.js/React, Python, Go).

### Giai đoạn 3: Tự động hóa khép kín (Codex Auto-Pilot)
- [ ] Xây dựng daemon script cho Codex tự động đọc `GET /api/tasks/{id}/diff` và tự duyệt PR qua API.
- [ ] Vẽ đồ thị phụ thuộc trực quan (Visual DAG / React Flow) trên Dashboard.

### Giai đoạn 4: Đóng gói sản phẩm thương mại (Packaging)
- [ ] Tích hợp Tauri shell bọc Frontend + Backend.
- [ ] Biên dịch Backend bằng Nuitka.
- [ ] Tạo file cài đặt phát hành: `.dmg` (macOS Universal ARM/x86) và `.exe` / `.msi` (Windows).
- [ ] Tích hợp License Key Manager & Auto-updater.

---

## 6. Phân Tích Thay Đổi Mã Nguồn Cụ Thể (Code Refactoring & Gap Analysis)

Để chuyển đổi hệ thống hiện tại sang Desktop App, cơ cấu mã nguồn được phân bổ:
- **70% Giữ nguyên**: Lõi kiến trúc đã được thiết kế chuẩn mực từ đầu.
- **20% Cần sửa đổi (Refactoring)**: Linh hoạt hóa các điểm cấu hình cá nhân.
- **10% Bổ sung mới**: Vỏ Desktop (Tauri) và giao diện Onboarding.

```text
┌────────────────────────────────────────────────────────────────────────┐
│               PHÂN BỔ KHỐI LƯỢNG THAY ĐỔI MÃ NGUỒN                     │
├──────────────────────────┬─────────────────────────┬───────────────────┤
│ 70% GIỮ NGUYÊN           │ 20% CẦN SỬA ĐỔI         │ 10% BỔ SUNG MỚI   │
│ - Scheduler Engine       │ - De-hardcode main.py   │ - Tauri Shell     │
│ - Git Worktree Manager   │ - Native Folder Picker  │ - Onboarding UI   │
│ - Scope & Secret Checks  │ - Dynamic AI Registry   │ - Nuitka Packager │
│ - Bounded Recovery Loop  │ - Local Git Author      │                   │
│ - SQLite Event Store     │                         │                   │
└──────────────────────────┴─────────────────────────┴───────────────────┘
```

### 6.1. Chi tiết những gì BẮT BUỘC PHẢI THAY ĐỔI trong mã nguồn

#### 1. Khử cấu hình cứng trong `backend/app/main.py`
- **Hiện trạng (Dòng 36–47)**:
  Hàm `bootstrap()` tự động gán cứng đường dẫn máy chủ cá nhân:
  ```python
  path = Path(os.getenv("CLINIC_REPO_PATH", Path.home() / "Documents" / "desktop_clinic" / "desktop"))
  remote = "https://github.com/jtuongdang/clinic_desktop.git"
  ```
- **Giải pháp chuyển đổi**:
  - Gỡ bỏ hoàn toàn logic ép đường dẫn mặc định này.
  - Khi App khởi động: Nếu cơ sở dữ liệu chưa có repository nào, API trả về cờ `needs_onboarding: true` để giao diện tự mở màn hình chọn repo lần đầu.

#### 2. Thêm Native Folder Picker (Hộp thoại chọn thư mục chuột)
- **Hiện trạng**: Form `RepositoryCreate` yêu cầu người dùng copy-paste chuỗi đường dẫn text `/Users/jtuong/...`.
- **Giải pháp chuyển đổi**:
  - Tích hợp hàm gọi hộp thoại của hệ điều hành thông qua Tauri Dialog API:
    ```typescript
    import { open } from '@tauri-apps/plugin-dialog';
    const selected = await open({ directory: true, multiple: false });
    ```
  - Người dùng bấm nút `[📁 Chọn thư mục]` ➔ Hộp thoại Finder (Mac) hoặc Explorer (Windows) mở ra ➔ Trả về đường dẫn tuyệt đối an toàn.

#### 3. Linh hoạt hóa quản lý AI trong `backend/app/registry.py` (Hỗ trợ đa dạng BYOK)
- **Hiện trạng**: Hệ thống tìm cứng các binary CLI (`codex`, `copilot`, `agy`) trên PATH của máy tính.
- **Giải pháp chuyển đổi**:
  - Mở rộng bảng `agents` trong SQLite: Bổ sung lưu trữ an toàn các thông số tùy biến (API Key, Base URL, Custom Model).
  - Hỗ trợ thêm các Adapter trực tiếp:
    - **OpenAI / Claude / Gemini API**: Cho người dùng không có CLI mà muốn nhập API Key cá nhân.
    - **Ollama / Local LLM**: Cho người dùng muốn kết nối model chạy offline (`http://localhost:11434`).
  - Tích hợp nút bấm kiểm tra kết nối (Probe Ping) thời gian thực cho từng Agent trên UI.

#### 4. Nhận diện tác giả Git Commit trong `backend/app/git_ops.py`
- **Hiện trạng (Dòng 133)**: Lệnh commit đang fix cứng tác giả:
  `-c user.name=DevAgent Orchestrator -c user.email=orchestrator@localhost`.
- **Giải pháp chuyển đổi**:
  - Tự động đọc cấu hình Git mặc định trên máy người dùng qua:
    `git config user.name` và `git config user.email`.
  - Commit của AI sinh ra sẽ mang đúng danh tính tác giả của người dùng hoặc gắn định dạng `DevAgent [Tên Người Dùng]`.

---

### 6.2. Chi tiết những gì CẦN BỔ SUNG MỚI

#### 1. Khung Desktop Shell bằng Tauri (`src-tauri/`)
- Thêm thư mục `src-tauri/` chứa mã Rust đóng vai trò Shell điều khiển:
  - Tự động kích hoạt file thực thi Python Backend ngầm khi App khởi động.
  - Tự động dọn dẹp và tắt tiến trình Backend khi người dùng bấm tắt cửa sổ App.
  - Quản lý icon trên thanh trạng thái (macOS Menu Bar / Windows System Tray).

#### 2. Giao diện Wizard chào mừng lần đầu (`dashboard/src/components/Onboarding/`)
- Tạo component React hiển thị luồng 3 bước khi lần đầu mở App:
  1. *Quét môi trường*: Kiểm tra Git, Java JDK 17, Maven và các CLI AI.
  2. *Chọn thư mục dự án*: Bấm chọn folder qua hộp thoại native.
  3. *Xác thực AI*: Bấm nút kết nối 1-chạm hoặc nhập API Key.

#### 3. Kịch bản đóng gói nhị phân (Packaging Pipeline với Nuitka)
- Viết script tự động biên dịch toàn bộ thư mục `backend/` thành một file thực thi C nhị phân duy nhất (`orchestrator-backend`):
  ```bash
  nuitka --standalone --onefile --output-dir=dist backend/app/main.py
  ```
- **Lợi ích**: Người dùng tải app về máy tính không cần cài Python, không cần cài thư viện qua `pip`, chạy ngay lập tức và mã nguồn được bảo vệ chống dịch ngược 100%.

---

### 6.3. Những thành phần CỐT LÕI ĐÃ RẤT CHUẨN (Giữ nguyên 70%)

Toàn bộ logic nghiệp vụ cốt lõi sau đây được giữ nguyên vẹn vì đã được thiết kế chuẩn xác:
- **`scheduler.py`**: Vòng lặp lập lịch, kiểm soát tải song song (`max_parallel`), cơ chế chống xung đột phạm vi file (`_scope_conflict`), kiểm tra phụ thuộc và cổng hợp đồng.
- **`worktrees.py` & `git_ops.py`**: Quản lý Git Worktree độc lập, tách nhánh tự động không làm bẩn thư mục làm việc chính.
- **`contracts.py`**: Quản lý giao ước DTO/Interface giữa các task.
- **`validation.py`**: Bộ lọc phạm vi file (`file_globs`), quét rò rỉ mã bí mật (Secret Scanner), và tự động kích hoạt `mvn clean verify`.
- **`engine.py`**: Cơ chế phục hồi tự sửa lỗi Bounded Recovery (thử lại 2 lần với log compiler).
- **`db.py`**: Cơ sở dữ liệu SQLite cục bộ bền bỉ, an toàn, hỗ trợ stream log WebSocket trực tiếp lên giao diện.

---

### 6.4. Ma Trận Công Việc Thực Hiện (Implementation Checklist)

| Hạng mục công việc | Mức độ phức tạp | Ước tính thời gian | File tác động trực tiếp |
|---|---|---|---|
| **1. Khử hardcode bootstrap repo** | Thấp | 1 giờ | `backend/app/main.py` |
| **2. Tích hợp Native Folder Picker** | Thấp | 1.5 giờ | `dashboard/src/App.tsx`, Tauri Dialog API |
| **3. Cập nhật Git commit theo user thật** | Thấp | 0.5 giờ | `backend/app/git_ops.py` |
| **4. Mở rộng cấu hình Agent & API Key** | Trung bình | 3 giờ | `backend/app/registry.py`, `dashboard/src/App.tsx` |
| **5. Dựng giao diện Onboarding Wizard** | Trung bình | 3 giờ | `dashboard/src/components/OnboardingModal.tsx` |
| **6. Khởi tạo Tauri Shell Desktop** | Trung bình | 4 giờ | Thư mục `src-tauri/` |
| **7. Viết script đóng gói Nuitka Backend** | Trung bình | 3 giờ | `scripts/build_desktop.sh` |
| **Tổng cộng** | — | **~2 ngày làm việc** | Toàn bộ chuyển đổi hoàn tất |

---

## 7. Khả Năng Tương Thích & Điều Khiển Bằng API (Headless API & Codex Integration)

Một vấn đề kỹ thuật then chốt: **Khi chuyển sang Desktop App, liệu Codex hoặc các công cụ bên ngoài có còn gọi API để tự động điều phối hệ thống được không?**

👉 **Câu trả lời là: 100% HOÀN TOÀN ĐƯỢC VÀ CÒN TIỆN LỢI HƠN GẤP NHIỀU LẦN!**

### 7.1. Nguyên Lý Kỹ Thuật: Localhost API Server Vẫn Luôn Hoạt Động
Khi người dùng nhấp đúp chuột mở App Desktop:
1. Tauri Shell mở cửa sổ đồ họa Desktop hiển thị Dashboard React.
2. Dưới nền, App tự động kích hoạt tiến trình Backend FastAPI nhị phân và lắng nghe trên cổng nội bộ:
   👉 `http://127.0.0.1:8765`
3. Cổng này nằm trên chính máy tính của người dùng (Localhost loopback). Do đó:
   - Ứng dụng **ChatGPT / Codex Desktop**,
   - Các dòng lệnh terminal (`curl`),
   - Các tiện ích mở rộng như **Cursor / VS Code**,
   - Hoặc bất kỳ AI nào khác (Claude, Gemini),
   **đều gửi request gọi API vào `http://127.0.0.1:8765` y hệt như hiện tại**, hoàn toàn không bị chặn hay gián đoạn.

### 7.2. So Sánh Trải Nghiệm Điều Khiển Qua API: Hiện Tại vs Khi Lên Desktop App

| Tiêu chí | Hệ thống Hiện Tại (Chưa đóng gói) | Khi Đã Lên Desktop App 🏆 |
|---|---|---|
| **Khởi động API Server** | Phải mở Terminal, gõ `./scripts/dev.sh`, chờ môi trường Python và Vite bật. Dễ bị tắt nhầm Terminal làm sập server. | **Chỉ cần nhấp đúp mở App**. Backend API tự động kích hoạt ngầm, không cần mở bất kỳ cửa sổ Terminal nào. |
| **Codex gọi API giao task** | Codex gọi `POST /api/tasks` và `POST /api/contracts` qua `127.0.0.1:8765`. | **Codex gọi y chang** qua `127.0.0.1:8765`. Không cần sửa đổi bất kỳ câu lệnh nào của Codex. |
| **Giám sát trực quan** | Phải mở trình duyệt web gõ `localhost:5173`. | Giao diện hiển thị sẵn trong một cửa sổ ứng dụng riêng biệt, mượt mà trên màn hình. |
| **Đóng và dọn dẹp hệ thống** | Phải vào terminal bấm `Ctrl + C` hoặc dùng lệnh `kill` PID thủ công. | Bấm nút **X** tắt App ➔ Tiến trình Backend tự dọn dẹp và đóng sạch sẽ, giải phóng RAM. |

### 7.3. Tính Năng Bổ Trợ: "1-Click Copy Prompt Cho Codex / AI Tech Lead"
Trên giao diện Desktop App, thiết kế thêm một nút bấm tiện ích:
> **`[📋 Sao chép Prompt Cho AI Tech Lead]`**

Khi người dùng bấm nút này, App sẽ tự động sao chép vào Clipboard một đoạn chỉ thị mẫu:
```text
Hệ thống Orchestrator đã sẵn sàng tại http://127.0.0.1:8765.
Bạn hãy đóng vai Tech Lead:
1. Đọc tài liệu và tự gọi POST /api/contracts để chốt DTO/Interface.
2. Gọi POST /api/tasks để phân phối công việc cho Copilot và Antigravity.
3. Khi task xong, gọi GET /api/tasks/{id}/diff để review code và tự động duyệt mở Draft PR!
```
Người dùng chỉ việc mở cửa sổ chat của Codex lên, bấm `Ctrl + V` (hoặc `Cmd + V`) dán vào là Codex tự động nhận diện và làm việc suốt cả ngày.

### 7.4. Nâng Cấp Nâng Cao: Chế Độ Tech Lead Tích Hợp Sẵn (Built-in Auto-Pilot)
Trong tương lai, để người dùng không cần phải mở song song ứng dụng ChatGPT/Codex ở bên ngoài nếu họ không muốn, Desktop App có thể tích hợp sẵn một công tắc:
> **Chế độ Tech Lead Tự Động (Auto-Pilot Mode):** `[ BẬT 🟢 / TẮT ⚪ ]`

- Khi **BẬT**: Bản thân App Desktop sẽ tự kích hoạt vòng lặp Tech Lead (dùng OpenAI API Key hoặc Copilot có sẵn) để tự động phân rã bài toán, tự giao task cho các worker, tự đọc git diff và tự duyệt PR **ngay bên trong phần mềm**, mang lại trải nghiệm tự động hóa 100% trọn gói "All-in-One".

---

## 8. Chiến Lược Bảo Mật Bản Quyền & Chống Đánh Cắp Mã Nguồn (Anti-Piracy & IP Protection)

Khi phát hành phần mềm thương mại, hai rủi ro lớn nhất là:
1. **Chia sẻ lậu (Piracy)**: Một người mua bản quyền rồi gửi file cài đặt cho nhiều người khác dùng chùa.
2. **Đánh cắp mã nguồn (Reverse-Engineering)**: Người dùng giải nén file cài đặt để đọc trộm thuật toán và logic điều phối.

Hệ thống được thiết kế theo **Mô hình phòng vệ 4 tầng (4-Layer Defense)** tiêu chuẩn công nghiệp:

### 8.1. Tầng 1: Khóa Bản Quyền Theo Phần Cứng (Hardware Node-Locking / Machine Fingerprint)
- **Mục tiêu**: Ngăn chặn tuyệt đối hành vi mua 1 bản quyền chia sẻ cho nhiều máy tính khác.
- **Cơ chế hoạt động**:
  - Khi cài đặt và kích hoạt App lần đầu trên máy tính:
    - App tự động trích xuất các thông số phần cứng duy nhất: CPU ID, Serial Number Mainboard, UUID ổ cứng và MAC Address.
    - Băm (hash) thành một chuỗi mã duy nhất: **Hardware ID (HWID)**.
  - License Key của khách hàng được gắn chặt vào Hardware ID này trên Server quản trị bản quyền.
  - **Xử lý vi phạm**: Nếu người mua gửi License Key cho người khác, máy thứ hai sẽ có Hardware ID khác ➔ App từ chối khởi động và hiện cảnh báo:  
    *`"License Key này đã được kích hoạt trên một thiết bị khác. Vui lòng nâng cấp bản quyền!"`*
  - **Chính sách kích hoạt**: Cho phép linh hoạt (ví dụ: 1 key kích hoạt tối đa 1 máy chính + 1 máy phụ). Khi muốn đổi máy mới, người dùng phải bấm "Hủy kích hoạt" ở máy cũ.

### 8.2. Tầng 2: Máy Chủ Bản Quyền & Công Tắc Khóa Từ Xa (License Server & Remote Kill-Switch)
- **Mục tiêu**: Đảm bảo quyền kiểm soát tối thượng luôn nằm trong tay tác giả phần mềm.
- **Cơ chế hoạt động**:
  - App giao tiếp với License Server qua HTTPS có mã hóa chữ ký số (RSA Public-Private Key).
  - Khi khởi động: App gửi `License_Key + Hardware_ID` để xác thực trạng thái.
  - **Công tắc khóa từ xa (Kill-Switch)**:
    - Nếu khách hàng đòi hoàn tiền (refund), vi phạm điều khoản hoặc phát hiện gian lận:
    - Tác giả vào Web quản trị bản quyền bấm **`[🔴 REVOKE / BAN]`**.
    - Ngay lập tức, lần tiếp theo App mở lên sẽ bị khóa cứng vĩnh viễn và tự động xóa dữ liệu tạm thời trên máy khách.
  - **Chính sách hỗ trợ Offline**: App hỗ trợ cơ chế cấp token chứng thực ngắn hạn (7 ngày). Trong 7 ngày không có mạng, người dùng vẫn làm việc bình thường; sau 7 ngày bắt buộc kết nối mạng 1 giây để gia hạn token.

### 8.3. Tầng 3: Chống Dịch Ngược Mã Nguồn Bằng Nuitka (Anti-Decompiling)
- **Mục tiêu**: Triệt tiêu nguy cơ bị decompile mã nguồn Python.
- **Rủi ro của phương pháp thông thường**: Nếu chỉ dùng `PyInstaller` đơn thuần, kẻ xấu có thể dùng các công cụ như `pycdc`, `uncompyle6` để khôi phục lại 100% mã nguồn Python thô (`.py`) trong vài giây.
- **Giải pháp Nuitka C-Compilation**:
  - Sử dụng **Nuitka** để biên dịch toàn bộ mã nguồn `backend/` sang mã nguồn C/C++, sau đó dùng trình biên dịch C (`clang`/`gcc`) tạo ra mã máy nhị phân thực thi thuần túy (`.so` trên Mac, `.pyd` / `.dll` trên Windows).
  - **Kết quả**: Khi kẻ xấu cố gắng dịch ngược, họ chỉ thấy các dòng lệnh Assembly nhị phân cấp thấp, **hoàn toàn không thể khôi phục lại code Python gốc**.
  - **Frontend Obfuscation**: Làm rối mã JavaScript/React bằng *JavaScript-Obfuscator*, mã hóa các chuỗi text nhạy cảm và chèn bẫy chống mở Chrome DevTools.

### 8.4. Tầng 4: Kiến Trúc "Não Trên Mây - Tay Chân Ở Máy" (Hybrid Secret Sauce Architecture)
- **Mục tiêu**: Bảo vệ tuyệt đối các thuật toán và bí quyết công nghệ cốt lõi (Secret Sauce).
- **Mô hình triển khai**:
  - **Tại máy khách (Desktop App)**: Chỉ đảm nhận phần "tay chân" (quản lý Git worktree, kích hoạt CLI, chạy lệnh Maven, hiển thị UI).
  - **Tại máy chủ của tác giả (Cloud API Gateway)**: Lưu giữ "bộ não" (logic lập kế hoạch chia task thông minh, prompt template độc quyền).
  - Mỗi khi tạo task mới: App gửi tóm tắt yêu cầu lên Server để nhận kế hoạch tối ưu về thực thi.
  - **Hiệu quả**: Dù người dùng có cố tình dịch ngược hay can thiệp vào Desktop App, họ cũng chỉ có được một "cái vỏ rỗng" mà không bao giờ sao chép được thuật toán thông minh cốt lõi của sản phẩm.

---

## 9. Cơ Chế Nhận Diện Thiết Bị & Quản Lý Bản Dùng Thử Ẩn Danh (Device Fingerprinting & Trial Management)

Ngay cả khi phát hành phiên bản **Thử nghiệm miễn phí (Beta / Free Trial 14 ngày)** mà chưa thu phí, việc nhận diện thiết bị vẫn đóng vai trò cốt lõi để:
1. **Đo lường chính xác lượng người dùng thật** (tránh trường hợp số liệu ảo từ máy ảo hoặc spam).
2. **Chống gian lận thời gian trải nghiệm** (ngăn chặn hành vi xóa app cài lại hoặc chỉnh lùi đồng hồ máy tính để dùng thử vĩnh viễn).
3. **Phân tích môi trường chạy thực tế** (biết app đang chạy trên chip Apple M1/M2/M3 hay Intel x86_64, Windows 10/11 để kịp thời tối ưu và sửa lỗi).

### 9.1. Các Thông Số Phần Cứng Được Quét (Minh Bạch & Hợp Pháp)
> [!NOTE]
> **Cam kết an toàn dữ liệu**: App tuyệt đối không đọc file cá nhân, hình ảnh, tài liệu hay mật khẩu của người dùng. App chỉ trích xuất các thông số kỹ thuật công khai được hệ điều hành cấp phép:

1. **Mainboard / System UUID**: Mã định danh duy nhất của bo mạch chủ do nhà sản xuất (Apple, Asus, Dell...) gắn cứng trong BIOS/ROM.
2. **CPU Architecture**: Kiến trúc vi xử lý (ví dụ: `Apple Silicon arm64` hoặc `Intel/AMD x86_64`).
3. **MAC Address của Card mạng chính**: Mã vật lý của chip Wi-Fi / Ethernet.
4. **Hệ điều hành & Phiên bản**: Ví dụ `macOS 15.1` hoặc `Windows 11 23H2`.
5. **Địa chỉ IP**: Tự động nhận diện từ gói tin mạng khi gửi về Server (để xác định phân bố quốc gia).

### 9.2. Thuật Toán Băm Ẩn Danh SHA-256 (Hardware ID - HWID)
Để bảo vệ quyền riêng tư tuyệt đối cho lập trình viên, thông tin phần cứng thô **không bao giờ được gửi trực tiếp lên mạng**. App sử dụng hàm băm mật mã học **SHA-256**:

```text
Chuỗi thô = [Mainboard UUID] + [CPU Architecture] + [Primary MAC Address]
                               │
                               ▼ (Hàm băm một chiều SHA-256)
     MÃ ĐỊNH DANH ẨN DANH (Hardware ID - HWID)
     Ví dụ: "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
```

- **Tính bất biến**: Mã này là duy nhất cho mỗi máy tính trên toàn cầu.
- **Tính một chiều**: Kẻ xấu nếu bắt được chuỗi mã này cũng không thể dịch ngược lại số seri hay thông tin cá nhân của người dùng.

### 9.3. Mã Lệnh Trích Xuất Native Trên Hệ Điều Hành
Quá trình quét diễn ra tức thì trong **0.01 giây** khi App khởi động:

- **Trên macOS**:
  ```bash
  ioreg -rd1 -c IOPlatformExpertDevice | awk -F'"' '/IOPlatformUUID/{print $4}'
  ```
- **Trên Windows (PowerShell native)**:
  ```powershell
  Get-CimInstance Win32_ComputerSystemProduct | Select-Object -ExpandProperty UUID
  ```

### 9.4. Luồng Kích Hoạt Dùng Thử 14 Ngày Qua Server (Request Flow)

```text
[Máy Tính Người Dùng (Client)]                              [Server Của Bạn (Backend)]
              │                                                         │
              │  1. Gửi HTTPS (Mã hóa SSL):                             │
              │     POST https://api.yourdomain.com/v1/devices          │
              │     {                                                   │
              │       "hwid": "e3b0c44298fc...",                        │
              │       "os": "macOS 15.1 arm64",                         │
              │       "app_version": "0.1.0-beta"                       │
              │     }                                                   │
              │ ──────────────────────────────────────────────────────► │
              │                                                         │ 2. Server kiểm tra DB:
              │                                                         │    - Nếu là HWID mới:
              │                                                         │      Lưu HWID, IP, ngày bắt đầu.
              │                                                         │      Gán hạn dùng: +14 ngày.
              │                                                         │    - Nếu HWID đã tồn tại:
              │                                                         │      Tính số ngày còn lại theo CSDL.
              │                                                         │
              │  3. Server phản hồi:                                    │
              │     { "status": "active", "days_left": 12 }             │
              │ ◄────────────────────────────────────────────────────── │
              ▼                                                         ▼
    [App mở giao diện chính, hiển thị số ngày trải nghiệm còn lại]
```

### 9.5. Cơ Chế Chống Gian Lận "Xóa Đi Cài Lại" (Anti-Bypass Protection)
- **Tình huống 1: Người dùng chỉnh lùi đồng hồ máy tính**: Vô tác dụng, vì thời gian hết hạn được tính và đối chiếu dựa trên đồng hồ của Server (NTP Server), không phụ thuộc vào giờ máy tính người dùng.
- **Tình huống 2: Người dùng xóa App đi cài lại**: Vô tác dụng, vì khi mở bản cài mới lên, App vẫn tính toán ra đúng mã `HWID` phần cứng của chiếc máy tính đó. Khi gửi lên Server, CSDL đối chiếu và thấy máy này đã hết hạn dùng thử, Server trả về `status: expired` và App khóa lại ngay lập tức.

### 9.6. Tuyên Bố Minh Bạch & Chuyên Nghiệp (Privacy Notice)
Khi người dùng mở App lần đầu, hiển thị thông báo ngắn gọn:
> *"DevAgent tạo mã định danh phần cứng ẩn danh để kích hoạt bản dùng thử 14 ngày và bảo đảm an toàn hệ thống. Chúng tôi cam kết không thu thập bất kỳ dữ liệu cá nhân hay mã nguồn nào của bạn."*

---

## 10. Kiến Trúc Máy Chủ Bản Quyền: C# (.NET 8/9) & MongoDB Atlas (Cloud NoSQL)

Theo quyết định chiến lược, toàn bộ hạ tầng Server quản lý bản quyền sẽ được xây dựng trên bộ đôi công nghệ hiện đại, siêu nhẹ và vận hành đám mây 24/7 hoàn toàn tự động: **C# (.NET 8/9 Minimal API)** và **MongoDB Atlas (Cloud NoSQL)**.

```text
┌────────────────────────────────────────────────────────────────────────┐
│             HẠ TẦNG LICENSE HUB (C# .NET 8 + MONGODB ATLAS)            │
├──────────────────────────────────┬─────────────────────────────────────┤
│ 1. C# (.NET 8/9 Minimal API)     │ 2. MongoDB Atlas (Cloud Database)   │
│ • Siêu nhẹ, ăn < 35MB RAM        │ • Chạy 24/7 tự động trên đám mây    │
│ • Khởi động tức thì (Native AOT) │ • Miễn phí trọn đời (Cluster 512MB) │
│ • Hiệu năng Web API top thế giới │ • Bạn tắt máy đi ngủ DB vẫn online  │
└──────────────────────────────────┴─────────────────────────────────────┘
```

### 10.1. Tại Sao Chọn MongoDB Atlas Cho Máy Chủ Bản Quyền?
1. **Chạy 24/24 trên Đám mây (Cloud-Hosted)**:
   - Bạn **không cần bật máy tính ở nhà 24/24**. Database được đặt trên đám mây toàn cầu của MongoDB (AWS/Google Cloud).
   - Có sẵn gói **Free Tier vĩnh viễn (512 MB)** — đủ lưu trữ thông tin của hàng triệu thiết bị mà không tốn một xu nào.
2. **Cực kỳ tiện lợi (Zero Server Maintenance)**:
   - Đăng ký tài khoản trên `mongodb.com/atlas` ➔ Bấm nút tạo cluster ➔ Lấy chuỗi kết nối `mongodb+srv://...` dán vào code C# là xong ngay trong **2 phút**.
3. **Linh hoạt cấu trúc (Flexible Document Schema)**:
   - Cho phép lưu trữ dễ dàng các dữ liệu phần cứng chi tiết của máy khách (CPU, RAM, OS version, Crash logs) dạng JSON tự nhiên mà không cần chạy migration bảng phức tạp.

---

### 10.2. Cấu Trúc Dữ Liệu (MongoDB Collections)

Hệ thống sử dụng 2 Collections chính trong database `LicenseHubDb`:

#### 1. Collection `devices` (Quản lý thiết bị & Dùng thử 14 ngày)
```json
{
  "_id": "ObjectId(...)",
  "hwid": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
  "ip_address": "14.232.x.x",
  "country": "VN",
  "os_info": "macOS 15.1 (arm64)",
  "app_version": "0.1.0-beta",
  "first_activated_at": "2026-11-06T05:00:00Z",
  "trial_expires_at": "2026-11-20T05:00:00Z",
  "status": "trial", // "trial", "active", "expired", "banned"
  "last_seen_at": "2026-11-06T05:50:00Z"
}
```

#### 2. Collection `licenses` (Quản lý bản quyền mua & Khóa theo HWID)
```json
{
  "_id": "ObjectId(...)",
  "license_key": "DEVA-9842-KLM8-3312",
  "user_email": "khachhang@gmail.com",
  "tier": "lifetime", // "monthly", "yearly", "lifetime"
  "max_devices": 1,
  "created_at": "2026-11-06T05:00:00Z",
  "expires_at": null,
  "is_active": true,
  "activated_hwids": [
    "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
  ]
}
```

---

### 10.3. Mẫu Code C# .NET 8 Minimal API Với `MongoDB.Driver` (Chạy 24/24)

Toàn bộ Backend C# gói gọn trong một file `Program.cs` cực kỳ sạch và nhẹ:

```csharp
using MongoDB.Bson;
using MongoDB.Bson.Serialization.Attributes;
using MongoDB.Driver;

var builder = WebApplication.CreateBuilder(args);

// Kết nối trực tiếp tới MongoDB Atlas Cloud 24/24
var mongoConnectionString = builder.Configuration["MongoDB:ConnectionString"];
var mongoClient = new MongoClient(mongoConnectionString);
var db = mongoClient.GetDatabase("LicenseHubDb");
var devicesCol = db.GetCollection<DeviceDocument>("devices");
var licensesCol = db.GetCollection<LicenseDocument>("licenses");

// Tạo Unique Index cho Hardware ID (chống trùng lặp tuyệt đối)
devicesCol.Indexes.CreateOne(new CreateIndexModel<DeviceDocument>(
    Builders<DeviceDocument>.IndexKeys.Ascending(d => d.Hwid),
    new CreateIndexOptions { Unique = true }));

var app = builder.Build();

// Endpoint 1: Nhận diện thiết bị và cấp/kiểm tra 14 ngày dùng thử
app.MapPost("/api/v1/devices/verify", async (VerifyRequest req, HttpContext http) =>
{
    var now = DateTime.UtcNow;
    var device = await devicesCol.Find(d => d.Hwid == req.Hwid).FirstOrDefaultAsync();

    if (device is null)
    {
        device = new DeviceDocument
        {
            Hwid = req.Hwid,
            IpAddress = http.Connection.RemoteIpAddress?.ToString(),
            OsInfo = req.OsInfo,
            AppVersion = req.AppVersion,
            FirstActivatedAt = now,
            TrialExpiresAt = now.AddDays(14),
            Status = "trial",
            LastSeenAt = now
        };
        await devicesCol.InsertOneAsync(device);
    }
    else
    {
        var update = Builders<DeviceDocument>.Update
            .Set(d => d.LastSeenAt, now)
            .Set(d => d.AppVersion, req.AppVersion);

        if (device.Status == "trial" && now > device.TrialExpiresAt)
        {
            update = update.Set(d => d.Status, "expired");
            device.Status = "expired";
        }
        await devicesCol.UpdateOneAsync(d => d.Hwid == req.Hwid, update);
    }

    if (device.Status == "banned")
        return Results.Json(new { status = "banned", message = "Thiết bị này đã bị thu hồi bản quyền!" }, statusCode: 403);

    var daysLeft = Math.Max(0, (device.TrialExpiresAt - now).Days);
    return Results.Ok(new { status = device.Status, daysLeft });
});

// Endpoint 2: Kích hoạt License Key vào máy (Khóa theo Hardware ID)
app.MapPost("/api/v1/licenses/activate", async (ActivateRequest req) =>
{
    var license = await licensesCol.Find(l => l.LicenseKey == req.LicenseKey && l.IsActive).FirstOrDefaultAsync();
    if (license is null) return Results.BadRequest("License Key không hợp lệ hoặc đã bị khóa!");

    var device = await devicesCol.Find(d => d.Hwid == req.Hwid).FirstOrDefaultAsync();
    if (device is null) return Results.NotFound("Không tìm thấy thông tin thiết bị!");

    if (!license.ActivatedHwids.Contains(req.Hwid))
    {
        if (license.ActivatedHwids.Count >= license.MaxDevices)
            return Results.Conflict("License Key này đã dùng hết số lượng máy cho phép!");

        await licensesCol.UpdateOneAsync(l => l.Id == license.Id,
            Builders<LicenseDocument>.Update.Push(l => l.ActivatedHwids, req.Hwid));
    }

    await devicesCol.UpdateOneAsync(d => d.Hwid == req.Hwid,
        Builders<DeviceDocument>.Update.Set(d => d.Status, "active"));

    return Results.Ok(new { status = "activated", tier = license.Tier, expiresAt = license.ExpiresAt });
});

app.Run();

// Các Models DTO & Document
public record VerifyRequest(string Hwid, string OsInfo, string AppVersion);
public record ActivateRequest(string LicenseKey, string Hwid);

public class DeviceDocument {
    [BsonId] public ObjectId Id { get; set; }
    public string Hwid { get; set; } = "";
    public string? IpAddress { get; set; }
    public string OsInfo { get; set; } = "";
    public string AppVersion { get; set; } = "";
    public DateTime FirstActivatedAt { get; set; }
    public DateTime TrialExpiresAt { get; set; }
    public string Status { get; set; } = "trial";
    public DateTime LastSeenAt { get; set; }
}

public class LicenseDocument {
    [BsonId] public ObjectId Id { get; set; }
    public string LicenseKey { get; set; } = "";
    public string UserEmail { get; set; } = "";
    public string Tier { get; set; } = "monthly";
    public int MaxDevices { get; set; } = 1;
    public DateTime CreatedAt { get; set; }
    public DateTime? ExpiresAt { get; set; }
    public bool IsActive { get; set; } = true;
    public List<string> ActivatedHwids { get; set; } = new();
}
```

---

### 10.4. Lợi Thế Tuyệt Đối Của Combo C# + MongoDB Atlas:
1. **0 Đồng & 24/7 Tự Động**: MongoDB Atlas chạy trên cloud của MongoDB hoàn toàn miễn phí trọn đời (Free 512MB), bạn không phải bật máy tính ở nhà hay lo cúp điện.
2. **Cực kỳ dễ kết nối**: C# tích hợp thư viện `MongoDB.Driver` chính chủ chỉ bằng 1 dòng lệnh cài đặt NuGet: `dotnet add package MongoDB.Driver`.
3. **Hiệu năng đỉnh cao & Nhẹ nhàng**: C# .NET 8 Minimal API chỉ tiêu tốn ~35MB RAM, xử lý hàng chục ngàn lượt xác thực thiết bị mỗi giây một cách dễ dàng!

---

### 10.5. Hướng Dẫn Tích Hợp Chi Tiết C# (.NET) Với MongoDB Atlas

Chính công ty MongoDB Inc. phát triển và bảo trì gói thư viện chính thức **`MongoDB.Driver`** dành riêng cho C# .NET với độ tương thích và tối ưu hóa 100%:

#### 1. Cài đặt gói thư viện chính chủ:
```bash
dotnet add package MongoDB.Driver
```

#### 2. Kết nối chuỗi đám mây (Connection String):
Chuỗi kết nối lấy trực tiếp từ bảng điều khiển MongoDB Atlas (Database Access & Network Access):
```csharp
// Đặt trong appsettings.json hoặc biến môi trường:
// "MongoDB": { "ConnectionString": "mongodb+srv://admin:mat_khau@cluster0.abc.mongodb.net/?retryWrites=true&w=majority" }
```

#### 3. Thao tác dữ liệu hoàn toàn bằng cú pháp LINQ của C#:
Lập trình viên không cần viết các câu lệnh MongoDB thô mà viết C# tự nhiên:
- **Truy vấn theo Hardware ID**:
  ```csharp
  var device = await devicesCol.Find(d => d.Hwid == req.Hwid).FirstOrDefaultAsync();
  ```
- **Thêm mới thiết bị dùng thử**:
  ```csharp
  await devicesCol.InsertOneAsync(newDevice);
  ```
- **Cập nhật trạng thái và ngày gia hạn**:
  ```csharp
  await devicesCol.UpdateOneAsync(
      d => d.Hwid == req.Hwid,
      Builders<DeviceDocument>.Update.Set(d => d.Status, "active")
  );
  ```

#### 4. Khả năng tương thích kiểu dữ liệu mạnh (Strongly-Typed Mapping):
Thư viện `MongoDB.Driver` tự động chuyển đổi đối tượng Class C# (`DeviceDocument`) thành BSON/JSON của MongoDB và ngược lại một cách tự động, bảo đảm an toàn kiểu dữ liệu, không lo lỗi chính tả hay sai lệch cấu trúc dữ liệu trên Cloud!

---

### 10.6. Quy Trình Triển Khai Thực Tế License Hub Server (Chi Phí 0đ)

Hệ thống License Hub được thiết kế để một lập trình viên độc lập có thể vận hành 24/7/365 mà không tốn bất kỳ chi phí duy trì máy chủ hay database nào:

| Thành phần | Công nghệ / Nền tảng | Gói dịch vụ | Chi phí | Thời gian hoạt động |
| :--- | :--- | :--- | :--- | :--- |
| **Database** | MongoDB Atlas Cloud | M0 Shared Cluster (512MB Storage) | **0đ trọn đời** | 24/7 Cloud tự động |
| **Backend Web API** | C# (.NET 8/9 Minimal API) | Render.com / Railway / Fly.io (Docker) | **0đ (Free tier)** | Tự động scale, HTTPS có sẵn |
| **Client App** | DevAgent Desktop (Tauri + C-Binary) | Chạy cục bộ trên máy khách (CPU/RAM User) | **0đ server load** | Offline-capable |

#### Dockerfile siêu tối ưu cho C# Minimal API:
```dockerfile
# Stage 1: Build source C#
FROM mcr.microsoft.com/dotnet/sdk:8.0-alpine AS build
WORKDIR /src
COPY . .
RUN dotnet publish -c Release -o /app/publish

# Stage 2: Runtime image siêu nhẹ (~45MB)
FROM mcr.microsoft.com/dotnet/aspnet:8.0-alpine AS runtime
WORKDIR /app
COPY --from=build /app/publish .
ENV ASPNETCORE_URLS=http://+:8080
EXPOSE 8080
ENTRYPOINT ["dotnet", "DevAgent.LicenseHub.dll"]
```

---

### 10.7. Luồng Kiểm Tra & Xác Thực Giữa Desktop Client Và License Hub

```mermaid
sequenceDiagram
    autonumber
    actor User as Khách hàng
    participant Desktop as DevAgent Desktop (Local C-Binary)
    participant Server as C# .NET Minimal API (Cloud Server)
    participant Mongo as MongoDB Atlas (Cloud NoSQL)

    User->>Desktop: Mở phần mềm lần đầu tiên
    Desktop->>Desktop: Trích xuất Hardware ID (SHA-256 từ ioreg / Get-CimInstance)
    Desktop->>Server: POST /api/v1/devices/verify { hwid, os_info, version }
    Server->>Mongo: devicesCol.Find(d => d.Hwid == hwid)
    alt Thiết bị mới hoàn toàn
        Server->>Mongo: InsertOneAsync(new DeviceDocument { Status: "trial", TrialExpiresAt: now + 14 days })
        Server-->>Desktop: 200 OK { status: "trial", daysLeft: 14 }
        Desktop-->>User: Hiển thị giao diện chính (Badge: "Dùng thử còn 14 ngày")
    else Đang trong hạn dùng thử
        Server->>Mongo: UpdateOneAsync (Cập nhật LastSeenAt)
        Server-->>Desktop: 200 OK { status: "trial", daysLeft: X }
        Desktop-->>User: Vào giao diện chính bình thường
    else Hết hạn dùng thử (> 14 ngày)
        Server->>Mongo: UpdateOneAsync (Status = "expired")
        Server-->>Desktop: 200 OK { status: "expired", daysLeft: 0 }
        Desktop-->>User: Khóa tính năng AI - Hiện Popup "Nhập License Key để tiếp tục"
    end

    opt Người dùng mua bản quyền và nhập License Key
        User->>Desktop: Nhập "PRO-XXXX-YYYY-ZZZZ"
        Desktop->>Server: POST /api/v1/licenses/activate { licenseKey, hwid }
        Server->>Mongo: Kiểm tra License & Gán hwid vào ActivatedHwids
        Server-->>Desktop: 200 OK { status: "activated", tier: "pro", expiresAt: ... }
        Desktop-->>User: Kích hoạt thành công, mở khóa toàn bộ tính năng!
    end
```

---

### 10.8. Bảng Tổng Kết Kiến Trúc Toàn Diện (Client & Server Stack Matrix)

| Tiêu chí | Client: Phần Mềm Desktop (DevAgent) | Server: License Hub Quản Lý Bản Quyền |
| :--- | :--- | :--- |
| **Ngôn ngữ cốt lõi** | Python (Biên dịch C-Binary qua Nuitka) + Rust (Tauri) | **C# (.NET 8/9 Minimal API)** |
| **Cơ sở dữ liệu** | **SQLite** (`devagent.db` cục bộ, mã hóa) | **MongoDB Atlas** (Cloud NoSQL 24/7) |
| **Hạ tầng phần cứng** | 100% CPU, RAM, Disk của máy tính khách hàng | Miễn phí trên Cloud (Atlas M0 + Free App Service) |
| **Xử lý tác vụ AI** | Gọi CLI LLM (Copilot, Codex, Antigravity) bằng Token của User | **HOÀN TOÀN KHÔNG** xử lý AI, không gánh tải |
| **Dữ liệu lưu trữ** | Toàn bộ mã nguồn dự án, git branches, test logs, tasks | Chỉ lưu: HWID, IP, Ngày bắt đầu, Hạn trial, License Key |
| **Tính bảo mật** | Dữ liệu khách hàng không bao giờ rời khỏi máy tính | Băm Hardware ID một chiều, chống giả mạo bằng JWT/Signature |
| **Độ phụ thuộc mạng** | Vẫn chạy và làm việc offline với các tác vụ local | Chỉ cần ping 1 request nhẹ (~500 bytes) khi mở ứng dụng |

---

## 11. Thiết Lập & Tương Tác Chuyên Sâu Với Codex (Tech Lead) Trong ChatGPT Desktop

### 11.1. Triết Lý Thiết Kế: Tại Sao Chọn Codex Làm Tech Lead?

Trong mô hình điều phối đa tác nhân (Multi-Agent System), việc định vị vai trò cho từng AI quyết định 90% hiệu quả công việc và chi phí:

1. **Bảo toàn và tiết kiệm Quota của ChatGPT**:
   - ChatGPT Plus/Team/Pro luôn có giới hạn số lượt nhắn (Rate limit reset sau mỗi chu kỳ vài tiếng).
   - Nếu để Codex trực tiếp viết hàng trăm dòng code Java, DTO, Entity hay Unit test lặp đi lặp lại, Codex sẽ **cạn kiệt quota cực kỳ nhanh** (buộc phải chờ đợi reset).
   - Khi chuyển Codex lên vị trí **Tech Lead**: Codex chỉ làm các tác vụ cấp cao (nghĩ kiến trúc, thiết kế Contract, chia nhỏ task, duyệt PR). Mỗi quyết định chỉ tốn vài chục tokens, giúp Codex có thể điều hành cả ngày mà không lo chạm ngưỡng giới hạn!
2. **Tận dụng tối đa thế mạnh của từng AI**:
   - **Codex (OpenAI Models)**: Tư duy trừu tượng, hiểu nghiệp vụ phức tạp, thiết kế kiến trúc chuẩn mực, phát hiện lỗi tiềm ẩn khi review PR cực kỳ sắc bén.
   - **GitHub Copilot CLI**: Kỹ sư thực thi bền bỉ, pass rate ~88%, viết code và unit test rất chuẩn xác theo prompt ngắn gọn.
   - **Google Antigravity CLI**: Kỹ sư tốc độ cao, tận dụng quota Gemini Flash dồi dào hàng tuần để xử lý song song các module phụ trợ, tài liệu, DTO.

---

### 11.2. Mẫu Prompt Chuẩn Khởi Động Codex Làm Tech Lead

Người dùng chỉ cần copy đoạn Prompt sau và gửi vào ứng dụng **ChatGPT Desktop** (đang chạy Codex):

```text
Chào Codex! Trong phiên làm việc này, bạn đóng vai trò là KIẾN TRÚC SƯ TRƯỞNG & TECH LEAD của dự án.

Hệ thống điều phối DevAgent Orchestrator đang chạy trên máy tính cục bộ tại:
👉 http://127.0.0.1:8765

Dưới trướng của bạn đang có 2 kỹ sư lập trình AI sẵn sàng nhận lệnh làm việc:
1. GitHub Copilot CLI (Chuyên gia thuật toán, logic nghiệp vụ chính)
2. Google Antigravity CLI (Kỹ sư triển khai DTO, Interface, Service, Unit tests)

NGUYÊN TẮC HOẠT ĐỘNG CỦA BẠN:
1. KHÔNG tự mình ngồi gõ code thô dài dòng để tránh lãng phí Quota của ChatGPT.
2. Đọc tài liệu kế hoạch triển khai tại `docs/` của dự án.
3. Khi có tính năng mới:
   - Thiết kế DTO/Giao diện chung và chốt hợp đồng: POST /api/contracts
   - Chia nhỏ bài toán thành các task độc lập, giới hạn rõ phạm vi file (file_globs) và lệnh test (verification_command).
   - Gọi POST /api/tasks để phân chia công việc cho Copilot hoặc Antigravity.
4. Khi công nhân làm xong và vượt qua kiểm thử tự động:
   - Gọi GET /api/tasks/{id}/diff để review chất lượng code.
   - Nếu đạt chuẩn, gọi POST /api/tasks/{id}/approve-draft-pr để gộp nhánh vào branch chính!
5. Hãy gọi GET http://127.0.0.1:8765/api/health để kiểm tra trạng thái và bắt đầu!
```

---

### 11.3. Vòng Đời Tương Tác Thực Tế Giữa Người Dùng, Codex Và Đội Ngũ Worker

```text
[Người Dùng (Product Owner)]
       │
       │  1. Nói chuyện tự nhiên: "Codex ơi, hãy làm tính năng Tiếp Đón Bệnh Nhân theo tài liệu!"
       ▼
[Codex trong ChatGPT Desktop (Tech Lead)]
       │
       ├─► 2. Tự gọi: POST /api/contracts (Chốt cấu trúc DTO / Interface)
       │
       ├─► 3. Tự gọi: POST /api/tasks (Tạo Task-1 cho Copilot, Task-2 cho Antigravity)
       ▼
[DevAgent Desktop Orchestrator (Bàn làm việc nội bộ)]
       │
       ├─► Tự động tạo 2 Git Worktrees cách ly hoàn toàn
       │
       ├──► Giao Task-1 cho Copilot CLI ───► Viết code ───► Tự chạy mvn test (Pass 100%)
       │
       └──► Giao Task-2 cho Antigravity ──► Viết code ───► Tự chạy mvn test (Pass 100%)
                                                                      │
                                                                      ▼
[Codex Tech Lead] ◄── 4. Nhận thông báo Task hoàn thành và đạt tiêu chuẩn test
       │
       ├─► 5. Gọi: GET /api/tasks/{id}/diff (Đọc toàn bộ thay đổi mã nguồn)
       ├─► 6. Soi lỗi review: Không có hardcode, đúng clean architecture
       └─► 7. Gọi: POST /api/tasks/{id}/approve-draft-pr (Tự merge vào branch chính)
       │
       ▼
[Người Dùng] ◄── Báo cáo trên chat: "Tôi đã nghiệm thu xong 2 task và merge code thành công!"
```

---

### 11.4. Quy Trình Khởi Động Nhanh Khi Tải App Mới Về Máy (Dành Cho Khách Hàng)

Khi khách hàng đã có sẵn **Antigravity, Codex, GitHub Copilot và Git**:

1. **Mở App Desktop**: Nhấp đúp mở App. Lõi C-Binary kích hoạt cổng `127.0.0.1:8765` ngầm. App tự kích hoạt 14 ngày dùng thử miễn phí qua HWID.
2. **Auto-Discovery**: App tự quét Keychain và CLI trên máy. Đèn Copilot, Antigravity và Git bật xanh lá ngay lập tức (không cần gõ API Key).
3. **Chọn thư mục**: Bấm nút `[📁 Chọn thư mục]` chọn repo cần code (ví dụ `~/Documents/desktop_clinic/desktop`).
4. **Copy Prompt**: Bấm nút `[📋 Copy Prompt Cho Codex]` trên giao diện Desktop.
5. **Dán vào ChatGPT Desktop**: Bấm `Cmd+V` (hoặc `Ctrl+V`) gửi vào ChatGPT.
6. **Thưởng thức**: Codex tự động kết nối và điều phối toàn bộ đội ngũ lập trình AI làm việc nhịp nhàng, thần tốc!

---

## 12. Kiến Trúc Hoán Đổi Vai Trò Linh Hoạt: Không Khóa Cứng Nhà Cung Cấp (Dynamic Role Matrix & Vendor-Agnostic)

Một quyết định kiến trúc cực kỳ quan trọng: **DevAgent Desktop KHÔNG BAO GIỜ khóa cứng (Hardcode) vai trò Tech Lead cho bất kỳ AI nào.** Người dùng có toàn quyền chỉ định bất kỳ AI nào họ đang sở hữu làm Tech Lead hoặc Worker tùy theo nhu cầu và ngân sách tài khoản.

### 12.1. Triết Lý Thiết Kế Mở (Vendor-Agnostic Principle)
- Hệ thống Orchestrator vận hành như một **"Nhà xưởng không người lái"** qua cổng Localhost REST API `http://127.0.0.1:8765`.
- **Bất kỳ tác nhân nào có khả năng đọc tài liệu và gửi HTTP request (hoặc chạy lệnh terminal `curl`)** đều có thể đảm nhiệm vai trò **Tech Lead** một cách hoàn hảo:
  - Có tài khoản Claude Pro? ➔ Đặt **Claude Code** làm Tech Lead.
  - Có tài khoản ChatGPT Plus? ➔ Đặt **Codex** làm Tech Lead.
  - Có Google Antigravity? ➔ Đặt **Antigravity** làm Tech Lead.
  - Lập trình viên muốn tự quyết định? ➔ Đặt chế độ **Manual Tech Lead (Người dùng tự duyệt)**.

---

### 12.2. Giao Diện: "Ma Trận Phân Vai" Trực Quan (Agent Role Matrix UI)

Trên màn hình Dashboard hoặc Cài đặt của App, người dùng có bảng điều khiển phân vai linh hoạt dạng Dropdown hoặc Drag & Drop:

```text
┌────────────────────────────────────────────────────────────────────────┐
│             BẢNG PHÂN VAI CÁC TÁC NHÂN AI (AGENT ROLE MATRIX)          │
├────────────────────────────────────────────────────────────────────────┤
│  👑 VAI TRÒ TECH LEAD (Kiến trúc sư trưởng - Điều phối):                │
│     [ Chọn AI: ▼ Claude Code ]                                         │
│     (Tùy chọn: Claude Code | Codex | Google Antigravity | Tự làm Lead) │
├────────────────────────────────────────────────────────────────────────┤
│  🛠️ ĐỘI NGŨ CÔNG NHÂN THỰC THI (Workers - Chạy song song):             │
│     [x] Google Antigravity CLI  (Ưu tiên: DTO, Service, Unit Tests)   │
│     [x] GitHub Copilot CLI      (Ưu tiên: Thuật toán phức tạp, Logic) │
│     [ ] OpenAI Codex            (Nghỉ ngơi / Dự phòng)                │
├────────────────────────────────────────────────────────────────────────┤
│  ⚡ TỰ ĐỘNG SINH CHỈ THỊ (Dynamic Prompt):                             │
│     [📋 SAO CHÉP PROMPT CHO CLAUDE CODE]                               │
└────────────────────────────────────────────────────────────────────────┘
```

---

### 12.3. Trình Tạo Prompt Động Theo Từng AI Được Chọn (Context-Aware Prompt Generator)

Khi người dùng chuyển đổi Tech Lead trong Dropdown, nút bấm **`[📋 Sao chép Prompt]`** sẽ tự động điều chỉnh câu lệnh phù hợp với đặc thù công cụ của AI đó:

#### Kịch bản 1: Chọn **Claude Code (`claude` CLI)** làm Tech Lead
Claude Code sở hữu công cụ gõ Bash/Terminal bản địa cực mạnh. Prompt sẽ kích hoạt Claude dùng quyền Bash gọi API nội bộ:
```text
Chào Claude Code! Bạn là TECH LEAD của dự án này.
Hệ thống Orchestrator đang mở tại: http://127.0.0.1:8765.
Hai công nhân lập trình dưới quyền bạn: Antigravity và Copilot.
Hãy dùng quyền gõ Bash của bạn để điều phối:
1. Đọc tài liệu yêu cầu trong thư mục `docs/`.
2. Gọi `curl -X POST http://127.0.0.1:8765/api/contracts` để chốt DTO/Interface.
3. Gọi `curl -X POST http://127.0.0.1:8765/api/tasks` để giao việc cho Antigravity hoặc Copilot.
4. Khi công nhân hoàn thành và test pass, gọi `curl http://127.0.0.1:8765/api/tasks/{id}/diff` để review code.
5. Nếu đạt chất lượng, gọi `curl -X POST http://127.0.0.1:8765/api/tasks/{id}/approve-draft-pr` để gộp nhánh!
Hãy bắt đầu bằng cách gọi `curl http://127.0.0.1:8765/api/health` để kiểm tra trạng thái!
```

#### Kịch bản 2: Chọn **Google Antigravity (`agy`)** làm Tech Lead
Antigravity sử dụng mô hình Gemini đa phương thức và khả năng đọc file toàn dự án để quy hoạch:
```text
Chào Antigravity! Bạn là KIẾN TRÚC SƯ TRƯỞNG & TECH LEAD của dự án.
Cổng Orchestrator đang lắng nghe tại: http://127.0.0.1:8765.
Công nhân thực thi: GitHub Copilot CLI và Codex.
Hãy khảo sát toàn bộ codebase, thiết lập Contracts và giao các task nặng cho Copilot thực thi qua API nội bộ, sau đó bạn sẽ nghiệm thu code diff và duyệt PR!
```

#### Kịch bản 3: Chọn **Người Dùng Tự Làm Tech Lead (Manual Mode)**
- Dành cho các Senior Developer muốn tự tay kiểm soát 100% từng task:
- Người dùng tự bấm nút **`[+ Tạo Task Mới]`** trên giao diện Desktop App.
- Chọn Worker thực hiện: `[Copilot]` hoặc `[Antigravity]`.
- Hệ thống tự tạo Git Worktree và cho AI code ngầm. Khi xong, người dùng tự mở Diff Viewer trên App xem và bấm **`[Duyệt PR]`**.

---

### 12.4. Bảng So Sánh Các Đội Hình Phối Hợp Tiêu Biểu (Recommended Team Setups)

| Đội hình | Tech Lead (Kiến trúc) | Workers (Thực thi) | Điểm mạnh vượt trội | Ngữ cảnh phù hợp |
| :--- | :--- | :--- | :--- | :--- |
| **Đội hình 1: Quota Optimizer** | **Codex** (ChatGPT) | **Copilot** + **Antigravity** | Tiết kiệm tối đa quota ChatGPT, khai thác quota Gemini miễn phí | Dành cho người có gói ChatGPT Plus thông thường |
| **Đội hình 2: Claude Powerhouse** | **Claude Code** (Sonnet 3.7) | **Antigravity** + **Copilot** | Kiến trúc và review code cực kỳ thông minh, khả năng refactor sâu sắc | Dành cho người có gói Claude Pro / Team |
| **Đội hình 3: Zero-Cost Google** | **Antigravity** (Gemini Pro) | **Antigravity** (Gemini Flash) | Hoàn toàn miễn phí, tận dụng hạn ngạch Google dồi dào | Dành cho người muốn trải nghiệm 0 đồng |
| **Đội hình 4: Senior Solo Control** | **Chính Người Dùng** | **Copilot** + **Antigravity** | Người dùng kiểm soát 100% kiến trúc, AI đóng vai trò đôi tay gõ code tốc độ cao | Dành cho Tech Lead kỳ cựu thích tự thiết kế |

---

## 13. Cơ Chế Cấp Quân Số Đa Tab & Đội Hình Sư Đoàn AI (Multi-Tab Concurrency & Agent Fleet Provisioning)

### 13.1. Nguyên Lý Phân Quyền: Chủ Doanh Nghiệp Cấp Quân Số ➔ Quản Đốc Điều Phối
Một thắc mắc kinh điển trong vận hành hệ thống đa tác nhân: **Ai là người nhân bản (mở thêm tab) cho các AI Worker?**

Câu trả lời rõ ràng trong kiến trúc DevAgent:
- **Người Dùng (Owner / CEO)**: Là người **duy nhất nắm quyền cấp hạn mức quân số (Resource Provisioning)** dựa trên gói thuê bao mình trả tiền và sức mạnh phần cứng máy tính.
- **Tech Lead (Quản Đốc / Claude / Codex)**: **Tuyệt đối không tự ý sinh tab bừa bãi** gây nghẽn máy hoặc cháy token người dùng. Thay vào đó, Tech Lead đọc hạn mức quân số từ API Orchestrator và tối ưu hóa việc phân chia công việc theo đúng số thợ được cấp.

```text
┌────────────────────────────────────────────────────────────────────────┐
│  👑 NGƯỜI DÙNG (Cấp quân số lúc Setup):                                │
│  "Tôi có Mac 32GB RAM + gói Copilot/Claude/Codex đầy đủ.               │
│   Tôi cấp: Claude (Lead) + 4 Copilot + 3 Codex + 1 Antigravity!"       │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │ (Lưu cấu hình Worker Pool)
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│  🏢 ORCHESTRATOR (Hạ Tầng Nhà Xưởng):                                  │
│  Mở sẵn 8 Git Worktrees riêng biệt (`.worktrees/task-101` .. `108`).   │
│  Báo qua API: {"tech_lead": "claude", "available_workers": 8}          │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│  🧠 CLAUDE TECH LEAD (Điều phối theo quân số):                         │
│  Chia tính năng thành 8 Task độc lập, giao đúng sở trường từng thợ,    │
│  chạy song song và nghiệm thu code diff khi hoàn thành!                │
└────────────────────────────────────────────────────────────────────────┘
```

---

### 13.2. Cấu Hình Bảng Quân Số Lập Trình (Agent Fleet Configuration UI)

Trên giao diện cài đặt của Desktop App, người dùng được toàn quyền tinh chỉnh số luồng (tab) song song của từng loại AI:

```text
┌────────────────────────────────────────────────────────────────────────┐
│            CẤU HÌNH QUÂN SỐ LẬP TRÌNH (AGENT FLEET CONFIG)             │
├────────────────────────────────────────────────────────────────────────┤
│  👑 CHỈ HUY TỐI CAO (TECH LEAD):  [ ▼ Claude Code ]                    │
│     (Có thể chọn: Claude Code | Codex | Antigravity | Tự làm Lead)     │
├────────────────────────────────────────────────────────────────────────┤
│  🛠️ PHÂN BỔ QUÂN SỐ THỢ CODE CHẠY SONG SONG:                           │
│     • GitHub Copilot CLI:      [ 4 ] Tabs/Slots (Cày logic, Service)   │
│     • OpenAI Codex CLI:        [ 3 ] Tabs/Slots (Xử lý thuật toán khó) │
│     • Google Antigravity CLI:  [ 1 ] Tab/Slot   (Viết DTO, Model, Test)│
├────────────────────────────────────────────────────────────────────────┤
│  🚀 TỔNG LỰC LƯỢNG SONG SONG: 8 WORKERS (8 Bàn làm việc Git Worktrees) │
│  ⚡ TRẠNG THÁI: 8 Slots sẵn sàng nhận lệnh từ Tech Lead                 │
└────────────────────────────────────────────────────────────────────────┘
```

---

### 13.3. Phân Phối Tác Vụ Thông Minh Theo Sở Trường Của Từng Thợ

Khi sở hữu "Sư đoàn 8 thợ lập trình", Tech Lead (ví dụ Claude Code) phân chia công việc tối ưu:
1. **4 Thợ Copilot (Slots 1, 2, 3, 4)**:
   - Đảm nhiệm: Viết Controller, Service nghiệp vụ chính, các hàm CRUD dữ liệu lớn.
   - Lý do: Tốc độ gõ mã cực nhanh, bền bỉ, không lo cạn kiệt Quota.
2. **3 Thợ Codex (Slots 5, 6, 7)**:
   - Đảm nhiệm: Các logic thuật toán khó, giải quyết xung đột Transaction DB, bài toán bảo mật/mã hóa.
   - Lý do: Suy luận toán học và logic sâu sắc của OpenAI models.
3. **1 Thợ Antigravity (Slot 8)**:
   - Đảm nhiệm: Viết các lớp Data Transfer Object (DTO), ánh xạ Mapper, sinh tài liệu OpenAPI, viết test mock.
   - Lý do: Khai thác hạn ngạch Gemini Flash miễn phí hàng tuần.

---

### 13.4. Phòng Vệ Chống Xung Đột & Quản Lý Phần Cứng

- **8 Git Worktrees cách ly 100%**: Mỗi thợ làm việc trong một thư mục nhánh riêng biệt (từ `.worktrees/task-101` đến `.worktrees/task-108`), không bao giờ xảy ra tình trạng AI này ghi đè lên file của AI kia.
- **Bộ trọng tài `_scope_conflict` (`scheduler.py`)**: Nếu 2 task đụng đến cùng 1 file, hệ thống tự động đưa task thứ 2 vào hàng đợi chờ task thứ nhất merge xong mới kích hoạt.
- **Tự động điều tiết tải CPU/RAM**:
  - Với máy tính có cấu hình mạnh (Mac M-series 16-32GB RAM): Cả 8 thợ chạy hết công suất song song, hoàn thành cả module tính năng phức tạp chỉ trong **15 - 20 phút**!
  - Với máy tính cấu hình nhẹ hơn (8GB RAM): Orchestrator tự động xếp hàng luân phiên (Throttle) 2-3 thợ chạy một lúc, đảm bảo máy tính luôn mát mẻ và không bị treo ứng dụng.

---

## 14. Bàn Giao Ca Trực Tech Lead (Succession Handover) & Cập Nhật Quân Số Nóng (Hot-Reloading Pool)

Trong quá trình phát triển phần mềm thực tế, việc cạn kiệt Token hoặc hồi lại Quota diễn ra liên tục. DevAgent Desktop được thiết kế với cơ chế **chuyển giao trạng thái không gián đoạn (Zero-Downtime State Handoff)**, giải quyết triệt để 2 bài toán:

### 14.1. Thêm Lại Worker Khi Hồi Token (Hot-Reloading Worker Pool)
- **Tình huống**: GitHub Copilot tạm thời cạn quota hoặc bị giới hạn tốc độ. Người dùng ban đầu tắt Copilot và chỉ cho Claude + Antigravity chạy. Sau 30 phút, Copilot hồi lại quota.
- **Thao tác người dùng**:
  1. Không cần khởi động lại App hay dừng các task đang chạy.
  2. Trên bảng điều khiển quân số (Agent Fleet), tích chọn lại: `[x] GitHub Copilot`.
  3. Chọn số tab muốn cấp: `[ 2 ] Tabs`.
  4. Bấm nút **`[ Cập Nhật Nóng (Hot Apply) ]`**.
- **Cơ chế xử lý ngầm**:
  - Orchestrator gọi API nội bộ cập nhật runtime pool: kích hoạt `copilot-cli` và tăng tổng số slot làm việc.
  - Tech Lead (dù là Claude hay Codex) khi gọi `GET /api/health` hoặc khi duyệt task tiếp theo sẽ thấy ngay quân số mới.
  - Các task đang chờ trong hàng đợi (Pending Queue) tự động được bốc và giao ngay cho 2 tab Copilot mới vừa gia nhập đội hình!

---

### 14.2. Chuyển Giao Quyền Lực Tech Lead Khi Hết Token (Tech Lead Succession Protocol)
- **Tình huống**: Claude Code đang làm Tech Lead và điều phối dự án rất tốt thì đột ngột hết hạn mức (Token limit). Người dùng muốn chuyển sang Codex (ChatGPT Desktop) làm Tech Lead để tiếp tục công việc, trong khi các tab worker của Codex và Copilot vẫn đang gõ code bình thường.
- **Nguyên lý cốt lõi: Single Source of Truth (Nguồn chân lý duy nhất)**:
  - Bộ não của dự án **không nằm trong bộ nhớ tạm của Claude**, mà được lưu trữ bền vững tại:
    1. **SQLite (`devagent.db`)**: Bảng `contracts` (hợp đồng DTO), bảng `tasks` (trạng thái DONE, RUNNING, PENDING), bảng `events` và `runs`.
    2. **Hệ thống Git Worktree & Docs**: Các commit, diff code và file kế hoạch `docs/ke-hoach-trien-khai/03-model-task-board.md`.
  - Do đó, **bất kỳ AI nào kế nhiệm làm Tech Lead đều có thể đọc trọn vẹn 100% bối cảnh dự án trong 0.5 giây!**

```text
┌────────────────────────────────────────────────────────────────────────┐
│               SƠ ĐỒ BÀN GIAO CA TRỰC TECH LEAD KHÔNG GIÁN ĐOẠN         │
├────────────────────────────────────────────────────────────────────────┤
│  1. CLAUDE HẾT TOKEN     ──► Bạn đổi Dropdown: [ ▼ Codex ]             │
│  2. APP SINH BÁO CÁO     ──► Tổng kết: DONE (2), RUNNING (2), QUEUE (4)│
│  3. COPY PROMPT BÀN GIAO ──► Dán vào ChatGPT Desktop (Codex)          │
│  4. CODEX TIẾP QUẢN      ──► Gọi GET /api/tasks -> Nắm 100% lộ trình   │
│  5. VẬN HÀNH TIẾP TỤC    ──► Duyệt PR cho các Worker đang code dở!     │
└────────────────────────────────────────────────────────────────────────┘
```

---

### 14.3. Mẫu Chỉ Thị Bàn Giao Ca Trực Tự Động (Auto-Generated Handover Prompt)

Khi người dùng chuyển đổi Tech Lead sang Codex trên giao diện, App tự động tạo ra một bản báo cáo bàn giao chuẩn mực:

```text
Chào Codex! Bạn được chỉ định làm TECH LEAD KẾ NHIỆM tiếp quản dự án từ Claude Code vừa hết ca (hết token).

Cổng điều phối Orchestrator đang mở tại: http://127.0.0.1:8765

TÌNH TRẠNG HIỆN TẠI ĐÃ ĐƯỢC HỆ THỐNG LƯU TRỮ TRONG CSDL:
- Hợp đồng DTO: Đã chốt tại GET /api/contracts
- Các task đã hoàn thành (DONE): Task-01, Task-02 (đã merge vào nhánh chính)
- Các task đang chạy dưới nền: Task-03, Task-04 (các tab worker vẫn đang chạy bình thường trong worktree riêng)
- Các task đang chờ trong hàng đợi: Task-05, Task-06, Task-07

NHIỆM VỤ TIẾP QUẢN CỦA BẠN:
1. Gọi GET /api/tasks để nắm danh sách chi tiết các task.
2. Tiếp tục giám sát: Khi Task-03, Task-04 hoàn tất kiểm thử, hãy gọi GET /api/tasks/{id}/diff để review code và duyệt PR.
3. Phân phối các task còn lại (Task-05, Task-06...) cho các worker đang sẵn sàng!
Hãy gọi GET http://127.0.0.1:8765/api/health để xác nhận quyền Tech Lead và bắt đầu ca trực!
```

---

### 14.4. Tính Độc Lập Giữa Tab "Codex Tech Lead" Và Các Tab "Codex Workers"

Một điểm kiến trúc cực kỳ tinh tế:
- **Codex Worker (Tab gõ code)**: Chạy bằng tiến trình CLI ngầm bên trong thư mục Git Worktree riêng, chỉ tập trung giải quyết bài toán kỹ thuật cụ thể và chạy `mvn test`.
- **Codex Tech Lead (Tab điều phối)**: Là phiên trò chuyện tương tác với bạn trên ChatGPT Desktop, chuyên lo chiến lược cấp cao, đọc diff và duyệt PR.
- Hai vai trò này hoạt động hoàn toàn tách biệt, **không hề dẫm chân lên nhau**, giúp cỗ máy vận hành mượt mà, liên tục 24/7 mà không sợ bất kỳ sự gián đoạn nào!

---

## 15. Cơ Chế Thợ Cứu Hộ Thời Vụ Tự Hủy (Ephemeral Rescue Workers & Auto-Teardown)

### 15.1. Khái Niệm: "Nhân Viên Part-Time Cứu Hộ Một Lần" (Ephemeral / One-Shot Worker)
Khi một AI Worker chính thức (ví dụ: Claude Worker #1) đang viết code dở dang đạt 70% thì bất ngờ bị "gãy cánh" giữa đường do tài khoản cạn kiệt Token (Rate Limit 429), hệ thống **tuyệt đối không vứt bỏ công sức đã làm** và cũng **không ép người dùng phải sửa đổi cấu hình quân số vĩnh viễn**.

Thay vào đó, Tech Lead (Codex hoặc Lead kế nhiệm) sẽ kích hoạt cơ chế **Thợ Cứu Hộ Thời Vụ (Ephemeral Rescue Worker)**:
- Tuyển dụng tạm thời một tab thợ mới (ví dụ: Copilot hoặc Antigravity còn token) đóng vai trò thợ Part-Time.
- Thợ Part-Time nhảy vào đúng phòng làm việc (Git Worktree) đang chứa 70% code dở của Claude, gõ nốt 30% còn lại và chạy kiểm thử.
- Ngay khi code được nghiệm thu và merge vào nhánh chính: **Tab thợ Part-Time tự động hủy (Self-Despawn), giải phóng ngay lập tức CPU, RAM và dọn dẹp worktree**.
- Quân số của hệ thống tự động co lại đúng mức ban đầu (Auto-Scaling Down).

---

### 15.2. Sơ Đồ Tuần Tự Cứu Hộ Dở Dang Không Gián Đoạn

```mermaid
sequenceDiagram
    autonumber
    participant ClaudeW as Claude Worker (Gãy cánh)
    participant Orch as Orchestrator (Nhà xưởng)
    participant Lead as Codex (Tech Lead kế nhiệm)
    participant PartTime as Copilot Part-Time (Thợ cứu hộ)
    participant DB as SQLite Event Store (Nhật ký)

    ClaudeW->>Orch: Bị văng lỗi 429 (Hết Token)
    Orch->>Orch: Tự động lưu checkpoint: "commit 8a4f21" (bảo toàn 70% code dở)
    Orch->>Lead: Báo động sự cố: Task-03 đang dở dang 70%!
    Lead->>Orch: Lệnh điều động: Tuyển gấp 1 Thợ Part-Time (Copilot) cứu hộ Task-03!
    Orch->>PartTime: Kích hoạt Tab Part-Time tạm thời, ném vào đúng Git Worktree dở
    PartTime->>PartTime: Đọc code dở + Viết nốt 30% còn lại + Chạy mvn test (Pass 100%)
    PartTime->>Lead: Báo cáo: Task-03 đã sửa xong và test pass 100%!
    Lead->>Orch: Duyệt PR & Merge vào nhánh chính
    Orch->>DB: Ghi sổ nhật ký chi tiết: "Claude (70%) + Copilot Part-time (30%)"
    Orch->>PartTime: TỰ ĐỘNG THU HỒI TAB (Auto-Teardown) -> Giải phóng RAM/CPU
```

---

### 15.3. Lưu Vết Lịch Sử Minh Bạch 100% (Audit Trail & Event Log)

Mọi hành động cứu hộ thời vụ đều được ghi nhận vĩnh viễn trong CSDL SQLite (`devagent.db` - bảng `events` và `runs`). Khi người dùng mở Dashboard xem lại lịch sử task, thông tin hiển thị cực kỳ minh bạch và chuyên nghiệp:

```text
┌────────────────────────────────────────────────────────────────────────┐
│  HỒ SƠ TIẾN TRÌNH: TASK-03 (Tạo ReceptionController)                   │
├────────────────────────────────────────────────────────────────────────┤
│  • 13:20:00 - Khởi tạo task, cấp Git Worktree `.worktrees/task-103`    │
│  • 13:20:05 - Bắt đầu thực thi: Claude Worker #1                       │
│  • 13:24:12 - Claude hoàn thành 70% -> Dừng khẩn cấp do Rate Limit 429 │
│  • 13:24:13 - Checkpoint an toàn: Lưu commit tạm `checkpoint-8a4f21`   │
│  • 13:25:00 - Tech Lead (Codex) điều động Thợ Part-time: Copilot-Rescue│
│  • 13:27:30 - Copilot-Rescue hoàn thành 30% còn lại                    │
│  • 13:27:45 - Chạy `mvn test`: 12/12 test PASSED 100%                  │
│  • 13:28:10 - Tech Lead duyệt PR -> Đã gộp thành công vào nhánh chính  │
│  • 13:28:12 - TỰ ĐỘNG THU HỒI: Thợ Part-time Copilot-Rescue tự hủy!   │
│  • 13:28:13 - Quân số hệ thống trở về baseline ban đầu                 │
└────────────────────────────────────────────────────────────────────────┘
```

---

### 15.4. Lợi Ích Cốt Lõi Của Cơ Chế Tự Hủy & Tự Thu Hồi Quân Số:
1. **Không lãng phí tài nguyên**: Không để các tiến trình AI chạy ngầm vô bổ sau khi hoàn thành nhiệm vụ.
2. **Tiết kiệm 70% thời gian & chi phí**: Kế thừa nguyên vẹn mã nguồn mà AI trước đã viết, thợ mới chỉ tốn vài chục giây để vá nốt phần còn thiếu.
3. **Hoàn toàn tự động hóa (Autonomous Resilience)**: Toàn bộ quá trình từ "gãy cánh" ➔ "bảo toàn checkpoint" ➔ "gọi cứu hộ" ➔ "nghiệm thu" ➔ "thu hồi quân số" diễn ra khép kín mà lập trình viên không phải can thiệp thủ công bất kỳ thao tác nào!

---

## 16. Ma Trận Xử Lý Các Tình Huống Biên & Bảo Vệ Hệ Thống (Edge-Case Resilience & System Safeguards)

Để đạt tiêu chuẩn của một phần mềm thương mại hoạt động ổn định trên hàng triệu máy tính cá nhân, hệ thống được trang bị bộ 6 cơ chế phòng vệ tự động giải quyết triệt để các tình huống sự cố thực tế:

### 16.1. Bẫy Đầy Ổ Cứng Do Mở Nhiều Git Worktrees (Disk Space & Build Bloat)
- **Vấn đề**: Khi mở 4 đến 8 tab chạy song song, mỗi tab là một Git Worktree. Với các dự án lớn (Java Maven sinh `target/`, Node.js sinh `node_modules/`), mỗi worktree có thể tốn từ 500MB đến 2GB. 8 worktrees có thể ngốn tới 16GB ổ cứng, gây nguy cơ treo hoặc sập hệ điều hành của người dùng.
- **Phương án xử lý 3 lớp**:
  1. **Pre-flight Disk Check**: Trước khi tạo Git Worktree mới, App kiểm tra dung lượng ổ đĩa trống. Nếu còn `< 3GB`, App từ chối tạo worktree mới và cảnh báo: *"Ổ đĩa của bạn sắp đầy, hệ thống tạm dừng mở thêm thợ để bảo vệ máy tính!"*
  2. **Auto-Prune Post-Merge**: Ngay sau khi task được merge vào nhánh chính, Orchestrator lập tức chạy `git worktree remove --force` và dọn sạch thư mục `target/` tạm, giải phóng 100% dung lượng đĩa trong tích tắc.
  3. **Shared Build Cache**: Cấu hình các lệnh Maven dùng chung thư mục local repository (`~/.m2/repository`) thay vì tải lại thư viện ở từng tab.

---

### 16.2. Xung Đột Merge Muộn Giữa 2 Worker Chạy Song Song (Late-Stage Merge Conflict)
- **Vấn đề**: Task A (Thợ 1) và Task B (Thợ 2) bắt đầu cùng lúc từ commit gốc `C0`. Dù phạm vi file khác nhau nhưng cả 2 đều vô tình sửa chung file cấu hình `pom.xml` (Thợ 1 thêm Lombok, Thợ 2 thêm MapStruct). Thợ 1 xong trước merge vào `main` tại `C1`. Khi Thợ 2 nộp PR vào `main` thì bị xung đột Git Conflict!
- **Phương án xử lý (Cơ chế Auto-Rebase 3 Tầng)**:
  - **Tầng 1 (Tự động Rebase)**: Trước khi tạo PR, Orchestrator tự động chạy `git rebase main` trong worktree của Thợ 2.
  - **Tầng 2 (AI Auto-Resolve)**: Nếu phát hiện xung đột, Orchestrator gom đoạn code đánh dấu `<<<<<<<`, `=======`, `>>>>>>>` và gửi ngay cho chính Worker đó: *"Hãy gộp cả 2 đoạn mã này lại sao cho cả 2 tính năng đều hoạt động!"*. Sau đó tự chạy lại `mvn test`.
  - **Tầng 3 (Visual Diff Fallback)**: Nếu AI không gộp được sau 2 lần thử, App hiển thị giao diện **3-Way Visual Diff Viewer** để lập trình viên click chuột chọn giữ đoạn code nào, không bao giờ làm hỏng code nhánh chính.

---

### 16.3. Tác Vụ Treo & Vòng Lặp Vô Tận (Zombie Process & Infinite Loop Watchdog)
- **Vấn đề**: AI viết code tạo ra vòng lặp vô tận (`while(true)`), hoặc lệnh `mvn test` bị treo do chờ kết nối socket/user input, hoặc AI CLI bị đơ do mất kết nối mạng, khiến tiến trình ngốn 100% CPU và giữ chặt slot làm việc vĩnh viễn.
- **Phương án xử lý**:
  1. **Hard Timeout Watchdog**: Mỗi task được gán thời gian tối đa (mặc định 600 giây = 10 phút, người dùng có thể tùy chỉnh).
  2. **Force Kill Subprocess**: Nếu quá thời gian đếm ngược mà tiến trình chưa kết thúc:
     - Gửi tín hiệu `SIGTERM` yêu cầu dừng mềm.
     - Sau 5 giây nếu không phản hồi, gửi `SIGKILL` tiêu diệt tận gốc tiến trình treo, thu hồi 100% CPU/RAM.
  3. **Báo cáo sự cố**: Đánh dấu task là `TIMED_OUT` và gửi log cho Tech Lead phân tích nguyên nhân.

---

### 16.4. Mất Kết Nối Mạng & Chế Độ Ngoại Tuyến (Offline Grace Period & Network Retry)
- **Vấn đề**: Người dùng bị rớt Wi-Fi hoặc mạng chập chờn khi đang làm việc.
- **Phương án xử lý**:
  1. **Thời Gian Ân Hạn Offline (Offline Grace Period - 7 Ngày)**: Khi kích hoạt bản quyền, License Hub cấp một chữ ký số RSA (JWT) lưu an toàn trong máy. Nếu mất mạng, App vẫn cho phép người dùng **làm việc offline hoàn toàn trong vòng 7 ngày** mà không hề bị khóa bản quyền.
  2. **Retry With Exponential Backoff**: Nếu AI CLI gặp lỗi kết nối API tạm thời (DNS error, TLS handshake timeout), Orchestrator tự động thử lại 3 lần (sau 5s, 15s, 30s) trước khi kết luận task thất bại, tránh việc hủy task oan vì mạng lag 1-2 giây.

---

### 16.5. Quét Rò Rỉ Mã Bí Mật Vào Git (Pre-Merge Secret & PII Scanner)
- **Vấn đề**: AI trong lúc code hoặc viết file cấu hình vô tình điền mã API Key thật, token GitHub, hoặc mật khẩu cơ sở dữ liệu (`password = "admin123"`) vào mã nguồn rồi commit lên Git.
- **Phương án xử lý**:
  - **Bộ lọc Secret Scanner tiền kiểm**: Trước khi Tech Lead bấm duyệt PR, Orchestrator tự động chạy regex quét toàn bộ `git diff` của task:
    - Tìm kiếm các mẫu nhạy cảm: `sk-...`, `ghp_...`, `AWS_SECRET`, Private Keys, Password plaintext.
  - **Chặn merge tức thì**: Nếu phát hiện chuỗi nghi vấn, hệ thống từ chối merge, cảnh báo đỏ và yêu cầu AI sửa lại ngay thành biến môi trường `System.getenv(...)` hoặc file `.env`.

---

### 16.6. Sao Lưu & Di Chuyển Dự Án (Project Portability & 1-Click Backup)
- **Vấn đề**: Người dùng đổi máy tính mới, hoặc chuyển dự án sang thư mục khác, hoặc cần sao lưu tiến độ làm việc.
- **Phương án xử lý**:
  - **1-Click Export Snapshot**: Bấm một nút trên giao diện để xuất toàn bộ CSDL SQLite (`devagent.db`), nhật ký task, và các hợp đồng contracts thành một file nén nhỏ gọn: `project-checkpoint.devagent`.
  - Trên máy mới, chỉ cần mở App và chọn `[Khôi Phục Snapshot]` là toàn bộ lịch sử và tiến độ được phục hồi 100% mà không mất 1 byte dữ liệu!

---

## 17. Trải Nghiệm Chuẩn Hóa Chế Độ 2: Ứng Dụng Bên Ngoài (External App Mode & Dual-Screen Workflow)

Theo quyết định chiến lược về sản phẩm, DevAgent Desktop chính thức chuẩn hóa **Chế Độ 2 (External App Mode)** làm phương thức vận hành chính. Đây là mô hình đem lại sự tự do tối đa và tối ưu hóa chi phí tốt nhất cho lập trình viên.

### 17.1. Triết Lý Thiết Kế: Mô Hình Hai Màn Hình (Phòng Chỉ Huy & Nhà Xưởng)

```text
┌──────────────────────────────────────────────┐        ┌──────────────────────────────────────────────┐
│  MÀN HÌNH 1: PHÒNG CHỈ HUY CHIẾN LƯỢC        │        │  MÀN HÌNH 2: NHÀ XƯỞNG CƠ KHÍ & ĐIỀU PHỐI    │
│  (Ứng dụng ChatGPT Desktop / Claude Desktop) │        │  (Ứng dụng DevAgent Desktop - Multi-Tab)     │
├──────────────────────────────────────────────┤        ├──────────────────────────────────────────────┤
│ • Giao tiếp tự nhiên bằng văn bản / Voice    │        │ • Giám sát 8 Tab thợ code chạy song song     │
│ • Tận dụng gói tháng sẵn có ($20/tháng)      │ ◄────► │ • Tự động tạo 8 Git Worktrees độc lập        │
│ • KHÔNG TỐN 1 ĐỒNG CHI PHÍ API TOKEN!        │  REST  │ • Tự động chạy 'mvn test', Bounded Recovery  │
│ • Codex/Claude làm Tech Lead quy hoạch       │  API   │ • Diff Viewer trực quan, lưu SQLite cục bộ   │
└──────────────────────────────────────────────┘        └──────────────────────────────────────────────┘
```

---

### 17.2. Giao Diện Thẻ Kích Hoạt 1-Chạm (Activation Card UI)

Ngay khi người dùng mở DevAgent Desktop và chọn thư mục dự án, màn hình chính sẽ hiển thị Thẻ Kích Hoạt trang nhã:

```text
┌────────────────────────────────────────────────────────────────────────────────────────┐
│  DỰ ÁN: clinic_desktop  |  BRANCH: main  |  CỔNG API NỘI BỘ: http://127.0.0.1:8765    │
├────────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                        │
│  👑 TRẠNG THÁI TECH LEAD:  🟡 Đang chờ kết nối từ ChatGPT Desktop...                  │
│                                                                                        │
│  ┌──────────────────────────────────────────────────────────────────────────────────┐  │
│  │  HƯỚNG DẪN KẾT NỐI NHANH TRONG 5 GIÂY:                                           │  │
│  │  1. Mở ứng dụng ChatGPT Desktop (hoặc Claude Desktop) của bạn lên.               │  │
│  │  2. Bấm nút bên dưới để sao chép chỉ thị điều phối chuẩn mực:                    │  │
│  │                                                                                  │  │
│  │        👉  [ 📋 SAO CHÉP PROMPT KÍCH HOẠT CHO CHATGPT DESKTOP ]                  │  │
│  │                                                                                  │  │
│  │  3. Dán (Cmd+V / Ctrl+V) vào cửa sổ ChatGPT. Hệ thống sẽ tự động bắt tay!        │  │
│  └──────────────────────────────────────────────────────────────────────────────────┘  │
│                                                                                        │
│  🛠️ ĐỘI NGŨ 8 WORKER TABS ĐÃ SẴN SÀNG (ĐANG CHỜ TECH LEAD GIAO VIỆC):                  │
│  [Tab 1: Copilot #1 ⚪] [Tab 2: Copilot #2 ⚪] [Tab 3: Codex #1 ⚪] [Tab 4: Antigravity ⚪]...│
└────────────────────────────────────────────────────────────────────────────────────────┘
```

---

### 17.3. Luồng Tự Động Bắt Tay (Automatic Handshake Sequence)

```mermaid
sequenceDiagram
    autonumber
    actor User as Bạn (Product Owner)
    participant App as DevAgent Desktop (Nhà xưởng 8 Tab)
    participant Ext as ChatGPT Desktop (Tech Lead)

    User->>App: Mở App, chọn Repo dự án & Cấp 8 Tab thợ code
    App->>App: Kích hoạt lõi ngầm tại http://127.0.0.1:8765
    App-->>User: Hiển thị nút [📋 Sao Chép Prompt Kích Hoạt]
    User->>App: Bấm nút Copy Prompt
    User->>Ext: Mở ChatGPT Desktop, bấm Cmd+V dán tin nhắn
    Ext->>App: Tự gọi GET http://127.0.0.1:8765/api/health
    App-->>Ext: Phản hồi: {"status": "ok", "available_workers": 8}
    App-->>User: Đèn Dashboard đổi xanh: "👑 Tech Lead: ĐÃ KẾT NỐI 🟢"
    User->>Ext: "Codex ơi, hãy làm tính năng Tiếp Đón Bệnh Nhân!"
    Ext->>App: Tự gọi POST /api/contracts & POST /api/tasks
    App->>App: 8 Tab mở 8 Git Worktrees cày code & tự chạy mvn test
    App-->>Ext: Báo về: "Task-101 pass 100%, sẵn sàng review!"
    Ext->>App: Tự gọi GET /api/tasks/101/diff & POST /approve-draft-pr
```

---

### 17.4. Bốn (4) Bước Làm Việc Hàng Ngày Của Lập Trình Viên (Daily 4-Step Routine)

1. **Bước 1**: Nhấp đúp mở **DevAgent Desktop** ➔ Chọn thư mục dự án ➔ Kiểm tra quân số (ví dụ: 4 Copilot, 3 Codex, 1 Antigravity).
2. **Bước 2**: Bấm nút **`[📋 Sao chép Prompt]`** trên DevAgent Desktop.
3. **Bước 3**: Mở **ChatGPT Desktop** (hoặc Claude Desktop), bấm `Cmd+V` (hoặc `Ctrl+V`) gửi vào. Trạng thái trên DevAgent Desktop lập tức sáng đèn xanh: **`🟢 ĐÃ KẾT NỐI`**.
4. **Bước 4**: Nói chuyện với ChatGPT về các tính năng muốn phát triển, sau đó ngả lưng quan sát 8 Tab thợ code chạy song song trên DevAgent Desktop và tận hưởng thành quả!

---

## 18. Cẩm Nang Giao Diện & Thao Tác Từng Nghiệp Vụ Thực Tế (UI/UX Operational Manual)

Để đảm bảo quy trình trải nghiệm người dùng đạt độ mượt mà tuyệt đối, phần này quy định chi tiết bố cục màn hình và từng bước thao tác chuột/phím cho 8 nghiệp vụ cốt lõi của phần mềm:

### 18.1. Bố Cục Tổng Thể Của Giao Diện (UI Layout Framework)
Cửa sổ DevAgent Desktop được chia thành 4 phân vùng trực quan:
```text
┌────────────────────────────────────────────────────────────────────────────────────────┐
│ [1] THANH TIÊU ĐỀ (HEADER BAR): Tên dự án | Branch Git | 👑 Tech Lead | ⚙️ Cài đặt     │
├────────────────────────────────────────────────────────────────────────────────────────┤
│ [2] THANH QUẢN LÝ TAB (TAB BAR): [Tab 1: Copilot] [Tab 2: Codex] ... [➕ Thêm Tab]    │
├────────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                        │
│ [3] VÙNG NỘI DUNG CHÍNH (MAIN WORKSPACE):                                              │
│     • Khu vực hiển thị Live Terminal Log / Diff Code / Kế hoạch Task                   │
│                                                                                        │
├────────────────────────────────────────────────────────────────────────────────────────┤
│ [4] THANH ĐIỀU KHIỂN & TRẠNG THÁI (FOOTER): CPU/RAM | Đĩa trống | Trạng thái License    │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

---

### 18.2. Nghiệp Vụ 1: Đăng Nhập Google & Kích Hoạt 14 Ngày Dùng Thử
- **Thời điểm**: Lần đầu tiên mở App sau khi cài đặt.
- **Thao tác**:
  1. Màn hình hiện nút: **`[ 🌐 Tiếp Tục Với Google ]`**.
  2. Người dùng click vào nút ➔ Trình duyệt mặc định tự động mở trang Google OAuth chuẩn.
  3. Chọn tài khoản Gmail cá nhân và bấm xác nhận cấp quyền.
  4. Trình duyệt báo thành công ➔ Người dùng quay lại App.
  5. App tự động hiển thị: Tên người dùng, Avatar và Badge xanh: **`🟢 BẢN DÙNG THỬ PRO: CÒN 14 NGÀY`**.

---

### 18.3. Nghiệp Vụ 2: Chọn Thư Mục Dự Án & Khám Sức Khỏe Tự Động (Health Check)
- **Thời điểm**: Sau khi đăng nhập, hoặc khi bấm nút "Đổi dự án" trên Header Bar.
- **Thao tác**:
  1. Bấm nút **`[ 📁 Chọn Thư Mục Dự Án ]`** ➔ Cửa sổ Finder (Mac) hoặc Explorer (Windows) mở ra.
  2. Chọn thư mục dự án (ví dụ `~/Documents/desktop_clinic/desktop`) và bấm Chọn.
  3. Hệ thống tự động kiểm tra trong 1 giây và hiển thị bảng đèn xanh:
     - `[✓] Git Repository: feature/patient-reception` (Hợp lệ, clean state).
     - `[✓] Java JDK 17 & Apache Maven 3.9.6` (Đã tìm thấy trên PATH).
     - `[✓] Dung lượng ổ đĩa: Còn trống 42GB` (Đạt chuẩn an toàn > 3GB).
  4. Bấm nút **`[ TIẾP TỤC ➔ ]`**.

---

### 18.4. Nghiệp Vụ 3: Cấu Hình Tech Lead & Cấp Quân Số Đa Tab (Fleet Matrix)
- **Thời điểm**: Trước khi bắt đầu phiên làm việc, hoặc khi bấm nút **`⚙️ Quản Lý Đội Ngũ`**.
- **Thao tác**:
  1. Tại mục **Tech Lead**: Bấm Dropdown chọn AI chỉ huy (ví dụ: `[ ▼ Claude Code ]`).
  2. Tại mục **Thợ Code Chạy Song Song**:
     - GitHub Copilot: Bấm `[+]` đặt `[ 4 ] Tabs`.
     - OpenAI Codex: Bấm `[+]` đặt `[ 3 ] Tabs`.
     - Google Antigravity: Bấm `[+]` đặt `[ 1 ] Tab`.
  3. Nhìn bảng tổng kết tài nguyên: `Tổng 8 Tabs song song | Ước tính ngốn ~1.2GB RAM`.
  4. Bấm nút **`[ LƯU & KÍCH HOẠT NHÀ XƯỞNG 🚀 ]`**.

---

### 18.5. Nghiệp Vụ 4: Kích Hoạt Tech Lead (ChatGPT Desktop / Claude Desktop Handshake)
- **Thời điểm**: Khi vào Dashboard để bắt đầu dự án.
- **Thao tác**:
  1. Thẻ Activation Card hiển thị đèn nhấp nháy vàng: `🟡 Đang chờ kết nối từ Tech Lead...`.
  2. Người dùng bấm nút: **`[ 📋 SAO CHÉP PROMPT KÍCH HOẠT CHO CHATGPT DESKTOP ]`**.
  3. Mở ứng dụng ChatGPT Desktop (hoặc Claude Desktop) bên cạnh, bấm `Cmd+V` (hoặc `Ctrl+V`) gửi vào.
  4. Trong vòng 2 giây, ChatGPT gọi API `http://127.0.0.1:8765/api/health` ➔ Đèn trên App đổi sang màu **Xanh Lá Cây 🟢**: `👑 Tech Lead: ĐÃ KẾT NỐI (Codex đang điều phối 8 thợ)`.
  5. Người dùng ra lệnh cho ChatGPT: *"Codex ơi, hãy làm tính năng Tiếp Đón Bệnh Nhân theo tài liệu!"*.

---

### 18.6. Nghiệp Vụ 5: Theo Dõi & Quản Lý Các Tab Đang Code Thời Gian Thực
- **Thời điểm**: Trong suốt quá trình các AI đang cày code ngầm.
- **Thao tác**:
  1. **Quan sát tổng thể**: Nhìn thanh Tab Bar thấy trạng thái từng thợ:
     - `[Tab 1: Copilot #1 🟢 Coding]`
     - `[Tab 2: Copilot #2 🟡 Testing (mvn test 80%)]`
     - `[Tab 8: Antigravity 🔵 Chờ duyệt]`
  2. **Xem chi tiết 1 Tab**: Bấm chuột vào `Tab 1` ➔ Vùng chính hiển thị Live Terminal Log xem trực tiếp thợ đang gõ dòng mã nào.
  3. **Soi mã nguồn**: Bấm nút **`[ 🔍 Xem Code Diff ]`** góc phải ➔ Màn hình chia đôi hiển thị dòng cũ (đỏ) và dòng mới (xanh lá).
  4. **Tạm dừng Tab**: Nếu máy nóng, bấm nút **`[ ⏸️ Tạm Dừng ]`** trên Tab đó để đóng băng tạm thời.

---

### 18.7. Nghiệp Vụ 6: Xử Lý Khi Thợ Bị Hết Token (Thợ Cứu Hộ Part-Time Tự Hủy)
- **Thời điểm**: Khi một thợ gặp lỗi Rate Limit 429 cạn kiệt token giữa chừng.
- **Thao tác**:
  1. Tab bị lỗi chuyển sang màu cam `[Tab 1: Claude - Tạm dừng ⚠️]`.
  2. Hộp thoại cứu hộ bật lên hỏi: *"Claude hết token ở mức 70%. Bạn muốn xử lý thế nào?"*.
  3. Người dùng chọn: `(🔘) Tuyển thợ Part-time (Copilot) vá nốt 30% dở dang (Khuyên dùng)`.
  4. Bấm **`[ ÁP DỤNG CỨU HỘ 🚀 ]`**.
  5. Một Tab cứu hộ xuất hiện: `[Tab Cứu Hộ: Copilot-Rescue 🟢]`, nhảy vào viết nốt 30% và chạy test.
  6. Khi test pass 100% ➔ Tab cứu hộ tự động biến mất, quân số tự co về ban đầu!

---

### 18.8. Nghiệp Vụ 7: Hồi Phục Token & Thêm Lại Thợ Vào Đội Hình (Hot-Reload)
- **Thời điểm**: Khi tài khoản Copilot hoặc Claude hồi lại hạn ngạch sau 30-45 phút.
- **Thao tác**:
  1. Bấm nút **`⚙️ Quản Lý Đội Ngũ`** trên thanh Header Bar.
  2. Tích chọn lại ô vuông: `[x] GitHub Copilot`.
  3. Bấm `[+]` đẩy lên `2 Tabs`.
  4. Bấm nút **`[ CẬP NHẬT NÓNG (HOT APPLY) ]`**.
  5. Ngay lập tức trên thanh Tab Bar xuất hiện thêm `Tab 9` và `Tab 10`, nhận ngay task trong hàng đợi để làm tiếp mà không cần khởi động lại App.

---

### 18.9. Nghiệp Vụ 8: Nghiệm Thu Code & Duyệt Gộp Nhánh (PR Approval & Merge)
- **Thời điểm**: Khi một thợ hoàn thành task và vượt qua bài test tự động 100%.
- **Thao tác (2 phương thức)**:
  - **Tự động (Tech Lead duyệt)**: Tech Lead trong ChatGPT Desktop đọc diff qua API, thấy chuẩn ➔ Gọi lệnh duyệt PR ➔ App hiện thông báo: *"Task-101 đã gộp vào main thành công!"*.
  - **Thủ công (Người dùng duyệt)**: Người dùng bấm vào Tab có đèn xanh dương `[Chờ Duyệt 🔵]`, bấm **`[ 🔍 Soi Code Diff ]`**, rồi bấm nút to màu xanh lá: **`[ 🚀 DUYỆT & GỘP VÀO NHÁNH CHÍNH (MERGE TO MAIN) ]`**.
  - Hệ thống tự động xóa worktree tạm thời và dọn thư mục `target/` để tiết kiệm ổ đĩa.

---

## 19. Hệ Thống Ngữ Cảnh Mã Nguồn Cục Bộ: Local Code Context Engine (Hybrid SQLite RAG)

Để cỗ máy đa tác nhân làm việc chính xác tuyệt đối mà không bị "ảo giác" (hallucination) hay viết trùng lặp class/DTO, DevAgent Desktop tích hợp một hệ thống **Truy xuất ngữ cảnh mã nguồn cục bộ (Local Code RAG)** siêu nhẹ, chạy 100% bằng SQLite nội bộ mà **không tốn 1 đồng chi phí API hay cần kết nối Internet**.

### 19.1. Tại Sao Cần RAG Nhưng Tuyệt Đối Tránh RAG Cloud Kiểu Cũ?
* **Rủi ro nếu KHÔNG CÓ RAG**:
  - AI Worker không biết dự án đã có sẵn class gì ➔ Tự viết lại các hàm `DateUtils`, `ApiResponse`, `BaseEntity` trùng lặp, gây xung đột mã nguồn.
  - Phải nhồi nhét quá nhiều file vào prompt ➔ Làm phình to Context Window, khiến tài khoản AI nhanh chóng chạm ngưỡng **Rate Limit (429)**.
* **Tại sao KHÔNG dùng RAG Cloud (Pinecone, Milvus, OpenAI Embeddings)**:
  - Tốn tiền gọi API embedding mỗi khi sửa code.
  - Làm chậm hệ thống (phải upload code lên cloud) và có nguy cơ lộ lọt bí mật mã nguồn dự án của khách hàng.
* 👉 **Giải pháp**: **Hybrid SQLite RAG** — Tận dụng chính cơ sở dữ liệu SQLite cục bộ (`devagent.db`) kết hợp mô hình tìm kiếm kết hợp (Cấu trúc AST + Tìm kiếm toàn văn FTS5).

---

### 19.2. Kiến Trúc 3 Tầng Của Hybrid Local Code RAG

```text
┌────────────────────────────────────────────────────────────────────────┐
│            KIẾN TRÚC HYBRID LOCAL CODE RAG (0Đ CHI PHÍ)                │
├────────────────────────────────────────────────────────────────────────┤
│  TẦNG 1: AST SYMBOL GRAPH (Bản đồ cấu trúc mã nguồn - 0.001s)          │
│  • Tự động trích xuất: Classes, Interfaces, Methods, Fields, DTOs.     │
│  • Lưu vào bảng SQLite `code_symbols`.                                 │
│  • Tra cứu tức thì chữ ký hàm (Method Signatures) mà không cần đọc file│
├────────────────────────────────────────────────────────────────────────┤
│  TẦNG 2: BM25 FULL-TEXT SEARCH (SQLite FTS5 tích hợp sẵn)              │
│  • Sử dụng SQLite Virtual Table `FTS5` (tính năng tìm kiếm có sẵn).    │
│  • Thuật toán xếp hạng BM25 định vị chính xác: Exception, Annotations, │
│    tên biến, chuỗi truy vấn SQL hoặc hàm nghiệp vụ.                   │
├────────────────────────────────────────────────────────────────────────┤
│  TẦNG 3: DYNAMIC CONTEXT INJECTOR (Bộ lọc & Tiêm ngữ cảnh chuẩn xác)  │
│  • Tự động nhặt DTO Contracts do Tech Lead vừa chốt (/api/contracts).  │
│  • Tự động nhặt quy tắc kiến trúc dự án (docs/quy-chuan-code.md).      │
│  • Chỉ tiêm đúng 3-5 snippet code liên quan nhất vào Prompt của Worker.│
└────────────────────────────────────────────────────────────────────────┘
```

---

### 19.3. Cấu Trúc Bảng Dữ Liệu SQLite Cho RAG Cục Bộ

Hệ thống bổ sung 2 bảng chuyên dụng bên trong file `devagent.db` nội bộ:

```sql
-- 1. Bảng lưu trữ cấu trúc định danh mã nguồn (AST Symbols)
CREATE TABLE IF NOT EXISTS code_symbols (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_path TEXT NOT NULL,
    symbol_name TEXT NOT NULL,      -- Ví dụ: 'PatientReceptionDto', 'registerPatient'
    symbol_type TEXT NOT NULL,      -- 'CLASS', 'INTERFACE', 'METHOD', 'RECORD'
    signature TEXT NOT NULL,        -- 'public PatientResponseDto registerPatient(PatientRequestDto req)'
    line_start INTEGER,
    line_end INTEGER,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_symbols_name ON code_symbols(symbol_name);

-- 2. Bảng tìm kiếm toàn văn FTS5 cho các đoạn code và tài liệu
CREATE VIRTUAL TABLE IF NOT EXISTS code_fts USING fts5(
    file_path UNINDEXED,
    content,
    tokenize = 'porter unicode61'
);
```

---

### 19.4. Cơ Chế Đánh Chỉ Mục Gia Số (Incremental Re-Indexing)
- Khi mở dự án lần đầu: App mất khoảng **1.5 giây** để quét toàn bộ codebase và nạp vào SQLite.
- Khi các Worker hoàn thành task và merge code:
  - Hệ thống **chỉ quét lại các file có trong `git diff`** của commit đó.
  - Cập nhật bảng `code_symbols` và `code_fts` trong **0.05 giây**, hoàn toàn không tốn CPU hay làm đơ máy tính!

---

### 19.5. Luồng Tiêm Ngữ Cảnh Tự Động (RAG Injection Sequence)

```mermaid
sequenceDiagram
    autonumber
    actor Lead as Tech Lead (Codex / Claude)
    participant Orch as DevAgent Orchestrator
    participant RAG as Local SQLite RAG Engine
    participant Worker as Worker (Copilot / Antigravity)

    Lead->>Orch: POST /api/tasks { "title": "Viết Service Tiếp Đón", "file_globs": ["*ReceptionService.java"] }
    Orch->>RAG: Truy vấn ngữ cảnh: "Reception", "Patient", "DTO", "Repository"
    RAG->>RAG: Tra cứu AST Symbols + SQLite FTS5 (BM25 Ranking)
    RAG-->>Orch: Trả về: Chữ ký PatientEntity, PatientDto, IReceptionRepository
    Orch->>Orch: Đóng gói Prompt tối ưu (Gốc + DTO Contract + 3 Snippets liên quan)
    Orch->>Worker: Khởi động Worker trong Git Worktree với Context chuẩn xác!
    Worker->>Worker: Code chính xác 100% chữ ký hàm, không bịa tên class mới
    Worker-->>Orch: Hoàn thành task & pass test ngay lần chạy đầu tiên!
```

---

### 19.6. Hiệu Quả Đột Phá Khi Có Local Code RAG:
1. **Tiết kiệm tới 75% Token Context Window**: Thay vì đọc 30 file mò mẫm (~25,000 tokens), AI chỉ cần nhận đúng 3 đoạn code mẫu chuẩn (~2,000 tokens), triệt tiêu nguy cơ dính lỗi Rate Limit 429.
2. **Loại bỏ 100% hiện tượng code trùng lặp**: Mọi class, DTO, mapper đã có trong dự án đều được tái sử dụng chuẩn xác.
3. **Bảo mật tuyệt đối & 0 đồng chi phí**: Toàn bộ chỉ mục nằm trong file SQLite cục bộ trên ổ cứng máy khách, không gửi bất kỳ dòng code nào ra dịch vụ ngoài!










