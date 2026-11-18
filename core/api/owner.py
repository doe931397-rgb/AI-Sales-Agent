"""Owner-only API routes; no owner access is granted by client-provided roles."""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, StrictBool

from core.services import auth_service, owner_dashboard_service as dashboard
from core.services import prepared_stock_service as prepared_stock

router = APIRouter(prefix="/owner", tags=["owner"])


def require_owner(request: Request) -> dict:
    account = auth_service.authenticate_token(request.cookies.get(auth_service.SESSION_COOKIE_NAME))
    if account is None:
        raise HTTPException(status_code=401, detail="Authentication required.")
    if account["role"] != "owner":
        raise HTTPException(status_code=403, detail="Owner access required.")
    return account


class StatusUpdate(BaseModel):
    status: str


class MenuUpdate(BaseModel):
    price: int | None = Field(default=None, ge=0, le=100000000)
    available: StrictBool | None = None



class PreparedStockConfig(BaseModel):
    product_id: int = Field(gt=0)
    variant: str = Field(default="standard", min_length=1, max_length=40)
    unit: str
    serving_units: int = Field(gt=0, le=1000000000)
    tolerance_units: int = Field(ge=0, le=1000000000)
    extra_units: int = Field(gt=0, le=1000000000)
    extra_tolerance_units: int = Field(ge=0, le=1000000000)
    extra_price: int = Field(ge=0, le=100000000)


class PreparedStockAdjustment(BaseModel):
    product_id: int = Field(gt=0)
    variant: str = Field(default="standard", min_length=1, max_length=40)
    action: str
    quantity_units: int = Field(ge=0, le=1000000000)
    reason: str = Field(min_length=3, max_length=240)


@router.get("/prepared-stock")
def owner_prepared_stock(request: Request):
    require_owner(request)
    return {"items": prepared_stock.list_stocks(), "checkout_enabled": False}


@router.get("/prepared-stock/history")
def owner_prepared_stock_history(request: Request):
    require_owner(request)
    return {"movements": prepared_stock.recent_movements()}


@router.post("/prepared-stock/config")
def owner_prepared_stock_config(payload: PreparedStockConfig, request: Request):
    owner = require_owner(request)
    try:
        return prepared_stock.configure(owner, payload.model_dump())
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/prepared-stock/adjust")
def owner_prepared_stock_adjust(payload: PreparedStockAdjustment, request: Request):
    owner = require_owner(request)
    try:
        return prepared_stock.adjust(owner, payload.model_dump())
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/me")
def owner_me(request: Request):
    return {"owner": require_owner(request)}


@router.get("/overview")
def owner_overview(request: Request):
    require_owner(request)
    return dashboard.overview()


@router.get("/orders")
def owner_orders(request: Request):
    require_owner(request)
    return {"orders": dashboard.list_orders()}


@router.put("/orders/{order_id}/status")
def owner_update_order_status(order_id: int, payload: StatusUpdate, request: Request):
    owner = require_owner(request)
    try:
        return dashboard.update_order_status(owner, order_id, payload.status)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/menu")
def owner_menu(request: Request):
    require_owner(request)
    return {"products": dashboard.list_menu()}


@router.put("/menu/{product_id}")
def owner_update_menu(product_id: int, payload: MenuUpdate, request: Request):
    owner = require_owner(request)
    try:
        return dashboard.update_product(owner, product_id, price=payload.price, available=payload.available)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/customers")
def owner_customers(request: Request):
    require_owner(request)
    return {"customers": dashboard.list_customers()}
