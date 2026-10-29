"""Draw.io diagrams for .kiro/specs/kiro-customer-monitoring (design only, nothing deployed).

Run: python build_architecture.py
  -> writes kiro-customer-monitoring-chi-tiet.drawio (2 pages) next to this file and prints the geometry check.
  kiro-customer-monitoring.drawio is maintained by hand in draw.io and is never written by this script;
  `--compact <other path>` writes the compact starting layout it was drawn from.
Every component and number here comes from design.md of the spec. Unverified items are red notes.
"""
import os
import sys
from xml.sax.saxutils import escape

from drawio_lib import Diagram

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "kiro-customer-monitoring.drawio")
OUT_DETAIL = os.path.join(HERE, "kiro-customer-monitoring-chi-tiet.drawio")


class KDiagram(Diagram):
    """Diagram whose notes keep explicit line breaks: a raw newline inside an XML attribute is
    normalised to a space on load, so it must become an HTML <br> in the label value."""

    def note(self, key, parent, x, y, w, h, color, text):
        super().note(key, parent, x, y, w, h, color, text)
        cid, val, *rest = self.cells[-1]
        self.cells[-1] = (cid, val.replace("\n", "<br>"), *rest)

    def frame(self, key, label, x, y, w, h, parent):
        """Plain pink frame for a logical component (CID module, sample stack), like the
        Step Functions frames of the reference diagram but without a service icon."""
        gid = self._id("g")
        g = self.groups[parent]
        style = ("rounded=0;whiteSpace=wrap;html=1;fillColor=none;strokeColor=#CD2264;fontColor=#CD2264;"
                 "fontSize=13;fontStyle=1;verticalAlign=top;align=left;spacingLeft=8;spacingTop=2;"
                 "container=1;collapsible=0;recursiveResize=0;")
        self.cells.append((gid, escape(label), style, x - g[1], y - g[2], w, h, g[0]))
        self.groups[key] = (gid, x, y, w, h)

    def edge(self, src, tgt, exit_, entry, points=(), dashed=False, num=None, badge_at=None):
        """Same as Diagram.edge, but badge_at=(x, y) puts the number badge at that point instead of
        the middle of the longest segment (used where two lines cross)."""
        if badge_at is None or num is None:
            return super().edge(src, tgt, exit_, entry, points, dashed, num)
        super().edge(src, tgt, exit_, entry, points, dashed, None)
        s, t, ex, en, pts, da, _ = self.edges[-1]
        self.edges[-1] = (s, t, ex, en, pts, da, num)
        cx, cy, b = badge_at[0], badge_at[1], 24
        style = ("ellipse;whiteSpace=wrap;html=1;aspect=fixed;fillColor=#232F3E;strokeColor=#FFFFFF;"
                 "strokeWidth=2;fontColor=#FFFFFF;fontStyle=1;fontSize=11;")
        self.cells.append((self._id("b"), str(num), style, cx - b / 2, cy - b / 2, b, b, "1"))
        self.boxes.append((f"badge{num}", "badge", cx - b / 2, cy - b / 2, b, b, None))

    def big_title(self, key, x, y, w, h, lines):
        style = ("text;html=1;align=center;verticalAlign=middle;fontSize=44;fontColor=#232F3E;fontStyle=1;"
                 "whiteSpace=nowrap;")
        self.cells.append((self._id("x"), "<br>".join(lines), style, x, y, w, h, "1"))
        self.boxes.append((key, "text", x, y, w, h, None))


