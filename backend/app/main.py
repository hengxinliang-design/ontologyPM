from __future__ import annotations

import csv
import io
from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import (
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
from .crm_import import CrmImportValidationError, parse_crm_import_base64, template_fields
from .db import Database
from .repository import SnapshotRepository
from .project_repository import ProjectRepository
from .source import build_source
from .sync_service import SyncService
from .xlsx_timesheet import parse_timesheet_xlsx_base64


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    database = Database(settings.database_path)
    source = build_source(settings)
    repository = SnapshotRepository(database)
    project_repository = ProjectRepository(database, settings.timezone)
    sync_service = SyncService(database, source, settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        database.initialize()
        if settings.auto_import_on_empty and database.active_snapshot_id() is None:
            job = sync_service.create_job()
            await sync_service.run_job(job["job_id"])
        if settings.source_file.name.endswith(".example.json"):
            project_repository.ensure_demo_data()
        yield

    app = FastAPI(
        title="TSG PM Data API",
        version="1.0.0",
        description="FastAPI query orchestration and SQLite persistence for TSG PM snapshots.",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.database = database
    app.state.repository = repository
    app.state.project_repository = project_repository
    app.state.sync_service = sync_service
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    async def require_auth(
        authorization: Annotated[str | None, Header()] = None,
    ) -> None:
        if not settings.api_token:
            return
        if authorization != f"Bearer {settings.api_token}":
            raise HTTPException(status_code=401, detail="invalid or missing bearer token")

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

    @app.get("/api/v1/records", dependencies=[auth], tags=["Records"])
    def list_records(
        request: Request,
        data_type: str | None = Query(None, pattern="^(contract_order|sales_opportunity)$"),
        project_number: str | None = None,
        owner: str | None = None,
        status: str | None = None,
        probability: int | None = Query(None, ge=0, le=100),
        q: str | None = Query(None, min_length=1, max_length=200),
        signed_from: str | None = None,
        signed_to: str | None = None,
        updated_since: str | None = None,
        page: int = Query(1, ge=1),
        page_size: int = Query(50, ge=1, le=200),
        sort: str = Query("project_number", pattern="^-?(project_number|subject|signed_at|owner|probability|status)$"),
    ) -> dict[str, Any]:
        result = repository.list_records(
            data_type=data_type,
            project_number=project_number,
            owner=owner,
            status=status,
            probability=probability,
            query=q,
            signed_from=signed_from,
            signed_to=signed_to,
            page=page,
            page_size=page_size,
            sort=sort,
        )
        if result is None:
            raise HTTPException(status_code=503, detail="no validated snapshot is available")
        if updated_since and result["updated_at"] <= updated_since:
            result["records"] = []
            result["total_records"] = 0
            result["total_pages"] = 0
        query_string = request.url.query
        self_link = f"{request.url.path}?{query_string}" if query_string else request.url.path
        next_link = None
        previous_link = None
        if page < result["total_pages"]:
            next_link = _page_link(request, page + 1)
        if page > 1 and result["total_pages"]:
            previous_link = _page_link(request, page - 1)
        return {
            "data": result["records"],
            "meta": {key: result[key] for key in (
                "page", "page_size", "total_records", "total_pages",
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
        risk_level: str | None = Query(None, pattern="^(healthy|warning|critical)$"),
        q: str | None = Query(None, min_length=1, max_length=200),
    ) -> dict[str, Any]:
        return project_repository.list_projects(
            contract_type=contract_type,
            lifecycle_status=lifecycle_status,
            risk_level=risk_level,
            query=q,
        )

    @app.get(
        "/api/v1/projects/{project_number}", dependencies=[auth], tags=["Projects"]
    )
    def get_project(project_number: str) -> dict[str, Any]:
        project = project_repository.get_project(project_number)
        if project is None:
            raise HTTPException(status_code=404, detail="project not found")
        return {"data": project}

    @app.get("/api/v1/project-health", dependencies=[auth], tags=["Projects"])
    def get_project_health(
        month: str | None = Query(None, pattern=r"^\d{4}-\d{2}$"),
        start_month: str | None = Query(None, pattern=r"^\d{4}-\d{2}$"),
        end_month: str | None = Query(None, pattern=r"^\d{4}-\d{2}$"),
        project_number: str | None = None,
    ) -> dict[str, Any]:
        try:
            return project_repository.project_health(
                month=month,
                start_month=start_month,
                end_month=end_month,
                project_number=project_number,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get(
        "/api/v1/projects/{project_number}/records",
        dependencies=[auth],
        tags=["Projects"],
    )
    def get_project_records(project_number: str) -> dict[str, Any]:
        records = repository.get_project(project_number)
        if not records:
            raise HTTPException(status_code=404, detail="project not found")
        return {"data": records, "meta": {"total_records": len(records)}}

    @app.patch(
        "/api/v1/projects/{project_number}/settings",
        dependencies=[auth],
        tags=["Projects"],
    )
    def update_project_settings(
        project_number: str, payload: dict[str, Any] = Body(...)
    ) -> dict[str, Any]:
        try:
            project = project_repository.upsert_settings(project_number, payload)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"data": project}

    @app.post(
        "/api/v1/projects/{project_number}/milestones",
        status_code=201,
        dependencies=[auth],
        tags=["Projects"],
    )
    def create_milestone(
        project_number: str, payload: dict[str, Any] = Body(...)
    ) -> dict[str, Any]:
        try:
            return {"data": project_repository.create_milestone(project_number, payload)}
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.patch(
        "/api/v1/projects/{project_number}/milestones/{milestone_id}",
        dependencies=[auth],
        tags=["Projects"],
    )
    def update_milestone(
        project_number: str, milestone_id: str, payload: dict[str, Any] = Body(...)
    ) -> dict[str, Any]:
        try:
            return {
                "data": project_repository.update_milestone(
                    project_number, milestone_id, payload
                )
            }
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post(
        "/api/v1/projects/{project_number}/milestones/{milestone_id}/confirm",
        dependencies=[auth],
        tags=["Projects"],
    )
    def confirm_milestone(
        project_number: str, milestone_id: str, payload: dict[str, Any] = Body(...)
    ) -> dict[str, Any]:
        try:
            return {
                "data": project_repository.confirm_milestone(
                    project_number, milestone_id, payload["confirmed_by"]
                )
            }
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.delete(
        "/api/v1/projects/{project_number}/milestones/{milestone_id}",
        dependencies=[auth],
        tags=["Projects"],
    )
    def delete_milestone(project_number: str, milestone_id: str) -> dict[str, Any]:
        try:
            return {
                "data": project_repository.delete_milestone(
                    project_number, milestone_id
                )
            }
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post(
        "/api/v1/projects/{project_number}/payment-nodes",
        status_code=201,
        dependencies=[auth],
        tags=["Projects"],
    )
    def create_payment_node(
        project_number: str, payload: dict[str, Any] = Body(...)
    ) -> dict[str, Any]:
        return {"data": project_repository.create_payment_node(project_number, payload)}

    @app.patch(
        "/api/v1/projects/{project_number}/payment-nodes/{payment_node_id}",
        dependencies=[auth],
        tags=["Projects"],
    )
    def update_payment_node(
        project_number: str, payment_node_id: str,
        payload: dict[str, Any] = Body(...),
    ) -> dict[str, Any]:
        try:
            return {
                "data": project_repository.update_payment_node(
                    project_number, payment_node_id, payload
                )
            }
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.delete(
        "/api/v1/projects/{project_number}/payment-nodes/{payment_node_id}",
        status_code=204,
        dependencies=[auth],
        tags=["Projects"],
    )
    def delete_payment_node(project_number: str, payment_node_id: str) -> None:
        try:
            project_repository.delete_payment_node(project_number, payment_node_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post(
        "/api/v1/projects/{project_number}/payment-nodes/{payment_node_id}/confirm",
        dependencies=[auth],
        tags=["Projects"],
    )
    def confirm_payment_node(
        project_number: str, payment_node_id: str,
        payload: dict[str, Any] = Body(...),
    ) -> dict[str, Any]:
        try:
            return {
                "data": project_repository.confirm_payment_node(
                    project_number,
                    payment_node_id,
                    payload["confirmed_by"],
                    payload.get("actual_amount"),
                    payload.get("actual_date"),
                )
            }
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post(
        "/api/v1/projects/{project_number}/interventions",
        status_code=201,
        dependencies=[auth],
        tags=["Projects"],
    )
    def create_intervention(
        project_number: str, payload: dict[str, Any] = Body(...)
    ) -> dict[str, Any]:
        return {"data": project_repository.create_intervention(project_number, payload)}

    @app.post(
        "/api/v1/time-entries/import",
        dependencies=[auth],
        tags=["Time Entries"],
    )
    def import_time_entries(
        entries: list[dict[str, Any]] = Body(...)
    ) -> dict[str, Any]:
        try:
            return project_repository.import_time_entries(entries)
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post(
        "/api/v1/time-entries/import-xlsx",
        dependencies=[auth],
        tags=["Time Entries"],
    )
    def import_time_entries_xlsx(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        try:
            entries = parse_timesheet_xlsx_base64(payload["content_base64"])
            result = project_repository.import_time_entries(entries)
            return {
                **result,
                "parsed": len(entries),
                "filename": payload.get("filename"),
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
        payload: dict[str, Any] = Body(...)
    ) -> dict[str, Any]:
        try:
            return {"data": project_repository.update_worktime_baseline(payload)}
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get(
        "/api/v1/employee-worktime", dependencies=[auth], tags=["Time Entries"]
    )
    def get_employee_worktime(
        period: str = Query("month", pattern="^(month|year)$"),
        month: str | None = Query(None, pattern=r"^\d{4}-\d{2}$"),
        year: str | None = Query(None, pattern=r"^\d{4}$"),
        employee_name: str | None = None,
    ) -> dict[str, Any]:
        try:
            return project_repository.employee_worktime_assessment(
                period=period,
                month=month,
                year=year,
                employee_name=employee_name,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

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
    def list_crm_imports(limit: int = Query(50, ge=1, le=200)) -> dict[str, Any]:
        """List successful manual CRM uploads, newest first."""
        return {"data": repository.list_crm_import_logs(limit=limit)}

    @app.post("/api/v1/crm-imports", dependencies=[auth], tags=["CRM Imports"])
    def import_crm_file(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
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

    return app


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


def _page_link(request: Request, page: int) -> str:
    params = dict(request.query_params)
    params["page"] = str(page)
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
