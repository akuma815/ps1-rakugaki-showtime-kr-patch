# 동적 텍스트 SSOT와 글리프 census

## 현재 판정

`TEXT-SSOT-001`의 324행에 번역문이 모두 입력됐다. 상태값 `translate`는 미검수
`draft` 호환값으로 처리하며, 현재 상태는 `SSOT_TRANSLATION_COMPLETE_UNREVIEWED`다.
물리 원문 좌표 기준으로 `ARA.BIN` 125개, `HAGECHO.BIN` 199개다.

교정된 완성 corpus의 요구 글리프는 **445개**다. 단일 atlas 예산 255개를 190개 초과하며,
완성형 한글 379개만으로도 단일 atlas에 들어갈 수 없다. 따라서 고정 atlas 전략은
탈락했고 255셀 정적 atlas 2뱅크 PoC를 선택했다. 두 뱅크 실사용 용량은 510셀,
현재 corpus 기준 여유는 65셀이다.

## 산출물 계약

번역 작업 파일은 `workspace/text/rakugaki_text_ja_ko.csv`다. UTF-8 BOM CSV이며 다음
필드를 갖는다.

| 필드 | 편집 | 설명 |
| --- | --- | --- |
| `id` | 금지 | 원본 파일과 물리 오프셋 기반 안정 ID |
| `source` | 금지 | `ARA.BIN` 또는 `HAGECHO.BIN` |
| `file_offset_hex` | 금지 | 원본 추출 파일의 문자열 시작 위치 |
| `block_index` | 금지 | HAGECHO 항목의 소유 block, ARA는 빈칸 |
| `original_hex` | 금지 | 원본 문자열 바이트, 종단자 제외 |
| `original_text` | 금지 | 확인된 게임 decoder 분석 결과 |
| `translation` | 허용 | 한국어 번역문 |
| `status` | 허용 | `untranslated`, `translate`, `draft`, `reviewed`, `approved`, `excluded` |
| `notes` | 허용 | 문맥·표기·검수 메모 |

`translate`, `draft`, `reviewed`, `approved` 상태에는 비어 있지 않은 번역문이 반드시
필요하다. `translate`는 외부 번역 작업의 호환값이며 의미상 미검수 `draft`로 취급한다.
번역을 덮어쓰지 않도록 exporter는 기존 CSV나 manifest가 있으면 일반 실행을 거부한다.
검증은 다음 명령으로 수행한다.

```powershell
python workspace/scripts/export_text_ssot.py --check
python workspace/scripts/census_hangul_glyphs.py
```

`workspace/text/rakugaki_text_manifest.json`은 byte length, 원본 위치, HAGECHO block,
분석 보고서의 모든 참조 경로를 보존한다. CSV의 편집 편의와 원본 구조 증거를 분리한
것이며, manifest는 수동 번역 대상이 아니다.

## 원문 검증

- CSV 행: 324
- 안정 ID: 324, 중복 0
- 원문 좌표: 324, 중복 0
- ARA: 125
- HAGECHO: 199
- CSV UTF-8 BOM: PASS
- CSV 불변 필드와 manifest: PASS
- `original_hex`와 실제 추출 파일 바이트: PASS
- 확인된 reader 범위: unknown 0
- 범위 밖: 그래픽에 구운 텍스트와 확인된 builder를 우회하는 미발견 경로

일부 원문은 일반 CP932 해석과 게임 decoder 분석 결과가 다르다. 따라서
`original_text`는 기존 decoder 감사 결과를 사용하고, 무결성은 일반 문자셋 재인코딩이
아니라 `original_hex`와 추출 파일의 실제 바이트 비교로 보장한다.

### 스프레드시트 자동 서식 복구

완료본 검사에서 원문 hex 11칸이 과학적 표기법으로, 전각 숫자 원문 20칸이 반각으로
변한 것을 발견했다. ID·좌표·행 순서는 정상이었다. `TEXT-SSOT-REPAIR-001`은 입력본을
`backups/rakugaki_text_ja_ko.pre_immutable_repair_20260805.csv`로 보존하고 manifest에서
불변 필드 31칸만 복원했다. 번역문·상태·메모 324행은 모두 그대로 유지됐다.

번역 교정본을 다시 저장했을 때 같은 자동 서식 변환 31칸이 재발했다.
`TEXT-SSOT-REPAIR-002`는 수정 직전 파일을
`backups/rakugaki_text_ja_ko.pre_immutable_repair_002.csv`로 별도 보존하고 동일한
불변 필드만 다시 복원했다. 교정된 번역문을 포함한 편집 가능 필드는 모두 보존됐다.

## 글리프 정책과 하드 게이트

`workspace/config/hangul_glyph_policy.json`은 현재 255셀 예산, `FF` blank/fallback,
NFC 정규화, 금지 제어문자와 overflow 정책을 선언한다. 임시 필수 글리프는 숫자 10개와
기본 구두점·기호 17개다. 라틴 문자와 추가 기호는 번역문에 실제 등장할 때 자동으로
예산에 포함된다. 이 필수 집합은 최종 빌드 전 검토가 필요하다.

census는 공백을 `FF` fallback으로 처리해 셀 예산에서 제외하고, 번역문에서 사용하는
모든 비공백 문자를 필수 집합과 합친다.

| 조건 | 판정 |
| --- | --- |
| 번역 0행 | `WAITING_FOR_KOREAN_TRANSLATION` |
| 일부 번역 | `PARTIAL_TRANSLATION_PROVISIONAL_COUNT_ONLY` |
| 전체 번역·제외 완료, 255 이하 | `FIXED_ATLAS_CAPACITY_ELIGIBLE_PENDING_POC` |
| 전체 번역·제외 완료, 255 초과 | `FIXED_ATLAS_OVERFLOW_TWO_BANK_POC_REQUIRED` |
| 잘못된 상태·NFC·제어문자 | `INVALID_TRANSLATION_INPUT` |

255개를 넘으면 고정 atlas 빌드를 실패시키고 2뱅크 hook PoC로 전환한다. 글꼴 예산을
맞추기 위한 의미 훼손이나 강제 축약은 하지 않는다.

## 번역 감사

`TRANSLATION-AUDIT-001`은 번역문을 자동 수정하지 않고 검토 후보만 기록한다.

| 항목 | 행 수 | 판정 |
| --- | ---: | --- |
| 번역문 있음 | 324/324 | PASS |
| 원문과 동일 | 17 | 영문 UI·플레이어 번호·구분선 포함, 사람 검토 |
| 가나 잔존 | 7 | 구분선·혼합 표기 포함, 사람 검토 |
| 앞뒤 공백 | 11 | 화면 정렬일 수 있어 runtime 확인 전 보존 |
| raw decoder placeholder | 0 | PASS |

`TXT-HAG-22634C`는 교정됐으며 raw placeholder와 CJK 잔존이 모두 제거됐다. 교정문은
`배경 스태프／타바타 미카, 호리 소타로, 키하라 유우지`다. 전체 빌드를 막는 감사
항목은 0개이며, 남은 플래그는 중복을 합쳐 32행이다.

## 다음 게이트

번역 감사 blocking이 0이므로 정적 2뱅크 atlas의 decoder·primitive builder hook PoC로
진행할 수 있다. 남은 32개 플래그의 사람 검수와 runtime 재삽입 승인은 별도 QA 게이트로
계속 관리한다.
