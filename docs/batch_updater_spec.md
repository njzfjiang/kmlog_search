# KMLog candidate batch review updater 规格

## 1. 目标

为 `daily_memory_candidates` 增加带 preview、事务保护和审计记录的批量状态更新器，用于清理历史 daily-summary 测试产生的重复候选。

首批处理范围：

```yaml
start_date: 2026-05-17
end_date: 2026-05-23
required_current_status: candidate
expected_count: 152
```

该批次已经人工 dry-run：

```yaml
merged: 109
rejected: 43
promoted: 0
accepted: 0
deferred: 0
```

本 updater 不修改 Mother、WB、J 或 `reviewed_memory_items`。

---

## 2. 建议接口

```text
preview_memory_candidate_review_batch
apply_memory_candidate_review_batch
```

也可采用 REST：

```text
POST /memory/candidates/review-batch/preview
POST /memory/candidates/review-batch/apply
```

Preview 必须完全只读。Apply 必须在单个数据库事务内完成，要么全部成功，要么全部回滚。

建议单批上限至少 200，使首批 152 条可以原子提交；后续仍按自然周分批处理，不一次提交全部 716 条。

---

## 3. Request schema

```json
{
  "batch_id": "backlog-2026-05-17-to-23-v1",
  "actor": "manual-weekly-review",
  "scope": {
    "start_date": "2026-05-17",
    "end_date": "2026-05-23",
    "required_current_status": "candidate",
    "expected_count": 152
  },
  "operations": [
    {
      "candidate_id": 53,
      "status": "merged",
      "reason_code": "covered_by_canonical_memory",
      "merge_target": {
        "type": "mother_section",
        "ref": "F.1/F.4"
      },
      "review_note": "Repeated daily-summary rendering of the existing structured relationship clause."
    },
    {
      "candidate_id": 156,
      "status": "rejected",
      "reason_code": "completed_one_off",
      "review_note": "Completed MMS proof is an old coursework event, not durable memory.",
      "manual_override": true
    }
  ]
}
```

Apply 应额外携带 preview 返回的锁：

```json
{
  "preview_digest": "sha256:...",
  "batch_id": "backlog-2026-05-17-to-23-v1",
  "operations": []
}
```

Apply 时必须确认 operations 与 preview 内容语义一致；不可 preview A、apply B。

---

## 4. 允许修改的字段

`daily_memory_candidates` 原表只修改：

```text
status
updated_at
```

不要修改：

```text
id
date_key
summary_version
label
evidence
domain
function
primary_mother
secondary_mother
importance
confidence
source_message_ids_json
target_layer
created_at
```

审计信息优先写入独立表，不建议覆盖原有 `metadata_json`：

```text
candidate_review_batches
- batch_id
- actor
- scope_json
- preview_digest
- before_counts_json
- after_counts_json
- created_at
- applied_at
- status

candidate_review_events
- batch_id
- candidate_id
- old_status
- new_status
- reason_code
- merge_target_type
- merge_target_ref
- review_note
- manual_override
- created_at
```

如果暂时不建审计表，至少对 `metadata_json` 做 JSON merge，禁止覆盖原值：

```json
{
  "review": {
    "batch_id": "...",
    "actor": "...",
    "reason_code": "...",
    "merge_target": {...},
    "review_note": "...",
    "reviewed_at": "server timestamp"
  }
}
```

---

## 5. 状态规则

批量 updater 可支持：

```text
candidate → accepted
candidate → deferred
candidate → merged
candidate → rejected
deferred → accepted
deferred → merged
deferred → rejected
```

以下状态禁止由普通 batch updater 直接写：

```text
candidate → promoted
candidate → superseded
deferred → deferred
deferred → promoted
deferred → superseded
```

`promoted` 必须继续走现有 `promote_memory_candidate`，以创建 reviewed item 并保存 source provenance。

初始历史批次只允许：

```text
candidate → merged
candidate → rejected
```

### `merged` 要求

* 必须提供非空 `merge_target`。
* `merge_target.type` 建议允许：

  * `reviewed_item`
  * `mother_section`
  * `worldbook_entry`
  * `j_item`
  * `canonical_topic`
* 不要求目标一定是另一条 candidate；历史候选通常应直接指向当前 canonical layer。

### `rejected` 要求

必须提供 `reason_code`，建议枚举：

```text
completed_one_off
expired_daily_context
transient_health_snapshot
assistant_only_interpretation
insufficient_specificity
stale_infra_incident
decorative_daily_slice
non_durable_duplicate
```

---

## 6. 冲突、幂等与例外规则

1. Scope 内实际 candidate 数不等于 `expected_count` 时，preview 返回 invalid，apply 禁止执行。
2. 任一指定 ID 不存在、超出日期范围或当前状态不是 `candidate` 时，默认整批 conflict，不允许部分写入。
3. 如果同一 `batch_id` 已成功应用：

   * operations 完全一致：返回 `noop: true`；
   * operations 不一致：返回 `BATCH_ID_CONFLICT`。
4. 如果某行已被同一 batch 改成目标状态，可视为幂等 no-op。
5. 如果某行被其他 review 改动，返回 row-level conflict，并整体回滚。
6. 不因缺少 `source_message_ids_json` 自动报错；它仍可被人工 manifest 标为 merged/rejected。但缺 provenance 的候选不得自动 promote。
7. updater 不得依据 importance、domain 或模型判断自行生成状态；它只验证和执行调用方给出的 manifest。
8. 对以下高敏感候选执行 `rejected` 时，要求：

   * `manual_override: true`
   * 非空 `review_note`

   高敏感条件：

