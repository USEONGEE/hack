from __future__ import annotations

# 공통 인터페이스/타입
from .interface import PoolInfo, PoolInfoFetcher

# 전략 구현체들(현재 Hybra만 노출)
from .hybra import HybraPoolInfoFetcher
from .kitten import KittenPoolInfoFetcher
from models.pool_info import PoolInfoModel
from hypurrquant.db.mongo import get_mongo
from typing import Dict
from web3 import AsyncWeb3

__all__ = [
    "PoolInfo",
    "PoolInfoFetcher",
    "HybraPoolInfoFetcher",
    "KittenPoolInfoFetcher",
]


class PoolInfoService:
    def __init__(self):
        self.col = get_mongo()["pool_infos"]

    async def get_by_addresses(
        self, pool_addresses: list[str]
    ) -> Dict[str, PoolInfoModel]:
        checksummed = [AsyncWeb3.to_checksum_address(addr) for addr in pool_addresses]
        cursor = self.col.find(
            {"pool_address": {"$in": checksummed}}, projection={"_id": 0}
        )
        results = await cursor.to_list(length=None)
        result_map = {r["pool_address"]: PoolInfoModel(**r) for r in results}
        return {addr: result_map.get(addr) for addr in checksummed}
