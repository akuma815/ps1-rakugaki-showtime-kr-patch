# 텍스트 모집단 1차 인벤토리

## 판정

현재 통합 상태는 `ATLAS_TEXT_READER_AUDIT_COMPLETE`다. 타이틀
전용 문자열 1개와 범용 renderer를 직접 부르는 HAGECHO 18곳·ARA 19곳의 형태는
모두 분류했고, 단일 문자열 wrapper의 caller 16곳도 형태를 전부 판별했다. 이 중
14곳은 즉시 정적 source가 해결됐고, 나머지 2곳도 런타임 생산자를 정적 테이블까지
역추적했다. 단일 문자열 wrapper 16곳은 전부 해결됐으며 열린 간접 분모는 포인터
배열 renderer 27곳도 모두 정적 배열 source까지 해결했다. 전달 소비자 분모 43곳은
`unknown=0`이고, 한 글자·별도 reader 감사도 완료했다. 확인된 decoder와 atlas
primitive builder를 통과하는 동적 텍스트 경로의 unknown은 0이다. 그래픽에 구운
텍스트와 확인된 builder를 우회하는 primitive 경로는 별도 범위이므로 자동 탐색
후보를 번역 원문 전수로 취급하지 않는다.

이 단계의 재현 가능한 보고서는
`work/analysis/text_population_001.json`,
`work/analysis/text_callsites_block1_001.json`,
`work/analysis/text_callsites_block10_001.json`이다. 생성기는 각각
`workspace/scripts/analyze_text_population.py`,
`workspace/scripts/analyze_text_callsites.py`,
`workspace/scripts/analyze_text_callsites_block10.py`다.
ARA 직접 호출과 간접 소비자 분모는 `work/analysis/text_callsites_ara_001.json`,
생성기는 `workspace/scripts/analyze_text_callsites_ara.py`가 소유한다. wrapper caller
분류는 `work/analysis/text_wrapper_callers_001.json`, 생성기는
`workspace/scripts/analyze_text_wrapper_callers.py`가 소유한다.
두 런타임 생산자 해소는 `work/analysis/text_wrapper_producers_001.json`, 생성기는
`workspace/scripts/analyze_text_wrapper_producers.py`가 소유한다.
포인터 배열 caller 분류는 `work/analysis/text_array_callers_001.json`, 생성기는
`workspace/scripts/analyze_text_array_callers.py`가 소유한다.
reader 누락 감사는 `work/analysis/text_reader_audit_001.json`, 생성기는
`workspace/scripts/analyze_text_reader_audit.py`가 소유한다.

## 확인된 문자열 경로

- 범용 문자열 renderer: runtime `0x80043348`
- `ARA.BIN`의 범용 renderer 호출: 19곳
- `HAGECHO.BIN`의 범용 renderer 호출: 18곳
- 해결된 HAGECHO 범용 호출: 18/18곳
- 분류된 ARA 직접 범용 호출: 19/19곳
- 열린 전달 소비자: 0곳
- 확인된 decoder·atlas builder reader 경로 unknown: 0곳
- 타이틀 전용 builder: runtime `0x80144CFC`
- 소비가 확인된 타이틀 문자열: HAGECHO `0x1A064`, 11자
- 원문: `プッシュスタートボタン`
- raw: `83 76 83 62 83 56 83 85 83 58 83 5E 81 5B 83 67 83 7B 83 5E 83 93`

범용 renderer `0x80043348`은 문자열 포인터의 lead byte가 0이 될 때까지
2바이트 단위로 전진하며 한 글자 renderer `0x80043298`을 호출한다. 따라서 현재
확인된 기본 저장 형식은 NUL 종단 2바이트 Shift-JIS/CP932다.

## HAGECHO 호출처 분류

block 1은 앞 16바이트가 wrapper이고 payload의 runtime base가 `0x8012BCE0`이다.
따라서 주소 환산식은 다음과 같다.

```text
file offset = block start + 0x10 + (runtime address - 0x8012BCE0)
```

wrapper를 고려하지 않은 이전 block 공통 환산의 runtime 호출 주소는 block 1과
block 10에서 `0x10` 높았다. 파일 호출 offset과 wrapper가 없는 block 0의 타이틀
주소는 영향을 받지 않는다.

### Block 1

