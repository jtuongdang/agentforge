# DevAgent Orchestrator — Kế hoạch hoàn thiện

> Trạng thái khảo sát: 2026-11-04. Đây là kế hoạch đề xuất dựa trên code hiện tại. Clinic Desktop chỉ là repository mẫu; mục tiêu sản phẩm là điều phối Agent cho nhiều Git repository.

## 1. Mục tiêu sản phẩm

Xây dựng công cụ local trước, cho phép một developer:

- Đăng ký nhiều Git repository local và chọn project cho từng task.
- Giao task có mục tiêu, tiêu chí nghiệm thu, phạm vi file và Agent phù hợp.
- Chạy Agent trong worktree riêng; nhiều task độc lập có thể chạy song song.
- Kiểm tra thay đổi, chạy bộ kiểm định phù hợp với project và lưu lịch sử.
- Chỉ push và tạo Draft PR sau khi người dùng duyệt; không tự merge.

Không đặt mục tiêu chạy mô hình AI trên máy. Các CLI Agent dùng dịch vụ của nhà cung cấp; Orchestrator quản lý lịch chạy, worktree, validation và trạng thái.

## 2. Trạng thái hiện tại

| Năng lực | Hiện trạng |
|---|---|
| Nhiều repository | Đăng ký Git repository local qua dashboard; startup không còn tự giả định project Clinic |
| Agent registry | Codex, Copilot, Antigravity CLI, generic CLI và IDE handoff; điều phối theo capability và concurrency |
| Cách ly code | Một worktree/branch cho mỗi task; kiểm tra phạm vi file trước khi commit |
| Điều phối task | Một task được giao cho một Agent; các task độc lập có thể chạy đồng thời; có priority và acceptance criteria |
| Validation | Tự phát hiện Maven root `pom.xml`; repository khác có thể cấu hình custom argv command; chưa có auto-detect profiles riêng cho Gradle/Node/Python |
| Review/publish | Diff patch (tối đa 250 KB) hiển thị trước khi duyệt; có thể hoàn tất local hoặc duyệt push/Draft PR trên GitHub |
| Restart safety | Task đang chạy được đánh dấu interrupted/failed; tác vụ đang publish bị block để operator kiểm tra remote; worktree được giữ lại |
| GitHub | Push và tạo Draft PR có bước duyệt; publish bị từ chối trước khi push với remote không phải GitHub |
| Lưu trữ | SQLite local; có task, repository, Agent, contract, run và event |
| Người dùng | Một operator local; chưa có tài khoản, role hoặc quyền truy cập nhiều người |
| Chi phí | Có thể kiểm tra credential/quota một số Agent; chưa có báo cáo chi phí/token thống nhất hoặc ngân sách task |

## 3. Quyết định nghiệp vụ đề xuất cho MVP

Các điểm này là giả định đề xuất để triển khai tiếp; cần được chốt trước khi xây tính năng liên quan.

| Chủ đề | Đề xuất ban đầu | Khi nào xem xét lại |
|---|---|---|
| Người dùng | Một operator local; không mở API ra mạng LAN/Internet | Khi nhiều người cần dùng chung dashboard |
| Giao việc Agent | Một Agent thực thi một task; planner có thể đề xuất task con nhưng operator duyệt kế hoạch trước | Khi cần tự động chia việc thường xuyên và đã có dữ liệu chất lượng đủ tin cậy |
| Chạy song song | Chỉ chạy task có phạm vi file độc lập; task dùng chung API phải qua contract | Khi có chiến lược xử lý xung đột và kiểm thử tích hợp |
| Project đầu tiên | Maven/Java ổn định trước; sau đó thêm Node/TypeScript, Python hoặc Gradle theo nhu cầu thật | Mỗi stack chỉ được bật khi validator và fixture E2E đạt |
| Git provider | GitHub là provider duy nhất trong MVP | Khi có repository GitLab/Bitbucket thực tế cần Draft/Merge Request |
| Duyệt thay đổi | Operator xem diff và validation, sau đó duyệt push/Draft PR; merge luôn do người dùng thực hiện | Nếu tổ chức yêu cầu reviewer/approval nhiều cấp |
| Chi phí | Hiển thị dữ liệu provider thực sự cung cấp; không ước lượng nếu thiếu số liệu | Chốt giới hạn theo tài khoản, project và task khi billing API/provider hỗ trợ |
| Dữ liệu | SQLite trên máy; không ghi token bí mật vào task/log; chưa tự xóa lịch sử | Chốt retention/backup trước khi dùng lâu dài hoặc cho team |

