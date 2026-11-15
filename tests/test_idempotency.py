import pytest

from catalog.models import (
    JobStatus,
    LandingEvent,
    LandingRecord,
    Policy,
    RecordStatus,
    RollbackTicket,
    TicketKind,
    TicketStatus,
)
from catalog.services import set_policy
from tests.conftest import land, parse_job


def _prep(client, job, mode):
    set_policy("admin", mode)
    parse_job(client, job)


# ---- 空钥匙 ---------------------------------------------------------------

@pytest.mark.django_db
def test_empty_key_forbidden(client, login, seeded_job):
    login(client)
    _prep(client, seeded_job, Policy.SKIP)
    r = client.post(f"/jobs/{seeded_job.id}/land/",
                    {"idem_key": "  ", "policy_mode": Policy.SKIP})
    assert r.status_code == 302
    assert not LandingRecord.objects.exists()
    body = client.get(f"/jobs/{seeded_job.id}/").content.decode("utf-8")
    assert "空" in body and "禁止落档" in body


# ---- SKIP：撞钥跳过并计数 -------------------------------------------------

@pytest.mark.django_db
def test_skip_policy_duplicate_key_skips_and_counts(client, login, seeded_job):
    login(client)
    _prep(client, seeded_job, Policy.SKIP)
    land(client, seeded_job, "KEY-A", Policy.SKIP)
    seeded_job.refresh_from_db()
    assert seeded_job.status == JobStatus.LANDED

    # 同钥匙再落档：跳过并计数，不新增落档量
    land(client, seeded_job, "KEY-A", Policy.SKIP)
    land(client, seeded_job, "KEY-A", Policy.SKIP)
    seeded_job.refresh_from_db()
    record = LandingRecord.objects.get(idem_key="KEY-A")
    assert record.status == RecordStatus.LANDED
    assert record.skipped == 2
    assert seeded_job.skip_count == 2
    assert seeded_job.landed == seeded_job.parsed  # 没有重复落档
    assert LandingEvent.objects.filter(kind="幂等撞钥").count() == 2

    # 落档列表横排读出跳过条数与策略名
    body = client.get("/jobs/").content.decode("utf-8")
    assert ">2<" in body
    assert seeded_job.policy_name in body


# ---- FAIL_BATCH：整批失败并自动撤回 ---------------------------------------

@pytest.mark.django_db
def test_fail_batch_duplicate_key_rolls_back_whole_batch(client, login, seeded_job):
    login(client)
    _prep(client, seeded_job, Policy.FAIL_BATCH)
    land(client, seeded_job, "KEY-B", Policy.FAIL_BATCH)
    seeded_job.refresh_from_db()
    assert seeded_job.status == JobStatus.LANDED

    # 撞钥：整批失败并自动撤回到落档前
    land(client, seeded_job, "KEY-B", Policy.FAIL_BATCH)
    seeded_job.refresh_from_db()
    assert seeded_job.status == JobStatus.PARSED
    assert seeded_job.landed == 0
    assert seeded_job.rollback_count == 1

    ticket = RollbackTicket.objects.get()
    assert ticket.kind == TicketKind.AUTO
    assert ticket.status == TicketStatus.DONE
    assert "撤回到落档前" in ticket.body
    record = LandingRecord.objects.get(idem_key="KEY-B")
    assert record.status == RecordStatus.ROLLED_BACK

    # 回滚详情旁能读出幂等结果
    body = client.get(f"/rollbacks/{ticket.id}/").content.decode("utf-8")
    assert "回滚单正文" in body and "撤回到落档前" in body


# ---- 同批后到争用失败 -----------------------------------------------------

@pytest.mark.django_db
def test_concurrent_second_arrival_marked_contention(client, login, seeded_job):
    # 同步模式下模拟处理中窗口：先造出一条"处理中"的在途批次，再到的请求记争用
    login(client)
    _prep(client, seeded_job, Policy.SKIP)
    from catalog.models import ParseJob
    from catalog.services import submit_landing
    in_flight = LandingRecord.objects.create(
        job=seeded_job, idem_key="KEY-C", idem_key_raw="KEY-C",
        operator="admin", policy_mode=Policy.SKIP,
        status=RecordStatus.IN_PROGRESS)
    ParseJob.objects.filter(pk=seeded_job.id).update(status=JobStatus.LANDING)
    submit_landing(seeded_job.id, "KEY-C", "auditor", Policy.SKIP)
    statuses = set(LandingRecord.objects.filter(job=seeded_job)
                   .values_list("status", flat=True))
    assert RecordStatus.CONTENTION in statuses
    assert in_flight.status == RecordStatus.IN_PROGRESS  # 在途批次不受影响
    event = LandingEvent.objects.get(kind="落档争用失败")
    assert "争用失败" in event.message
    # 真线程版同批争抢见 tests/test_background.py


@pytest.mark.django_db
def test_different_key_after_landed_is_contention(client, login, seeded_job):
    login(client)
    _prep(client, seeded_job, Policy.SKIP)
    land(client, seeded_job, "KEY-1", Policy.SKIP)
    # 同批换钥匙再落档：后到争用失败
    land(client, seeded_job, "KEY-2", Policy.SKIP)
    later = LandingRecord.objects.get(idem_key_raw="KEY-2")
    assert later.status == RecordStatus.CONTENTION
    assert later.display_key == "KEY-2"
    assert LandingEvent.objects.filter(kind="落档争用失败").exists()