| 구조 | 호출 수 | 정적 비어 있지 않은 원문 |
| --- | ---: | ---: |
| 전각 숫자 10개, 4바이트 고정 슬롯 | 2 | 10 |
| 캐릭터명 20레코드×2개 11바이트 슬롯 | 4 | 28 |
| 아이템명 8레코드, stride `0x38`, 문자열 `+0x14` | 1 | 8 |
| 조작명 8개 포인터 테이블 | 1 | 8 |
| 직접·조건·분기 UI 리터럴 | 7 | 12 |
| 합계 | 15 | 66 |

15개 호출 모두 `resolved_static_source`이며 `unknown=0`이다. 캐릭터명 레코드는
runtime `0x801608A4`, stride 30, 문자열 offset `+8/+19`이고, 아이템명 레코드는
runtime `0x80160FB8`, stride `0x38`, 문자열 offset `+0x14`다. 원시 레코드와
빈 슬롯도 보고서에 보존했다.

### Block 10

block 10의 호출 3개도 wrapper `+0x10`을 적용한다. 호출 인자는 HAGECHO가 아닌
ARA의 정적 리터럴을 직접 가리킨다.

| ARA offset | 원문 |
| ---: | --- |
| `0xD01A4` | `リスタート？` |
| `0xD01B4` | `ＹＥＳ` |
| `0xD01BC` | `ＮＯ` |

따라서 HAGECHO의 직접 범용 호출 18/18곳은 분류와 정적 source 연결을 완료했다. 현재
소비가 확인된 정적 source 항목은 타이틀 1개, block 1의 66개, block 10의 3개로
70개였다. 이후 ARA 직접 호출 조사에서 중복 source를 제외한 전체 확인 좌표는
96개로 늘었다. 이는 전체 게임 모집단이 아니라 현재 해결된 source 좌표 수다.

## ARA 직접 호출과 간접 소비자 확장

ARA runtime base는 `0x7FFD6000`이며 decoder `0x800430DC`가 ARA `0x6D0DC`에
대응하는 것으로 검산했다.

| 직접 호출 분류 | 호출 수 |
| --- | ---: |
| 정적 source 해결 | 17 |
| 전달 API | 2 |
| 형태 미확정 | 0 |
| 합계 | 19 |

확정된 ARA 정적 source는 전각 숫자 10개와 리터럴 19개로 총 29개다. 여기에는
설정 통계 라벨, restart 선택지, 도발 문구, 튜토리얼 문구가 포함된다. block 10에서
이미 확인한 restart 리터럴 3개는 같은 ARA 좌표이므로 통합 좌표 수에서 중복
계수하지 않는다.

직접 호출 `0x800433DC`는 `a0`을 `0x800433D0` wrapper로 전달하고, 이 wrapper를
부르는 곳은 ARA 4곳과 HAGECHO 12곳, 합계 16곳이다. 직접 호출 `0x80040D8C`는
호출자가 준 문자열 포인터 배열·개수·Y 좌표를 순회하는 `0x80040D34` API이며
ARA caller가 27곳이다.

wrapper caller 분류 결과를 반영한 열린 분모는 다음과 같다.

| 간접 소비자 | caller | resolved | unknown |
| --- | ---: | ---: | ---: |
| 단일 문자열 wrapper `0x800433D0` | 16 | 16 | 0 |
| 포인터 배열 renderer `0x80040D34` | 27 | 27 | 0 |
| 합계 | 43 | 43 | 0 |

직접 `jal 0x80043348` 검색으로 얻은 37개는 이제 전체 소비자 수가 아니라 최초
직접 호출 분모로만 해석한다. 전달 API caller의 source가 해결되기 전에는 텍스트
모집단을 닫지 않는다.

## 단일 문자열 wrapper caller 분류

16곳은 모두 호출 형태를 판별해 `unknown_callsite_shape=0`이다. ARA의 4곳 중
3곳은 `１Ｐ`–`４Ｐ`의 5바이트 고정 슬롯과 `つづける`, `やめる` 정적 리터럴이고,
나머지 1곳은 간접 callback 함수가 받은 구조체 `+0x24` 필드를 사용한다. 플레이어 슬롯은 실제 유효 인덱스가
0–3이며 인접 `0x800A4880`은 다섯 번째 문자열이 아니라 palette 데이터다.