## 4. Các đợt triển khai

### Đợt 0 — Chốt yêu cầu và chuẩn hóa nghiệp vụ

**Trạng thái:** hoàn tất theo quyết định MVP trong tài liệu này: local single-user, một Agent/task, SQLite, GitHub, review thủ công trước publish, không auto-merge/xóa lịch sử.

**Mục tiêu:** tránh xây sai workflow trước khi mở rộng kỹ thuật.

**Đầu ra:**

- Chốt phạm vi local single-user cho MVP.
- Định nghĩa task: mục tiêu, tiêu chí nghiệm thu, file scope, priority, retry và người duyệt.
- Định nghĩa trạng thái cuối: thành công local, chờ duyệt Draft PR, thất bại, bị chặn, hủy.
- Chốt GitHub là provider đầu tiên và Maven là validator đầu tiên.
- Chốt cách lưu prompt/log, backup SQLite và việc không tự động xóa lịch sử.

**Tiêu chí hoàn thành:** có checklist nghiệm thu MVP; mỗi trạng thái và hành động nhạy cảm có chủ sở hữu rõ ràng.

### Đợt 1 — Hoàn thiện workflow single-user local

**Trạng thái:** luồng MVP local đã hoàn tất ở mức operator đơn: acceptance criteria, priority scheduler, chặn task giao phạm vi, diff preview, hoàn tất local, kiểm tra branch trước khi enqueue và reconciliation sau restart. Acceptance criteria hiện là hướng dẫn có evidence trong báo cáo Agent, chưa tự động chấm từng tiêu chí nghiệp vụ.

**Mục tiêu:** biến luồng tạo task → chạy Agent → kiểm tra → duyệt thành luồng sử dụng được hằng ngày.

**Đầu ra:**

- Form task có tiêu chí nghiệm thu, project, Agent, file globs, dependencies và retry.
- Hiển thị patch trước khi duyệt push; preview bị giới hạn 250 KB để bảo vệ dashboard.
- Sửa/hủy task an toàn, log rõ lý do chờ hoặc lỗi, khôi phục trạng thái queued/running sau khi backend khởi động lại.
- Xác nhận repository/branch còn hợp lệ trước khi enqueue.
- UI diễn giải rõ quyền và tác động của kiểm tra Copilot, push, tạo Draft PR và lệnh CLI.

**Tiêu chí hoàn thành:** operator có thể kiểm tra trạng thái, tiêu chí, diff, validation và chọn hoàn tất local hoặc duyệt Draft PR mà không cần đọc log terminal. Đạt cho local single-user MVP; chưa có auto-grade acceptance criteria.

### Đợt 2 — Validation đa stack theo cấu hình project

**Trạng thái:** đã tách entry point validation khỏi engine Maven, tự nhận diện Maven và cho phép cấu hình lệnh argv theo repository; lệnh không chạy qua shell, có timeout, giới hạn output và redaction secret cơ bản. Chưa có auto-detect profile riêng cho Gradle/Node/Python.

**Mục tiêu:** cho phép dùng repository không phải Maven mà vẫn có kiểm định phù hợp.

**Đầu ra:**

- Tạo abstraction `Validator`/`ValidationProfile`, tách khỏi task engine.
- Project khai báo profile/lệnh kiểm định; ưu tiên argv tách phần tử, không chạy shell tùy ý.
- Hỗ trợ lần lượt Maven, Gradle, Node/TypeScript và Python khi có nhu cầu.
- Log command, thời gian, exit code và summary; giới hạn timeout và dung lượng output.
- Không tự động cài dependency hoặc chạy script chưa được policy cho phép.

