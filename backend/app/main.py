from __future__ import annotations

import csv
import json
import io
import json
import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Annotated, Any

from fastapi import (
    BackgroundTasks,
    Body,
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Query,
    Request,
)
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response

from .config import Settings
from .auth import ALL_MENU_PERMISSIONS, AuthService, AuthenticationError, AuthorizationError
from .crm_import import CrmImportValidationError, parse_crm_import_base64, template_fields
from .db import Database
from .plans import AnnualPlanRepository
from .investment import InvestmentService
from .ontology import (
    DEFAULT_METADATA_DIR,
    ActionValidationError,
    OntologyNotFoundError,
    OntologyPermissionError,
    OntologyService,
    load_ontology,
)
from .repository import SnapshotRepository
from .project_repository import ProjectRepository
from .pm_assistant import PmAssistantError, PmAssistantService
from .pagination import CursorError
from .source import build_source
from .sync_service import SyncService
from .xlsx_timesheet import parse_timesheet_xlsx_base64
from .china_holiday_calendar import ChinaHolidayCalendarService


performance_logger = logging.getLogger("tsg_pm.performance")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    database = Database(settings.database_path)
    source = build_source(settings)
    repository = SnapshotRepository(database)
    china_holiday_calendar = ChinaHolidayCalendarService(
        settings.china_holiday_rest_ics_url,
        settings.china_holiday_work_ics_url,
        cache_hours=settings.china_holiday_cache_hours,
    )
    project_repository = ProjectRepository(
        database,
        settings.timezone,
        require_crm_project_match=settings.require_crm_project_match,
        holiday_calendar=china_holiday_calendar,
    )
    sync_service = SyncService(database, source, settings)
    # Project analytics are derived in the ontology; base programs and the assistant read them from there.

    auth_service = AuthService(database, session_hours=settings.session_hours)
    investment_service = InvestmentService(database, project_repository, repository, settings.timezone)
    annual_plans = AnnualPlanRepository(database, settings.timezone)
    ontology_service = OntologyService(
        load_ontology(settings.ontology_metadata_dir or DEFAULT_METADATA_DIR),
        database,
        project_repository,
        repository,
    )
    # G5: the assistant answers from the ontology, as the asking user.
    pm_assistant = PmAssistantService(settings, ontology_service, database)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        database.initialize()
        ontology_service.register_version()
        if settings.auth_enabled:
            auth_service.ensure_initial_admin(
                settings.initial_admin_username, settings.initial_admin_password
            )
        project_repository.remove_legacy_demo_time_entries()
        if settings.auto_import_on_empty and database.active_snapshot_id() is None:
            job = sync_service.create_job()
            await sync_service.run_job(job["job_id"])
        if settings.require_crm_project_match:
            project_repository.cleanup_legacy_orphaned_maintenance_once()
        elif settings.source_file.name.endswith(".example.json"):
            project_repository.ensure_demo_data()
        ontology_service.warm_index()
        yield

    app = FastAPI(
        title="TSG PM Data API",
        version="1.0.0",
        description="FastAPI query orchestration and SQLite persistence for TSG PM snapshots.",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.settings = settings
    app.state.database = database
    app.state.repository = repository
    app.state.project_repository = project_repository
    app.state.sync_service = sync_service
    app.state.pm_assistant = pm_assistant
    app.state.auth_service = auth_service
    app.state.ontology_service = ontology_service
    app.state.investment_service = investment_service
    app.state.china_holiday_calendar = china_holiday_calendar
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def collect_request_metrics(request: Request, call_next: Any) -> Response:
        started = time.perf_counter()
        response = await call_next(request)
        duration_ms = (time.perf_counter() - started) * 1000
        response, payload = await _materialize_json_response(response)
        response.headers["Server-Timing"] = f"app;dur={duration_ms:.1f}"
        if request.url.path.startswith("/api/v1"):
            endpoint = getattr(request.scope.get("route"), "path", request.url.path)
            returned_rows = _returned_row_count(payload)
            is_import = "/import" in endpoint or endpoint.endswith("/sync-jobs")
            try:
                database.record_api_metric(
                    endpoint=endpoint,
                    method=request.method,
                    status_code=response.status_code,
                    duration_ms=duration_ms,
                    returned_rows=returned_rows,
                    is_import=is_import,
                    created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                )
            except Exception:
                performance_logger.exception("request metrics persistence failed")
            if duration_ms >= settings.slow_request_ms:
                performance_logger.warning(
                    "slow request endpoint=%s method=%s status=%s duration_ms=%.1f rows=%s",
                    endpoint,
                    request.method,
                    response.status_code,
                    duration_ms,
                    returned_rows,
                )
        return response

    def _bearer_token(authorization: str | None) -> str | None:
        if not authorization or not authorization.startswith("Bearer "):
            return None
        token = authorization.removeprefix("Bearer ").strip()
        return token or None

    def _permission_for_path(path: str) -> str | None:
        if path.startswith("/api/v1/exports"):
            return "crm"  # the full CRM snapshot (7.7.0: formerly open to every logged-in user)
        if path.startswith("/api/v1/pm-assistant"):
            return "projects"
        if path.startswith("/api/v1/records"):
            return "crm"
        if path == "/api/v1/project-health":
            return "health"
        if path == "/api/v1/resource-operations":
            return "resource"
        if path.startswith("/api/v1/investment"):
            return "investment"
        if path.startswith("/api/v1/department"):
            return "department"
        if path.startswith("/api/v1/employee-worktime") or path.startswith("/api/v1/worktime-baseline") or path.startswith("/api/v1/time-entries"):
            return "employeeWorktime"
        if path.startswith("/api/v1/crm-imports") or path.startswith("/api/v1/sync-") or path == "/api/v1/sync-status":
            return "sync"
        if path.startswith("/api/v1/projects"):
            return "projects"
        return None

    async def require_auth(
        request: Request,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        if not settings.auth_enabled:
            user = {"username": "test", "is_admin": True, "permissions": list(ALL_MENU_PERMISSIONS)}
            request.state.user = user
            return user
        token = _bearer_token(authorization)
        if not token:
            raise HTTPException(status_code=401, detail="请先登录")
        try:
            user = auth_service.current_user(token)
        except AuthenticationError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        permission = _permission_for_path(request.url.path)
        if permission and not user["is_admin"] and permission not in user["permissions"]:
            raise HTTPException(status_code=403, detail="当前用户没有该页面的访问权限")
        request.state.user = user
        return user

    def audit_legacy_edit(
        request: Request,
        action_type: str,
        object_type: str,
        primary_key: str,
        payload: dict[str, Any],
        edits: list[dict[str, str]],
    ) -> None:
        """Record writes made through the legacy REST endpoints in the ontology audit log."""
        user = getattr(request.state, "user", None) or {}
        ontology_service.log_action(
            action_type, object_type, primary_key, payload,
            actor=user.get("username") or "anonymous", status="succeeded",
            edits=edits, channel="legacy_api",
        )

    async def require_admin(
        user: dict[str, Any] = Depends(require_auth),
    ) -> dict[str, Any]:
        if not user["is_admin"]:
            raise HTTPException(status_code=403, detail="仅管理员可以管理系统用户")
        return user

    auth = Depends(require_auth)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        return _problem(
            request,
            status=exc.status_code,
            title="Request failed",
            detail=str(exc.detail),
            code=f"HTTP_{exc.status_code}",
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return _problem(
            request,
            status=422,
            title="Validation failed",
            detail=str(exc.errors()),
            code="VALIDATION_ERROR",
        )

    @app.get("/health", tags=["System"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/v1/performance", tags=["System"])
    def get_performance_metrics(
        sample_limit: int = Query(2000, ge=1, le=10000),
        _user: dict[str, Any] = Depends(require_admin),
    ) -> dict[str, Any]:
        """Recent P50/P95 endpoint timings, row counts and import durations."""
        return {"data": database.performance_summary(limit=sample_limit)}

    @app.get(
        "/api/v1/calendar/china-holidays",
        dependencies=[Depends(require_auth)],
        tags=["Calendar"],
    )
    def get_china_holidays() -> dict[str, Any]:
        """Public holidays and makeup workdays used to annotate project date pickers."""
        return china_holiday_calendar.list_days()

    @app.post("/api/v1/auth/login", tags=["Authentication"])
    def login(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        if not settings.auth_enabled:
            return {"data": {"token": "development-bypass", "user": {"username": "test", "is_admin": True, "permissions": list(ALL_MENU_PERMISSIONS), "is_active": True}, "expires_at": None}}
        try:
            return {"data": auth_service.login(payload.get("username", ""), payload.get("password", ""))}
        except AuthenticationError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc

    @app.get("/api/v1/auth/me", tags=["Authentication"])
    def current_account(
        user: dict[str, Any] = Depends(require_auth),
    ) -> dict[str, Any]:
        return {"data": user}

    @app.post("/api/v1/auth/logout", tags=["Authentication"])
    def logout(
        authorization: Annotated[str | None, Header()] = None,
        _user: dict[str, Any] = Depends(require_auth),
    ) -> dict[str, bool]:
        if settings.auth_enabled:
            token = _bearer_token(authorization)
            if token:
                auth_service.logout(token)
        return {"data": True}

    def require_in_scope(user: dict[str, Any], project_number: str) -> None:
        """Endpoints that read the base program directly follow the same data scope as the ontology (D2)."""
        scope = ontology_service.project_scope(user)
        if scope is not None and project_number not in scope:
            raise HTTPException(status_code=404, detail="project not found")

    @app.get("/api/v1/project-managers", dependencies=[auth], tags=["Users"])
    def list_project_manager_names() -> dict[str, Any]:
        """Names of active project-manager accounts, the choices for a project's project manager (D10)."""
        return {"data": auth_service.project_manager_names()}

    @app.get("/api/v1/pm-assistant/log", tags=["PM Assistant"])
    def pm_assistant_log(
        limit: int = Query(100, ge=1, le=500), _user: dict[str, Any] = Depends(require_admin),
    ) -> dict[str, Any]:
        """Requirements baseline D17: who asked what and which tools ran; answers are not kept."""
        return {"data": pm_assistant.audit_log(limit)}

    @app.get("/api/v1/users", tags=["Users"])
    def list_users(
        _user: dict[str, Any] = Depends(require_admin),
    ) -> dict[str, Any]:
        return {"data": auth_service.list_users()}

    @app.post("/api/v1/users", status_code=201, tags=["Users"])
    def create_user(
        payload: dict[str, Any] = Body(...),
        _user: dict[str, Any] = Depends(require_admin),
    ) -> dict[str, Any]:
        try:
            return {"data": auth_service.create_user(payload)}
        except (ValueError, AuthorizationError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.patch("/api/v1/users/{username}", tags=["Users"])
    def update_user(
        username: str,
        payload: dict[str, Any] = Body(...),
        _user: dict[str, Any] = Depends(require_admin),
    ) -> dict[str, Any]:
        try:
            return {"data": auth_service.update_user(username, payload)}
        except (ValueError, AuthorizationError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/v1/pm-assistant/chat", dependencies=[auth], tags=["PM Assistant"])
    async def pm_assistant_chat(
        payload: dict[str, Any] = Body(...), user: dict[str, Any] = Depends(require_auth)
    ) -> dict[str, Any]:
        message = payload.get("message")
        if not isinstance(message, str) or not message.strip():
            raise HTTPException(status_code=422, detail="message is required")
        if len(message) > 2000:
            raise HTTPException(status_code=422, detail="message is too long")
        history = payload.get("history", [])
        if not isinstance(history, list):
            raise HTTPException(status_code=422, detail="history must be a list")
        try:
            answer = await pm_assistant.answer(user, message.strip(), history)
        except PmAssistantError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return {"data": answer}

    @app.post(
        "/api/v1/projects/{project_number}/ai-analysis",
        dependencies=[auth],
        tags=["PM Assistant"],
    )
    async def project_ai_analysis(project_number: str, user: dict[str, Any] = Depends(require_auth)) -> dict[str, Any]:
        require_in_scope(user, project_number)
        try:
            analysis = await pm_assistant.analyze_project(user, project_number)
        except PmAssistantError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return {"data": analysis}

    @app.get("/api/v1/records", dependencies=[auth], tags=["Records"])
    def list_records(
        request: Request,
        data_type: str | None = Query(None, pattern="^(contract_order|sales_opportunity)$"),
        project_number: str | None = None,
        owner: str | None = None,
        status: str | None = None,
        sales_stage: str | None = Query(None, max_length=200),
        probability: int | None = Query(None, ge=0, le=100),
        q: str | None = Query(None, min_length=1, max_length=200),
        signed_from: str | None = None,
        signed_to: str | None = None,
        updated_since: str | None = None,
        cursor: str | None = Query(None, min_length=8, max_length=1000),
        page: int = Query(1, ge=1),
        page_size: int = Query(50, ge=1, le=200),
        sort: str = Query("project_number", pattern="^-?(project_number|subject|signed_at|owner|probability|sales_stage|status|source_updated_at)$"),
    ) -> dict[str, Any]:
        if page > 1 and not cursor:
            raise HTTPException(status_code=422, detail="deep pages require cursor")
        try:
            result = repository.list_records(
                data_type=data_type,
                project_number=project_number,
                owner=owner,
                status=status,
                sales_stage=sales_stage,
                probability=probability,
                query=q,
                signed_from=signed_from,
                signed_to=signed_to,
                cursor=cursor,
                page_size=page_size,
                sort=sort,
            )
        except CursorError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if result is None:
            raise HTTPException(status_code=503, detail="no validated snapshot is available")
        if updated_since and result["updated_at"] <= updated_since:
            result["records"] = []
            result["total_records"] = 0
            result["total_pages"] = 0
            result["next_cursor"] = None
        query_string = request.url.query
        self_link = f"{request.url.path}?{query_string}" if query_string else request.url.path
        next_link = None
        previous_link = None
        if result["next_cursor"]:
            next_link = _cursor_link(request, result["next_cursor"])
        return {
            "data": result["records"],
            "meta": {key: result[key] for key in (
                "page_size", "total_records", "total_pages", "next_cursor",
                "schema_version", "updated_at",
            )},
            "links": {"self": self_link, "next": next_link, "previous": previous_link},
        }

    @app.get("/api/v1/records/{record_id}", dependencies=[auth], tags=["Records"])
    def get_record(record_id: str) -> dict[str, Any]:
        record = repository.get_record(record_id)
        if record is None:
            raise HTTPException(status_code=404, detail="record not found")
        return {"data": record}

    @app.get("/api/v1/projects", dependencies=[auth], tags=["Projects"])
    def list_projects(
        contract_type: str | None = Query(
            None, pattern="^(construction|maintenance|open_ended|sales|unknown)$"
        ),
        lifecycle_status: str | None = Query(
            None, pattern="^(planned|active|on_hold|completed|closed)$"
        ),
        category: str | None = Query(None, min_length=1, max_length=200),
        risk_level: str | None = Query(None, pattern="^(healthy|warning|critical)$"),
        q: str | None = Query(None, min_length=1, max_length=200),
        cursor: str | None = Query(None, min_length=8, max_length=1000),
        page_size: int = Query(50, ge=1, le=200),
        user: dict[str, Any] = Depends(require_auth),
    ) -> dict[str, Any]:
        """Compatibility wrapper: the project list is derived in the ontology (function projectList)."""
        return ontology_page(user, "projectList", {
            "contractType": contract_type, "lifecycleStatus": lifecycle_status, "category": category,
            "riskLevel": risk_level, "query": q, "cursor": cursor, "pageSize": page_size,
        })

    @app.get(
        "/api/v1/projects/{project_number}", dependencies=[auth], tags=["Projects"]
    )
    def get_project(project_number: str, user: dict[str, Any] = Depends(require_auth)) -> dict[str, Any]:
        """Compatibility wrapper: the project view is derived in the ontology (function projectOverview)."""
        return {"data": project_overview(user, project_number)}

    @app.get("/api/v1/project-health", dependencies=[auth], tags=["Projects"])
    def get_project_health(
        month: str | None = Query(None, pattern=r"^\d{4}-\d{2}$"),
        start_month: str | None = Query(None, pattern=r"^\d{4}-\d{2}$"),
        end_month: str | None = Query(None, pattern=r"^\d{4}-\d{2}$"),
        project_number: str | None = None,
        contract_type: str | None = Query(
            None, pattern="^(construction|maintenance|open_ended|sales|unknown)$"
        ),
        risk_level: str | None = Query(None, pattern="^(critical|warning|healthy)$"),
        master_category: str | None = Query(None, pattern="^(项目|商机)$"),
        category: str | None = Query(None, min_length=1, max_length=200),
        lifecycle_status: str | None = Query(
            None, pattern="^(planned|active|on_hold|completed|closed)$"
        ),
        q: str | None = None,
        cursor: str | None = Query(None, min_length=8, max_length=1000),
        page_size: int = Query(50, ge=1, le=200),
        user: dict[str, Any] = Depends(require_auth),
    ) -> dict[str, Any]:
        """Compatibility wrapper: project monitoring is derived in the ontology (function portfolioHealth)."""
        return ontology_page(user, "portfolioHealth", {
            "month": month, "startMonth": start_month, "endMonth": end_month, "projectNumber": project_number,
            "contractType": contract_type, "riskLevel": risk_level, "masterCategory": master_category,
            "category": category, "lifecycleStatus": lifecycle_status, "query": q, "cursor": cursor, "pageSize": page_size,
        })

    def ontology_page(user: dict[str, Any], function_name: str, parameters: dict[str, Any]) -> Any:
        """Run a page function for ``user``; failures keep the plain REST error shapes."""
        try:
            return ontology_service.execute_function(
                user, function_name, {key: value for key, value in parameters.items() if value is not None}
            )["result"]
        except OntologyNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except OntologyPermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except (ActionValidationError, CursorError) as exc:
            if "不存在" in str(exc):
                raise HTTPException(status_code=404, detail="project not found") from exc
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    def project_overview(user: dict[str, Any], project_number: str) -> dict[str, Any]:
        return ontology_page(user, "projectOverview", {"project": project_number})

    @app.get(
        "/api/v1/projects/{project_number}/records",
        dependencies=[auth],
        tags=["Projects"],
    )
    def get_project_records(project_number: str, user: dict[str, Any] = Depends(require_auth)) -> dict[str, Any]:
        require_in_scope(user, project_number)
        records = repository.get_project(project_number)
        if not records:
            raise HTTPException(status_code=404, detail="project not found")
        return {"data": records, "meta": {"total_records": len(records)}}

    # ------------------------------------------------------------ maintenance (compatibility wrappers)
    # Maintenance after import happens in the ontology: every endpoint below runs the matching ontology
    # action (validation, permissions and audit live there) and answers in its former shape.
    SETTING_PARAMS = {"contract_type": "contractType", "lifecycle_status": "lifecycleStatus",
                      "planned_person_days": "plannedPersonDays", "hours_per_person_day": "hoursPerPersonDay",
                      "planned_start": "plannedStart", "planned_end": "plannedEnd",
                      "project_manager": "projectManager", "notes": "notes"}
    MILESTONE_PARAMS = {"name": "name", "planned_start": "plannedStart", "planned_end": "plannedEnd",
                        "owner": "owner", "deliverable": "deliverable", "status": "status",
                        "notes": "notes", "sequence_no": "sequenceNo",
                        "payment_term_id": "paymentTermId"}
    PAYMENT_NODE_PARAMS = {"name": "name", "planned_date": "plannedDate", "planned_amount": "plannedAmount", "notes": "notes"}
    PAYMENT_TERM_PARAMS = {"name": "name", "payment_ratio": "paymentRatio", "planned_amount": "plannedAmount", "notes": "notes"}
    INTERVENTION_PARAMS = {"title": "title", "intervention_type": "interventionType", "owner": "owner",
                           "due_date": "dueDate", "notes": "notes"}
    CONFIRM_PARAMS = {"confirmed_by": "confirmedBy", "actual_amount": "actualAmount", "actual_date": "actualDate"}

    def action_parameters(action: str, payload: dict[str, Any], mapping: dict[str, str], **fixed: Any) -> dict[str, Any]:
        """Former field names → action parameters; fields the action does not take are ignored."""
        known = {p.api_name for p in ontology_service.ontology.action_type(action).parameters}
        params = {mapping[key]: value for key, value in payload.items() if key in mapping and mapping[key] in known}
        return {**params, **fixed}

    def maintain(user: dict[str, Any], action: str, parameters: dict[str, Any], *,
                 project_number: str, target: tuple[str, str] | None = None, missing: str = "not found") -> Any:
        if target is not None:
            obj = ontology_service.context(user).get(*target)
            if obj is None or obj.get("projectNumber") != project_number:
                raise HTTPException(status_code=404, detail=missing)
        try:
            return ontology_service.apply_action(user, action, parameters, include_record=True, channel="business").get("record")
        except OntologyNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except OntologyPermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ActionValidationError as exc:
            if f"Project:{project_number} 不存在" in str(exc):
                raise HTTPException(status_code=404, detail="project not found") from exc
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.patch("/api/v1/projects/{project_number}/settings", tags=["Projects"])
    def update_project_settings(
        project_number: str, payload: dict[str, Any] = Body(...), user: dict[str, Any] = Depends(require_auth),
    ) -> dict[str, Any]:
        maintain(user, "configureProject", action_parameters("configureProject", payload, SETTING_PARAMS, project=project_number),
                 project_number=project_number)
        return {"data": project_overview(user, project_number)}

    @app.post("/api/v1/projects/{project_number}/milestones", status_code=201, tags=["Projects"])
    def create_milestone(
        project_number: str, payload: dict[str, Any] = Body(...), user: dict[str, Any] = Depends(require_auth),
    ) -> dict[str, Any]:
        if "planned_end" not in payload and "planned_date" in payload:  # former alias
            payload = {**payload, "planned_end": payload["planned_date"]}
        params = action_parameters("createMilestone", payload, MILESTONE_PARAMS, project=project_number)
        return {"data": maintain(user, "createMilestone", params, project_number=project_number)}

    @app.patch("/api/v1/projects/{project_number}/milestones/{milestone_id}", tags=["Projects"])
    def update_milestone(
        project_number: str, milestone_id: str, payload: dict[str, Any] = Body(...),
        user: dict[str, Any] = Depends(require_auth),
    ) -> dict[str, Any]:
        params = action_parameters("editMilestone", payload, MILESTONE_PARAMS, milestone=milestone_id)
        return {"data": maintain(user, "editMilestone", params, project_number=project_number,
                                 target=("Milestone", milestone_id), missing="milestone not found")}

    @app.post("/api/v1/projects/{project_number}/milestones/{milestone_id}/confirm", tags=["Projects"])
    def confirm_milestone(
        project_number: str, milestone_id: str, payload: dict[str, Any] = Body(...),
        user: dict[str, Any] = Depends(require_auth),
    ) -> dict[str, Any]:
        params = action_parameters("confirmMilestoneCompletion", payload, CONFIRM_PARAMS, milestone=milestone_id)
        return {"data": maintain(user, "confirmMilestoneCompletion", params, project_number=project_number,
                                 target=("Milestone", milestone_id), missing="milestone not found")}

    @app.delete("/api/v1/projects/{project_number}/milestones/{milestone_id}", tags=["Projects"])
    def delete_milestone(
        project_number: str, milestone_id: str, user: dict[str, Any] = Depends(require_auth)
    ) -> dict[str, Any]:
        maintain(user, "deleteMilestone", {"milestone": milestone_id}, project_number=project_number,
                 target=("Milestone", milestone_id), missing="milestone not found")
        return {"data": project_overview(user, project_number)}

    @app.post("/api/v1/projects/{project_number}/payment-terms", status_code=201, tags=["Projects"])
    def create_payment_term(
        project_number: str, payload: dict[str, Any] = Body(...), user: dict[str, Any] = Depends(require_auth),
    ) -> dict[str, Any]:
        params = action_parameters("createPaymentTerm", payload, PAYMENT_TERM_PARAMS, project=project_number)
        return {"data": maintain(user, "createPaymentTerm", params, project_number=project_number)}

    @app.patch("/api/v1/projects/{project_number}/payment-terms/{payment_term_id}", tags=["Projects"])
    def update_payment_term(
        project_number: str, payment_term_id: str, payload: dict[str, Any] = Body(...),
        user: dict[str, Any] = Depends(require_auth),
    ) -> dict[str, Any]:
        params = action_parameters("editPaymentTerm", payload, PAYMENT_TERM_PARAMS, paymentTerm=payment_term_id)
        return {"data": maintain(user, "editPaymentTerm", params, project_number=project_number,
                                 target=("PaymentTerm", payment_term_id), missing="payment term not found")}

    @app.delete("/api/v1/projects/{project_number}/payment-terms/{payment_term_id}", status_code=204, tags=["Projects"])
    def delete_payment_term(project_number: str, payment_term_id: str, user: dict[str, Any] = Depends(require_auth)) -> None:
        maintain(user, "deletePaymentTerm", {"paymentTerm": payment_term_id}, project_number=project_number,
                 target=("PaymentTerm", payment_term_id), missing="payment term not found")

    @app.patch("/api/v1/projects/{project_number}/payment-nodes/{payment_node_id}", tags=["Projects"])
    def update_payment_node(
        project_number: str, payment_node_id: str, payload: dict[str, Any] = Body(...),
        user: dict[str, Any] = Depends(require_auth),
    ) -> dict[str, Any]:
        params = action_parameters("editPaymentNode", payload, PAYMENT_NODE_PARAMS, paymentNode=payment_node_id)
        return {"data": maintain(user, "editPaymentNode", params, project_number=project_number,
                                 target=("PaymentNode", payment_node_id), missing="payment node not found")}

    @app.delete("/api/v1/projects/{project_number}/payment-nodes/{payment_node_id}", status_code=204, tags=["Projects"])
    def delete_payment_node(project_number: str, payment_node_id: str, user: dict[str, Any] = Depends(require_auth)) -> None:
        maintain(user, "deletePaymentNode", {"paymentNode": payment_node_id}, project_number=project_number,
                 target=("PaymentNode", payment_node_id), missing="payment node not found")

    @app.post("/api/v1/projects/{project_number}/payment-nodes/{payment_node_id}/confirm", tags=["Projects"])
    def confirm_payment_node(
        project_number: str, payment_node_id: str, payload: dict[str, Any] = Body(...),
        user: dict[str, Any] = Depends(require_auth),
    ) -> dict[str, Any]:
        params = action_parameters("confirmPaymentReceived", payload, CONFIRM_PARAMS, paymentNode=payment_node_id)
        return {"data": maintain(user, "confirmPaymentReceived", params, project_number=project_number,
                                 target=("PaymentNode", payment_node_id), missing="payment node not found")}

    @app.post("/api/v1/projects/{project_number}/interventions", status_code=201, tags=["Projects"])
    def create_intervention(
        project_number: str, payload: dict[str, Any] = Body(...), user: dict[str, Any] = Depends(require_auth),
    ) -> dict[str, Any]:
        params = action_parameters("openIntervention", payload, INTERVENTION_PARAMS, project=project_number)
        return {"data": maintain(user, "openIntervention", params, project_number=project_number)}

    @app.post("/api/v1/projects/{project_number}/interventions/{intervention_id}/status", tags=["Projects"])
    def update_intervention_status(
        project_number: str, intervention_id: str, payload: dict[str, Any] = Body(...),
        user: dict[str, Any] = Depends(require_auth),
    ) -> dict[str, Any]:
        """D31: the project drawer moves an intervention forward or closes it (closed ones are locked)."""
        params = action_parameters("updateInterventionStatus", payload, {"status": "status", "notes": "notes"},
                                   intervention=intervention_id)
        return {"data": maintain(user, "updateInterventionStatus", params, project_number=project_number,
                                 target=("Intervention", intervention_id), missing="intervention not found")}

    @app.post(
        "/api/v1/time-entries/import",
        dependencies=[auth],
        tags=["Time Entries"],
    )
    def import_time_entries(
        background: BackgroundTasks, entries: list[dict[str, Any]] = Body(...)
    ) -> dict[str, Any]:
        try:
            result = project_repository.import_time_entries(entries)
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        background.add_task(ontology_service.warm_index)
        return result

    @app.post(
        "/api/v1/time-entries/import-xlsx",
        dependencies=[auth],
        tags=["Time Entries"],
    )
    def import_time_entries_xlsx(
        background: BackgroundTasks, payload: dict[str, Any] = Body(...)
    ) -> dict[str, Any]:
        try:
            entries = parse_timesheet_xlsx_base64(payload["content_base64"])
            result = project_repository.import_time_entries(entries)
            background.add_task(ontology_service.warm_index)
            return {
                **result,
                "parsed": len(entries),
                "filename": payload.get("filename"),
                "date_range": {
                    "start": min(entry["work_date"] for entry in entries),
                    "end": max(entry["work_date"] for entry in entries),
                },
            }
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get(
        "/api/v1/worktime-baseline", dependencies=[auth], tags=["Time Entries"]
    )
    def get_worktime_baseline() -> dict[str, Any]:
        return {"data": project_repository.get_worktime_baseline()}

    @app.patch(
        "/api/v1/worktime-baseline", dependencies=[auth], tags=["Time Entries"]
    )
    def update_worktime_baseline(
        request: Request, payload: dict[str, Any] = Body(...)
    ) -> dict[str, Any]:
        try:
            result = project_repository.update_worktime_baseline(payload)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        # Thresholds change how risk, health and resource metrics are computed.
        audit_legacy_edit(request, "updateOperatingPolicy", "OperatingPolicy", "default", payload, [])
        return {"data": result}

    @app.get(
        "/api/v1/employee-worktime", dependencies=[auth], tags=["Time Entries"]
    )
    def get_employee_worktime(
        period: str = Query("month", pattern="^(month|year)$"),
        month: str | None = Query(None, pattern=r"^\d{4}-\d{2}$"),
        year: str | None = Query(None, pattern=r"^\d{4}$"),
        employee_name: str | None = None,
        user: dict[str, Any] = Depends(require_auth),
    ) -> dict[str, Any]:
        """Compatibility wrapper: the worktime assessment is derived in the ontology (function employeeWorktimeAssessment)."""
        return ontology_page(user, "employeeWorktimeAssessment",
                             {"period": period, "month": month, "year": year, "employeeName": employee_name})

    @app.get(
        "/api/v1/resource-operations", dependencies=[auth], tags=["Time Entries"]
    )
    def get_resource_operations(
        month: str | None = Query(None, pattern=r"^\d{4}-\d{2}$"),
        start_month: str | None = Query(None, pattern=r"^\d{4}-\d{2}$"),
        end_month: str | None = Query(None, pattern=r"^\d{4}-\d{2}$"),
        employee_name: str | None = Query(None, max_length=100),
        cursor: str | None = Query(None, min_length=8, max_length=1000),
        page_size: int = Query(50, ge=1, le=200),
        user: dict[str, Any] = Depends(require_auth),
    ) -> dict[str, Any]:
        """Compatibility wrapper: resource operations are derived in the ontology (function resourceOperations)."""
        return ontology_page(user, "resourceOperations", {
            "month": month, "startMonth": start_month, "endMonth": end_month,
            "employeeName": employee_name, "cursor": cursor, "pageSize": page_size})

    def _finance_visible(user: dict[str, Any]) -> bool:
        return bool(user.get("is_admin")) or bool({"projects", "crm"} & set(user.get("permissions") or []))

    # ------------------------------------------------------------ department review
    @app.get("/api/v1/department/review", dependencies=[auth], tags=["Department"])
    def department_review(
        year: int | None = Query(None, ge=2000, le=2100),
        month: str | None = Query(None, pattern=r"^\d{4}-\d{2}$"),
        user: dict[str, Any] = Depends(require_auth),
    ) -> dict[str, Any]:
        """Compatibility wrappers below: department analytics are derived in the ontology."""
        return {"data": ontology_page(user, "departmentOperatingReview", {"year": year, "month": month})}

    @app.get("/api/v1/department/resource-health", tags=["Department"])
    def department_resource_health(
        month: str | None = Query(None, pattern=r"^\d{4}-\d{2}$"), user: dict[str, Any] = Depends(require_auth)
    ) -> dict[str, Any]:
        return {"data": ontology_page(user, "resourceHealth", {"month": month})}

    @app.get("/api/v1/department/annual-plans", dependencies=[auth], tags=["Department"])
    def list_annual_plans() -> dict[str, Any]:
        return {"data": annual_plans.list_plans()}

    @app.put("/api/v1/department/annual-plans/{year}", tags=["Department"])
    def upsert_annual_plan(year: int, payload: dict[str, Any] = Body(...), user: dict[str, Any] = Depends(require_auth)) -> dict[str, Any]:
        """Compatibility wrapper over the ontology action setAnnualOperatingPlan."""
        mapping = {"department": "department", "new_contract_target": "newContractTarget",
                   "collection_target": "collectionTarget", "capacity_person_days_target": "capacityPersonDaysTarget",
                   "headcount_target": "headcountTarget", "utilization_target_percent": "utilizationTargetPercent", "notes": "notes"}
        params = action_parameters("setAnnualOperatingPlan", payload, mapping, policy="default", year=year)
        try:
            record = ontology_service.apply_action(user, "setAnnualOperatingPlan", params, include_record=True)["record"]
        except OntologyPermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ActionValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"data": record}

    @app.get("/api/v1/investment/analysis", tags=["Investment"])
    def investment_analysis(
        start_month: str | None = Query(None, pattern=r"^\d{4}-\d{2}$"),
        end_month: str | None = Query(None, pattern=r"^\d{4}-\d{2}$"),
        user: dict[str, Any] = Depends(require_auth),
    ) -> dict[str, Any]:
        try:
            return {"data": investment_service.analysis(
                start_month=start_month, end_month=end_month, include_cost=_finance_visible(user)
            )}
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/v1/investment/cost-objects/{code}", dependencies=[auth], tags=["Investment"])
    def investment_cost_object(code: str) -> dict[str, Any]:
        item = investment_service.cost_object(code)
        if item is None:
            raise HTTPException(status_code=404, detail="project code not found")
        return {"data": item}

    @app.get("/api/v1/investment/settings", dependencies=[auth], tags=["Investment"])
    def get_investment_settings() -> dict[str, Any]:
        return {"data": investment_service.get_settings()}

    @app.patch("/api/v1/investment/settings", tags=["Investment"])
    def update_investment_settings(
        request: Request, payload: dict[str, Any] = Body(...), user: dict[str, Any] = Depends(require_auth)
    ) -> dict[str, Any]:
        if "standard_person_day_cost" in payload and not _finance_visible(user):
            raise HTTPException(status_code=403, detail="维护标准人天成本需要财务金额查看权限")
        try:
            result = investment_service.update_settings(payload)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        audit_legacy_edit(request, "updateInvestmentSettings", "CostObject", "*", payload, [])
        return {"data": result}

    @app.get("/api/v1/sync-status", dependencies=[auth], tags=["Sync"])
    def get_sync_status() -> dict[str, Any]:
        status = repository.sync_status()
        status["timezone"] = settings.timezone
        status["next_scheduled_at"] = None
        return status

    @app.get("/api/v1/crm-imports/template", dependencies=[auth], tags=["CRM Imports"])
    def get_crm_import_template(
        data_type: str = Query(..., pattern="^(contract_order|sales_opportunity)$"),
    ) -> dict[str, Any]:
        return template_fields(data_type)

    @app.get("/api/v1/crm-imports", dependencies=[auth], tags=["CRM Imports"])
    def list_crm_imports(
        page: int = Query(1, ge=1),
        page_size: int = Query(10, ge=1, le=50),
    ) -> dict[str, Any]:
        """List successful manual CRM uploads, newest first."""
        return repository.list_crm_import_logs(page=page, page_size=page_size)

    @app.post("/api/v1/crm-imports", dependencies=[auth], tags=["CRM Imports"])
    def import_crm_file(
        background: BackgroundTasks, payload: dict[str, Any] = Body(...)
    ) -> dict[str, Any]:
        data_type = str(payload.get("data_type") or "")
        filename = str(payload.get("filename") or "")
        content_base64 = str(payload.get("content_base64") or "")
        if not filename or not content_base64:
            raise HTTPException(status_code=422, detail="filename and content_base64 are required")
        try:
            records, summary = parse_crm_import_base64(
                content_base64=content_base64,
                filename=filename,
                data_type=data_type,
            )
            result = sync_service.import_manual_records(
                data_type=data_type,
                records=records,
                filename=filename,
            )
        except (CrmImportValidationError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        background.add_task(ontology_service.warm_index)
        return {"data": {**summary, **result}}

    @app.post(
        "/api/v1/sync-jobs",
        status_code=202,
        dependencies=[auth],
        tags=["Sync"],
    )
    async def create_sync_job() -> dict[str, Any]:
        raise HTTPException(
            status_code=410,
            detail="automatic source synchronization has been retired; upload contract or opportunity data manually",
        )

    @app.get(
        "/api/v1/sync-jobs/{job_id}", dependencies=[auth], tags=["Sync"]
    )
    def get_sync_job(job_id: str) -> dict[str, Any]:
        job = sync_service.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="sync job not found")
        return job

    @app.get("/api/v1/exports/latest", dependencies=[auth], tags=["Exports"])
    def export_latest(format: str = Query(..., pattern="^(json|csv)$")) -> Response:
        snapshot = repository.export_records()
        if snapshot is None:
            raise HTTPException(status_code=503, detail="no validated snapshot is available")
        if format == "json":
            return JSONResponse(snapshot)
        return Response(
            content=_to_csv(snapshot),
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": 'attachment; filename="xtcrm-latest.csv"',
                "Last-Modified": snapshot["updated_at"],
            },
        )

    # ------------------------------------------------------------ ontology
    def ontology_call(fn, *args, **kwargs) -> Any:
        try:
            return fn(*args, **kwargs)
        except OntologyNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except OntologyPermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ActionValidationError as exc:
            raise _ActionRejected(str(exc), exc.details) from exc

    def period_call(fn, *args, **kwargs) -> Any:
        """Object browsing that may carry a statistics period: a bad month or a type without one is a 422."""
        try:
            return ontology_call(fn, *args, **kwargs)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.exception_handler(_ActionRejected)
    async def action_rejected(request: Request, exc: "_ActionRejected") -> JSONResponse:
        response = _problem(
            request, status=422, title="Action rejected",
            detail=exc.message, code="ONTOLOGY_ACTION_REJECTED",
        )
        body = json.loads(response.body)
        body["errors"] = exc.details
        return JSONResponse(status_code=422, media_type="application/problem+json", content=body)

    @app.get("/api/v1/ontology/schema", tags=["Ontology"])
    def ontology_schema(user: dict[str, Any] = Depends(require_auth)) -> dict[str, Any]:
        return {"data": ontology_service.schema(user)}

    @app.get("/api/v1/ontology/versions", tags=["Ontology"])
    def ontology_versions(user: dict[str, Any] = Depends(require_auth)) -> dict[str, Any]:
        return {"data": ontology_call(ontology_service.list_versions, user)}

    @app.get("/api/v1/ontology/versions/diff", tags=["Ontology"])
    def ontology_version_diff(
        from_version: str = Query(..., alias="from", pattern=r"^\d+\.\d+\.\d+$"),
        to_version: str = Query(..., alias="to", pattern=r"^\d+\.\d+\.\d+$"),
        user: dict[str, Any] = Depends(require_auth),
    ) -> dict[str, Any]:
        return {
            "data": ontology_call(ontology_service.diff_versions, user, from_version, to_version)
        }

    @app.get("/api/v1/ontology/versions/{version}", tags=["Ontology"])
    def ontology_version_documents(
        version: str, user: dict[str, Any] = Depends(require_auth)
    ) -> dict[str, Any]:
        return {"data": ontology_call(ontology_service.version_documents, user, version)}

    @app.post("/api/v1/ontology/objectSets/loadObjects", tags=["Ontology"])
    def ontology_load_object_set(
        payload: dict[str, Any] = Body(...), user: dict[str, Any] = Depends(require_auth)
    ) -> dict[str, Any]:
        return ontology_call(ontology_service.load_object_set, user, payload)

    @app.post("/api/v1/ontology/objectSets/aggregate", tags=["Ontology"])
    def ontology_aggregate_object_set(
        payload: dict[str, Any] = Body(...), user: dict[str, Any] = Depends(require_auth)
    ) -> dict[str, Any]:
        return ontology_call(ontology_service.aggregate_object_set, user, payload)

    @app.get("/api/v1/ontology/index", tags=["Ontology"])
    def ontology_index_status(user: dict[str, Any] = Depends(require_auth)) -> dict[str, Any]:
        return {"data": ontology_call(ontology_service.index_status, user)}

    @app.post("/api/v1/ontology/index/refresh", tags=["Ontology"])
    def ontology_index_refresh(user: dict[str, Any] = Depends(require_auth)) -> dict[str, Any]:
        return {"data": ontology_call(ontology_service.refresh_index, user)}

    @app.get("/api/v1/ontology/objects/{object_type}", tags=["Ontology"])
    def ontology_list_objects(
        request: Request,
        object_type: str,
        q: str | None = Query(None, max_length=200),
        order_by: str | None = Query(None, alias="orderBy", max_length=80),
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=200, alias="pageSize"),
        month: str | None = Query(None, max_length=7),
        month_to: str | None = Query(None, alias="monthTo", max_length=7),
        user: dict[str, Any] = Depends(require_auth),
    ) -> dict[str, Any]:
        where = {
            key.removeprefix("where."): value
            for key, value in request.query_params.items()
            if key.startswith("where.")
        }
        return period_call(
            ontology_service.list_objects, user, object_type,
            query=q, where=where, order_by=order_by, page=page, page_size=page_size, month=month, month_to=month_to,
        )

    @app.get("/api/v1/ontology/objects/{object_type}/{primary_key}", tags=["Ontology"])
    def ontology_get_object(
        object_type: str, primary_key: str, month: str | None = Query(None, max_length=7),
        user: dict[str, Any] = Depends(require_auth),
    ) -> dict[str, Any]:
        return {"data": period_call(ontology_service.get_object, user, object_type, primary_key, month=month)}

    @app.get(
        "/api/v1/ontology/objects/{object_type}/{primary_key}/links/{link_api_name}",
        tags=["Ontology"],
    )
    def ontology_traverse(
        object_type: str,
        primary_key: str,
        link_api_name: str,
        page: int = Query(1, ge=1),
        page_size: int = Query(50, ge=1, le=200, alias="pageSize"),
        month: str | None = Query(None, max_length=7),
        user: dict[str, Any] = Depends(require_auth),
    ) -> dict[str, Any]:
        return period_call(
            ontology_service.traverse, user, object_type, primary_key, link_api_name,
            page=page, page_size=page_size, month=month,
        )

    @app.get("/api/v1/ontology/objects/{object_type}/{primary_key}/actions", tags=["Ontology"])
    def ontology_object_actions(
        object_type: str, primary_key: str, user: dict[str, Any] = Depends(require_auth)
    ) -> dict[str, Any]:
        return {
            "data": ontology_call(
                ontology_service.available_actions, user, object_type, primary_key
            )
        }

    @app.post("/api/v1/ontology/actions/{action_type}/apply", tags=["Ontology"])
    def ontology_apply_action(
        action_type: str,
        payload: dict[str, Any] = Body(...),
        user: dict[str, Any] = Depends(require_auth),
        channel: Annotated[str | None, Header(alias="X-TSG-Channel")] = None,
    ) -> dict[str, Any]:
        parameters = payload.get("parameters", {})
        validate_only = payload.get("validateOnly", False)
        if not isinstance(validate_only, bool):
            raise HTTPException(status_code=422, detail="validateOnly must be a boolean")
        return {
            "data": ontology_call(
                ontology_service.apply_action, user, action_type, parameters,
                validate_only=validate_only, channel="business" if channel == "business" else "ontology",
            )
        }

    @app.get("/api/v1/ontology/action-log", tags=["Ontology"])
    def ontology_action_log(
        object_type: str | None = Query(None, alias="objectType"),
        primary_key: str | None = Query(None, alias="primaryKey"),
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100, alias="pageSize"),
        user: dict[str, Any] = Depends(require_auth),
    ) -> dict[str, Any]:
        return ontology_service.action_log(
            user, object_type=object_type, primary_key=primary_key,
            page=page, page_size=page_size,
        )

    @app.post("/api/v1/ontology/functions/{function_name}/execute", tags=["Ontology"])
    def ontology_execute_function(
        function_name: str,
        payload: dict[str, Any] = Body(...),
        user: dict[str, Any] = Depends(require_auth),
    ) -> dict[str, Any]:
        return {
            "data": ontology_call(
                ontology_service.execute_function, user, function_name,
                payload.get("parameters", {}),
            )
        }

    return app


class _ActionRejected(Exception):
    def __init__(self, message: str, details: dict[str, Any]):
        super().__init__(message)
        self.message = message
        self.details = details


def _to_csv(snapshot: dict[str, Any]) -> bytes:
    output = io.StringIO()
    headers = [
        "record_id", "data_type", "project_number", "subject", "customer",
        "category", "signed_at", "total_amount", "received_amount",
        "invoiced_amount", "owner", "probability", "status",
        "merged_from_opportunity", "updated_at",
    ]
    writer = csv.DictWriter(output, fieldnames=headers)
    writer.writeheader()
    for record in snapshot["records"]:
        row = dict(record)
        for field in ("total_amount", "received_amount", "invoiced_amount"):
            value = row[field]
            row[field] = value["amount"] if value else ""
        writer.writerow({key: row.get(key, "") for key in headers})
    return ("\ufeff" + output.getvalue()).encode("utf-8")


async def _materialize_json_response(response: Response) -> tuple[Response, Any | None]:
    """Read JSON once for observability, then return an equivalent response."""
    content_type = response.headers.get("content-type", "")
    if "application/json" not in content_type:
        return response, None
    body = b"".join([chunk async for chunk in response.body_iterator])
    try:
        payload = json.loads(body)
    except (TypeError, ValueError):
        payload = None
    headers = dict(response.headers)
    headers.pop("content-length", None)
    return (
        Response(
            content=body,
            status_code=response.status_code,
            headers=headers,
            media_type=response.media_type,
            background=response.background,
        ),
        payload,
    )


def _returned_row_count(payload: Any | None) -> int | None:
    if not isinstance(payload, dict):
        return None
    data = payload.get("data")
    if isinstance(data, list):
        return len(data)
    if isinstance(data, dict):
        for key in ("parsed", "imported", "inserted_records", "total_records"):
            if isinstance(data.get(key), int):
                return data[key]
    for key in ("parsed", "imported", "inserted_records", "total_records"):
        if isinstance(payload.get(key), int):
            return payload[key]
    return None


def _cursor_link(request: Request, cursor: str) -> str:
    params = dict(request.query_params)
    params.pop("page", None)
    params["cursor"] = cursor
    query = "&".join(f"{key}={value}" for key, value in params.items())
    return f"{request.url.path}?{query}"


def _problem(
    request: Request, *, status: int, title: str, detail: str, code: str
) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        media_type="application/problem+json",
        content={
            "type": f"https://xtcrm.local/problems/{code.lower()}",
            "title": title,
            "status": status,
            "detail": detail,
            "instance": request.url.path,
            "code": code,
            "trace_id": request.headers.get("x-request-id", "local"),
        },
    )


app = create_app()
