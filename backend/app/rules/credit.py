from pydantic import BaseModel

from app.erp.schemas import OutletDTO, Paise


class CreditEvaluation(BaseModel):
    """Outcome of checking whether an order fits within an outlet's credit limit."""

    is_approved: bool
    reason: str | None = None
    excess_amount_paise: Paise = 0


def evaluate_credit(
    outlet: OutletDTO,
    order_grand_total_paise: Paise,
) -> CreditEvaluation:
    """Evaluate outlet credit exposure against credit_limit_paise in integer paise."""
    if order_grand_total_paise < 0:
        raise ValueError("order_grand_total_paise must be non-negative")
    if outlet.credit_limit_paise < 0:
        raise ValueError("outlet.credit_limit_paise must be non-negative")
    if outlet.outstanding_paise < 0:
        raise ValueError("outlet.outstanding_paise must be non-negative")

    total_exposure = outlet.outstanding_paise + order_grand_total_paise
    if total_exposure > outlet.credit_limit_paise:
        return CreditEvaluation(
            is_approved=False,
            reason="CREDIT_LIMIT_EXCEEDED",
            excess_amount_paise=total_exposure - outlet.credit_limit_paise,
        )

    return CreditEvaluation(is_approved=True)
