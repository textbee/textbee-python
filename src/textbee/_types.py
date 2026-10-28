"""Response shapes. Keys are the API's wire names, so they stay camelCase."""

from __future__ import annotations

from typing import List, Literal, Optional, TypedDict, Union, final

Direction = Literal["all", "sent", "received"]
"""Filter values for ``direction``."""

Order = Literal["desc", "asc"]
"""``desc`` (default) for newest first, ``asc`` to walk forward when polling."""

MessageStatus = Literal[
    "pending",
    "dispatched",
    "sent",
    "delivered",
    "failed",
    "unknown",
    "received",
]
"""Delivery state of a message, lowercase."""

SmsBatchStatus = Literal[
    "pending",
    "processing",
    "completed",
    "partial_success",
    "failed",
    "unknown",
]

WebhookEvent = Literal[
    "MESSAGE_RECEIVED",
    "MESSAGE_SENT",
    "MESSAGE_DELIVERED",
    "MESSAGE_FAILED",
    "UNKNOWN_STATE",
    "SMS_STATUS_UPDATED",
]
"""Event names textbee sends in a webhook payload."""


class SendSmsWarning(TypedDict):
    """A reason the send will probably fail, such as ``SMS_PERMISSION_MISSING``."""

    code: str
    message: str


class _SendSmsQueuedRequired(TypedDict):
    success: Literal[True]
    message: str
    smsBatchId: str
    recipientCount: int


@final
class SendSmsQueuedResponse(_SendSmsQueuedRequired, total=False):
    """Returned when the message was accepted for delivery and queued."""

    estimatedCompletionAt: str
    """When a large send, spread over several waves, should finish."""

    warning: SendSmsWarning
    """Present when the phone last reported a problem, such as the SMS permission off."""


class _SendSmsImmediateRequired(TypedDict):
    successCount: int


@final
class SendSmsImmediateResponse(_SendSmsImmediateRequired, total=False):
    """Returned when the message was dispatched to the device immediately."""

    success: bool
    failureCount: int
    warning: SendSmsWarning
    """Present when the phone last reported a problem, such as the SMS permission off."""


SendSmsResponse = Union[SendSmsQueuedResponse, SendSmsImmediateResponse]
"""The send response shape depends on how the message was dispatched.

Narrow it with ``"smsBatchId" in result`` before reading batch fields.
"""


class _DeviceRequired(TypedDict):
    _id: str
    enabled: bool
    isDefault: bool
    sentSMSCount: int
    receivedSMSCount: int


class Device(_DeviceRequired, total=False):
    name: str
    brand: str
    manufacturer: str
    model: str
    lastHeartbeat: str
    os: str
    osVersion: str
    appVersionName: str
    appVersionCode: int
    createdAt: str
    updatedAt: str


class _MessageDeviceRequired(TypedDict):
    _id: str


class MessageDevice(_MessageDeviceRequired, total=False):
    name: str
    brand: str
    model: str
    buildId: str
    enabled: bool


MessageErrorSource = Literal["push", "device", "server"]
"""Where a failure was reported from."""


class _MessageErrorEntryRequired(TypedDict):
    code: str
    at: str
    """When the failure happened. ISO date."""

    source: MessageErrorSource


class MessageErrorEntry(_MessageErrorEntryRequired, total=False):
    """One failure a message had. Kept after the message later succeeds."""

    message: str
    backfilled: bool
    """Set on entries rebuilt from old message fields."""


class _MessageRequired(TypedDict):
    _id: str
    message: str
    type: Literal["SENT", "RECEIVED"]
    """Direction, uppercase. Deprecated: read ``direction`` instead."""


class _MessageFields(_MessageRequired, total=False):
    channel: str
    """Message channel. Currently always ``sms``; absent means ``sms``."""

    status: MessageStatus
    """Lowercase, and absent on messages stored before status tracking."""

    sender: str
    recipient: str
    requestedAt: str
    dispatchedAt: str
    sentAt: str
    deliveredAt: str
    failedAt: str
    """When the message failed. Present only while status is ``failed``."""

    receivedAt: str
    errorCode: str
    """Current failure code. Present only while status is ``failed``."""

    errorMessage: str
    """Current failure text. Present only while status is ``failed``, or as a timeout note on ``unknown``."""

    errorHistory: List[MessageErrorEntry]
    """Every failure the message had, oldest first, at most 5."""

    simSubscriptionId: int
    smsBatch: str
    createdAt: str
    updatedAt: str


class _SmsBatchRequired(TypedDict):
    _id: str
    message: str
    recipientCount: int
    successCount: int
    """Messages sent or delivered: ``sentCount + deliveredCount``."""

    failureCount: int
    """Messages that failed."""

    status: SmsBatchStatus


class SmsBatch(_SmsBatchRequired, total=False):
    """A send batch.

    The status and counts are calculated from the batch's messages, so they
    change as each message reports.
    """

    recipientPreview: str
    pendingCount: int
    """Messages not yet handed to the phone."""

    dispatchedCount: int
    """Messages handed to the phone with no result yet."""

    sentCount: int
    """Messages sent with no delivery report yet."""

    deliveredCount: int
    """Messages with a delivery report."""

    unknownCount: int
    """Messages with no report from the phone within 20 minutes."""

    error: str
    completedAt: str
    """When the batch reached its current final status. Absent while processing."""

    statusCheckedAt: str
    """When the status and counts were last calculated."""

    createdAt: str
    updatedAt: str


class _MessageListMetaRequired(TypedDict):
    limit: int


class MessageListMeta(_MessageListMetaRequired, total=False):
    """Meta for the account-level message list.

    Page mode carries the counters and the cursor fields. Cursor mode carries
    ``limit``, ``nextCursor`` and ``hasMore`` only, skipping the total count.
    """

    page: int
    total: int
    totalPages: int
    nextCursor: Optional[str]
    """Opaque position after the last message. ``None`` on the final page."""

    hasMore: bool


class Message(_MessageFields, total=False):
    direction: Literal["sent", "received"]
    """Direction, lowercase. Matches the ``direction`` filter values."""

    device: MessageDevice
    """The sending or receiving device."""


class Sms(_MessageFields, total=False):
    """A message as ``get_sms`` and ``get_sms_batch`` return it: ``device`` is
    the id string, and ``direction`` is absent (read ``type``)."""

    device: str
    """The id of the sending or receiving device."""


class MessageList(TypedDict):
    data: List[Message]
    meta: MessageListMeta


class SmsBatchResult(TypedDict):
    batch: SmsBatch
    messages: List[Sms]