**Tiêu chí hoàn thành:** fixture repository cho từng stack chạy build/test thành công; profile sai hoặc thiếu tool báo blocker cụ thể, không đánh dấu thành công giả.

### Đợt 3 — Planner và phối hợp nhiều Agent

**Mục tiêu:** chia task lớn thành các phần có thể thực thi độc lập mà vẫn kiểm soát contract và xung đột.

**Đầu ra:**

- Planner tạo proposal gồm task cha/con, phụ thuộc, file scope, capability và acceptance criteria.
- Operator duyệt/chỉnh proposal trước khi scheduler dispatch.
- Mỗi task con tiếp tục có worktree riêng; contract chung được duyệt trước khi các task phụ thuộc chạy.
- Scheduler phát hiện glob giao nhau và yêu cầu contract hoặc chạy tuần tự.
- Tổng hợp báo cáo task con cho task cha; không để một Agent tự ghi vào worktree của Agent khác.

**Tiêu chí hoàn thành:** E2E chia một thay đổi nhỏ thành ít nhất hai task độc lập, chạy song song, validation riêng và có báo cáo tổng hợp; task giao nhau bị chặn đúng cách.

### Đợt 4 — Quan sát quota, chi phí và chính sách dữ liệu

**Trạng thái:** token check có với một số CLI/provider; không lưu raw token. Health tổng hợp trạng thái SQLite/scheduler/Agent, nhưng chưa có usage/chi phí chuẩn hóa hoặc retention/export.

**Mục tiêu:** giúp operator dự đoán giới hạn và hiểu dữ liệu nào được lưu.

**Đầu ra:**

- Chuẩn hóa `usage` theo Agent: trạng thái xác thực, quota, timestamp và nguồn dữ liệu.
- Tách số liệu thật từ provider khỏi ước lượng; không lưu token thô.
- Cảnh báo thao tác có thể tiêu thụ request hoặc quota; ghi lại người khởi phát và kết quả.
- Thêm cấu hình retention, backup/restore và export lịch sử; không xóa dữ liệu tự động trước khi có chính sách được duyệt.
- Rà soát redaction, quyền đọc task/log và lưu ý dữ liệu source code gửi tới nhà cung cấp AI.

**Tiêu chí hoàn thành:** operator có thể trả lời task nào chạy với Agent nào, lúc nào, usage nào được provider báo cáo và dữ liệu task được lưu ở đâu.

### Đợt 5 — Git provider và phát hành

**Trạng thái:** GitHub Draft PR là provider path duy nhất; remote được kiểm tra trước publish và thao tác cần operator approval. Tách `GitProvider` và kiểm thử E2E trên GitHub thật còn để sau; không thêm provider hoặc tự merge trong MVP.

**Mục tiêu:** củng cố luồng review/phát hành trước khi thêm provider mới.

**Đầu ra:**

- Tách interface `GitProvider` khỏi GitHub PR manager.
- Hoàn thiện GitHub: kiểm tra remote/base branch, diff, checks và trạng thái Draft PR.
- Chỉ thêm GitLab/Bitbucket khi có nhu cầu xác thực và acceptance criteria cụ thể.
- Giữ push/PR sau xác nhận rõ ràng; không thêm auto-merge vào MVP.

**Tiêu chí hoàn thành:** test adapter bằng repository fixture; E2E GitHub tạo Draft PR chỉ sau approval và không merge.

### Đợt 6 — Bảo mật vận hành và phát hành local

**Trạng thái:** health kiểm tra SQLite và báo trạng thái scheduler/task/repository/Agent; dashboard Storage hiển thị SQLite/WAL, worktree/build, dữ liệu handoff, số lượng bản ghi và dung lượng ổ đĩa (chỉ đọc, làm mới 30 giây). Đã có script snapshot/verify/restore SQLite; restore ghi sang file mới, không ghi đè. Chưa có log rotation, auto-start macOS hoặc quy trình phát hành/cập nhật tự động.

**Mục tiêu:** giảm lỗi khi chạy Orchestrator lâu dài trên máy developer.

