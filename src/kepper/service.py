"""
# 헷지 시나리오
1. NFT Vault에 예치된 모든 nft 조회하기
2. 순회하면서 각 nft의 token0/1 ratio를 비교해서 헷지 할지 안 할지, 헷지 대상인지 아닌지 판단하기
3. 헷지 대상이면 헷지 주문 넣기

## 필요한 컨트렉
- ✅ nft 전체조회
- ✅ nft 개별 조회 (token0, 1)
- ✅ nft 헷지 여부 조회
- Hedge vault에 헷지 주문 넣기

# 헷지 풀기 시나리오
1. nft vault에서 헷지가 된 nft 조회하기
2. 순회하면서 100:0 이 꺠지면 헷지 풀기 (헷지가 풀리면서 사용자의 수수료 수취에서 일부를 가져갈 수도 있음)
"""

from hypurrquant.utils.graceful_shutdown import GracefulShutdownMixin
from hypurrquant.evm import Web3Utils, Web3Ctx, use_chain
from typing import Dict, List, Tuple, Any, Optional

from web3 import AsyncWeb3
from web3.contract import AsyncContract

from hypurrquant.logging_config import configure_logging
from hypurrquant.utils.graceful_shutdown import GracefulShutdownMixin
from ..constants import Chain
from web3.exceptions import ContractLogicError
from dataclasses import dataclass

from .abi import *

_logger = configure_logging(__name__)

NFT_VAULT_ADDRESS = "0xYourVaultAddressHere"
HEDGE_CONTRACT_ADDRESS = "0xYourHedgeContractAddressHere"


@dataclass
class PositionInfo:
    success: bool
    token0: Optional[str] = None
    token1: Optional[str] = None
    fee: Optional[int] = None
    liquidity: Optional[int] = None
    tickLower: Optional[int] = None
    tickUpper: Optional[int] = None
    currentTick: Optional[int] = None
    sqrtPriceX96: Optional[int] = None
    isInRange: Optional[bool] = None
    owed0: Optional[int] = None
    owed1: Optional[int] = None
    error: Optional[str] = None


@dataclass
class NFTHedgeStatus:
    # Deposit info
    deposit_active: bool
    deposit_owner: Optional[str]
    # Hedge signal computed by Vault
    hedge_signal: bool
    target_token_ratio_bps: Optional[int]
    hedge_signal_reason: Optional[str]
    # Actual hedge status on HyperliquidHedge (keyed by vault address)
    hedge_active: bool
    hedge_size: Optional[int]
    hedge_entry_price: Optional[int]
    hedge_current_price: Optional[int]
    hedge_stable_deposit: Optional[int]


@dataclass
class HedgeDecision:
    """
    - open: 헷지 시작 권고
    - close: 헷지 종료 권고
    - hold: 현재 상태 유지
    """

    action: str  # "open", "close", "hold"
    reason: str  # 사람이 읽을 수 있는 설명


class HedgeOnKeeper(GracefulShutdownMixin):
    def __init__(self):
        super().__init__()

    async def run_once(self):
        async with use_chain(Chain.HYPERLIQUID) as web3ctx:
            # 1) Vault가 보유 중인 모든 NFT 수집
            nft_tokens_map = await fetch_vault_held_nft_ids(web3ctx, NFT_VAULT_ADDRESS)

            if not nft_tokens_map:
                _logger.info("No NFTs held by vault. Nothing to do.")
                return

            # 2) 모든 (nft, tokenId) 포지션 상태를 multicall로 조회 (한 번)
            position_info_map = await multicall_get_position_info(
                web3ctx, NFT_VAULT_ADDRESS, nft_tokens_map
            )

            # 3) evaluate를 전체 dict에 대해 '한 번' 호출
            evaluated_map = await evaluate_positions_for_hedge(
                web3ctx,
                vault_address=NFT_VAULT_ADDRESS,
                hedge_contract_address=HEDGE_CONTRACT_ADDRESS,
                positions=position_info_map,  # ✅ 전체 맵을 넘긴다
            )

            # 4) 결과 순회하며 액션 수행
            for (nft, token_id), (status, decision) in evaluated_map.items():
                if decision.action == "open":
                    _logger.info(
                        f"[Hedge Decision] OPEN — {nft}#{token_id} | "
                        f"reason={decision.reason} | ratio_bps={status.target_token_ratio_bps}"
                    )
                    # TODO: 헷지 주문 로직 (HyperliquidHedge.executeHedge 트리거 등)
                elif decision.action == "close":
                    _logger.info(
                        f"[Hedge Decision] CLOSE — {nft}#{token_id} | "
                        f"reason={decision.reason} | ratio_bps={status.target_token_ratio_bps}"
                    )
                    # TODO: 헷지 종료 로직
                else:
                    _logger.info(
                        f"[Hedge Decision] HOLD — {nft}#{token_id} | "
                        f"signal={status.hedge_signal} active={status.hedge_active}"
                    )


