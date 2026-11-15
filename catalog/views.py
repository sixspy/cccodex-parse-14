from django.conf import settings
from django.contrib import messages
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_http_methods, require_POST

from catalog.models import (
    IdempotencyPolicy,
    JobStatus,
    LandingEvent,
    LandingRecord,
    ParseJob,
    Policy,
    RollbackTicket,
    TicketStatus,
)
from catalog.services import (
    ServiceError,
    get_active_policy,
    request_rollback,
    set_policy,
    submit_landing,
)


def _authed(request) -> bool:
    return bool(request.session.get("user"))


def _user(request) -> str:
    return request.session.get("user", "")


def root(request):
    return HttpResponseRedirect("/jobs/" if _authed(request) else "/login")


@require_http_methods(["GET", "POST"])
def login_page(request):
    err = ""
    if request.method == "POST":
        user = request.POST.get("username", "").strip()
        pwd = request.POST.get("password", "")
        if settings.LAB_USERS.get(user) == pwd:
            request.session["user"] = user
            return HttpResponseRedirect("/jobs/")
        err = "账号或密码错误"
    return render(request, "login.html", {"error": err, "lab": settings.LAB_NAME})


def logout_view(request):
    request.session.flush()
    return HttpResponseRedirect("/login")


def job_list(request):
    if not _authed(request):
        return HttpResponseRedirect("/login")
    items = list(ParseJob.objects.all())
    policy = get_active_policy()
    return render(request, "jobs_list.html", {
        "items": items, "lab": settings.LAB_NAME, "user": _user(request),
        "policy": policy,
    })


def job_detail(request, job_id: int):
    if not _authed(request):
        return HttpResponseRedirect("/login")
    job = get_object_or_404(ParseJob, pk=job_id)
    records = list(job.records.all())
    tickets = list(job.tickets.all())
    events = list(job.events.all())[:15]
    policy = get_active_policy()
    # 回滚进行中 / 落档处理中时，台面禁用对应按钮
    rolling_back = job.status == JobStatus.ROLLING_BACK or \
        job.tickets.filter(status=TicketStatus.IN_PROGRESS).exists()
    return render(request, "job_detail.html", {
        "job": job, "lab": settings.LAB_NAME, "user": _user(request),
        "records": records, "tickets": tickets, "events": events,
        "policy": policy, "rolling_back": rolling_back,
    })


@require_POST
def job_parse(request, job_id: int):
    if not _authed(request):
        return HttpResponseRedirect("/login")
    job = get_object_or_404(ParseJob, pk=job_id)
    if job.status in (JobStatus.PENDING, JobStatus.PARSE_FAILED):
        job.parsed = 12 + (job.id * 2) % 9
        job.landed = 0
        job.status = JobStatus.PARSED
        job.save(update_fields=["parsed", "landed", "status"])
    return HttpResponseRedirect(f"/jobs/{job.id}/")


@require_POST
def job_land(request, job_id: int):
    if not _authed(request):
        return HttpResponseRedirect("/login")
    job = get_object_or_404(ParseJob, pk=job_id)
    try:
        submit_landing(
            job_id=job.id,
            idem_key=request.POST.get("idem_key", ""),
            operator=_user(request),
            policy_mode=request.POST.get("policy_mode", ""),
        )
        messages.success(request, "落档已受理，去重与落档在后台执行，可刷新详情查看进度。")
    except ServiceError as exc:
        messages.error(request, str(exc))
    return HttpResponseRedirect(f"/jobs/{job.id}/")


@require_POST
def job_rollback(request, record_id: int):
    if not _authed(request):
        return HttpResponseRedirect("/login")
    record = get_object_or_404(LandingRecord, pk=record_id)
    job_id = record.job_id
    try:
        request_rollback(
            record_id=record.id,
            operator=_user(request),
            reason=request.POST.get("reason", ""),
        )
        messages.success(request, "回滚单已受理，回滚在后台执行。")
    except ServiceError as exc:
        messages.error(request, str(exc))
    return HttpResponseRedirect(f"/jobs/{job_id}/")


def rollback_detail(request, ticket_id: int):
    if not _authed(request):
        return HttpResponseRedirect("/login")
    ticket = get_object_or_404(RollbackTicket, pk=ticket_id)
    # 回滚详情旁可读幂等结果与落档争用失败字样
    idem_events = list(
        LandingEvent.objects.filter(job=ticket.job,
                                    kind__in=["幂等撞钥", "落档争用失败"])
    )
    return render(request, "rollback_detail.html", {
        "ticket": ticket, "lab": settings.LAB_NAME, "user": _user(request),
        "idem_events": idem_events,
    })


@require_http_methods(["GET", "POST"])
def policy_page(request):
    if not _authed(request):
        return HttpResponseRedirect("/login")
    policy = get_active_policy()
    locked_today = policy is not None  # 写进策略后当天不许换
    if request.method == "POST":
        try:
            policy = set_policy(_user(request), request.POST.get("mode", ""))
            messages.success(request, f"幂等策略已写定：{policy.label}，当天不许换弦。")
            return HttpResponseRedirect("/policy/")
        except ServiceError as exc:
            messages.error(request, str(exc))
    return render(request, "policy.html", {
        "lab": settings.LAB_NAME, "user": _user(request),
        "policy": policy, "locked_today": locked_today,
        "choices": Policy.CHOICES,
    })
