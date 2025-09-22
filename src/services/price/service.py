from typing import List, Dict, Optional, Tuple, Any
import json
import math
import asyncio

from hypurrquant.utils.singleton import singleton
from hypurrquant.logging_config import configure_logging, coroutine_logging
from hypurrquant.evm import Web3Ctx, Chain, Web3Utils
from hypurrquant.db.redis import get_redis_async
from hypurrquant.api.async_http import send_request_for_external

from web3 import AsyncWeb3

_logger = configure_logging(__name__)

# ==========================
# Dexscreener 설정
# ==========================
DEXSCREENER_TOKEN_URL = "https://api.dexscreener.com/token-pairs/v1/{chain}/{address}"
CHAIN_TO_DS_SLUG = {
    Chain.HYPERLIQUID: "hyperevm",
}
USDT0_BY_CHAIN = {
    Chain.HYPERLIQUID: "0xB8CE59FC3717ada4C02eaDF9682A9e934F625ebb",  # USDT0
}


# ==========================
# 반환 타입
# ==========================
class ResultType(dict):
    address: str
    ticker: str
    price_usd: Optional[float]


def _parse_float(x: Any) -> Optional[float]:
    if x is None:
        return None
    try:
        return float(x)
    except Exception:
        try:
            # 일부 응답이 문자열 숫자일 수 있음(공백/개행 포함 방지)
            return float(str(x).strip())
        except Exception:
            return None


