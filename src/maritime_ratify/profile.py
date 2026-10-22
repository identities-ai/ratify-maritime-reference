"""Public constants for the Maritime work-order authority profile."""

from ratify_protocol_v2.constraint_evaluator_v2 import CONSTRAINT_ALLOWED_VALUES_V2
from ratify_protocol_v2.constraints_v2 import CONSTRAINT_RECEIVER_AUDIENCE

PROTOCOL_LINE = "v2"

# An exact custom scope: one opaque name, matched by byte equality, never a
# wildcard or a family (SPEC-v2 §2.4).
WORK_ORDER_SCOPE = "custom:work_order:create"
WORK_ORDER_OPERATION = "work_order.create"
DEFAULT_RESOURCE = "site:warehouse-seattle-01"
SECOND_RESOURCE = "site:warehouse-portland-01"
DEFAULT_CATEGORY = "electrical"
DEFAULT_CURRENCY = "USD"
DEFAULT_MAX_AMOUNT_MINOR = 50_000
SECOND_MAX_AMOUNT_MINOR = 20_000

# The work-order action states amounts in cents. Protocol v2 states every
# amount in integer micro-units of the major unit (10^-6 USD), so one cent is
# 10,000 micro-units. The receiver accepts only USD, whose minor unit is 10^-2.
MICROS_PER_MINOR = 10_000

# What this receiver will do at all, which is not what any one agent may do.
# The sites it manages, and an absolute ceiling set well above every delegated
# ceiling so receiver capability never masks a delegation's own constraint.
MANAGED_RESOURCES = (DEFAULT_RESOURCE, SECOND_RESOURCE)
RECEIVER_CEILING_MINOR = 500_000

# The stable receiver identifier that delegations name in their
# ratify.receiver_audience constraint and that every expected challenge
# carries as expected_receiver_id. It is not the verifier ID, which in v2 is
# the 64-hex key ID of the receiver's verifier key.
RECEIVER_ID = "maritime-ratify-demo-receiver"
# Session-context workspace: a canonical lowercase UUID (SPEC-v2 §2.4).
WORKSPACE_ID = "6f3c8d2a-4b1e-4c7a-9d5f-2e8b0a1c3d47"
SESSION_ID = "maritime-reference"
# The receiver's own application context policy. It requires the signed
# session context to equal the one the receiver computed from the
# authenticated action when it issued the challenge.
CONTEXT_POLICY_PROFILE = "maritime-reference.work-order-session/v1"
CONTEXT_POLICY_FIELDS = (
    "verifier_id", "workspace_id", "agent_id", "session_id", "invocation_id",
    "request_hash",
)

# The work-order category is bounded by the standard allowed_values/v1
# evaluator profile, which reads the authenticated value under this field.
CATEGORY_CONSTRAINT = CONSTRAINT_ALLOWED_VALUES_V2
CATEGORY_FIELD = "work_order_category"
AUDIENCE_CONSTRAINT = CONSTRAINT_RECEIVER_AUDIENCE

# Replay namespace and epoch for this receiver's challenges.
VERIFIER_NAMESPACE = "maritime-work-orders"
REPLAY_EPOCH = 1
CHALLENGE_LIFETIME_SECONDS = 300
STATUS_KEY_EPOCH = 1
# A signed status list is current for at most one day (the v2 maximum). The
# principal re-signs it at least daily; see docs/CREDENTIAL-ROTATION.md.
STATUS_LIST_LIFETIME_SECONDS = 86_400


# Where each runtime reads its authority from. The artifacts live on the
# runtime's persistent volume rather than in the image, so rotation is a write
# and a restart. Named here because issuance, the rotation script and the local
# reproduction must all agree.
VOLUME_DIRECTORY = "/data/ratify"
