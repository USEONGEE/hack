from web3 import AsyncWeb3
from hypurrquant.utils.singleton import singleton
from hypurrquant.logging_config import configure_logging
from hypurrquant.db.mongo import get_mongo
from hypurrquant.db import init_db, close_db
from hypurrquant.evm import Web3Utils, Web3Ctx
from models.pool_info import PoolInfoModel
from dataclasses import dataclass
from constants import ERC20_ABI
import asyncio
import time

logger = configure_logging(__name__)
# ------------------------
# 환경설정
# ------------------------
POOL_ABI = [
    {
        "name": "token0",
        "outputs": [{"type": "address"}],
        "inputs": [],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "name": "token1",
        "outputs": [{"type": "address"}],
        "inputs": [],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "name": "fee",
        "outputs": [{"type": "uint24"}],
        "inputs": [],
        "stateMutability": "view",
        "type": "function",
    },
]

# Swap 이벤트 토픽 (Uniswap V3 Pool)
# event Swap(address indexed sender, address indexed recipient, int256 amount0, int256 amount1, uint160 sqrtPriceX96, uint128 liquidity, int24 tick)
SWAP_TOPIC = AsyncWeb3.keccak(
    text="Swap(address,address,int256,int256,uint160,uint128,int24)"
).hex()


# ------------------------
# 유틸 함수
# ------------------------
async def get_token_info(w3: AsyncWeb3, token_addr, pool_address):
    try:
        pool_address = AsyncWeb3.to_checksum_address(pool_address)
        token_addr = AsyncWeb3.to_checksum_address(token_addr)
        token = w3.eth.contract(address=token_addr, abi=ERC20_ABI)
        symbol, decimals, balance = await asyncio.gather(
            token.functions.symbol().call(),
            token.functions.decimals().call(),
            token.functions.balanceOf(pool_address).call(),
        )
        balance = balance / (10**decimals)
        return {
            "address": token_addr,
            "symbol": symbol,
            "decimals": decimals,
            "balance": balance,
        }
    except Exception as e:
        logger.exception(f"Error fetching token info for {token_addr}: {e}")
        raise e


# ------------------------
# TVL 계산 (USD 환산은 외부 가격 API 필요)
# ------------------------
async def get_tvl(web3ctx: Web3Ctx, model: PoolInfoModel):
    w3 = await web3ctx.get_w3()
    t0 = model.token0_address
    t1 = model.token1_address
    info0 = await get_token_info(w3, t0, model.pool_address)
    info1 = await get_token_info(w3, t1, model.pool_address)
    # 여기서는 USD 가격 API 연결해야 완전한 USD TVL 산출 가능
    # 일단 토큰 개수만 리턴
    return {"token0": info0, "token1": info1}


async def get_logs_chunked(w3, address, topic, start_block, end_block, step=500):
    address = AsyncWeb3.to_checksum_address(address)
    all_logs = []
    for from_block in range(start_block, end_block + 1, step):
        to_block = min(from_block + step - 1, end_block)
        logs = await w3.eth.get_logs(
            {
                "fromBlock": from_block,
                "toBlock": to_block,
                "address": address,
                "topics": [topic],
            }
        )
        all_logs.extend(logs)
    return all_logs


# ------------------------
# 최근 1시간 수수료 추정
# ------------------------
async def get_fees_last_hour(web3ctx: Web3Ctx, model: PoolInfoModel):
    # 현재 블록
    w3 = await web3ctx.get_w3()
    latest_block = await w3.eth.block_number
    latest_ts = (await w3.eth.get_block(latest_block))["timestamp"]

    # 1시간 전 타임스탬프
    one_hour_ago = latest_ts - 3600

    # 블록 범위 찾기 (간단하게 뒤로 스캔)
    start_block = latest_block
    while True:
        block = await w3.eth.get_block(start_block)
        if block["timestamp"] <= one_hour_ago:
            break
        start_block -= 50  # 블록 단위는 체인마다 조정

    # Swap 이벤트 가져오기
    logs = await get_logs_chunked(
        w3, model.pool_address, SWAP_TOPIC, start_block, latest_block
    )

    # feeTier 읽기
    fee = (model.fee or 10_000) / 1_000_000  # ex) 2500 -> 0.25%

    # 이벤트 디코딩 없이 amount0/1를 바로 쓰진 않고
    # 단순히 "거래량 × fee%"로 수수료 추정 (USD 변환은 별도)
    total_fees_token0 = 0
    total_fees_token1 = 0

    for log in logs:
        # input data: (amount0, amount1, sqrtPriceX96, liquidity, tick)
        data_bytes = bytes(log["data"])  # HexBytes → bytes
        # amount0, amount1은 int256이므로 32바이트씩
        amount0 = int.from_bytes(data_bytes[0:32], byteorder="big", signed=True)
        amount1 = int.from_bytes(data_bytes[32:64], byteorder="big", signed=True)
        # 수수료 추정 (거래량 × fee%)
        # 실제 fee는 풀 내부 계산과 살짝 다를 수 있지만 해커톤용 근사에는 충분
        total_fees_token0 += abs(amount0) * fee
        total_fees_token1 += abs(amount1) * fee

    t0_decimals = await Web3Utils.decimals(
        web3ctx, AsyncWeb3.to_checksum_address(model.token0_address)
    )
    t1_decimals = await Web3Utils.decimals(
        web3ctx, AsyncWeb3.to_checksum_address(model.token1_address)
    )
    total_fees_token0 = total_fees_token0 / 10**t0_decimals
    total_fees_token1 = total_fees_token1 / 10**t1_decimals

    return {"fees_token0": total_fees_token0, "fees_token1": total_fees_token1}


