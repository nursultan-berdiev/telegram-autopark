"""Общие DTO HTTP-контрактов: импортируются core-api, ботом и адаптером."""
from .alerts import AlertDTO, RuleDTO, RuleUpsert
from .announcements import (
    ANNOUNCEMENT_MAX_TEXT,
    AnnouncementCreate,
    AnnouncementDTO,
    render_announcement,
    telegram_length,
)
from .cars import (
    CarCreate,
    CarDTO,
    CarOverviewDTO,
    CarStateDTO,
    TrackerDTO,
    TrackerUpsert,
)
from .commands import CommandDTO, CommandRequest, CommandResult
from .common import DTO, Problem, Role
from .documents import (
    DOCUMENT_MAX_FILE_ID,
    DOCUMENT_MAX_FILE_NAME,
    DOCUMENT_MAX_TITLE,
    DocumentCreate,
    DocumentDTO,
)
from .drivers import (
    DriverDTO,
    DriverRegister,
    DriverStatsDTO,
    DriverWithStats,
    InvitationDTO,
    InviteCheckDTO,
    MeDTO,
)
from .fleet import (
    FineCreate,
    FineDTO,
    FineImportItem,
    FineImportResult,
    MaintenanceDTO,
    MaintenanceUpsert,
    PeriodicTaskDTO,
    PeriodicTaskPatch,
    PeriodicTaskUpsert,
    TaskRunDTO,
)
from .payments import (
    CarPaymentDTO,
    PaymentCreate,
    PaymentDTO,
    PaymentResult,
    RecognizedReceiptDTO,
)
from .photo import (
    PHOTO_COMMENT_MAX,
    PhotoInspectionCreate,
    PhotoInspectionDTO,
    PhotoInspectionResult,
    PhotoStatusDTO,
)
from .reports import (
    AdminDigest,
    AssistantAnswer,
    AssistantQuery,
    AssistantTurn,
    CarDriverRow,
    CarTotalRow,
    DriverNotice,
    DriverNoticePlan,
    DriverTotalRow,
    OutboundMessageDTO,
    ReminderItem,
    ReminderMark,
    ReminderPlanDTO,
    UpcomingRow,
)
from .schedules import (
    ScheduleDTO,
    ScheduleStatusDTO,
    ScheduleUpsert,
    ScheduleWithStatus,
)
from .telemetry import TelemetryBatchResult, TelemetryPoint

__all__ = [
    "DTO", "Problem", "Role",
    "CarDTO", "CarCreate", "CarOverviewDTO", "CarStateDTO", "TrackerDTO", "TrackerUpsert",
    "DriverDTO", "DriverStatsDTO", "DriverWithStats", "DriverRegister",
    "MeDTO", "InvitationDTO", "InviteCheckDTO",
    "ScheduleDTO", "ScheduleStatusDTO", "ScheduleWithStatus", "ScheduleUpsert",
    "PaymentDTO", "CarPaymentDTO", "PaymentCreate", "PaymentResult", "RecognizedReceiptDTO",
    "CarDriverRow", "UpcomingRow", "DriverTotalRow", "CarTotalRow",
    "ReminderItem", "ReminderPlanDTO", "ReminderMark",
    "DriverNotice", "DriverNoticePlan", "AdminDigest", "OutboundMessageDTO",
    "AssistantQuery", "AssistantAnswer", "AssistantTurn",
    "TelemetryPoint", "TelemetryBatchResult",
    "CommandRequest", "CommandDTO", "CommandResult",
    "RuleDTO", "RuleUpsert", "AlertDTO",
    "FineDTO", "FineCreate", "FineImportItem", "FineImportResult",
    "MaintenanceDTO", "MaintenanceUpsert",
    "PeriodicTaskDTO", "PeriodicTaskUpsert", "PeriodicTaskPatch", "TaskRunDTO",
    "AnnouncementCreate", "AnnouncementDTO",
    "ANNOUNCEMENT_MAX_TEXT", "render_announcement", "telegram_length",
    "DocumentCreate", "DocumentDTO", "DOCUMENT_MAX_TITLE",
    "DOCUMENT_MAX_FILE_NAME", "DOCUMENT_MAX_FILE_ID",
    "PhotoInspectionCreate", "PhotoInspectionDTO", "PhotoInspectionResult", "PhotoStatusDTO",
    "PHOTO_COMMENT_MAX",
]
