# Options 런타임 캡처 세션

## 관측 ID

`HITL-OPTIONS-001`

목적은 `FONT-CONSUME-001`을 판정하기 위해 타이틀의 Options 화면에서 화면,
texture sample, CPU→VRAM upload를 같은 실행 세션으로 확보하는 것이다.

## 고정 기준선

| 항목 | SHA-256 |
| --- | --- |
| 원본 BIN | `99DC6C7F748C4882D6F197A0A0A2EB943AFFC25BB4271DEFF9BB9BF55859AA54` |
| 원본 CUE | `4C6D97218E38FB592E9C426BF2FA086A2110005A82AB062352387594E0AF3F97` |
| DuckStation 설정 | `09D2A47AFC2F773382F0203C157B1A3DEBED3EBD846FAA80B4079B9FD2F5629D` |

## 사용자 조작

1. 캡처 도구가 띄운 게임에서 타이틀의 Options 화면으로 이동한다.
2. Options 화면이 보이는 상태에서 `F10`을 한 번 눌러 스크린샷을 남긴다.
3. DuckStation 창을 닫는다.

도구는 실행 중에만 `DumpCPUToVRAMCopies`와 `DumpTextures`를 켠다. 종료 여부와
관계없이 `settings.ini` 원문 바이트를 복원하며, 이번 실행에서 새로 만들어진
파일만 `work/runtime/options_session_001/`로 이동한다. 기존 덤프와 스크린샷은
이동하거나 덮어쓰지 않는다.

## 판정 분기

| 관측 | 판정과 다음 단계 |
| --- | --- |
| Options 스크린샷과 `#005` texture sample 확보 | palette row·UV·화면 글리프를 연결한다. |
| Options는 보이지만 `#005` sample 없음 | 별도 글꼴/구운 그래픽 경로인지 조사한다. |
| 스크린샷 없음 | 조작 증거가 없으므로 더 짧은 세션으로 반복한다. |

## 결과

`PASS` — 2026-08-04 22:17:46–22:20:14 KST 세션에서 Options 화면을 확보했다.

| 항목 | 결과 |
| --- | --- |
| DuckStation 종료 코드 | `0` |
| 화면 캡처 | 1개 |
| CPU→VRAM dump | 385개 |
| texture dump | 544개 |
| 설정 복원 | PASS, 기준 SHA-256과 일치 |
| `#005` image upload | ID 139와 330, RGB555 전량 일치 |
| `#005` 실제 sample | PASS, texture hash `87256C8FB824543F` |
| 정확히 일치한 palette | 0, 1, 2, 3 |
| 집계 sample 영역 | `(x=16, y=176, w=228, h=80)` |

스크린샷에는 `オプション セレクト`와 Options 항목들이 실제로 표시됐다. 같은
세션의 256×256 full texture 네 장은 정적 `#005` 디코드의 palette 0–3과 각각
65,536/65,536 pixel 완전 일치했다. 따라서 `FONT-CONSUME-001`은 PASS다.

세부 기계 판정은 `work/analysis/options_session_001_font_consume.json`, 전체 세션
manifest는 `work/analysis/options_session_001.json`에 기록했다. 집계 영역은 여러
primitive가 사용한 atlas 범위를 합친 값이므로 개별 글자의 정확한 UV로 해석하지
않는다.
