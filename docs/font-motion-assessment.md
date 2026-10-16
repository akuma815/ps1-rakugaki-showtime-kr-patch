# 움직이는 글자 구현 검토 — FONT-MOTION-001

## 결론

타이틀 테두리의 한글에 별도 “춤추는 글자” 루틴을 새로 구현할 필요는 없다.
현재 확인한 완성형 16×16 한글 셀을 기존 문자열 builder에 통과시키면 원본의 이동을
자동으로 상속한다. 판정은 **기존 구현 재사용 가능**이며, 실제 한글 연속 동영상
확인은 최종 대표 문자열 E2E 때 수행한다.

## 제공 영상

| 항목 | 값 |
| --- | --- |
| 원본 | `G:\Downloads\ラクガキショータイム 2026-08-04-23-12-33.mp4` |
| SHA-256 | `92BFFE6FB43A14174375371EE79508A63F7322A67A5DA6B739B546711411E181` |
| 영상 | H.264, 640×480, 약 59.82 fps |
| 길이 | 4.89초, 293 frames |
| 표본 | 전 구간에서 균등 추출한 12 frames |

표본에서 중앙 장면은 고정된 반면 좌우 테두리 문자열 조각은 시간에 따라 서로 다른
세로 위치를 차지한다. 영상은 원본 움직임의 기준이며, 한글 동작 자체를 직접 촬영한
증거로 확대하지 않는다.

## 코드 구조

| 역할 | runtime | HAGECHO offset | 동작 |
| --- | --- | --- | --- |
| 문자열 builder | `0x80144CFC` | `0x1901C` | 문자마다 decoder→16×16 `POLY_FT4`, 선택 축 16px 전진 |
| motion controller | `0x80144E38` | `0x19158` | 매 update마다 기준 좌표와 순환 offset 갱신 |
| scroll state | `0x80145CC0`, `0x80145CC4` | block 0 data | 양쪽 방향의 순환 위치 |
| bounce counter | `0x80145CC8` | block 0 data | 제곱값을 양쪽 edge 기준 위치에 반대 방향으로 적용 |

controller는 문자열 builder를 update마다 6회 호출한다. 두 edge 방향 각각에 대해
`scroll-256`, `scroll`, `scroll+256` 세 복사본을 그려 화면 경계를 순환한다. 방향
인수는 글자별 진행을 `+16` 또는 `-16` pixel로 만들고, 반대 edge에서는 UV 정점
순서를 바꾼다.

중요한 점은 이 계산이 Shift-JIS decoder와 글리프 셀 선택 **뒤의 quad 위치·방향**에
적용된다는 것이다. bitmap 내용이 일본어인지 한글인지는 motion controller가 보지
않는다. 따라서 `FONT-VISIBILITY-001`처럼 한글이 기존 셀을 통해 같은
`POLY_FT4`로 생성되면 위치 변화도 그대로 받는다.

## 구현 조건과 위험

- 한 음절이 기존과 같이 하나의 16×16 `POLY_FT4`여야 한다.
- title 경로의 글자 진행 폭 16px와 양 방향 모드를 유지해야 한다.
- 반복 간격이 256px이므로 title border 문자열은 최대 16셀 안에서 검증한다.
- 조합형 한글처럼 한 글자를 여러 primitive로 만들면 모든 구성 primitive에 같은
  이동과 방향 변환을 적용하는 추가 작업이 필요하다.
- 다른 메뉴·대사 화면이 같은 controller를 쓴다는 증거는 아니므로, 움직임이 필요한
  다른 경로는 별도로 확인한다.

현재 계획인 완성형 1코드→1셀 방식에서는 위 첫 세 조건을 그대로 만족시킬 수 있다.
안전한 코드 배정이 끝난 뒤 16셀 이하 대표 한국어 prompt를 넣고 1회 연속 영상으로
순환, 양쪽 방향, clipping·겹침 부재를 최종 확인한다.

## 증거

- `work/analysis/font_motion_001_video.json`
- `work/analysis/font_motion_001_code.json`
- `work/video/font_motion_001/contact_sheet.png`
- `work/analysis/font_writer_analysis.json`
- `docs/font-visibility-poc.md`
