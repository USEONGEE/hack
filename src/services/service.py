from dataclasses import dataclass

from typing import Dict
from hypurrquant.evm import use_chain, Chain, Web3Ctx
from hypurrquant.utils.singleton import singleton
from hypurrquant.logging_config import configure_logging
from hypurrquant.db.redis import get_redis_async

from .index.tvl import TvlService, TvlResponse
from .pools import PoolInfoService
from models.pool_info import PoolInfoModel
from .price import TokenPriceService, ResultType
from dataclasses import dataclass

_logger = configure_logging(__name__)


@dataclass
class InfoResponse:
    tvl_response: TvlResponse
    pool_info: PoolInfoModel
    contract_link: str
    tvl_usd: float
    total_fee_usd: float
    apr: float
    t0_price: float
    t1_price: float


@singleton
class Service:
    def __init__(self):
        self.redis = get_redis_async()
        self.tvl_service = TvlService()
        self.pool_info_service = PoolInfoService()
        self.token_price_service = TokenPriceService()

    # TODO cache 추가하기
    async def get_all_pools(self) -> list[InfoResponse]:
        # 1. tvl, 1h fee 정보 가져오기
        response: list[TvlResponse] = await self.tvl_service.get_all_tvls()

        _logger.info(f"Fetched {len(response)} TVL responses")
        pool_addresses = [r.pool_address for r in response]

        # 2. 조회된 풀의 메타 정보 가져오기
        pool_info_map: Dict[str, PoolInfoModel] = (
            await self.pool_info_service.get_by_addresses(pool_addresses)
        )
        _logger.info(f"Fetched {len(pool_info_map)} pool info entries")

        # 3. 모든 address의 가격 정보 조회하기
        token_addresses = set()
        for r in response:
            token_addresses.add(r.t0_addr)
            token_addresses.add(r.t1_addr)
        async with use_chain(Chain.HYPERLIQUID) as web3ctx:
            token_price_map: Dict[str, ResultType] = (
                await self.token_price_service.get_token_price_in_usd(
                    web3ctx, list(token_addresses)
                )
            )
        _logger.info(f"Fetched price info for {len(token_price_map)} tokens")

        # 4. 데이터 조립하기
        info_responses: list[InfoResponse] = []

        for r in response:
            pool_info = pool_info_map.get(r.pool_address, None)
            if not pool_info:
                continue
            t0_price = token_price_map.get(r.t0_addr, {}).get("price_usd", 0) or 0
            t1_price = token_price_map.get(r.t1_addr, {}).get("price_usd", 0) or 0

            t0_fee_usd = r.t0_fees * t0_price
            t1_fee_usd = r.t1_fees * t1_price
            total_fee_usd = t0_fee_usd + t1_fee_usd

            t0_balance_usd = r.t0_balance * t0_price
            t1_balance_usd = r.t1_balance * t1_price
            tvl_usd = t0_balance_usd + t1_balance_usd

            info_responses.append(
                InfoResponse(
                    tvl_response=r,
                    pool_info=pool_info,
                    contract_link=f"https://hyperevmscan.io/address/{r.pool_address}",
                    total_fee_usd=total_fee_usd,
                    tvl_usd=tvl_usd,
                    apr=total_fee_usd / tvl_usd * 24 * 365 if tvl_usd > 0 else 0,
                    t0_price=t0_price,
                    t1_price=t1_price,
                )
            )

        _logger.info(f"Fetched {len(info_responses)} pool info responses")

        #  5. v2 제거
        info_responses = [
            r for r in info_responses if "v2" not in (r.pool_info.version or "")
        ]
        _logger.info(
            f"Filtered to {len(info_responses)} pool info responses after v2 removal"
        )
        return info_responses
