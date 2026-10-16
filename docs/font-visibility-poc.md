# 한글 글리프 가시성 PoC — FONT-VISIBILITY-001

## 판정

`FONT-VISIBILITY-001`: **PASS**

HAGECHO block 0의 TIM `#005`에서 기존 셀 `D9`(`プ`)와 `C5`(`ッ`)를 각각
16×16 한글 `한`, `글`로 제자리 교체했다. 문자열이나 decoder는 바꾸지 않았으며,
기존 Shift-JIS→셀→`POLY_FT4` 경로가 수정 atlas를 그대로 소비하게 했다.

## 빌드 경계

| 항목 | 값 |
| --- | --- |
| 원본 BIN SHA-256 | `99DC6C7F748C4882D6F197A0A0A2EB943AFFC25BB4271DEFF9BB9BF55859AA54` |
| PoC BIN SHA-256 | `316A0C0D9819D5CF82FE006C50484E80B511B1748746F2CFF2D2917A65C9A54F` |
| PoC CUE SHA-256 | `F3F504360634C3A176C64CDCC0D1170E665E5DE13AA151F492B60D1F9BDDB896` |
| 크기 | 591,880,800 bytes, 원본과 동일 |
| 수정 raw sector | LBA 615, 616, 617 |
| 매체 처리 | MODE2 Form 1 EDC와 P/Q ECC 재계산 |
| Expected Write | 세 raw sector의 원본 SHA-256과 최종 SHA-256을 적용 전에 검증 |

빌드는 원본 BIN 전체를 기준으로 다시 만들며 HAGECHO의 논리 변경을 포함하는 세
sector만 덮어쓴다. 수정하지 않은 sector는 정규화하지 않는다. 빌드 뒤 HAGECHO
extent를 다시 읽어 계획한 바이트와 완전 일치하는지 확인했다.

글리프는 로컬 PoC 입력인 Malgun Gothic `malgun.ttf` SHA-256
`0086C19E81D293A542E7D75564C645FB58070CC850AEFEBF8FA1C397858E510C`에서
15px로 생성했다. 글꼴 파일과 패치된 전체 디스크 이미지는 배포 산출물이 아니다.

## 런타임 증거

| 단계 | 증거 | 판정 |
| --- | --- | --- |
| 새 부팅→타이틀 | `23-07-55.png`, 세로 prompt에서 교체 글리프 표시 | PASS |
| 타이틀→Mode Select | `23-08-13.png`, 메뉴 정상 진입과 복수 palette 표시 | PASS |
| Mode Select→타이틀 | 게임에 직접 복귀 경로가 없음 | 해당 없음 |

초기 캡처 요청은 세 번째 단계까지 요구했으나 실제 게임 흐름과 맞지 않았다. 사용자가
두 유효 화면을 캡처한 뒤 요청을 `타이틀 → Mode Select`로 수정했다. 강제 종료로
원시 세션 JSON은 `INCOMPLETE`지만, 두 캡처의 해시와 빌드 기준선은 보존됐고 별도
review JSON에서 이 게임에 맞는 조건으로 PASS 판정했다.

공유 셀 교체이므로 `プ`와 `ッ`이 쓰인 다른 메뉴 라벨에도 `한`, `글`이 나타난다.
이는 소비 경로와 여러 palette 도달을 보여 주는 PoC의 의도된 부작용이며 최종 문자
배정으로 승인한 것은 아니다.

## 증명 범위와 다음 게이트

이번 결과로 같은 TIM `#005`를 쓰는 확인된 title/menu 경로에서 16×16 한글 픽셀이
저장→섹터→HAGECHO 적재→셀 선택→VRAM→화면까지 도달함을 증명했다. 전체 한글
레퍼토리, 안전한 코드 공간, 전체 텍스트 모집단, 레이아웃과 다른 렌더 경로는 아직
증명하지 않았다.

다음 단계는 원본 코드 사용 전수와 decoder 예약 영역을 조사해 안전한 한글 코드
배정 방식을 결정하고, 텍스트 모집단 추출기의 경계를 확정하는 것이다.
