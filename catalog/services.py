"""落档 / 幂等 / 回滚核心服务。

去重与回滚都在后台线程执行；测试可把 settings.LANDING_SYNCHRONOUS 置 True
改为同步执行，保证用例确定性。
"""
import threading
import time
import uuid

from django.conf import settings
from django.db import close_old_connections, transaction
from django.utils import timezone

from .models import (
    IdempotencyPolicy,
    JobStatus,
    LandingEvent,
    LandingRecord,
    ParseJob,
    Policy,
    RecordStatus,
    RollbackTicket,
    TicketKind,
    TicketStatus,
)


class ServiceError(Exception):
    """业务校验失败，消息直接展示给台面。"""


# 同进程内串行化落档提交：SQLite 写锁 + 单进程多线程下保证后到争用可判定
_submit_lock = threading.Lock()


# ---- 后台执行 -------------------------------------------------------------

def _spawn(worker):
    if getattr(settings, "LANDING_SYNCHRONOUS", False):
        worker()
        return

    gate = getattr(settings, "LANDING_GATE", None)  # 测试钩子：线程开跑先等门

    def _runner():
        try:
            if gate is not None:
                gate.wait(timeout=10)
            worker()
        except Exception:
            import logging
            logging.getLogger("catalog").exception("后台落档/回滚任务异常")
            raise
        finally:
            # 线程复用连接会跨请求脏读，用完即关
            close_old_connections()

    threading.Thread(target=_runner, daemon=True).start()


def _work_delay():
    """模拟后台去重/回滚耗时；同步模式不等待。"""
    if not getattr(settings, "LANDING_SYNCHRONOUS", False):
        time.sleep(getattr(settings, "LANDING_WORK_SECONDS", 0.2))


def _log(job, kind, message, actor="", record=None):
    LandingEvent.objects.create(
        job=job, kind=kind, message=message, actor=actor, record=record
    )


# ---- 幂等策略 -------------------------------------------------------------

def get_active_policy():
    return IdempotencyPolicy.objects.order_by("-id").first()


def set_policy(operator, mode):
    if mode not in (Policy.SKIP, Policy.FAIL_BATCH):
        raise ServiceError("幂等处置策略只允许二选一")
    today = timezone.localdate()
    current = get_active_policy()
    if current is not None and current.set_date == today:
        raise ServiceError("幂等策略写进后当天不许换弦")
    return IdempotencyPolicy.objects.create(mode=mode, set_by=operator, set_date=today)


# ---- 落档 -----------------------------------------------------------------

def submit_landing(job_id, idem_key, operator, policy_mode):
    """台面点“执行落档”的入口；校验在请求线程内同步完成，重活进后台。"""
    idem_key = (idem_key or "").strip()
    if not idem_key:
        raise ServiceError("幂等钥匙空着，禁止落档")

    policy = get_active_policy()
    if policy is None:
        raise ServiceError("尚未设定幂等处置策略，禁止落档")
    if policy_mode != policy.mode:
        # 点落档时处理若跟幂等策略不一致，拦住
        raise ServiceError("落档处理与当前幂等策略不一致，已拦截")

    with _submit_lock:
        job = ParseJob.objects.select_for_update().get(pk=job_id)

        open_ticket = job.tickets.filter(status=TicketStatus.IN_PROGRESS).first()
        if open_ticket is not None or job.status == JobStatus.ROLLING_BACK:
            raise ServiceError("回滚尚未结束，不得再次落档")

        # 幂等撞钥优先判定：同钥匙一律按当前唯一策略处置
        existing = (
            LandingRecord.objects.filter(job=job, idem_key=idem_key)
            .order_by("-id")
            .first()
        )
        if existing is not None:
            if existing.status == RecordStatus.IN_PROGRESS:
                return _contention(job, idem_key, operator, policy_mode,
                                   "同钥匙落档仍在处理，后到记落档争用失败")
            return _handle_collision(job, existing, operator, policy)

        # 无同钥匙记录：批次已在处理中或已落档，换钥匙的后到请求记争用失败
        if job.status != JobStatus.PARSED:
            return _contention(job, idem_key, operator, policy_mode,
                               "同批两人争抢落档，后到记争用失败")

        job.policy_name = policy.label
        job.status = JobStatus.LANDING
        job.save(update_fields=["policy_name", "status"])
        record = LandingRecord.objects.create(
            job=job, idem_key=idem_key, idem_key_raw=idem_key,
            operator=operator,
            policy_mode=policy.mode, status=RecordStatus.IN_PROGRESS,
        )
        _log(job, "落档受理", f"钥匙 {idem_key} 受理，后台去重落档", operator, record)

    _spawn(lambda: _process_landing(record.id))
    return record


def _contention(job, idem_key, operator, policy_mode, message):
    """同批后到：记争用失败，留一条失败台账与事件。

    存储钥匙加后缀以满足 (job, idem_key) 唯一约束；台面仍展示原始钥匙。
    """
    suffix = uuid.uuid4().hex[:6]
    stored = f"{idem_key}#c-{suffix}"
    if len(stored) > 128:
        stored = stored[:120] + f"-{suffix}"
    record = LandingRecord.objects.create(
        job=job, idem_key=stored,
        idem_key_raw=idem_key, operator=operator,
        policy_mode=policy_mode or "", status=RecordStatus.CONTENTION,
        note=message, finished_at=timezone.now(),
    )
    _log(job, "落档争用失败", message, operator, record)
    return record