async def fetch_vault_held_nft_ids(
    web3ctx: Web3Ctx,
    vault_address: str,
    page_size: int = 500,
) -> Dict[str, List[int]]:
    """
    LPNFTVault가 '현재' 소유하고 있는 모든 NFT tokenId를 컬렉션별로 수집.
    반환: { nft_address -> [tokenId, ...], ... }
    """
    w3 = await web3ctx.get_w3()
    vault_address = AsyncWeb3.to_checksum_address(vault_address)
    vault: AsyncContract = await Web3Utils.build_contract(
        web3ctx, vault_address, LPNFTVAULT_ABI
    )

    total = await vault.functions.getWhitelistedNFTCount().call()
    _logger.info(f"[Vault] whitelisted NFT collections: {total}")

    nft_to_ids: Dict[str, List[int]] = {}

    # 페이지네이션으로 화이트리스트된 NFT 주소 모으기
    fetched = 0
    nft_addrs: List[str] = []
    while fetched < total:
        batch = await vault.functions.getWhitelistedNFTs(
            fetched, min(page_size, total - fetched)
        ).call()
        nft_addrs.extend([AsyncWeb3.to_checksum_address(a) for a in batch])
        fetched += len(batch)

    # 각 NFT 컬렉션에서 vault 소유 tokenId들 수집
    for nft in nft_addrs:
        try:
            erc721 = await Web3Utils.build_contract(web3ctx, nft, ERC721_ENUMERABLE_ABI)
            bal = await erc721.functions.balanceOf(vault_address).call()
        except ContractLogicError as e:
            _logger.warning(
                f"[Vault] {nft} does not implement ERC721Enumerable? skip. err={e}"
            )
            continue
        except Exception as e:
            _logger.warning(
                f"[Vault] Failed to read balanceOf on {nft}: {e}", exc_info=True
            )
            continue

        ids: List[int] = []
        for i in range(int(bal)):
            try:
                token_id = await erc721.functions.tokenOfOwnerByIndex(
                    vault_address, i
                ).call()
                ids.append(int(token_id))
            except Exception as e:
                _logger.warning(
                    f"[Vault] tokenOfOwnerByIndex failed on {nft} index={i}: {e}",
                    exc_info=True,
                )
                break

        if ids:
            nft_to_ids[nft] = ids
            _logger.info(f"[Vault] {nft}: {len(ids)} tokenIds collected")

    return nft_to_ids


async def multicall_get_position_info(
    web3ctx: Web3Ctx,
    vault_address: str,
    nft_to_ids: Dict[str, list[int]],
) -> Dict[Tuple[str, int], PositionInfo]:
    w3 = await web3ctx.get_w3()
    vault_address = AsyncWeb3.to_checksum_address(vault_address)
    vault: AsyncContract = await Web3Utils.build_contract(
        web3ctx, vault_address, LPNFTVAULT_ABI
    )
    multicall = await Web3Utils.get_multicall(web3ctx)

    fn = vault.get_function_by_name("getPositionInfo")
    fn_abi = fn.abi
    output_types = [o["type"] for o in fn_abi["outputs"]]
    output_names = [o.get("name", f"_{i}") for i, o in enumerate(fn_abi["outputs"])]

    calls, pair_keys = [], []
    for nft, token_ids in nft_to_ids.items():
        nft = AsyncWeb3.to_checksum_address(nft)
        for tid in token_ids:
            calldata = vault.encodeABI(fn_name="getPositionInfo", args=[nft, int(tid)])
            calls.append({"target": vault_address, "callData": calldata})
            pair_keys.append((nft, int(tid)))

    results = await multicall.functions.tryAggregate(False, calls).call()
    decoded: Dict[Tuple[str, int], PositionInfo] = {}

    for key, (success, return_data) in zip(pair_keys, results):
        if not success or not return_data:
            decoded[key] = PositionInfo(success=False, error="call failed")
            continue

        try:
            values = w3.codec.decode_abi(output_types, return_data)
            decoded[key] = PositionInfo(
                success=True, **{name: val for name, val in zip(output_names, values)}
            )
        except Exception as e:
            decoded[key] = PositionInfo(success=False, error=f"decode failed: {e}")

    return decoded


