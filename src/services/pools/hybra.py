from __future__ import annotations
from hypurrquant.api.async_http import send_request_for_external
from typing import List, Optional, Dict, Any

from .interface import PoolInfo, PoolInfoFetcher


def _lower(addr: Optional[str]) -> Optional[str]:
    return addr.lower() if isinstance(addr, str) else addr


class HybraPoolInfoFetcher(PoolInfoFetcher):
    """
    Hybra 백엔드 응답을 PoolInfo로 일반화.
    - GET/POST 상관 없이 해당 URL로 호출하면 풀 리스트가 온다고 가정.
    - pool_id 인자는 다음을 의미로 해석:
        * None: 전체 반환
        * "0x..." : poolAddress가 일치하는 것만 필터링
        * "id:123": id(숫자)로 필터링
    """

    def __init__(
        self,
        url: str = "https://server.hybra.finance/api/points/pool-config/getAllPoolConfigs",
        timeout: float = 30.0,
    ):
        self.url = url
        self.timeout = timeout
        self.dex_type = "hybra"

    def _normalize_item(self, item: Dict[str, Any]) -> Optional[PoolInfo]:
        """
        Hybra 응답 예:
        {
            "id": 35,
            "chainId": 999,
            "poolAddress": "0x7618...",
            "protocolType": "v3",
            "poolCategory": "cl",
            "feeTier": 2500,
            "token0Address": "0x1359...",
            "token1Address": "0x5555...",
            ...
        }
        """
        pool_addr = _lower(item.get("poolAddress"))
        t0 = _lower(item.get("token0Address"))
        t1 = _lower(item.get("token1Address"))
        fee = item.get("feeTier")  # int or None
        version = _lower(item.get("protocolType"))

        if not pool_addr or not t0 or not t1:
            return None

        try:
            fee_val = int(fee) if fee is not None else None
        except Exception:
            fee_val = None

        return PoolInfo(
            token0_address=t0,
            token1_address=t1,
            fee=fee_val,
            pool_address=pool_addr,
            version=version,
            protocol="uniswap",
        )

    async def _fetch(self) -> List[PoolInfo]:
        """
        동기 호출 버전. FastAPI/비동기 컨텍스트가 필요하면 아래 async 버전 추가 가능.
        """
        data = await send_request_for_external("GET", self.url)

        if not isinstance(data, list):
            # 일부 백엔드는 {"data": [...]} 형태일 수 있으니 보완
            data = data.get("data", []) if isinstance(data, dict) else []

        out: List[PoolInfo] = []
        for it in data:
            norm = self._normalize_item(it)
            if norm:
                out.append(norm)
        return out
