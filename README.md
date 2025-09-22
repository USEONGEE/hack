### 파이썬 단일 파일 실행하기
```shell
REDIS_HOST=localhost \
REDIS_PORT=6379 \
REDIS_DB=2 \
DB_CLUSTER_ENDPOINT=mongo \
DB_PORT=27017 \
DB_USERNAME=testuser \
DB_PASSWORD=testpassword \
DB_NAME=hack_backend \
BOT_TOKEN=8176985108:AAGs9ixDip6YxQlhG4KBY0itxGGcFvRAaLQ \
PROFILE=test \
BASE_URL=http://localhost:80/api \
BUILDER_ADDRESS=0x362294a899b304c933135781bb1f976ed8062781 \
BUILDER_ADDRESS_FEE=0 \
REGION_NAME=ap-northeast-1 \
FETCH_INTERVAL=10 \
KAFKA_BOOTSTRAP_SERVER_HOST=localhost \
KAFKA_BOOTSTRAP_SERVER_PORT=9092 \
SERVER_NAME=hack_backend \
LOKI_URL=http://localhost:3100/loki/api/v1/push \
uv run python -m services.index.tvl
uv run python -m services.pools
or 
```