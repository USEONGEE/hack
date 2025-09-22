from __future__ import annotations
from typing import List, Optional, Dict, Any, Iterable
import os
import asyncio

from hypurrquant.api.async_http import send_request_for_external
from .interface import PoolInfo, PoolInfoFetcher


def _lower(addr: Optional[str]) -> Optional[str]:
    return addr.lower() if isinstance(addr, str) else addr


def _pick(obj: Dict[str, Any], keys: Iterable[str]) -> Optional[Any]:
    for k in keys:
        if k in obj and obj[k] is not None:
            return obj[k]
    return None


def _pick_version(labels: Optional[list]) -> Optional[str]:
    if not isinstance(labels, list):
        return "v3"
    for tag in labels:
        t = str(tag).lower()
        if "v2" in t:
            return "v2"
        if "v3" in t:
            return "v3"
    return "v3"


class KittenPoolInfoFetcher(PoolInfoFetcher):
    """
    Kittenswap 응답을 PoolInfo로 일반화.
    응답은 Dexscreener 유사 구조(리스트 또는 딕셔너리 래핑)라고 가정.
    """

    def __init__(self):
        self.url = "https://api.kittenswap.finance/pairs-data"
        self.timeout = 30
        self.dex_type = "kitten"

    def _normalize_item(self, item: Dict[str, Any]) -> Optional[PoolInfo]:
        # pair/pool 주소
        pair = _pick(item, ["pairAddress", "poolAddress", "address"])
        pair = _lower(pair)

        # token 주소 (base/quote 우선, 없으면 token0/token1)
        base_addr = _lower(
            _pick(
                (
                    item.get("baseToken", {})
                    if isinstance(item.get("baseToken"), dict)
                    else {}
                ),
                ["address"],
            )
            or _pick(item, ["token0Address", "token0"])
        )
        quote_addr = _lower(
            _pick(
                (
                    item.get("quoteToken", {})
                    if isinstance(item.get("quoteToken"), dict)
                    else {}
                ),
                ["address"],
            )
            or _pick(item, ["token1Address", "token1"])
        )

        version = _pick_version(item.get("labels"))

        if not pair or not base_addr or not quote_addr:
            return None

        return PoolInfo(
            token0_address=base_addr,
            token1_address=quote_addr,
            fee=None,  # v2 AMM 가정: 수수료 미제공 → None
            pool_address=pair,
            version=version,
            protocol="algebra",
        )

    def _extract_items(self, data: Any) -> List[Dict[str, Any]]:
        """
        다양한 응답 래핑을 지원:
        - [ {...}, {...} ]
        - { "pairs": [ ... ] }
        - { "data": [ ... ] }
        - { "result": { "pairs": [ ... ] } }
        - { "result": [ ... ] }
        """
        if isinstance(data, list):
            return [x for x in data if isinstance(x, dict)]

        if isinstance(data, dict):
            # 일반적인 키 시도
            for key in ("pairs", "data"):
                v = data.get(key)
                if isinstance(v, list):
                    return [x for x in v if isinstance(x, dict)]

            # result 래핑
            res = data.get("result")
            if isinstance(res, dict):
                for key in ("pairs", "data"):
                    v = res.get(key)
                    if isinstance(v, list):
                        return [x for x in v if isinstance(x, dict)]
            if isinstance(res, list):
                return [x for x in res if isinstance(x, dict)]

        # 알 수 없는 구조 → 빈 목록
        return []

    async def _fetch(self) -> List[PoolInfo]:
        data = await send_request_for_external("GET", self.url)

        items = self._extract_items(data)

        out: List[PoolInfo] = []
        for it in items:
            norm = self._normalize_item(it)
            if norm:
                out.append(norm)
        return out


# 모듈 단독 실행용 (python -m services.pools.kitten)
async def _amain() -> int:
    url = ""
    if not url:
        # 예시: Dexscreener 스타일 엔드포인트를 주입하세요.
        # 필수 환경변수로 강제
        print(
            "KITTEN_POOLS_URL is not set. Please export KITTEN_POOLS_URL=<endpoint returning list/dict>",
            flush=True,
        )
        return 2

    limit = int(os.getenv("LIMIT", "10"))
    as_json = os.getenv("JSON", "0") == "1"

    fetcher = KittenPoolInfoFetcher(url=url)
    pools = await fetcher.fetch()
    pools = list(filter(lambda p: p.version == "v3", pools))

    if as_json:
        import json

        def to_dict(p: PoolInfo):
            return {
                "pool_address": p.pool_address,
                "token0_address": p.token0_address,
                "token1_address": p.token1_address,
                "fee": p.fee,
                "version": p.version,
            }

        payload = pools if limit == 0 else pools[:limit]
        print(json.dumps([to_dict(p) for p in payload], ensure_ascii=False, indent=2))
    else:
        print(f"[kitten] fetched {len(pools)} pools from: {url}")
        preview = pools if limit == 0 else pools[:limit]
        for i, p in enumerate(preview, 1):
            print(
                f"{i:>3}. pool={p.pool_address}  ver={p.version or '-'}  "
                f"token0={p.token0_address}  token1={p.token1_address}"
            )
        if limit and len(pools) > limit:
            print(f"... ({len(pools) - limit} more)")
    return 0


if __name__ == "__main__":
    try:
        asyncio.run(_amain())
    except KeyboardInterrupt:
        pass