def _handle_collision(job, existing, operator, policy):
    """幂等撞钥的两种处置，全台只许定一种。"""
    if policy.mode == Policy.SKIP:
        # 撞钥跳过并计数
        existing.skipped += 1
        existing.save(update_fields=["skipped"])
        job.skip_count += 1
        job.save(update_fields=["skip_count"])
        _log(job, "幂等撞钥", f"钥匙 {existing.idem_key} 撞钥，跳过并计数（累计跳过 {job.skip_count} 条）",
             operator, existing)
        return existing

    # FAIL_BATCH：整批失败并自动撤回到落档前
    _log(job, "幂等撞钥", f"钥匙 {existing.idem_key} 撞钥，整批失败并自动撤回", operator, existing)
    ticket = RollbackTicket.objects.create(
        record=existing, job=job, kind=TicketKind.AUTO, operator="系统",
        reason=f"幂等撞钥（钥匙 {existing.idem_key}），整批失败并自动撤回到落档前",
        status=TicketStatus.IN_PROGRESS,
    )
    job.status = JobStatus.ROLLING_BACK
    job.save(update_fields=["status"])
    _spawn(lambda: _process_rollback(ticket.id))
    return existing


def _process_landing(record_id):
    """后台去重落档。"""
    try:
        with transaction.atomic():
            record = LandingRecord.objects.select_for_update().get(pk=record_id)
            if record.status != RecordStatus.IN_PROGRESS:
                return
            job = ParseJob.objects.select_for_update().get(pk=record.job_id)
            _work_delay()
            record.total = job.parsed
            record.status = RecordStatus.LANDED
            record.finished_at = timezone.now()
            record.save(update_fields=["total", "status", "finished_at"])
            job.landed = job.parsed
            job.status = JobStatus.LANDED
            job.save(update_fields=["landed", "status"])
            _log(job, "落档完成",
                 f"幂等结果：正常落档 {record.total} 条，钥匙 {record.idem_key}",
                 record.operator, record)
    finally:
        close_old_connections()


# ---- 回滚 -----------------------------------------------------------------

def request_rollback(record_id, operator, reason):
    reason = (reason or "").strip()
    if not reason:
        raise ServiceError("回滚必须填缘故")

    with _submit_lock:
        record = LandingRecord.objects.select_for_update().get(pk=record_id)
        if record.status != RecordStatus.LANDED:
            raise ServiceError("只有已落档的批次能发起回滚")
        if record.operator == operator:
            raise ServiceError("不能由刚才点落档的人自己点回滚")
        job = ParseJob.objects.select_for_update().get(pk=record.job_id)
        if job.tickets.filter(status=TicketStatus.IN_PROGRESS).exists():
            raise ServiceError("已有回滚单处理中，请勿重复发起")

        ticket = RollbackTicket.objects.create(
            record=record, job=job, kind=TicketKind.MANUAL,
            reason=reason, operator=operator, status=TicketStatus.IN_PROGRESS,
        )
        job.status = JobStatus.ROLLING_BACK
        job.save(update_fields=["status"])
        _log(job, "回滚受理", f"回滚单 #{ticket.id} 受理，缘故：{reason}", operator, record)

    _spawn(lambda: _process_rollback(ticket.id))
    return ticket


def _process_rollback(ticket_id):
    """后台执行回滚：撤回到落档前，并写回滚单正文。"""
    try:
        with transaction.atomic():
            ticket = RollbackTicket.objects.select_for_update().get(pk=ticket_id)
            if ticket.status != TicketStatus.IN_PROGRESS:
                return
            record = LandingRecord.objects.select_for_update().get(pk=ticket.record_id)
            job = ParseJob.objects.select_for_update().get(pk=ticket.job_id)
            _work_delay()

            landed_before = job.landed
            job.landed = 0
            job.rollback_count += 1
            job.status = JobStatus.PARSED  # 撤回到落档前
            job.save(update_fields=["landed", "rollback_count", "status"])

            record.status = RecordStatus.ROLLED_BACK
            record.finished_at = timezone.now()
            record.save(update_fields=["status", "finished_at"])

            ticket.status = TicketStatus.DONE
            ticket.finished_at = timezone.now()
            ticket.body = (
                f"回滚单 #{ticket.id}\n"
                f"类型：{ticket.kind}\n"
                f"关联落档：#{record.id}（钥匙 {record.display_key}，落档人 {record.operator}）\n"
                f"发起人：{ticket.operator}\n"
                f"缘故：{ticket.reason}\n"
                f"撤回条数：{landed_before}\n"
                f"结果：已撤回到落档前，任务回到「{JobStatus.PARSED}」\n"
                f"完成时间：{timezone.localtime(ticket.finished_at).strftime('%Y-%m-%d %H:%M:%S')}\n"
            )
            ticket.save(update_fields=["status", "finished_at", "body"])
            _log(job, "回滚完成",
                 f"回滚单 #{ticket.id} 完成，幂等结果：{ticket.kind}，已撤回到落档前",
                 ticket.operator, record)
    finally:
        close_old_connections()