async def multicall_should_hedge_positions(
    web3ctx: Web3Ctx,
    vault_address: str,
    keys: List[Tuple[str, int]],
) -> Dict[Tuple[str, int], Dict[str, Any]]:
    """
    shouldHedgePosition(nft, tokenId)를 multicall로 일괄 조회.
    반환: {(nft, tokenId): {"shouldHedge": bool, "ratio_bps": int, "reason": str, ...}}
    """
    w3 = await web3ctx.get_w3()
    vault_address = AsyncWeb3.to_checksum_address(vault_address)
    vault: AsyncContract = await Web3Utils.build_contract(
        web3ctx, vault_address, LPNFTVAULT_MIN_ABI
    )
    multicall = await Web3Utils.get_multicall(web3ctx)

    fn = vault.get_function_by_name("shouldHedgePosition")
    fn_abi = fn.abi
    out_types = [o["type"] for o in fn_abi["outputs"]]
    out_names = [o.get("name") or f"_{i}" for i, o in enumerate(fn_abi["outputs"])]

    calls = []
    for nft, token_id in keys:
        calldata = vault.encodeABI(
            fn_name="shouldHedgePosition",
            args=[AsyncWeb3.to_checksum_address(nft), int(token_id)],
        )
        calls.append({"target": vault_address, "callData": calldata})

    if not calls:
        return {}

    results = await multicall.functions.tryAggregate(False, calls).call()
    decoded: Dict[Tuple[str, int], Dict[str, Any]] = {}

    for key, (success, returndata) in zip(keys, results):
        if not success or not returndata:
            decoded[key] = {"ok": False, "err": "call failed"}
            continue
        try:
            vals = w3.codec.decode_abi(out_types, returndata)
            row = {name: val for name, val in zip(out_names, vals)}
            decoded[key] = {
                "ok": True,
                "shouldHedge": bool(row["shouldHedge"]),
                "targetTokenBalance": int(row["targetTokenBalance"]),
                "otherTokenBalance": int(row["otherTokenBalance"]),
                "ratio_bps": int(row["targetTokenRatio"]),  # 10000=100%
                "reason": str(row["reason"]),
            }
        except Exception as e:
            decoded[key] = {"ok": False, "err": f"decode failed: {e}"}

    return decoded


async def get_vault_hedge_status(
    web3ctx: Web3Ctx,
    hedge_contract_address: str,
    vault_address: str,
) -> Dict[str, Any]:
    """
    HyperliquidHedge.getHedgeStatus(vault_address) 단건 조회.
    """
    hedge: AsyncContract = await Web3Utils.build_contract(
        web3ctx, hedge_contract_address, HYPERLIQUID_HEDGE_MIN_ABI
    )
    vault_address = AsyncWeb3.to_checksum_address(vault_address)
    isActive, hedgeSize, entryPrice, currentPrice, stableDeposit = (
        await hedge.functions.getHedgeStatus(vault_address).call()
    )
    return {
        "isActive": bool(isActive),
        "hedgeSize": int(hedgeSize),
        "entryPrice": int(entryPrice),
        "currentPrice": int(currentPrice),
        "stableDeposit": int(stableDeposit),
    }


