from __future__ import annotations
from hypurrquant.db.mongo import get_mongo
from models.pool_info import PoolInfoModel
from dataclasses import dataclass
from abc import ABC, abstractmethod
from typing import List, Optional
from web3 import AsyncWeb3


@dataclass(frozen=True)
class PoolInfo:
    token0_address: str
    token1_address: str
    fee: Optional[int]  # v2 계열 등 수수료가 없을 수 있어 Optional
    pool_address: str
    protocol: str
    version: Optional[str] = None  # "v2", "v3", "cl" 등


class PoolInfoFetcher(ABC):
    """
    각 DEX별 풀 정보를 통일된 형태로 내려주는 전략 인터페이스
    """

    @abstractmethod
    async def _fetch(self) -> List[PoolInfo]:
        """
        pool_id가 주어지면 해당 풀만(또는 필터링) 반환. 없으면 전부.
        DEX마다 의미가 다를 수 있으니 구현체에서 해석.
        """
        raise NotImplementedError

    async def _fetch_and_save(self) -> None:
        """
        _fetch() 결과를 JSON으로 직렬화하여 파일에 저장.
        """
        response = await self._fetch()
        models: List[PoolInfoModel] = []
        for p in response:
            can_hedge = self._can_hedge(p)
            model = PoolInfoModel(
                dex_type=self.dex_type,
                can_hedge=can_hedge,
                token0_address=AsyncWeb3.to_checksum_address(p.token0_address),
                token1_address=AsyncWeb3.to_checksum_address(p.token1_address),
                fee=p.fee,
                pool_address=AsyncWeb3.to_checksum_address(p.pool_address),
                version=p.version,
            )
            models.append(model)
        col = get_mongo()["pool_infos"]
        await col.insert_many([m.model_dump(by_alias=True) for m in models])

    def _can_hedge(self, pool: PoolInfo) -> bool:
        """
        해당 풀에서 헤지(스왑)가 가능한지 여부 판단.
        기본적으로 수수료가 있는 풀에서만 헤지 가능하다고 가정.
        """
        from hypurrquant.evm.constants._hyperliquid.token_address import (
            WHYPE_ADDRESS,
            USDT0_ADDRESS,
        )

        return (
            pool.token0_address.lower() == USDT0_ADDRESS.lower()
            or pool.token1_address.lower() == USDT0_ADDRESS.lower()
        ) and (
            pool.token0_address.lower() == WHYPE_ADDRESS.lower()
            or pool.token1_address.lower() == WHYPE_ADDRESS.lower()
        )

    async def get_random_one(self):
        dex_type = self.dex_type
        col = get_mongo()["pool_infos"]
        doc = await col.aggregate(
            [{"$match": {"dex_type": dex_type}}, {"$sample": {"size": 1}}]
        ).to_list(length=1)
        if not doc:
            return None
        return PoolInfoModel.model_validate(doc[0])

    async def get_all(self) -> List[PoolInfoModel]:
        col = get_mongo()["pool_infos"]
        docs = await col.find({"dex_type": self.dex_type}).to_list(length=None)
        return [PoolInfoModel.model_validate(doc) for doc in docs]