HAGECHO의 12곳 중 block 1의 5곳은 이미 계수한 전각 숫자 테이블을 재사용한다.
block 4는 완료 메시지와 난이도별 clear 문구 등 정적 좌표 8개를 추가했고, 고정
runtime buffer `0x80148270`을 받는 1곳이 있다. block 6은
18행×3열 포인터 테이블의 54개 문자열과 `つかれたね` 1개를 해결했다.

이번 분류에서 추가된 고유 정적 좌표는 ARA 6개, block 4의 8개, block 6의
55개로 총 69개다. block 1 숫자 10개는 기존 좌표와 중복이다. 따라서 확인된
고유 정적 source 좌표는 96개에서 165개로 늘었다. 이는 여전히 전체 게임 모집단이
아니라 현재 실행 소비자에 연결된 좌표 수다.

### 런타임 생산자 2곳 해소

block 4의 `0x80148270`은 비어 있는 문자열이 아니라 `0x80147A6C`–`0x80147A84`에서
조립되는 버퍼다. 17개 캐릭터명 포인터 테이블 중 하나를 `strcpy`하고 고정 suffix
`　ゲット！！`를 `strcat`한다. 테이블 뒤의 18번째 word는 NULL sentinel이다.

ARA callback `0x8008034C`가 읽는 구조체 `+0x24`는 생성자 `0x80080534`가 객체
내부 `+0x44` 버퍼로 설정한다. 상태 머신은 `0x800A7064`의 이름·팀명 22개 또는
`0x800A70BC`의 `ＷＩＮ♪/ＬＯＳＥ/ＤＲＡＷ` 3개에서 현재 문자 2바이트를 복사하고
NUL을 붙인다. 따라서 이 callsite는 동적 외부 문자열이 아니라 정적 테이블 원문을
한 글자씩 움직여 표시하는 경로다.

생산자 해소로 새 정적 좌표 43개(block 4 이름 17, suffix 1, ARA 이름·팀명 22,
결과 3)를 추가했다. 확인된 고유 정적 source 좌표는 165개에서 208개로 늘었고,
wrapper 분모는 `resolved=16`, `unknown=0`으로 닫혔다.

## 포인터 배열 renderer caller 분류

`0x80040D34`는 `a0`의 little-endian 32비트 포인터 배열을 최대 `a1`개 순회한다.
이번 27개 caller의 최대 개수는 모두 3이며, 슬롯이 NULL이면 남은 개수와 무관하게
즉시 종료한다. 각 비NULL 문자열은 Y 좌표를 16px씩 내리며 범용 renderer로 보낸다.

정적 배열 카탈로그는 ARA `0x800A4CDC`–`0x800A4E5C`의 32개 레코드다. 레코드
stride는 12바이트이고 포인터 3칸을 그대로 보존한다.

| 항목 | 수량 |
| --- | ---: |
| 배열 | 32 |
| 전체 포인터 슬롯 | 96 |
| 비NULL 슬롯 | 54 |
| NULL 슬롯 | 42 |
| 고유 문자열 좌표 | 41 |
| 중복 포인터 참조 | 13 |
| 문자열 내부 진입 | 0 |

직접 배열뿐 아니라 helper가 전달하는 분기 선택 배열과 상태 구조체 `+0x20/+0x24`에
저장되는 배열도 생산 지점까지 역추적했다. 32개 배열은 모두 하나 이상의 실제
consumer 경로에 연결된다. 중복 포인터는 합치지 않고 각 원본 슬롯의 순서와
정체성을 보고서에 보존했다. 이번 41개를 더해 확인된 고유 정적 source 좌표는
208개에서 249개로 늘었다.

## 한 글자·별도 reader 감사

확인된 code→cell decoder `0x800430DC`의 직접 caller는 세 경로뿐이다. 범용 한 글자
renderer `0x80043298`, 단일 글리프 renderer `0x8003CE18`, 타이틀 builder
`0x80144CFC`다. atlas primitive builder `0x80042F28`의 직접 caller 네 곳도 이
세 경로 안에 모두 포함된다. decoder·builder·범용 한 글자 renderer 주소를 저장한
raw function-pointer word는 ARA/HAGECHO에서 발견되지 않았다.

