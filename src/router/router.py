from hypurrquant.server.response import success_response
from hypurrquant.logging_config import configure_logging


from constants import (
    Chain,
    get_address_by_ticker,
)
from services import Service, get_service

from typing import List
from fastapi import APIRouter, Depends

router = APIRouter()


_logger = configure_logging(__name__)


@router.get("/pool-infos", summary="ticker -> address로 변환")
async def ticker_to_address(service: Service = Depends(get_service)):

    response = await service.get_all_pools()
    return success_response(response)