def compact():
    """One page in the style of the reference diagram: short labels, numbered arrows, legend below."""
    d = KDiagram("Kiro Enterprise Monitoring", 1160, 1200)
    # ---- customer ----
    d.group("accC", "Account khách hàng", 20, 20, 400, 870, "account")
    d.group("rC", "us-east-1 / eu-central-1", 40, 50, 360, 330, "region", "accC")
    d.icon("dev", "rC", 90, 150, None, None, ["Developer"], shape="user")
    d.icon("kiro", "rC", 210, 150, "codewhisperer", "ml", ["Kiro"], pos="top")
    d.icon("rpt", "rC", 340, 150, "s3", "storage", ["Amazon S3", "report"])
    d.icon("plog", "rC", 90, 300, "s3", "storage", ["Amazon S3", "prompt log"])
    d.icon("trail", "rC", 210, 300, "cloudtrail", "mgmt", ["AWS CloudTrail", "data event"])
    d.icon("idc", "accC", 100, 580, "single_sign_on", "security", ["IAM Identity", "Center"])
    d.icon("role", "accC", 300, 580, "identity_and_access_management", "security", ["IAM role", "chỉ đọc"])
    d.icon("cur", "accC", 300, 760, "cost_and_usage_report", "mgmt", ["Data Exports", "CUR 2.0"])
    # ---- observability ----
    d.group("accR", "Account trung tâm", 450, 20, 690, 870, "account")
    d.group("rR", "ap-southeast-1", 470, 50, 650, 820, "region", "accR")
    d.frame("fCid", "CID Data Collection", 490, 90, 270, 140, "rR")
    d.icon("col", "fCid", 550, 150, "lambda", "compute", ["Lambda", "collector"])
    d.icon("dcb", "fCid", 690, 150, "s3", "storage", ["Amazon S3", "Data Collection"])
    d.icon("ctb", "rR", 690, 300, "s3", "storage", ["Amazon S3", "CloudTrail"])
    d.frame("fSmp", "Sample QuickSight (lớp 3)", 490, 380, 270, 280, "rR")
    d.icon("nor", "fSmp", 550, 450, "lambda", "compute", ["Lambda", "normalizer"])
    d.icon("res", "fSmp", 690, 450, "s3", "storage", ["Amazon S3", "kết quả"])
    d.icon("idm", "fSmp", 550, 580, "lambda", "compute", ["Lambda", "identity map"])
    d.icon("idb", "fSmp", 690, 580, "s3", "storage", ["Amazon S3", "identity map"])
    d.icon("dex", "rR", 690, 760, "s3", "storage", ["Amazon S3", "CUR"])
    d.icon("ath", "rR", 880, 450, "athena", "analytics", ["Amazon Athena", "Glue tables"])
    d.icon("qs", "rR", 1050, 450, "quick_suite", "analytics", ["Amazon Quick", "SPICE"], pos="top")
    d.icon("dash", "rR", 1050, 620, "quick_suite", "analytics", ["Dashboards"])
    d.icon("viewer", "rR", 880, 620, None, None, ["Người xem"], shape="user")
    d.icon("fresh", "rR", 880, 780, "lambda", "compute", ["Lambda", "kiểm độ tươi"])
    d.icon("sns", "rR", 1050, 780, "sns", "integ", ["Amazon SNS"])
    # ---- edges, numbered in reading order ----
    d.edge("dev", "kiro", (1, 0.5), (0, 0.5))
    d.edge("kiro", "rpt", (1, 0.5), (0, 0.5), num=1)
    d.edge("col", "rpt", (0, 0.5), (1, 0.5), dashed=True, num=2)
    d.edge("col", "dcb", (1, 0.5), (0, 0.5), num=3)
    d.edge("nor", "rpt", (0, 0.5), (1, 0.75), points=[(435, 450), (435, 165)], dashed=True, num=4,
           badge_at=(435, 380))
    d.edge("nor", "res", (1, 0.5), (0, 0.5), num=5)
    d.edge("idm", "role", (0, 0.5), (1, 0.5), dashed=True, num=6)
    d.edge("role", "idc", (0, 0.5), (1, 0.5), num=7)
    d.edge("idm", "idb", (1, 0.5), (0, 0.5), num=8)
    d.edge("kiro", "trail", (0.5, 1), (0.5, 0), num=9)
    d.edge("trail", "ctb", (1, 0.5), (0, 0.5), num=10, badge_at=(560, 300))
    d.edge("kiro", "plog", (0, 0.75), (1, 0.5), points=[(150, 165), (150, 300)], num=11)
    d.edge("cur", "dex", (1, 0.5), (0, 0.5), num=12)
    for src, y in (("dcb", 150), ("ctb", 300), ("idb", 580)):
        d.edge(src, "ath", (1, 0.5), (0, 0.5), points=[(800, y), (800, 450)])
    d.edge("res", "ath", (1, 0.5), (0, 0.5))
    d.edge("dex", "ath", (1, 0.5), (0, 0.5), points=[(800, 760), (800, 450)], num=13)
    d.edge("ath", "qs", (1, 0.5), (0, 0.5), num=14)
    d.edge("qs", "dash", (0.5, 1), (0.5, 0), num=15)
    d.edge("viewer", "dash", (1, 0.5), (0, 0.5), num=16)
    d.edge("fresh", "sns", (1, 0.5), (0, 0.5), num=17)
    # ---- legend and title ----
    items = [
        "1  Kiro ghi report CSV mỗi ngày lúc 02:00 UTC (prefix kiro/, SSE-S3).",
        "2  Lambda collector của CID kéo report lúc 03:00 UTC.",
        "3  Collector ghi report vào bucket Data Collection, chia theo account_id.",
        "4  Lambda normalizer của sample kéo report lúc 03:15 UTC (bản vá P1, P2).",
        "5  Normalizer ghi bản đã chuẩn hoá vào bucket kết quả.",
        "6  Lambda identity map assume role chỉ đọc của khách (ExternalId, bản vá P3).",
        "7  Role đọc danh sách user từ IAM Identity Center.",
        "8  Tên và email user được ghi vào bucket identity map.",
        "9  Kiro sinh data event cho từng request (chưa xác minh, OQ-1).",
        "10  Trail giao log sang bucket CloudTrail của bên vận hành.",
        "11  Prompt log (toàn văn) ở lại account khách, bên vận hành không đọc.",
        "12  Data Exports ở account payer giao CUR 2.0 sang bên vận hành.",
        "13  Athena đọc các bucket qua bảng Glue.",
        "14  SPICE refresh lúc 04:00 UTC.",
        "15  Dashboards: CUDOS (chi phí), CID Kiro (license), Sample (mức dùng), Audit (request).",
        "16  Người xem phía vận hành mở dashboard.",
        "17  Lambda kiểm độ tươi chạy lúc 05:00 UTC, gửi cảnh báo qua SNS khi dữ liệu trễ.",
        "Nét đứt: Lambda ở bên vận hành chủ động đọc tài nguyên của khách.",
        "Sample QuickSight: repo aws-samples/sample-kiro-user-analytics-quicksight-dashboard, mỗi khách một bản.",
        "Account payer, account Kiro và account Identity Center của khách có thể là các account khác nhau.",
    ]
    d.text("legend", None, 40, 920 + len(items) * 13 / 2, items, size=10, align="left")
    d.big_title("bigtitle", 690, 960, 440, 120, ["Kiro Enterprise", "Monitoring"])
    return d