@dataclass
class TvlResponse:
    pool_address: str
    t0_addr: str
    t1_addr: str
    t0_decimal: int
    t1_decimal: int
    t0_balance: float
    t1_balance: float
    t0_symbol: str
    t1_symbol: str
    # 1 hour
    t0_fees: float
    t1_fees: float


@singleton
class TvlService:
    def __init__(self):
        db = get_mongo()
        self.pool_col = db["tvl"]

    async def get_by_pool_address(self, pool_address: str) -> TvlResponse | None:
        doc = await self.pool_col.find_one({"pool_address": pool_address})
        if doc:
            t0 = doc["tvl"]["token0"]
            t1 = doc["tvl"]["token1"]
            fees = doc.get("fees", {})
            return TvlResponse(
                pool_address=pool_address,
                t0_addr=t0["address"],
                t1_addr=t1["address"],
                t0_decimal=t0["decimals"],
                t1_decimal=t1["decimals"],
                t0_balance=t0["balance"],
                t1_balance=t1["balance"],
                t0_symbol=t0["symbol"],
                t1_symbol=t1["symbol"],
                t0_fees=fees.get("fees_token0", 0),
                t1_fees=fees.get("fees_token1", 0),
            )
        return None

    async def get_all_tvls(self) -> list[TvlResponse]:
        cursor = self.pool_col.find({})
        results = []
        async for doc in cursor:
            t0 = doc["tvl"]["token0"]
            t1 = doc["tvl"]["token1"]
            fees = doc.get("fees", {})
            results.append(
                TvlResponse(
                    pool_address=doc["pool_address"],
                    t0_addr=t0["address"],
                    t1_addr=t1["address"],
                    t0_decimal=t0["decimals"],
                    t1_decimal=t1["decimals"],
                    t0_balance=t0["balance"],
                    t1_balance=t1["balance"],
                    t0_symbol=t0["symbol"],
                    t1_symbol=t1["symbol"],
                    t0_fees=fees.get("fees_token0", 0),
                    t1_fees=fees.get("fees_token1", 0),
                )
            )
        return results


# ------------------------
# 실행
# ------------------------
if __name__ == "__main__":

    async def main():
        async def fetch_tvl(web3ctx: Web3Ctx, model: PoolInfoModel, deduplicate=True):
            try:
                tvl_col = get_mongo()["tvl"]
                if deduplicate:
                    response = await service.get_by_pool_address(model.pool_address)
                    if response:
                        logger.debug(
                            f"[tvl] skipping existing {model.pool_address} at {time.time()}"
                        )
                        return
                tvl = await get_tvl(web3ctx, model)
                fees = await get_fees_last_hour(web3ctx, model)
                await tvl_col.update_one(
                    {"pool_address": model.pool_address},  # filter
                    {
                        "$set": {
                            "timestamp": int(time.time()),
                            "tvl": tvl,
                            "fees": fees,
                        }
                    },
                    upsert=True,
                )

            except Exception as e:
                logger.exception(f"Error processing pool {model.pool_address}: {e}")

        from hypurrquant.evm import use_chain, Chain
        from services.pools import HybraPoolInfoFetcher

        init_db()
        hybra_fetcher = HybraPoolInfoFetcher()
        service = TvlService()
        pools = await hybra_fetcher.get_all()
        filtered = list(filter(lambda p: p.fee != None, pools))
        try:
            cap = 20
            async with use_chain(Chain.HYPERLIQUID) as web3ctx:
                for filters in range(0, len(filtered), cap):
                    batch = filtered[filters : filters + cap]
                    logger.debug(f"{batch=}")
                    print(
                        f"[tvl] processing batch {filters // cap + 1} "
                        f"({len(batch)} pools) at {time.time()}"
                    )
                    tasks = [fetch_tvl(web3ctx, p) for p in batch]
                    await asyncio.gather(*tasks)
        except Exception as e:
            return 1

        finally:
            await close_db()

    import asyncio

    asyncio.run(main())
