"""真后台线程的用例：去重/回滚在 daemon 线程内执行。

pytest-django 的 django_db 用例包在事务里，后台线程拿不到未提交视图，
因此这里用 Django 的 TransactionTestCase + 文件级 SQLite 库；
再用 settings.LANDING_GATE（threading.Event）门控后台线程，确定性地
观察"处理中/回滚中"窗口，避免靠 sleep 猜时机。门只用一次性 Event，
不做 clear/set 复用（已 wait 阻塞的线程不会被复用事件重新唤醒）。
"""
import os
import tempfile
import threading
import time

from django.test import TransactionTestCase, override_settings

from catalog.models import (
    JobStatus,
    LandingEvent,
    LandingRecord,
    Policy,
    RecordStatus,
    RollbackTicket,
)
from catalog.seed import ensure_seed
from catalog.services import set_policy

_TMP_DB = os.path.join(tempfile.gettempdir(), "cc_parse_bg_test.db")
_DB_OPT = {"timeout": 20, "transaction_mode": "IMMEDIATE"}


class _BgCase(TransactionTestCase):
    gate = None

    def _settings(self, gate):
        opts = {
            "LANDING_SYNCHRONOUS": False,
            "LANDING_WORK_SECONDS": 0.05,
            "DATABASES": {"default": {
                "ENGINE": "django.db.backends.sqlite3",
                "NAME": _TMP_DB, "OPTIONS": dict(_DB_OPT),
            }},
        }
        if gate is not None:
            opts["LANDING_GATE"] = gate
        return override_settings(**opts)

    def setUp(self):
        self._ctx = self._settings(self.gate)
        self._ctx.enable()
        from django.core.management import call_command
        call_command("flush", interactive=False, verbosity=0)
        ensure_seed()
        from catalog.models import ParseJob
        self.job = ParseJob.objects.order_by("id").first()
        from django.test import Client
        self.admin = Client()
        self.auditor = Client()
        self.admin.post("/login", {"username": "admin", "password": "123456"})
        self.auditor.post("/login", {"username": "auditor", "password": "123456"})

    def tearDown(self):
        # 放行所有可能仍在等待的门
        if self.gate is not None:
            self.gate.set()
        for g in getattr(self, "_extra_gates", []):
            g.set()
        self._wait_idle()
        self._ctx.disable()
        super().tearDown()

    def _wait_idle(self, timeout=5.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            self.job.refresh_from_db()
            if self.job.status not in (JobStatus.LANDING, JobStatus.ROLLING_BACK):
                return
            time.sleep(0.03)

    def _parse(self):
        self.admin.post(f"/jobs/{self.job.id}/parse/")
        self.job.refresh_from_db()

    def _land(self, client, key, mode=Policy.SKIP):
        return client.post(
            f"/jobs/{self.job.id}/land/",
            {"idem_key": key, "policy_mode": mode})

    def _wait_status(self, status, timeout=5.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            self.job.refresh_from_db()
            if self.job.status == status:
                return True
            time.sleep(0.03)
        self.job.refresh_from_db()
        return self.job.status == status


class LandingBackgroundTests(_BgCase):
    """不带门控：后台自然跑完，验证异步执行与最终一致性。"""

    def test_landing_runs_in_background_then_completes(self):
        set_policy("admin", Policy.SKIP)
        self._parse()
        self._land(self.admin, "KEY-BG-1")
        assert self._wait_status(JobStatus.LANDED)
        record = LandingRecord.objects.get(idem_key="KEY-BG-1")
        assert record.status == RecordStatus.LANDED
        assert record.total == self.job.parsed
        assert LandingEvent.objects.filter(kind="落档完成").exists()


class GatedBackgroundTests(_BgCase):
    def setUp(self):
        self.landing_gate = threading.Event()   # 卡住去重落档线程
        self.rollback_gate = threading.Event()  # 卡住回滚线程
        self.gate = self.landing_gate
        self._extra_gates = [self.rollback_gate]
        super().setUp()

    def _switch_gate(self, gate):
        """后续 spawn 的后台线程改由另一道门控制。"""
        from django.conf import settings
        settings.LANDING_GATE = gate

    def test_in_progress_window_then_complete(self):
        set_policy("admin", Policy.SKIP)
        self._parse()
        self._land(self.admin, "KEY-G1")
        # 门未开：后台线程挂起，任务稳定停在处理中
        time.sleep(0.15)
        self.job.refresh_from_db()
        assert self.job.status == JobStatus.LANDING
        self.landing_gate.set()
        assert self._wait_status(JobStatus.LANDED)

    def test_same_batch_second_arrival_is_contention(self):
        set_policy("admin", Policy.SKIP)
        self._parse()
        # 第一批受理后卡在后台处理中
        self._land(self.admin, "KEY-G2")
        time.sleep(0.15)
        self.job.refresh_from_db()
        assert self.job.status == JobStatus.LANDING
        # 同批后到（不同操作员、另一钥匙）→ 争用失败
        self.auditor.post(
            f"/jobs/{self.job.id}/land/",
            {"idem_key": "KEY-G2B", "policy_mode": Policy.SKIP})
        contention = LandingRecord.objects.get(status=RecordStatus.CONTENTION)
        assert contention.display_key == "KEY-G2B"
        assert contention.operator == "auditor"
        event = LandingEvent.objects.get(kind="落档争用失败")
        assert "争用失败" in event.message
        # 放行第一批，正常落档；争用单不改写
        self.landing_gate.set()
        assert self._wait_status(JobStatus.LANDED)
        assert LandingRecord.objects.filter(
            status=RecordStatus.CONTENTION, idem_key_raw="KEY-G2B").exists()

    def test_no_landing_while_rollback_in_progress(self):
        set_policy("admin", Policy.SKIP)
        self._parse()
        self.landing_gate.set()  # 落档阶段放通
        self._land(self.admin, "KEY-G3")
        assert self._wait_status(JobStatus.LANDED)
        record = LandingRecord.objects.get(idem_key="KEY-G3")

        # 回滚线程交给回滚门（一次性、初始关闭）
        self._switch_gate(self.rollback_gate)
        self.auditor.post(f"/records/{record.id}/rollback/",
                          {"reason": "撤回核对"})
        time.sleep(0.15)
        self.job.refresh_from_db()
        assert self.job.status == JobStatus.ROLLING_BACK
        # 回滚尚未结束时落档 → 拦住
        r = self.admin.post(
            f"/jobs/{self.job.id}/land/",
            {"idem_key": "KEY-G3-NEW", "policy_mode": Policy.SKIP})
        assert r.status_code == 302
        assert not LandingRecord.objects.filter(idem_key="KEY-G3-NEW").exists()
        # 放行回滚
        self.rollback_gate.set()
        assert self._wait_status(JobStatus.PARSED)
        self.job.refresh_from_db()
        assert self.job.landed == 0
        assert self.job.rollback_count == 1
        ticket = RollbackTicket.objects.get()
        assert "撤回核对" in ticket.body
        assert ticket.operator == "auditor"

    def test_fail_batch_auto_rollback_runs_in_background(self):
        set_policy("admin", Policy.FAIL_BATCH)
        self._parse()
        self.landing_gate.set()  # 首次落档放通
        self._land(self.admin, "KEY-G4", Policy.FAIL_BATCH)
        assert self._wait_status(JobStatus.LANDED)
        # 撞钥触发自动撤回，撤回线程交给回滚门
        self._switch_gate(self.rollback_gate)
        self._land(self.admin, "KEY-G4", Policy.FAIL_BATCH)
        time.sleep(0.15)
        self.job.refresh_from_db()
        assert self.job.status == JobStatus.ROLLING_BACK
        ticket = RollbackTicket.objects.get(kind="自动撤回")
        assert "幂等撞钥" in ticket.reason
        self.rollback_gate.set()
        assert self._wait_status(JobStatus.PARSED)
        self.job.refresh_from_db()
        assert self.job.rollback_count == 1
        ticket.refresh_from_db()
        assert ticket.status == "已完成"
        assert "撤回到落档前" in ticket.body