def architecture():
    d = KDiagram("1. Kiến trúc và luồng dữ liệu", 2560, 1580)
    d.text("title", None, 40, 35, ["Theo dõi Kiro của khách hàng, dashboard tập trung ở account trung tâm "
                                   "(thiết kế theo spec kiro-customer-monitoring, chưa deploy, 29/10/2026)"],
           size=16, bold=True, align="left")

    # ---------------- customer: payer ----------------
    d.group("accPay", "Account payer của khách (management account)", 20, 70, 900, 250, "account")
    d.note("n_cur", "accPay", 40, 110, 560, 150, "red",
           "D2 chưa chốt: lấy toàn bộ CUR, hay chỉ các dòng line_item_product_code = 'Kiro' bằng WHERE trong "
           "query của export. Lấy toàn bộ thì bên vận hành thấy mọi chi phí AWS của khách. Chưa xác minh (OQ-7): "
           "CUDOS và CID có chạy đúng trên CUR đã lọc không. OQ-10: export phải bật resource ID, vì CID đếm "
           "license theo Identity Center user ID trong line_item_resource_id. Muốn có quá khứ thì payer mở "
           "Support case xin backfill, tối đa 14 tháng.")
    d.icon("cur", "accPay", 760, 190, "cost_and_usage_report", "mgmt",
           ["AWS Data Exports", "CUR 2.0 (stack", "CID-DataExports-Source)"])

    # ---------------- customer: Kiro account ----------------
    d.group("accKiro", "Account Kiro của khách (nơi user subscribe Kiro)", 20, 340, 900, 820, "account")
    d.group("rK", "us-east-1 hoặc eu-central-1 (region của Kiro profile)", 40, 380, 860, 760, "region", "accKiro")
    d.icon("dev", "rK", 110, 480, None, None, ["Developer", "Kiro IDE, Kiro CLI"], shape="user")
    d.icon("kiro", "rK", 290, 480, "codewhisperer", "ml", ["Kiro profile", "namespace codewhisperer"])
    d.icon("rpt", "rK", 600, 480, "s3", "storage",
           ["S3 bucket report", "<bucket>/kiro/AWSLogs/...", "SSE-S3, khác bucket prompt"])
    d.note("n_rpt", "rK", 470, 585, 410, 120, "grey",
           "Đã đọc trong tài liệu và source CID: bucket phải ở account và region của Kiro profile. Prefix kiro/ "
           "là bắt buộc khi deploy qua Data Collection Stack. CID không hỗ trợ SSE-KMS. Bucket policy chỉ cho "
           "GetObject trên kiro/* và ListBucket có điều kiện prefix kiro/*.")
    d.icon("kmsC", "rK", 290, 760, "key_management_service", "security", ["KMS key của khách", "mã hoá prompt log"])
    d.icon("plog", "rK", 600, 760, "s3", "storage",
           ["S3 bucket prompt log", "toàn văn, ở lại khách", "SSE-KMS, bên vận hành không đọc"])
    d.icon("meta", "rK", 800, 760, "lambda", "compute", ["Lambda metadata", "(tuỳ chọn, D4)"], faded=True)
    d.icon("trail", "rK", 600, 960, "cloudtrail", "mgmt", ["CloudTrail trail", "kiro-mon-audit", "chỉ data event"])
    d.note("n_oq1", "rK", 680, 1000, 210, 110, "red",
           "Chưa xác minh (OQ-1): Kiro có sinh data event AWS::CodeWhisperer::Profile không. Nếu không, bỏ lớp 4 "
           "và dashboard audit.")
    d.icon("audC", "rK", 290, 1040, "athena", "analytics",
           ["Athena + role", "kiro-prompt-auditor", "(của khách)"])

    d.edge("dev", "kiro", (1, 0.5), (0, 0.5))
    d.edge("kiro", "rpt", (1, 0.5), (0, 0.5), num=2)
    d.text("t_rpt", None, 445, 455, ["02:00 UTC ghi CSV"])
    d.edge("kiro", "plog", (1, 0.75), (0, 0.5), points=[(430, 495), (430, 760)], num=10)
    d.text("t_plog", None, 500, 745, ["ghi prompt log"])
    d.edge("kiro", "trail", (0, 0.75), (0, 0.5), points=[(200, 495), (200, 960)], num=8)
    d.text("t_devt", None, 385, 945, ["ghi data event"])
    d.edge("plog", "meta", (1, 0.5), (0, 0.5), dashed=True, num=11)

    # ---------------- customer: Identity Center ----------------
    d.group("accIdc", "Account Identity Center của khách (có thể là payer)", 20, 1180, 900, 260, "account")
    d.icon("idc", "accIdc", 290, 1300, "single_sign_on", "security", ["IAM Identity Center", "Identity Store d-..."])
    d.icon("idcRole", "accIdc", 600, 1300, "identity_and_access_management", "security",
           ["IAM role chỉ đọc", "kiro-mon-idc-read-<khách>", "ListUsers, bắt buộc ExternalId"])
    d.edge("idc", "idcRole", (1, 0.5), (0, 0.5))

    # ---------------- observability ----------------
    d.group("accR", "Account trung tâm (nhận dữ liệu, đặt dashboard)", 960, 70, 1580, 1490, "account")
    d.group("rR", "ap-southeast-1 (Singapore), nơi QuickSight đang chạy", 980, 110, 1540, 1430, "region", "accR")
    # landing buckets (column 1350) and pullers (column 1100)
    d.icon("dex", "rR", 1350, 190, "s3", "storage", ["S3 data-exports", "cid-cur2/<payer>/..."])
    d.icon("col", "rR", 1100, 465, "lambda", "compute",
           ["Lambda collector CID", "03:00 UTC, kéo report", "CID-DC-kiro-user-activity"])
    d.icon("dcb", "rR", 1350, 465, "s3", "storage",
           ["S3 Data Collection", "kiro-user-activity/", "account_id=.../region=..."])
    d.icon("nor", "rR", 1100, 620, "lambda", "compute", ["Lambda normalizer", "sample, 03:15 UTC", "đã vá P1, P2"])
    d.icon("res", "rR", 1350, 620, "s3", "storage", ["S3 kết quả sample", "normalized/facts, models"])
    d.icon("pmb", "rR", 1350, 760, "s3", "storage", ["S3 metadata prompt", "(tuỳ chọn, D4)", "chỉ field cho phép"],
           faded=True)
    d.icon("ctb", "rR", 1350, 960, "s3", "storage",
           ["S3 CloudTrail khách", "AWSLogs/<id>/CloudTrail/", "SSE-KMS key bên vận hành"])
    d.icon("kmsR", "rR", 1350, 1110, "key_management_service", "security", ["KMS key bên vận hành", "cho bucket CloudTrail"])
    d.icon("idm", "rR", 1100, 1300, "lambda", "compute",
           ["Lambda identity map", "03:30 UTC, đã vá P3", "AssumeRole + ExternalId"])
    d.icon("idb", "rR", 1350, 1300, "s3", "storage", ["S3 identity map", "SSE-KMS (key của sample)", "tên, email user"])
    # catalog, query, BI
    d.icon("glue", "rR", 1650, 330, "glue", "analytics",
           ["AWS Glue Data Catalog", "optimization_data, kiro_<khách>"], pos="right")
    d.icon("ath", "rR", 1650, 620, "athena", "analytics", ["Amazon Athena", "view kiro_request_audit"])
    d.icon("qs", "rR", 1950, 620, "quick_suite", "analytics", ["Amazon Quick (QuickSight)", "SPICE"])
    d.icon("dCudos", "rR", 2250, 260, "quick_suite", "analytics", ["CUDOS (lớp 1)", "chi phí Kiro"])
    d.icon("dCid", "rR", 2250, 440, "quick_suite", "analytics", ["CID Kiro (lớp 2)", "license, overage"])
    d.icon("dSmp", "rR", 2250, 620, "quick_suite", "analytics", ["Sample kiro-<khách>", "(lớp 3) mức dùng"])
    d.icon("dAud", "rR", 2250, 800, "quick_suite", "analytics", ["Kiro Request", "Audit (lớp 4, 5)"])
    d.icon("viewer", "rR", 2440, 620, None, None, ["Người xem", "bên vận hành (D1)"], shape="user")
    # monitoring
    d.icon("fresh", "rR", 1650, 1180, "lambda", "compute", ["Lambda kiểm độ tươi", "05:00 UTC, đọc S3", "và CloudWatch"])
    d.icon("sns", "rR", 1950, 1180, "sns", "integ", ["SNS", "kiro-mon-alerts"])
    d.icon("cw", "rR", 1650, 1360, "cloudwatch_2", "mgmt", ["CloudWatch alarm", "normalizer-errors,", "normalizer-not-running"])
    # notes
    d.note("n_patch", "rR", 1560, 720, 500, 130, "orange",
           "Sample đặt ở account trung tâm cần 3 bản vá (D3). P1: thêm SourceAccountId cho RAW_PREFIX, vì template "
           "dòng 313 đang ghim account deploy. P2: preflight chỉ cảnh báo khi bucket ở account khác. P3: identity "
           "map truyền ExternalId, vì Lambda gốc không truyền. Mỗi khách một bản sample, STACK_PREFIX=kiro-<khách>.")
    d.note("n_oq", "rR", 1560, 870, 500, 130, "red",
           "Chưa xác minh. OQ-11: trail có ghi được vào bucket ở ap-southeast-1 không; nếu không, bucket CloudTrail "
           "nằm ở region của trail. OQ-2: requestID của CloudTrail có trùng requestId của prompt log không. OQ-3: "
           "field nào trong CloudTrail chứa user ID. Ba câu này quyết định cách nối trong view kiro_request_audit.")
    d.note("n_legend", "rR", 2140, 900, 370, 610, "grey",
           "Thứ tự trong ngày (giờ UTC, giờ Việt Nam = UTC + 7):\n"
           "1. Payer giao CUR vào bucket data-exports, ít nhất mỗi ngày một lần. Lần đầu mất 24-72 giờ.\n"
           "2. 02:00 Kiro ghi report CSV, mỗi loại client một file.\n"
           "3. 03:00 Lambda collector CID kéo report còn thiếu.\n"
           "4. 03:15 Lambda normalizer của sample kéo report.\n"
           "5. 03:30 Lambda identity map đọc Identity Center của khách.\n"
           "6. 04:00 SPICE của sample refresh. Lịch của CID chưa đo (OQ-13).\n"
           "7. 05:00 Lambda kiểm độ tươi gửi cảnh báo nếu trễ quá 2 ngày.\n"
           "8-9. Data event ghi theo từng request, trail giao log sau vài phút.\n"
           "10-11. Prompt log ở lại khách. Chỉ khi D4 = có thì Lambda gửi metadata về bên vận hành.\n\n"
           "Mũi tên là hướng dữ liệu. Chữ kéo nghĩa là Lambda ở bên vận hành chủ động đọc bucket của khách, phía "
           "khách chỉ gắn bucket policy.\n"
           "Mờ: tuỳ chọn, chỉ có khi chốt D4.\n"
           "Đỏ: chưa xác minh. Cam: phải sửa code. Xám: đã đọc trong tài liệu hoặc source.")

    # ---------------- cross-account and in-account edges ----------------
    d.edge("cur", "dex", (1, 0.5), (0, 0.5), num=1)
    d.text("t_cur", None, 1055, 165, ["giao CUR, lần đầu 24-72 giờ"])
    d.edge("rpt", "col", (1, 0.25), (0, 0.5), num=3)
    d.text("t_pull1", None, 740, 450, ["kéo 03:00 UTC"])
    d.edge("rpt", "nor", (1, 0.75), (0, 0.5), points=[(940, 495), (940, 620)], num=4)
    d.text("t_pull2", None, 1012, 560, ["kéo 03:15 UTC"])
    d.edge("meta", "pmb", (1, 0.5), (0, 0.5), dashed=True)
    d.text("t_meta", None, 1075, 745, ["chỉ field cho phép (D4)"])
    d.edge("trail", "ctb", (1, 0.5), (0, 0.5), num=9)
    d.text("t_trail", None, 760, 945, ["giao log (OQ-11)"])
    d.edge("idcRole", "idm", (1, 0.5), (0, 0.5), num=5)
    d.text("t_idc", None, 850, 1278, ["AssumeRole + ExternalId, ListUsers 03:30 UTC"])

    d.edge("col", "dcb", (1, 0.5), (0, 0.5))
    d.edge("nor", "res", (1, 0.5), (0, 0.5))
    d.edge("idm", "idb", (1, 0.5), (0, 0.5))
    # bus x=1500 into Athena
    for src, y, dashed in (("dex", 190, False), ("dcb", 465, False), ("pmb", 760, True),
                           ("ctb", 960, False), ("idb", 1300, False)):
        d.edge(src, "ath", (1, 0.5), (0, 0.5), points=[(1500, y), (1500, 620)], dashed=dashed)
    d.edge("res", "ath", (1, 0.5), (0, 0.5))
    d.edge("glue", "ath", (0.5, 1), (0.5, 0), dashed=True)
    d.edge("ath", "qs", (1, 0.5), (0, 0.5), num=6)
    d.text("t_spice", None, 1800, 598, ["04:00 UTC"])
    # bus x=2100 into dashboards
    for dst, y in (("dCudos", 260), ("dCid", 440), ("dAud", 800)):
        d.edge("qs", dst, (1, 0.5), (0, 0.5), points=[(2100, 620), (2100, y)])
    d.edge("qs", "dSmp", (1, 0.5), (0, 0.5))
    d.edge("fresh", "sns", (1, 0.5), (0, 0.5), num=7)
    d.text("t_fresh", None, 1800, 1158, ["cảnh báo"])
    d.edge("cw", "sns", (1, 0.5), (0, 0.75), points=[(1800, 1360), (1800, 1195)])
    return d


