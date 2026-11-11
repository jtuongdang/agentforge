# Quy ước phát triển dự án và làm việc của Agent

Tài liệu này là quy ước nền của DevAgent Orchestrator. Hệ thống tự nạp tài liệu này vào task của Agent trước khi Agent bắt đầu sửa mã. Khi được dùng trực tiếp trong một repository khác, có thể đặt thêm bản sao ở `docs/PROJECT_CONVENTIONS.md`; hướng dẫn riêng của repository được nạp cùng tài liệu nền.

## 1. Thứ tự ưu tiên và cách đọc hướng dẫn

Trước khi sửa file, Agent phải đọc phần **Mandatory project guidance** trong gói task, sau đó kiểm tra các hướng dẫn của repository. Orchestrator tự tìm và đưa nội dung các file phù hợp vào gói task, gồm:

- `AGENTS.md` ở thư mục gốc và các thư mục cha liên quan tới file scope.
- `README.md`, `CONTRIBUTING.md`.
- `docs/PROJECT_CONVENTIONS.md`, `docs/DEVELOPMENT_CONVENTIONS.md`, `docs/README.md`.
- Tài liệu nghiệp vụ/kiến trúc nếu có: `DOMAIN_ANALYSIS.md`, `ERD_DIAGRAM.md`, `UI_CODING_STANDARDS.md`, `ARCHITECTURE.md`.

Nếu hướng dẫn xung đột nhau, ưu tiên theo thứ tự: yêu cầu task và contract đã duyệt → hướng dẫn gần file nhất (`AGENTS.md` trong thư mục con) → quy ước riêng của repository → quy ước nền này. Báo cáo xung đột thay vì tự đoán. Trước khi tạo file, tìm class/file tương tự và dùng lại cấu trúc, cách đặt tên, thư viện và kiểu test hiện có.

Khi hoàn tất, báo danh sách tài liệu hướng dẫn đã được đọc, các file đã tạo/sửa, lệnh kiểm định và kết quả. Nếu tài liệu bắt buộc không có hoặc không đọc được, nêu rõ trong báo cáo.

## 2. Tên file, class, package và symbol

### Java

- Tên package viết chữ thường, không dấu, theo namespace đã khai báo. Thư mục phải khớp package; không tự tạo namespace khác.
- Tên class/interface/enum/record dùng `UpperCamelCase`, ví dụ `BenhNhan`, `SearchPatientsQuery`. Mỗi public top-level type đặt trong file cùng tên, ví dụ `BenhNhan.java`.
- Tên method và biến dùng `lowerCamelCase`; hằng số `static final` dùng `UPPER_SNAKE_CASE` khi đúng convention hiện hữu.
- Test dùng tên tương ứng với type được kiểm thử, ví dụ `BenhNhanTest.java` hoặc convention sẵn có của module. Tên test nêu hành vi/kết quả, không dùng tên mơ hồ như `test1`.
- Tránh hậu tố chung chung như `Manager`, `Helper`, `Util`, `Common` nếu tên không nói rõ trách nhiệm. Dùng hậu tố theo vai trò thực tế và theo project, ví dụ `Repository`, `RepositoryImpl`, `Command`, `Query`, `Handler`, `Panel`.
- Không tạo package `model`/`common` thành nơi chứa mọi loại class. Nhóm type theo feature và vai trò, theo cấu trúc hiện hữu.

### File khác

- Dùng quy ước đang có trong repository. Nếu chưa có, chọn tên dễ tìm, nhất quán với loại nội dung: tài liệu Markdown `UPPER_SNAKE_CASE.md` hoặc style hiện hữu; migration giữ đúng format/version của công cụ migration; asset đặt tên mô tả chức năng.
- Không tạo hai file cùng mục đích với tên gần giống nhau. Không tự đổi tên/xóa file đang được dùng nếu task không yêu cầu.
- Tên file không dấu, không khoảng trắng, không ký tự ngẫu nhiên; chỉ dùng dấu gạch dưới/gạch ngang khi phù hợp convention của stack.

## 3. Tạo file và package theo kiến trúc

- Trước khi thêm file, tìm luồng tương tự và xác định module/layer sở hữu trách nhiệm. Thêm vào đúng module và package; không đặt code nghiệp vụ vào UI hoặc JDBC vào domain.
- Với Clean Architecture, giữ dependency hướng vào trong: domain không phụ thuộc framework/UI/JDBC; application chứa use case và port; infrastructure triển khai persistence/adapter; desktop trình bày và gọi application. Tên module, package và luật cụ thể trong repository luôn được ưu tiên.
- Tách domain model, DTO/request-response, persistence record/entity và view model khi chúng có trách nhiệm hoặc vòng đời khác nhau. Không nhân bản type chỉ để đổi tên.
- Một file nên có một trách nhiệm chính. Không tạo lớp abstraction/interface nếu chỉ có một cách dùng và project không yêu cầu.
- Không chỉnh generated source, `target/`, `build/`, `dist/`, `node_modules/`, file IDE cá nhân hoặc lockfile ngoài phạm vi cần thiết.
- Migration schema phải có version, mô tả rõ, tương thích dữ liệu cũ và dùng đúng công cụ của project. Không sửa migration đã phát hành nếu quy trình yêu cầu migration mới.
- UI desktop phải theo toolkit và `UI_CODING_STANDARDS.md` của project. Giữ thao tác nặng ngoài UI thread, không truy cập DB trực tiếp từ component giao diện.

## 4. Quy ước nghiệp vụ và dữ liệu

- Đọc tài liệu domain, ERD, use case và màn hình thiết kế trước khi tự định nghĩa trường hoặc luồng mới.
- Giữ nguyên ngôn ngữ nghiệp vụ, tên trường, nullable/required, quy tắc định danh, trạng thái và quan hệ như tài liệu hiện hành. Không tự phát minh dữ liệu y tế hoặc quy tắc nghiệp vụ.
- Dữ liệu bệnh nhân/khách hàng dùng trong test phải là dữ liệu tổng hợp. Không đưa dữ liệu thật, thông tin định danh, thông tin đăng nhập hay secret vào source, log, fixture hoặc prompt.
- Validate ở đúng layer; không chỉ dựa vào UI để bảo vệ invariant. Không làm mất dữ liệu hoặc thay đổi schema một cách phá hủy.

## 5. Test, build và chất lượng thay đổi

- Thêm/sửa test tập trung cho hành vi thay đổi; ưu tiên test ổn định, không phụ thuộc dịch vụ ngoài.
- Dùng lệnh build/test đã khai báo trong repository. Với Maven, dùng Maven Wrapper nếu project có, nếu không dùng Maven theo tài liệu project.
- Không tuyên bố kiểm thử thành công nếu chưa chạy; ghi rõ lệnh, kết quả và blocker.
- Giữ thay đổi trong file globs được giao. Nếu cần mở rộng scope, dừng trước khi sửa ngoài scope và yêu cầu operator tạo/cập nhật task.
- Không tự cài dependency, chạy migration lên database thật, gọi production, push, merge, force-push, xóa branch/worktree hoặc xóa lịch sử.

## 6. Format báo cáo khi hoàn tất

Agent cần báo ngắn gọn:

1. Tài liệu hướng dẫn đã đọc.
2. File tạo/sửa và lý do.
3. Quy tắc nghiệp vụ/kiến trúc đã áp dụng.
4. Lệnh kiểm định và kết quả thực tế.
5. Acceptance criteria đạt/chưa đạt và blocker còn lại.