async def evaluate_positions_for_hedge(
    web3ctx: Web3Ctx,
    vault_address: str,
    hedge_contract_address: str,
    positions: Dict[Tuple[str, int], "PositionInfo"],  # key=(nft, tokenId)
) -> Dict[Tuple[str, int], Tuple["NFTHedgeStatus", HedgeDecision]]:
    """
    1) (nft, tokenId)별 shouldHedgePosition(multicall)
    2) vault 단위 getHedgeStatus(단건)
    3) 로직:
       - hedge_active and not hedge_signal -> "close"
       - not hedge_active and hedge_signal -> "open"
       - else -> "hold"
    """
    # (1) 헷지 신호 상태 일괄 획득
    keys = list(positions.keys())
    signals = await multicall_should_hedge_positions(web3ctx, vault_address, keys)

    # (2) vault 기준 실제 헷지 포지션 상태 1회 조회
    hedge_stat = await get_vault_hedge_status(
        web3ctx, hedge_contract_address, vault_address
    )
    hedge_active_global = hedge_stat["isActive"]

    # (3) deposit_active/owner는 getDeposit으로도 뽑을 수 있지만
    #     여기서는 빠르게 처리하려면 이미 positions가 "Vault 보유" 기준으로 조회된 것이라면
    #     deposit_active를 True로 보고, owner는 None 처리해도 됨.
    #     정확히 하려면 getDeposit(nft, tokenId)로 한 번 더 가져오세요(아래 주석의 옵션 참고).
    vault: AsyncContract = await Web3Utils.build_contract(
        web3ctx, vault_address, LPNFTVAULT_MIN_ABI
    )

    results: Dict[Tuple[str, int], Tuple["NFTHedgeStatus", HedgeDecision]] = {}

    # 선택: deposit 정보를 실제로 조회하고 싶다면 multicall로 추가 조회
    # 여기서는 개별 call(정확성 우선). 대량이면 multicall로 변환 권장.
    async def fetch_deposit(nft: str, token_id: int):
        dep = await vault.functions.getDeposit(
            AsyncWeb3.to_checksum_address(nft), int(token_id)
        ).call()
        return {
            "owner": dep[0],
            "active": bool(dep[3]),
        }

    # 병렬화
    import asyncio

    deposit_infos = await asyncio.gather(
        *[fetch_deposit(n, t) for (n, t) in keys], return_exceptions=True
    )
    dep_map = {}
    for key, di in zip(keys, deposit_infos):
        if isinstance(di, Exception):
            _logger.warning(f"getDeposit failed for {key}: {di}")
            dep_map[key] = {"owner": None, "active": False}
        else:
            dep_map[key] = di

    for key in keys:
        nft, tid = key
        pos = positions[key]
        sig = signals.get(key, {"ok": False, "err": "no signal"})
        dep = dep_map.get(key, {"owner": None, "active": False})

        # 기본값
        hedge_signal = False
        ratio_bps = None
        reason = None
        if sig.get("ok"):
            hedge_signal = bool(sig["shouldHedge"])
            ratio_bps = int(sig["ratio_bps"])
            reason = sig["reason"]
        else:
            reason = f"signal_unavailable: {sig.get('err')}"

        status = NFTHedgeStatus(
            deposit_active=bool(dep["active"]),
            deposit_owner=dep["owner"],
            hedge_signal=hedge_signal,
            target_token_ratio_bps=ratio_bps,
            hedge_signal_reason=reason,
            hedge_active=bool(hedge_active_global),
            hedge_size=hedge_stat["hedgeSize"],
            hedge_entry_price=hedge_stat["entryPrice"],
            hedge_current_price=hedge_stat["currentPrice"],
            hedge_stable_deposit=hedge_stat["stableDeposit"],
        )

        # 액션 결정
        if status.hedge_active and not status.hedge_signal:
            decision = HedgeDecision(
                action="close",
                reason="Hedge active but signal is off → consider closing hedge.",
            )
        elif (not status.hedge_active) and status.hedge_signal:
            decision = HedgeDecision(
                action="open",
                reason="Hedge inactive but signal is on → consider opening hedge.",
            )
        else:
            decision = HedgeDecision(
                action="hold",
                reason="Hedge status matches signal (both on or both off).",
            )

        results[key] = (status, decision)

    return results
