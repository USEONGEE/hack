from __future__ import annotations
import os
import sys
import json
import argparse
import asyncio
from typing import List, Dict, Any

from . import HybraPoolInfoFetcher, KittenPoolInfoFetcher, PoolInfo


def poolinfo_to_dict(p: PoolInfo) -> Dict[str, Any]:
    return {
        "pool_address": p.pool_address,
        "token0_address": p.token0_address,
        "token1_address": p.token1_address,
        "fee": p.fee,
    }


async def amain() -> int:
    from hypurrquant.db import init_db, close_db

    init_db()
    fetcher = KittenPoolInfoFetcher()
    await fetcher._fetch_and_save()

    fetcher = HybraPoolInfoFetcher()
    await fetcher._fetch_and_save()

    await close_db()


if __name__ == "__main__":

    sys.exit(asyncio.run(amain()))
