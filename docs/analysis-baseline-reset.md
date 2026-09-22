# Analysis baseline reset

운영 DB와 JSONL telemetry를 분석할 때 서로 다른 시기에 추가된 observability field가 섞이면,
과거 row의 missing field를 실제 runtime behavior 차이로 잘못 해석할 수 있습니다.

`hina-reset-analysis-data`는 장기기억과 설정을 유지한 채 새 분석 기준선을 시작하기 위한
**오프라인 maintenance command**입니다.

## 실행 전

**반드시 bot process를 먼저 종료하세요.**

runtime의 usage/event logger는 `RotatingFileHandler`를 사용합니다. Linux에서 열린 파일을
unlink하면 실행 중인 process가 삭제된 inode에 계속 기록할 수 있으므로, 이 도구는 실행 중인
bot의 logger handle을 hot-rotate하지 않습니다.

먼저 dry-run으로 삭제 범위와 pending memory를 확인합니다.

```bash
python -m hina_bot.tooling.reset_analysis_data --dry-run
# editable/package install 이후에는:
hina-reset-analysis-data --dry-run
```

실제 reset은 명시적으로 `--yes`가 필요합니다.

```bash
python -m hina_bot.tooling.reset_analysis_data --yes
```

경로는 runtime과 같은 환경변수를 사용합니다.

- `DATABASE_PATH`
- `USAGE_LOG_PATH`
- `EVENT_LOG_PATH`

필요하면 `--database`, `--usage-log`, `--event-log`로 덮어쓸 수 있습니다.

## 삭제되는 데이터

SQLite:

- `turns`
- `shared_calls`

Telemetry:

- `USAGE_LOG_PATH`
- usage log의 numeric rotating backups
- 같은 directory의 `discord-usage.jsonl` 및 numeric rotating backups
- `EVENT_LOG_PATH` 및 numeric rotating backups

숫자 suffix가 아닌 임의의 backup 파일은 삭제하지 않습니다.

## 보존되는 데이터

이 command의 목적은 **observability/raw conversation baseline만 초기화**하는 것입니다.
다음 persistent state는 보존합니다.

- `memory_items`
- `summaries`, `shared_summaries`
- `memory_reconciliation_proposals`
- `memory_extraction_cursors`
- `memory_modes`, `chat_log_modes`
- `notes`
- `emoji_registry`
- `runtime_config`
- `instructions`, `runtime_knowledge`
- admin migration state

`memory_items.source_message_ids`처럼 이미 persistent memory에 들어간 provenance는 유지됩니다.
원본 `turns` row는 bounded raw retention이므로 reset 이후에는 해당 source turn을 다시 열 수 없을
수 있습니다.

## Pending memory safety check

삭제 직전에 다음 cursor 뒤에 남아 있는 raw row가 있는지 확인합니다.

- personal legacy summary
- structured-memory extraction
- shared/public summary

하나라도 pending이면 `--yes` 실행을 거부합니다. dry-run 결과에서 종류별 pending turn과
scope 수를 확인할 수 있습니다.

검사는 SQLite write lock을 획득한 뒤 한 번 더 수행하므로, reset plan과 실제 DELETE 사이에
추가된 pending row도 삭제하지 않습니다.

## Observability epoch

성공한 reset마다 SQLite에 `observability_epochs` row를 하나 추가합니다.

```text
id | reset_at
```

이 marker는 raw analysis data와 함께 삭제되지 않습니다. 이후 dashboard/export 분석에서
서로 다른 telemetry schema 세대를 구분할 기준으로 사용할 수 있습니다.

현재 command 자체는 epoch를 telemetry row에 자동 주입하지 않습니다. 우선 reset 시점의
persistent marker만 제공하며, epoch-aware dashboard filtering이 필요해지면 별도 작업으로
확장합니다.