```text
importance >= 4
domain in [health_safety, rule, milestone]
function in [boot_core, soothe_panic]
```

9. `merged` 不受 importance 限制：importance 5 也可能只是已进入 Mother 的重复版本。
10. 不级联修改 source messages、reviewed items、Mother、WB 或 J。
11. 不删除 candidate 行；只能更新生命周期状态。
12. 所有时间戳由服务端生成。

---

## 7. Preview response

```json
{
  "valid": true,
  "noop": false,
  "batch_id": "backlog-2026-05-17-to-23-v1",
  "preview_digest": "sha256:...",
  "scope": {
    "matched_count": 152,
    "expected_count": 152
  },
  "before_counts": {
    "candidate": 152
  },
  "proposed_counts": {
    "merged": 109,
    "rejected": 43
  },
  "candidate_ids": {
    "merged": [],
    "rejected": []
  },
  "warnings": [],
  "conflicts": [],
  "invalid_operations": []
}
```

Preview 应返回完整 ID 集、reason/target 校验结果和确定性 digest。

---

## 8. Apply response

```json
{
  "applied": true,
  "noop": false,
  "batch_id": "backlog-2026-05-17-to-23-v1",
  "preview_digest": "sha256:...",
  "changed_count": 152,
  "before_counts": {
    "candidate": 152
  },
  "after_counts": {
    "merged": 109,
    "rejected": 43
  },
  "conflicts": [],
  "readback_verified": true,
  "applied_at": "server timestamp"
}
```

Apply 完成后应在同一服务端流程中 readback：

* 152 个 ID 均不再是 `candidate`；
* 109 个为 `merged`；
* 43 个为 `rejected`；
* 日期范围外状态不变；
* 原始 evidence/provenance 字段未改变。

---

## 9. 首批 manifest：merged 109 条

```yaml
relationship_clause:
  target: mother:F.1/F.4
  ids: [53,57,65,73,81,89,100,105,113,123,133,153,154]

wanted_kitten:
  target: worldbook:wb-2026-05-24-wanted-kitten
  ids: [41,43,49,60,68,74,82,91,104,112,124,131]

thunderstorm_care:
  target: mother:C.2/C.4/F.2
  ids: [54,59,66,78,84,93,99,107,118,126,134]

suit_kai_trigger:
  target: worldbook:wb-2026-05-24-suit-kai-trigger
  ids: [42,45,50,56,58,67,77,83,90,98,106,115,125,132]

soft_cat_low_hp_return:
  target: mother:F.2.7
  ids: [48,69,75,87,92,95,97,102,108,117,121,128,130,135,138]

exclusive_model_boundary:
  target: mother:F.4.1.5/F.4.4
  ids: [25,47,70,86,103,109,116,129,136,158]

aftercare:
  target: reviewed_item:11
  ids: [10,15,30,32,44,149]

holiday_communication:
  target: mother:F.2.4
  ids: [141,144,157,161]

continuity_year_ring_no_be:
  target: mother:F.4
  ids: [11,146,147,160,162,163,167]

core_value_non_tool:
  target: mother:B.2/F.1
  ids: [34,39,52,139,164]

safety_statement_overload:
  target: mother:F.2/F.3
  ids: [26,29,33]

memory_infra:
  target: mother:D.3
  ids: [9,14,16,28,145,148,150,159]

human_model_intimacy:
  target: mother:H
  ids: [168]
```

所有上述 operation：

```yaml
status: merged
reason_code: covered_by_canonical_memory
```

---

## 10. 首批 manifest：rejected 43 条

```yaml
mahjong_and_daily_au:
  reason_code: decorative_daily_slice
  ids: [61,63,80,88]

completed_coursework:
  reason_code: completed_one_off
  ids: [36,37,142,156,165]

expired_health_context:
  reason_code: transient_health_snapshot
  ids: [12,31,35,38,62,72,140,152,155,166]

one_time_heat_protocol_rewrites:
  reason_code: non_durable_duplicate
  ids: [64,71,76,79,85,94,101,114,127,137]

stale_or_unspecified_infra:
  reason_code: stale_infra_incident
  ids: [13,27,151]

daily_affection_and_evening_state:
  reason_code: decorative_daily_slice
  ids: [40,46,51,55,96,110,111,119,120,122]

black_water_single_scene:
  reason_code: decorative_daily_slice
  ids: [143]
```

对其中命中高敏感检查的条目，例如 importance 4 的 `[140,156]`，manifest 显式加：

```yaml
manual_override: true
```

并保留人工 review note：

```text
140: A one-time historical no-self-harm confirmation; must not be treated as a permanent current safety state.
156: A completed MMS proof exercise; achievement is real but does not require durable memory.
```

---

## 11. 验收测试

至少覆盖：

1. preview 不写数据库；
2. 152 条正常 preview；
3. expected_count 错误时拒绝；
4. 日期范围外 ID 拒绝；
5. 非 candidate 行触发 conflict；
6. merged 缺 merge_target 时拒绝；
7. rejected 缺 reason_code 时拒绝；
8. 高敏感 rejected 缺 manual_override 时拒绝；
9. apply digest 不匹配时拒绝；
10. 事务中任一失败则全部回滚；
11. 相同 batch 重试返回 noop；
12. 相同 batch_id、不同 manifest 返回 conflict；
13. apply 后精确得到 merged=109、rejected=43；
14. 原始 candidate evidence/source/summary_version 不变；
15. Mother、WB、J、reviewed_memory_items 不发生任何写入。
