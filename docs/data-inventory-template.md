# Danh mục dữ liệu chuyển sang bên vận hành: `<tên khách>`

Mẫu này dùng cho task 1.2. Bản đã điền nằm ở `customers/<khách>/data-inventory.md` (không commit). Bên vận hành gửi khách, khách xác nhận từng lớp bằng văn bản. Lớp nào khách chưa đồng ý thì ghi "không triển khai" và không bật.

Thông tin chung:
- Account trung tâm nhận dữ liệu: `<observability-account-id>`, region `ap-southeast-1`.
- Ngày gửi khách: `<yyyy-mm-dd>`.
- Người nhận phía khách: `<tên, chức vụ>`.

## Hai điều khách phải biết trước khi đồng ý

1. **CUR không lọc chứa toàn bộ chi phí AWS** của mọi account nằm dưới account payer, không chỉ chi phí Kiro. Nếu chọn lọc chỉ Kiro (quyết định D2) thì export chỉ còn các dòng có `line_item_product_code = 'Kiro'`.
2. **Dữ liệu lớp 4 chứa IP nguồn và user agent** của từng request Kiro, tức địa chỉ IP mạng nơi developer làm việc và phiên bản IDE hoặc CLI. Bên vận hành chỉ đưa lên dashboard tên client rút gọn, không đưa IP và toàn chuỗi user agent.

## Bảng theo lớp

| Lớp | Dữ liệu | Field chính | Account nguồn | Account đích | Mục đích | Thời gian giữ ở bên vận hành | Đồng ý (ngày, người) |
|---|---|---|---|---|---|---|---|
| 1. CUR 2.0 | Chi phí và usage theo dòng billing | Account ID, dịch vụ, usage type, số lượng, đơn giá, chi phí, `line_item_resource_id` (với dòng Kiro là Identity Center user ID), tag của resource | Payer `<id>` | Trung tâm | Chi phí Kiro theo tháng, license bị tính tiền | Theo mặc định của CID | |
| 2 và 3. User activity report | Một dòng cho mỗi user, mỗi ngày, mỗi client | `date`, `userid`, `client_type`, `chat_conversations`, `credits_used`, `overage_cap`, `overage_credits_used`, `overage_enabled`, `profileid`, `subscription_tier`, `total_messages`, `new_user`, `email` (chỉ có ở report từ tháng 4/2026), số message theo từng model | Kiro `<id>` | Trung tâm | License bỏ không, mức dùng, model | Theo mặc định của CID và của sample | |
| 3. Identity map | Danh tính của user có hoạt động trong report | `user_id`, `username`, `display_name`, `email` | Identity Center `<id>` | Trung tâm, bucket mã hoá KMS riêng | Hiện tên thay cho GUID trên dashboard | Ghi đè mỗi ngày | |
| 4. CloudTrail data event | Một dòng cho mỗi request Kiro | Thời điểm, tên API, request ID, mã lỗi, user ID, account, region, **IP nguồn**, **user agent** | Kiro `<id>` | Trung tâm, bucket mã hoá KMS | Truy từng request theo user | `<D5, đề xuất 13 tháng>` | |
| 5. Prompt log (toàn văn) | Nội dung prompt và câu trả lời | Toàn văn | Kiro `<id>` | **Không chuyển**. Ở lại account khách | Audit nội bộ của khách | Theo chính sách của khách | |
| 5b. Metadata prompt (chỉ khi D4 = có) | Thông tin mô tả từng request, không có nội dung | Request ID, conversation ID, user ID, thời điểm, độ dài prompt và câu trả lời (số ký tự), số link và số code reference | Kiro `<id>` | Trung tâm | Nối request với phiên chat | `<D5>` | |

Danh sách field lớp 5b là danh sách trắng ở `design.md` mục 3.5. Field nào không có tên trong danh sách thì không ra khỏi account khách.

## Ai ở bên vận hành đọc được dữ liệu

| Dữ liệu | Người hoặc dịch vụ đọc |
|---|---|
| Lớp 1-3 | Lambda thu thập, Athena, QuickSight, người xem dashboard theo danh sách D1 |
| Lớp 4 bảng gốc (có IP) | Chỉ role audit |
| Lớp 4 view đã lược | QuickSight |
| Lớp 5 | Không ai |

## Thu hồi

Khách thu hồi đồng ý cho lớp nào thì bên vận hành gỡ lớp đó theo `docs/TEARDOWN.md` và xoá dữ liệu đã nhận của lớp đó.