def permissions():
    d = KDiagram("2. Quyền truy cập xuyên account", 1600, 1500)
    d.text("title", None, 40, 35, ["Quyền truy cập xuyên account: ai đọc hoặc ghi được tài nguyên nào, "
                                   "kèm điều kiện (design.md mục 3.4, 3.5, 4)"], size=16, bold=True, align="left")
    d.group("accC", "Account của khách", 20, 70, 760, 1400, "account")
    d.group("accR", "Account trung tâm", 820, 70, 760, 1400, "account")

    rows = [
        # key, y, customer side, observability side, arrow direction, note color, note text
        ("rpt", 200,
         ("s3", "storage", ["S3 bucket report", "kiro/*"]),
         ("lambda", "compute", ["Role collector CID", "Role normalizer sample"]),
         "R2C", "grey",
         "Bucket policy của khách cho đúng 2 role này: s3:GetObject trên kiro/*, s3:ListBucket có điều kiện "
         "s3:prefix = kiro/*. Không có quyền ghi hay xoá. Bucket policy lưu role theo unique ID: role bị tạo "
         "lại thì phải gắn lại policy, nếu không Lambda chép 0 file mà không báo lỗi."),
        ("idc", 430,
         ("identity_and_access_management", "security", ["Role kiro-mon-idc-read", "ListUsers"]),
         ("lambda", "compute", ["Role Lambda identity map", "(bản sample của khách)"]),
         "R2C", "orange",
         "Trust policy của role phía khách: principal là đúng ARN role identity map, điều kiện sts:ExternalId. "
         "Lambda gốc của sample không truyền ExternalId, nên cần bản vá P3. Quyền: identitystore:ListUsers "
         "trên Identity Store của khách."),
        ("ct", 660,
         ("cloudtrail", "mgmt", ["Trail kiro-mon-audit", "cloudtrail.amazonaws.com"]),
         ("s3", "storage", ["S3 CloudTrail khách", "+ KMS key bên vận hành"]),
         "C2R", "red",
         "Bucket policy phía vận hành: s3:PutObject trên AWSLogs/<account khách>/*, s3:GetBucketAcl, điều kiện "
         "aws:SourceArn = ARN của trail. Key policy cho cloudtrail.amazonaws.com mã hoá với cùng điều kiện. "
         "Chưa xác minh: OQ-1 (có data event không) và OQ-11 (bucket khác region được không)."),
        ("cur", 890,
         ("cost_and_usage_report", "mgmt", ["Data Exports ở payer", "bcm-data-exports"]),
         ("s3", "storage", ["S3 data-exports", "(stack CID Destination)"]),
         "C2R", "grey",
         "Stack CID-DataExports-Destination tạo bucket policy cho phép account payer của khách giao export. "
         "Export ghi vào <export-name>/<account payer>/. Phía khách chạy stack CID-DataExports-Source, hoặc "
         "tạo export lọc chỉ Kiro bằng tay nếu chốt D2."),
        ("plog", 1120,
         ("s3", "storage", ["S3 bucket prompt log", "SSE-KMS key khách"]),
         ("lambda", "compute", ["Không có principal", "nào của bên vận hành"]),
         "NONE", "grey",
         "Chỉ q.amazonaws.com được ghi (điều kiện aws:SourceAccount và aws:SourceArn arn:aws:codewhisperer:...). "
         "Chỉ role kiro-prompt-auditor của khách được đọc và giải mã. Task 7.3 chứng minh bằng head-object từ "
         "bên vận hành phải ra 403. Metadata chỉ đi qua Lambda của khách khi chốt D4."),
    ]
    for key, y, (c_res, c_cat, c_lbl), (r_res, r_cat, r_lbl), direction, color, text in rows:
        d.icon(f"{key}C", "accC", 200, y, c_res, c_cat, c_lbl)
        d.icon(f"{key}R", "accR", 1000, y, r_res, r_cat, r_lbl, faded=(direction == "NONE"))
        d.note(f"n_{key}", "accR", 1150, y - 80, 410, 170, color, text)
        if direction == "R2C":
            d.edge(f"{key}R", f"{key}C", (0, 0.5), (1, 0.5))
            d.text(f"t_{key}", None, 600, y - 18, ["Bên vận hành gọi API, đọc"])
        elif direction == "C2R":
            d.edge(f"{key}C", f"{key}R", (1, 0.5), (0, 0.5))
            d.text(f"t_{key}", None, 600, y - 18, ["dịch vụ AWS ghi sang"])
        else:
            d.text(f"t_{key}", None, 600, y - 18, ["không có đường nào"])
    d.note("n_qs", "accR", 840, 1290, 720, 160, "grey",
           "QuickSight chỉ đọc bucket và Athena trong account trung tâm. Bảng Athena của sample trỏ vào bucket kết "
           "quả normalized/, không trỏ vào bucket của khách (đã đo trong build_views.py dòng 93-94). Vì vậy bucket "
           "report của khách không cấp quyền cho QuickSight. IP nguồn trong CloudTrail chỉ role audit của bên vận hành "
           "query được, không nạp vào SPICE.")
    d.note("n_cust", "accC", 40, 1290, 720, 160, "grey",
           "Mọi thay đổi trên account khách là task có đánh dấu XÁC NHẬN trong tasks.md: agent hỏi người vận hành trước "
           "từng lần chạy. Bucket policy của bucket report được dựng lại toàn bộ bằng Terraform, giữ nguyên các "
           "statement đang có (gồm statement cho q.amazonaws.com ghi report).")
    return d


