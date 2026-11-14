from catalog.models import ParseJob

SEEDS = [
    ("活动页字段抽取", "promo_html", "待解析", "https://mock.local/promo"),
    ("报价单表格解析", "quote_xlsx", "待解析", "file://drops/quote.xlsx"),
    ("公告正文抽取", "notice_html", "待解析", "https://mock.local/notice"),
    ("目录索引解析", "index_json", "待解析", "https://mock.local/index.json"),
]

def ensure_seed():
    if ParseJob.objects.count():
        return
    for title, code, status, uri in SEEDS:
        ParseJob.objects.create(title=title, code=code, status=status, source_uri=uri)