**Đầu ra:**

- Health/status cho API, scheduler, SQLite và Agent CLI.
- Sao lưu SQLite có hướng dẫn phục hồi; kiểm tra đường dẫn worktree sau khi restart.
- Hướng dẫn cài đặt/cập nhật, log rotation và tắt sạch tiến trình.
- Tùy chọn tự khởi chạy sau đăng nhập macOS, có nút dừng và trạng thái rõ ràng.
- Kiểm tra dependency, phân quyền localhost và không expose API ra mạng ngoài mặc định.

**Tiêu chí hoàn thành:** restart ứng dụng không mất task/history, không chạy trùng task và operator biết cách khôi phục từ backup.

### Đợt 7 — Team mode (chỉ khi có nhu cầu)

**Mục tiêu:** chuyển từ công cụ local cá nhân thành dịch vụ dùng chung.

**Đầu ra cần thiết:** đăng nhập, role/RBAC, audit, quản lý secret, project-level access, shared deployment, backup/restore, migration SQLite sang database client-server và quản lý concurrency đa tiến trình.

**Khuyến nghị:** không đưa MongoDB vào chỉ để thay SQLite. Khi có nhiều máy ghi vào cùng dữ liệu, đánh giá PostgreSQL trước vì mô hình task/repository/run/contract/event có quan hệ và cần truy vấn/giao dịch nhất quán.

**Tiêu chí hoàn thành:** threat model, phân quyền, backup, concurrent scheduler và quy trình vận hành được kiểm thử trước khi onboarding người dùng.

## 5. Thứ tự ưu tiên đề xuất

1. **P0:** Đợt 0 + phần review diff/restart safety của Đợt 1.
2. **P0:** Đợt 2 cho các stack thực sự cần dùng; bắt đầu với Maven, sau đó chọn stack kế tiếp dựa trên repository người dùng đăng ký.
3. **P1:** Đợt 3, nhưng chỉ bật planner chia task sau khi contract, scope conflict và báo cáo tổng hợp có acceptance test.
4. **P1:** Đợt 4 để quản lý credential/quota và dữ liệu source code.
5. **P2:** Đợt 5 mở rộng provider; GitHub vẫn là provider MVP.
6. **P2/điều kiện:** Đợt 6 củng cố local operations; Đợt 7 chỉ triển khai khi chuyển sang nhiều người dùng.

## 6. Cổng nghiệm thu chung

- Unit/integration test chạy với dữ liệu tổng hợp; không dùng dữ liệu thật hoặc token thật.
- Mỗi task chỉ sửa file trong scope; diff, secret scan, validation và trạng thái approval hiển thị được.
- Hai task độc lập chạy trong hai worktree/branch riêng; task xung đột không được dispatch đồng thời.
- Lỗi Agent, timeout, restart API hoặc thiếu CLI không làm task bị báo thành công.
- Không push, tạo PR, merge, xóa worktree hoặc xóa lịch sử nếu chưa có luồng/phê duyệt tương ứng.
- Tài liệu nêu rõ khả năng đang hỗ trợ, giới hạn, dữ liệu lưu và hướng dẫn khôi phục.

## 7. Các quyết định cần chủ dự án chốt trước khi làm roadmap

1. MVP tiếp tục chỉ dành cho một người trên Mac hay cần nhiều người dùng ngay?
2. Sau Maven, project thực tế tiếp theo cần validator cho stack nào?
3. Có cần planner tự chia task và chạy nhiều Agent trên một feature trong MVP không, hay chỉ cần chạy nhiều task do người dùng tạo?
4. Chỉ GitHub hay cần GitLab/Bitbucket?
5. Cần giữ log/prompt trong bao lâu, và có được gửi mã nguồn riêng tư tới các nhà cung cấp Agent không?
6. Có ngân sách/giới hạn request theo task hoặc theo ngày không?

Cho tới khi các câu trả lời này thay đổi, lựa chọn ít rủi ro nhất là hoàn thiện local single-user, giữ SQLite, GitHub, một Agent trên mỗi task và bổ sung validator theo repository thực tế.