def write(path, builders):
    pages, probs, icons, edges = [], [], 0, 0
    for i, build in enumerate(builders, 1):
        d = build()
        probs += [f"[{d.title}] {p}" for p in d.check()]
        pages.append(d.page(f"p{i}"))
        icons += sum(1 for b in d.boxes if b[1] == "icon")
        edges += len(d.edges)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write('<mxfile host="app.diagrams.net" type="device">\n' + "\n".join(pages) + "\n</mxfile>")
    return probs, icons, edges


if __name__ == "__main__":
    # The main diagram (OUT) is edited by hand in draw.io: never regenerate it from here.
    # Default run writes only the detailed file. Pass --compact <path> to also write the compact
    # starting layout somewhere else (it is refused when <path> is the hand-edited file).
    targets = [(OUT_DETAIL, [architecture, permissions])]
    if "--compact" in sys.argv:
        dest = os.path.abspath(sys.argv[sys.argv.index("--compact") + 1])
        if os.path.normcase(dest) == os.path.normcase(os.path.abspath(OUT)):
            sys.exit(f"STOP: {OUT} is edited by hand; write the compact layout to another path.")
        targets.append((dest, [compact]))
    bad = 0
    for path, builders in targets:
        probs, icons, edges = write(path, builders)
        print(f"{os.path.basename(path)}: {os.path.getsize(path)} byte, {len(builders)} page, {icons} icon, "
              f"{edges} edge, {len(probs)} geometry problem")
        for p in probs:
            print("   ", p)
        bad += len(probs)
    sys.exit(1 if bad else 0)
