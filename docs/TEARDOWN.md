# Gỡ bỏ theo từng lớp

Người vận hành chạy các lệnh này. Policy của Kiro Crew chặn agent xoá stack, bucket hay resource. Agent chỉ đo lại sau khi gỡ.

## Thứ tự

1. **Trail (lớp 4) và Lambda metadata (lớp 5b) trước.** Đây là hai thứ đang ghi dữ liệu. Nếu bucket bị xoá trước trail, CloudTrail sẽ thử giao lại log trong 30 ngày và vẫn tính phí cho các event đó (tài liệu CloudTrail, mục "Amazon S3 bucket policy for CloudTrail").
2. **Dashboard và dataset**, để không còn gì đọc các bucket.
3. **Bucket** ở account trung tâm. Dự án không tạo KMS key nào (D9), nên không có key phải chờ xoá.
4. **Bucket policy của khách:** trả về đúng policy lúc khảo sát.
5. **Role chỉ đọc ở account Identity Center.**
6. **Stack CID và sample.**

## 1. Lớp 4: CloudTrail

```bash
# Account khách: gỡ trail
cd infra/roots/customer   # đặt enable_trail = false trong customer.tfvars
terraform plan  -var-file=../../../customers/<slug>/customer.tfvars   # chỉ được xoá aws_cloudtrail
terraform apply -var-file=../../../customers/<slug>/customer.tfvars

# Account trung tâm: bucket, bảng
cd ../observability-customer   # enable_trail_bucket = false
terraform plan  -var-file=../../../customers/<slug>/observability.tfvars
terraform apply -var-file=../../../customers/<slug>/observability.tfvars
```

- Bucket không có `force_destroy`. Nếu còn object, Terraform dừng ở bước xoá bucket. Muốn xoá dữ liệu thật thì làm rỗng bucket bằng tay trước, sau khi đã kiểm thời gian giữ đã cam kết với khách.

## 2. Lớp 5 và 5b: prompt log và metadata

- Lambda metadata (account khách): `enable_prompt_metadata = false` ở root customer, apply. Sau đó `enable_prompt_metadata = false` ở root observability-customer, apply.
- **Bucket prompt log là dữ liệu của khách.** Chỉ gỡ khi khách yêu cầu bằng văn bản. Trước khi gỡ, khách tắt prompt logging trong Kiro console. Sau đó `enable_prompt_logs = false`, apply.

## 3. Dashboard audit

`enable_audit_dashboard = false` ở root observability-customer, apply. View `kiro_<slug>.kiro_request_audit` xoá bằng Athena: `DROP VIEW kiro_<slug>.kiro_request_audit`.

## 4. Bucket policy của bucket report (account khách)

**Không chạy `terraform destroy` cho module `report_bucket_policy`.** Destroy xoá toàn bộ bucket policy, kể cả statement cho `q.amazonaws.com`, và Kiro sẽ không ghi được report nữa. Làm như sau:

```bash
aws s3api put-bucket-policy --bucket <report-bucket> \
    --policy file://customers/<slug>/survey/bucket-policy-report.json --profile <kiro-profile>
cd infra/roots/customer
terraform state rm 'module.report_bucket_policy[0].aws_s3_bucket_policy.this'
```

Agent đo lại bằng `get-bucket-policy`: danh sách `Sid` phải bằng danh sách lúc khảo sát.

## 5. Role đọc Identity Center

`enable_idc_read_role = false` ở root customer, apply. Gỡ sau stack identity-map của sample, vì Lambda đó assume role này mỗi ngày.

## 6. CID và sample (account trung tâm)

- Sample: `scripts/teardown.sh` với cùng `STACK_PREFIX=kiro-<slug>`. Bản sample gốc giữ lại KMS key của identity map sau teardown. Bản trong repo này đã vá P4 nên không tạo key đó.
- Khách bỏ bucket của mình khỏi `KiroSourceBuckets` của Data Collection Stack, cập nhật stack.
- CUR: D2 = chỉ Kiro thì `terraform destroy` ở root `infra/roots/payer`. D2 = toàn bộ thì xoá stack `CID-DataExports-Source` ở account payer. Bỏ account payer khỏi `SourceAccountIds` của stack `CID-DataExports-Destination`.
- Freshness: bỏ khách khỏi `config.customers` của root `infra/roots/shared`, apply. Gỡ hẳn: `terraform destroy` ở root đó.

## 7. Resource thử OQ (task 3.7)

Chép mục này vào `customers/<D7>/teardown-oq.md` lúc chạy thử.

1. Kiro console: tắt prompt logging, hoặc trả về S3 location cũ nếu khách đã có.
2. Gỡ:

```bash
cd infra/experiments/oq
terraform destroy -var account_id=<D7> -var kiro_region=<region>
```

Bucket thử có `force_destroy`, nên destroy xoá cả object. Không có KMS key.

3. Agent đo lại:

```bash
aws cloudtrail describe-trails --region <region> --query "trailList[?Name=='kiro-mon-oq']"   # phải rỗng
aws s3api head-bucket --bucket kiro-mon-trail-oq-<D7>                                     # phải 404
aws s3api head-bucket --bucket oq-kiro-prompt-logs-<D7>                                   # phải 404
```
