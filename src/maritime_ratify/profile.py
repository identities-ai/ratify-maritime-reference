"""Public constants for the Maritime work-order authority profile."""

WORK_ORDER_SCOPE = "custom:work_order:create"
DEFAULT_RESOURCE = "site:warehouse-seattle-01"
SECOND_RESOURCE = "site:warehouse-portland-01"
DEFAULT_CATEGORY = "electrical"
DEFAULT_CURRENCY = "USD"
DEFAULT_MAX_AMOUNT_MINOR = 50_000
SECOND_MAX_AMOUNT_MINOR = 20_000

# What this receiver will do at all, which is not what any one agent may do.
# The sites it manages, and an absolute ceiling set well above every delegated
# ceiling so receiver capability never masks a delegation's own constraint.
MANAGED_RESOURCES = (DEFAULT_RESOURCE, SECOND_RESOURCE)
RECEIVER_CEILING_MINOR = 500_000
VERIFIER_ID = "maritime-ratify-demo-receiver"
WORKSPACE_ID = "maritime-ratify-reference"
CATEGORY_CONSTRAINT = "com.ratifyprotocol.maritime.work_order_category"
AUDIENCE_CONSTRAINT = "com.ratifyprotocol.maritime.audience"


# Where each runtime reads its authority from. The artifacts live on the
# runtime's persistent volume rather than in the image, so rotation is a write
# and a restart. Named here because issuance, the rotation script and the local
# reproduction must all agree.
VOLUME_DIRECTORY = "/data/ratify"
