"""Общие DTO HTTP-контрактов: импортируются core-api, ботом и адаптером."""
from .alerts import AlertDTO, RuleDTO, RuleUpsert
from .cars import CarCreate, CarDTO, CarStateDTO, TrackerDTO, TrackerUpsert
from .commands import CommandDTO, CommandRequest, CommandResult
from .common import DTO, Problem, Role
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
)
from .payments import (
    PaymentCreate,
    PaymentDTO,
    PaymentResult,
    RecognizedReceiptDTO,
)
from .reports import (
    AssistantAnswer,
    AssistantQuery,
    CarDriverRow,
    CarTotalRow,
    DriverTotalRow,
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
    "CarDTO", "CarCreate", "CarStateDTO", "TrackerDTO", "TrackerUpsert",
    "DriverDTO", "DriverStatsDTO", "DriverWithStats", "DriverRegister",
    "MeDTO", "InvitationDTO", "InviteCheckDTO",
    "ScheduleDTO", "ScheduleStatusDTO", "ScheduleWithStatus", "ScheduleUpsert",
    "PaymentDTO", "PaymentCreate", "PaymentResult", "RecognizedReceiptDTO",
    "CarDriverRow", "UpcomingRow", "DriverTotalRow", "CarTotalRow",
    "ReminderItem", "ReminderPlanDTO", "ReminderMark",
    "AssistantQuery", "AssistantAnswer",
    "TelemetryPoint", "TelemetryBatchResult",
    "CommandRequest", "CommandDTO", "CommandResult",
    "RuleDTO", "RuleUpsert", "AlertDTO",
    "FineDTO", "FineCreate", "FineImportItem", "FineImportResult",
    "MaintenanceDTO", "MaintenanceUpsert",
]