단일 글리프 renderer의 ARA 경로는 기존 이름·팀명 22개를 재사용하고, 별도 사망
문구 포인터 테이블 24개를 문자 단위로 표시한다. HAGECHO block 6 경로는 크레딧
문자열을 한 글자씩 객체 내부 버퍼로 복사한다. 크레딧은 두 variant의 6페이지
outer table이며, 각 page는 8바이트 `(control, string pointer)` 레코드와
`(0xFF, 0)` terminator를 사용한다. 두 variant가 공유하는 페이지를 중복 제거한
고유 문자열 좌표는 50개다. 별도 고정 저작권 문구
`トレジャー／エニックス　１９９９`도 같은 단일 글리프 경로로 소비된다.

타이틀 controller의 builder 호출 6회는 모두 기존 고정 source
`プッシュスタートボタン`을 반복 사용하므로 신규 좌표가 아니다. 이번 감사에서
사망 문구 24개, 크레딧 50개, 저작권 문구 1개, 총 75개를 추가해 확인된 고유 정적
source 좌표는 249개에서 324개로 늘었다.

이로써 확인된 decoder·atlas builder 기반 동적 텍스트 범위는 unknown 0으로
닫혔다. 이는 그래픽에 구운 텍스트까지 전수 조사했다는 뜻은 아니다.

## 후보 탐색 결과와 한계

두 문자 이상이며 decoder가 인식하는 2바이트 코드로만 구성되고 다음 lead byte가
0인 최대 구간을 두 parity에서 찾았다.

| 구분 | 수량 |
| --- | ---: |
| 전체 후보 | 324 |
| `HAGECHO.BIN` 후보 | 208 |
| `ARA.BIN` 후보 | 116 |
| wrapper 보정 후 정적 overlay 포인터가 연결된 후보 | 19 |
| 정적 포인터→후보 연결 | 20 |
| 초기 scanner 자체에서 소비가 확정된 항목 | 1 |
| 후보 전체에서 관찰된 고유 코드 | 183 |

이 탐색은 실행 경로가 아닌 바이트 패턴 탐색이다. 데이터 테이블이나 우연히 조건을
만족하는 바이트가 포함될 수 있고, 한 글자 문자열·비-NUL 형식·토큰 문자열은 빠질
수 있다. 그러므로 324는 모집단 크기도, 번역 대상 수량도 아니다.

정적 포인터 연결 수가 6에서 19로 늘어난 것은 wrapped module의 payload `+0x10`
환산을 바로잡은 결과다. 그래도 포인터 모양만으로 소비를 확정하지 않는다. block 1과
10의 확정 항목은 별도 callsite 보고서의 `jal`·`a0` 데이터 흐름과 레코드 경계를
근거로 승격했다.

## 코드 공간 판정

| 항목 | 값 |
| --- | ---: |
| decoder 유효 입력 코드 | 257 |
| decoder가 도달하는 고유 atlas 셀 | 254 |
| 점유된 atlas 셀 `0x00`–`0xFE` | 255 |
| 현재 decoder가 도달하지 못하는 점유 셀 | `0x30` |
| alias 셀 | `F6`, `F7`, `F8` |
| 안전하다고 입증된 도달 가능 빈 셀 | 0 |
| 실제 소비가 확인된 decoder fallback 코드 | `8140` |

결론은 `NO_SAFE_HANGUL_CODE_ALLOCATION_YET`다. 후보 문자열에서 한 번도 보이지
않는 코드라도 다른 소비자, 한 글자 문자열 또는 제어 용도로 쓰일 수 있다.
decoder가 현재 거부하는 코드 역시 code hook 없이 곧바로 재사용할 수 없다.
실제로 아이템명에는 CP932 전각 공백 `8140`이 들어 있고 decoder의 `0xFF` 빈
fallback 셀로 표시된다. 따라서 atlas `0xFF`도 안전한 미사용 셀이 아니다.
따라서 기존 셀을 덮어쓰는 방식은 PoC 전용이며 최종 배정안이 아니다.

## 다음 게이트

1. 직접 코드 재사용은 배제하고 atlas 확장 또는 decoder/font hook 중 한글 전략을 고른다.
2. 그래픽에 구운 텍스트는 별도 카탈로그 범위로 계속 관리한다.
3. 전략 선택 뒤 대표 메뉴 문자열 1단위 재삽입 PoC를 만든다.

문자열 변경·번역 CSV 생성·디스크 재삽입은 이 게이트가 닫힌 뒤 진행한다.