@singleton
class TokenPriceService:
    def __init__(self):
        self.redis_client = get_redis_async()

    # ============ 캐시 유틸 ============
    def _ds_pairs_cache_key(self, chain: Chain, addr: str) -> str:
        return f"dexscreener:pairs:{chain.value}:{addr.lower()}"

    async def _cache_get_pairs(
        self, chain: Chain, addr: str
    ) -> Optional[List[Dict[str, Any]]]:
        key = self._ds_pairs_cache_key(chain, addr)
        raw = await self.redis_client.get(key)
        if not raw:
            return None
        try:
            return json.loads(raw)
        except Exception:
            return None

    async def _cache_set_pairs(
        self, chain: Chain, addr: str, pairs: List[Dict[str, Any]], ttl_sec: int = 45
    ):
        key = self._ds_pairs_cache_key(chain, addr)
        try:
            await self.redis_client.set(key, json.dumps(pairs), ex=ttl_sec)
        except Exception as e:
            _logger.info(f"⚠️ failed to cache pairs {addr=}: {e}", exc_info=True)

    # ============ Dexscreener 호출 ============
    async def _fetch_pairs_from_dexscreener(
        self, chain: Chain, addr: str
    ) -> List[Dict[str, Any]]:
        """Dexscreener에서 특정 토큰 주소의 모든 페어 목록 조회."""
        slug = CHAIN_TO_DS_SLUG.get(chain)
        if not slug:
            return []

        # 캐시 먼저 확인
        cached = await self._cache_get_pairs(chain, addr)
        if cached is not None:
            return cached

        url = DEXSCREENER_TOKEN_URL.format(chain=slug, address=addr)
        try:
            data = await send_request_for_external("GET", url, timeout=20)
            # 이 엔드포인트는 배열(List[Pair])을 반환
            if isinstance(data, list):
                pairs = data
            elif isinstance(data, dict) and "pairs" in data:  # 안전장치
                pairs = data["pairs"]
            else:
                pairs = []
            await self._cache_set_pairs(chain, addr, pairs)
            return pairs
        except Exception as e:
            _logger.info(f"❌ Dexscreener fetch failed: {url} — {e}", exc_info=True)
            return []

    # ============ 필터/추출 도우미 ============
    def _pairs_with_base(
        self, pairs: List[Dict[str, Any]], base_addr: str
    ) -> List[Dict[str, Any]]:
        b = base_addr.lower()
        out = []
        for p in pairs:
            try:
                if p["baseToken"]["address"].lower() == b:
                    out.append(p)
            except Exception:
                continue
        return out

    def _pairs_with_quote(
        self, pairs: List[Dict[str, Any]], quote_addr: str
    ) -> List[Dict[str, Any]]:
        q = quote_addr.lower()
        out = []
        for p in pairs:
            try:
                if p["quoteToken"]["address"].lower() == q:
                    out.append(p)
            except Exception:
                continue
        return out

    def _extract_prices_base_is_token_price_usd(
        self, pairs: List[Dict[str, Any]], token_addr: str
    ) -> List[float]:
        prices: List[float] = []
        for p in self._pairs_with_base(pairs, token_addr):
            val = _parse_float(p.get("priceUsd"))
            if val is not None and math.isfinite(val) and val > 0:
                prices.append(val)
        return prices

    def _extract_prices_from_usdt0_over_token_via_reciprocal_price_native(
        self, usdt_pairs: List[Dict[str, Any]], token_addr: str
    ) -> List[float]:
        """
        USDT0를 조회해서 quote==token 인 페어들(USDT0/token)을 모은 뒤
        각 dict의 priceNative 값을 역수 처리하여 token의 USD 가격으로 변환.
        - priceNative 필드가 없으면 price(=base/quote 환율)로 폴백.
        """
        prices: List[float] = []
        for p in self._pairs_with_quote(usdt_pairs, token_addr):
            # 우선순위: priceNative → price (둘 다 문자열일 수 있음)
            native = _parse_float(p.get("priceNative"))
            rate = native if native is not None else _parse_float(p.get("price"))
            if rate is None:
                continue
            try:
                inv = 1.0 / rate
                if math.isfinite(inv) and inv > 0:
                    prices.append(inv)
            except Exception:
                continue
        return prices

    # -------- 아웃라이어 필터 훅 (지금은 패스스루) --------
    def _filter_outliers(
        self, values: List[float], strategy: Optional[str] = None
    ) -> List[float]:
        """
        추후 IQR/MAD/윈저라이징 등 전략을 붙이기 위한 훅.
        현재는 필터 없이 그대로 반환.
        """
        return values

    # ============ 심볼 조회 (기존 멀티콜 캐시 재활용) ============
    async def _get_tickers(self, web3ctx: Web3Ctx, addrs: List[str]) -> Dict[str, str]:
        try:
            return await self.get_ticker_by_addresses(web3ctx, *addrs)
        except Exception:
            return {a: a[:6] for a in addrs}

    async def get_ticker_by_addresses(self, web3ctx: Web3Ctx, *tokens, batch_size=500):
        tokens = [AsyncWeb3.to_checksum_address(t) for t in tokens]
        keys = [
            f"hypurrquant:common:{web3ctx.chain_id}:tickerByAddress:{t}" for t in tokens
        ]
        cached = await self.redis_client.mget(keys)

        results: Dict[str, str] = {}
        no_cache: List[str] = []
        for addr, symbol in zip(tokens, cached):
            if symbol:
                results[addr] = symbol
            else:
                no_cache.append(addr)

        if no_cache:
            from web3.contract import AsyncContract
            from eth_abi import decode

            multicall: AsyncContract = await Web3Utils.get_multicall(web3ctx)
            fetched: Dict[str, str] = {}
            for i in range(0, len(no_cache), batch_size):
                chunk = no_cache[i : i + batch_size]
                calls, meta = [], []
                for addr in chunk:
                    a = AsyncWeb3.to_checksum_address(addr)
                    calls.append(
                        {"target": a, "callData": Web3Utils.encode_selector("symbol()")}
                    )
                    meta.append(a)
                res = await multicall.functions.tryAggregate(False, calls).call()
                for a, (success, ret) in zip(meta, res):
                    if not success or not ret:
                        continue
                    try:
                        symbol = decode(["string"], ret)[0]
                    except Exception:
                        try:
                            symbol = ret.decode("utf-8").rstrip("\x00")
                        except Exception:
                            continue
                    fetched[a] = symbol

            results.update(fetched)
            if fetched:
                await self.redis_client.mset(
                    {
                        f"hypurrquant:common:{web3ctx.chain_id}:tickerByAddress:{k}": v
                        for k, v in fetched.items()
                    }
                )
        return results

    # ============ 메인: 가격 조회 ============
    @coroutine_logging
    async def get_token_price_in_usd(
        self,
        web3ctx: Web3Ctx,
        token_addresses: List[str],
        outlier_strategy: Optional[str] = None,  # ← 추후 이상치 전략용
    ) -> Dict[str, ResultType]:
        """
        Dexscreener를 이용해 각 토큰의 USD 가격을 구한다.
        - 1차: A를 조회해 baseToken==A 인 페어들의 priceUsd 산술평균
        - 2차: (1차 실패 시) USDT0를 조회해 quoteToken==A 인 페어들의 (1/priceNative) 산술평균
        - 둘 다 없으면 None
        """
        chain = web3ctx.chain
        slug = CHAIN_TO_DS_SLUG.get(chain)
        if not slug:
            raise RuntimeError(f"Dexscreener chain slug not configured for {chain=}")

        usdt0 = USDT0_BY_CHAIN.get(chain)
        if not usdt0:
            raise RuntimeError(f"USDT0 not configured for {chain=}")

        # 주소 정규화
        addrs = [AsyncWeb3.to_checksum_address(a) for a in token_addresses]
        usdt0 = AsyncWeb3.to_checksum_address(usdt0)

        # 심볼(티커) 채우기
        tickers = await self._get_tickers(web3ctx, addrs + [usdt0])

        results: Dict[str, ResultType] = {}

        async def process_one(addr: str) -> Tuple[str, ResultType]:
            # USDT0는 1.0 고정
            if addr.lower() == usdt0.lower():
                return addr, {
                    "address": addr,
                    "ticker": tickers.get(addr, "USDT"),
                    "price_usd": 1.0,
                }

            # --- 1) A 조회 → base==A 의 priceUsd 수집
            token_pairs = await self._fetch_pairs_from_dexscreener(chain, addr)
            direct_prices = self._extract_prices_base_is_token_price_usd(
                token_pairs, addr
            )

            # 아웃라이어 필터 훅
            direct_prices = self._filter_outliers(direct_prices, outlier_strategy)

            all_prices: List[float] = []
            if direct_prices:
                all_prices.extend(direct_prices)
            else:
                # --- 2) A가 base에 없으면: USDT0 조회 → quote==A 의 priceNative 역수 수집
                usdt_pairs = await self._fetch_pairs_from_dexscreener(chain, usdt0)
                via_recip = self._extract_prices_from_usdt0_over_token_via_reciprocal_price_native(
                    usdt_pairs, addr
                )
                via_recip = self._filter_outliers(via_recip, outlier_strategy)
                all_prices.extend(via_recip)

            # 집계
            all_prices = [
                p
                for p in all_prices
                if isinstance(p, (int, float)) and math.isfinite(p) and p > 0
            ]
            if not all_prices:
                return addr, {
                    "address": addr,
                    "ticker": tickers.get(addr, addr[:6]),
                    "price_usd": None,
                }

            avg_price = sum(all_prices) / float(len(all_prices))
            return addr, {
                "address": addr,
                "ticker": tickers.get(addr, addr[:6]),
                "price_usd": avg_price,
            }

        # 병렬 처리
        tasks = [process_one(a) for a in addrs]
        done = await asyncio.gather(*tasks, return_exceptions=True)
        for item in done:
            if isinstance(item, Exception):
                _logger.info(f"❌ price task failed: {item}", exc_info=True)
                continue
            addr, res = item
            results[addr] = res

        return results


# ---- 테스트/예시 진입점(옵션): main2를 간단히 유지 ----
async def main2():
    from hypurrquant.evm import use_chain, Chain
    from hypurrquant.evm.constants._hyperliquid.token_address import (
        WHYPE_ADDRESS,
        KITTEN_ADDRESS,
        UBTC_ADDRESS,
        USDT0_ADDRESS,
    )
    from hypurrquant.db import init_db, close_db
    from time import time
    import aiohttp, ssl, certifi

    ssl_ctx = ssl.create_default_context(cafile=certifi.where())
    connector = aiohttp.TCPConnector(ssl=ssl_ctx)
    async with aiohttp.ClientSession(connector=connector) as session:
        init_db()
        try:
            svc = TokenPriceService()
            async with use_chain(Chain.HYPERLIQUID) as web3ctx:
                toks = [WHYPE_ADDRESS]
                t0 = time()
                res = await svc.get_token_price_in_usd(web3ctx, toks)
                print("Elapsed time:", time() - t0)
                print(res)
        finally:
            await close_db()
            await asyncio.sleep(1)


if __name__ == "__main__":
    import asyncio

    try:
        asyncio.run(main2())
    except:
        pass
