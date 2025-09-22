LPNFTVAULT_ABI = [
    {
        "type": "function",
        "name": "getWhitelistedNFTCount",
        "inputs": [],
        "outputs": [{"type": "uint256"}],
        "stateMutability": "view",
    },
    {
        "type": "function",
        "name": "getWhitelistedNFTs",
        "inputs": [
            {"name": "offset", "type": "uint256"},
            {"name": "limit", "type": "uint256"},
        ],
        "outputs": [{"type": "address[]"}],
        "stateMutability": "view",
    },
    {
        "type": "function",
        "name": "getPositionInfo",
        "inputs": [
            {"name": "nft", "type": "address"},
            {"name": "tokenId", "type": "uint256"},
        ],
        "outputs": [
            {"name": "token0", "type": "address"},
            {"name": "token1", "type": "address"},
            {"name": "fee", "type": "uint24"},
            {"name": "liquidity", "type": "uint128"},
            {"name": "tickLower", "type": "int24"},
            {"name": "tickUpper", "type": "int24"},
            {"name": "currentTick", "type": "int24"},
            {"name": "sqrtPriceX96", "type": "uint160"},
            {"name": "isInRange", "type": "bool"},
            {"name": "owed0", "type": "uint256"},
            {"name": "owed1", "type": "uint256"},
        ],
        "stateMutability": "view",
    },
]

# ERC721Enumerable 최소 인터페이스: balanceOf, tokenOfOwnerByIndex
ERC721_ENUMERABLE_ABI = [
    {
        "type": "function",
        "name": "balanceOf",
        "inputs": [{"name": "owner", "type": "address"}],
        "outputs": [{"type": "uint256"}],
        "stateMutability": "view",
    },
    {
        "type": "function",
        "name": "tokenOfOwnerByIndex",
        "inputs": [
            {"name": "owner", "type": "address"},
            {"name": "index", "type": "uint256"},
        ],
        "outputs": [{"type": "uint256"}],
        "stateMutability": "view",
    },
]


LPNFTVAULT_MIN_ABI = [
    {  # deposit 상태 조회
        "type": "function",
        "name": "getDeposit",
        "inputs": [
            {"name": "nft", "type": "address"},
            {"name": "tokenId", "type": "uint256"},
        ],
        "outputs": [
            {
                "type": "tuple",
                "components": [
                    {"name": "owner", "type": "address"},
                    {"name": "nft", "type": "address"},
                    {"name": "tokenId", "type": "uint256"},
                    {"name": "active", "type": "bool"},
                    {"name": "token0", "type": "address"},
                    {"name": "token1", "type": "address"},
                    {"name": "fee", "type": "uint24"},
                    {"name": "tickLower", "type": "int24"},
                    {"name": "tickUpper", "type": "int24"},
                    {"name": "liquiditySnapshot", "type": "uint128"},
                ],
            }
        ],
        "stateMutability": "view",
    },
    {  # 헷지 신호 판단
        "type": "function",
        "name": "shouldHedgePosition",
        "inputs": [
            {"name": "nft", "type": "address"},
            {"name": "tokenId", "type": "uint256"},
        ],
        "outputs": [
            {"name": "shouldHedge", "type": "bool"},
            {"name": "targetTokenBalance", "type": "uint256"},
            {"name": "otherTokenBalance", "type": "uint256"},
            {"name": "targetTokenRatio", "type": "uint256"},
            {"name": "reason", "type": "string"},
        ],
        "stateMutability": "view",
    },
]

HYPERLIQUID_HEDGE_MIN_ABI = [
    {
        "type": "function",
        "name": "getHedgeStatus",
        "inputs": [{"name": "user", "type": "address"}],
        "outputs": [
            {"name": "isActive", "type": "bool"},
            {"name": "hedgeSize", "type": "uint64"},
            {"name": "entryPrice", "type": "uint64"},
            {"name": "currentPrice", "type": "uint64"},
            {"name": "stableDeposit", "type": "uint256"},
        ],
        "stateMutability": "view",
    }
]
