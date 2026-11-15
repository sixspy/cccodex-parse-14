from django.db import models


# ---- 状态常量 -------------------------------------------------------------

class JobStatus:
    PENDING = "待解析"
    PARSED = "已解析待入库"
    LANDING = "落档处理中"
    LANDED = "已入库"
    ROLLING_BACK = "回滚处理中"
    PARSE_FAILED = "解析失败"


class Policy:
    SKIP = "SKIP"          # 幂等撞钥时跳过并计数
    FAIL_BATCH = "FAIL_BATCH"  # 幂等撞钥时整批失败并自动撤回到落档前

    CHOICES = [
        (SKIP, "撞钥跳过并计数"),
        (FAIL_BATCH, "撞钥整批失败并自动撤回"),
    ]
    LABELS = dict(CHOICES)


class RecordStatus:
    IN_PROGRESS = "处理中"
    LANDED = "已落档"
    SKIPPED = "已跳过"
    CONTENTION = "争用失败"
    FAILED = "整批失败"
    ROLLED_BACK = "已回滚"


class TicketStatus:
    IN_PROGRESS = "处理中"
    DONE = "已完成"
    FAILED = "失败"


class TicketKind:
    MANUAL = "手动回滚"
    AUTO = "自动撤回"


# ---- 模型 -----------------------------------------------------------------

class ParseJob(models.Model):
    title = models.CharField(max_length=120)
    code = models.CharField(max_length=64, unique=True)
    status = models.CharField(max_length=32, default=JobStatus.PENDING)
    source_uri = models.CharField(max_length=255)
    parsed = models.IntegerField(default=0)
    landed = models.IntegerField(default=0)
    # 落档相关汇总字段（落档列表横排展示）
    skip_count = models.IntegerField(default=0)          # 跳过条数
    rollback_count = models.IntegerField(default=0)      # 已回滚次数
    policy_name = models.CharField(max_length=32, blank=True, default="")  # 处置策略名
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]


class IdempotencyPolicy(models.Model):
    """幂等处置策略：全台唯一一种，写进策略后当天不许换。"""
    mode = models.CharField(max_length=32, choices=Policy.CHOICES)
    set_by = models.CharField(max_length=64)
    set_date = models.DateField()                 # 设定当天锁定，不许换弦
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-id"]

    @property
    def label(self) -> str:
        return Policy.LABELS.get(self.mode, self.mode)


class LandingRecord(models.Model):
    """一次落档（一批）的台账记录，幂等钥匙在同一任务内唯一。"""
    job = models.ForeignKey(ParseJob, on_delete=models.CASCADE, related_name="records")
    idem_key = models.CharField(max_length=128)              # 幂等钥匙（存储值，争用单加后缀保唯一），空着禁止落档
    idem_key_raw = models.CharField(max_length=128, blank=True, default="")  # 台面提交的原始钥匙
    operator = models.CharField(max_length=64)               # 点落档的人
    policy_mode = models.CharField(max_length=32)            # 提交时的策略快照
    status = models.CharField(max_length=32, default=RecordStatus.IN_PROGRESS)
    total = models.IntegerField(default=0)                   # 本批条数
    skipped = models.IntegerField(default=0)                 # 撞钥跳过条数
    note = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-id"]
        constraints = [
            models.UniqueConstraint(fields=["job", "idem_key"], name="uq_landing_job_key"),
        ]

    @property
    def policy_label(self) -> str:
        return Policy.LABELS.get(self.policy_mode, self.policy_mode)

    @property
    def display_key(self) -> str:
        return self.idem_key_raw or self.idem_key


class RollbackTicket(models.Model):
    """回滚单：缘故必填，正文可读；自动撤回也留单。"""
    record = models.ForeignKey(LandingRecord, on_delete=models.CASCADE, related_name="tickets")
    job = models.ForeignKey(ParseJob, on_delete=models.CASCADE, related_name="tickets")
    kind = models.CharField(max_length=32, default=TicketKind.MANUAL)
    reason = models.TextField(blank=True, default="")        # 回滚缘故，必填
    operator = models.CharField(max_length=64)               # 发起人（手动）/ 系统（自动）
    status = models.CharField(max_length=32, default=TicketStatus.IN_PROGRESS)
    body = models.TextField(blank=True, default="")          # 回滚单正文
    created_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-id"]


class LandingEvent(models.Model):
    """落档事件流水：争用失败、幂等结果等，详情页可读。"""
    job = models.ForeignKey(ParseJob, on_delete=models.CASCADE, related_name="events")
    record = models.ForeignKey(LandingRecord, null=True, blank=True, on_delete=models.SET_NULL, related_name="events")
    kind = models.CharField(max_length=48)
    message = models.CharField(max_length=255)
    actor = models.CharField(max_length=64, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-id"]
