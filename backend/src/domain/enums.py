"""状态枚举（18 号数据模型口径，字符串常量即可，不强绑 Enum 类型）。"""

RUN_ACTIVE = "active"
RUN_CLOSED = "closed"

TASK_RUNNING = "running"
TASK_COMPLETED = "completed"
TASK_FAILED = "failed"
TASK_CANCEL_REQUESTED = "cancel_requested"
TASK_CANCELLED = "cancelled"

APPROVAL_PENDING = "pending"
APPROVAL_APPROVED = "approved"
APPROVAL_REJECTED = "rejected"
APPROVAL_TIMEOUT = "timeout"
APPROVAL_CANCELLED = "cancelled"

INSTANCE_ACTIVE = "active"
INSTANCE_DISABLED = "disabled"

ROLE_USER = "user"
ROLE_ADMIN = "platform_admin"
