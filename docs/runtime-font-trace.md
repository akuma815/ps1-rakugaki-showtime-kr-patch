# Rakugaki Showtime 런타임 글꼴 추적 — 2026-08-04

## 결론

`HAGECHO.BIN` block 0의 TIM `#005`는 **실제 런타임 글꼴 atlas**로 확인됐다.
이미지 payload 전체와 8개 CLUT row 전체가 CPU→VRAM dump와 PS1 RGB555 단위로
일치했고, Options 화면 세션에서는 palette 0–3의 256×256 texture sample도 정적
디코드와 각각 65,536 pixel 전량 일치했다.

저장 문자열의 문자 코드→16×16 셀→개별 primitive 경로도 확인됐다. 문자열은
2바이트 Shift-JIS이며 `0x800430DC` decoder를 거쳐 1바이트 셀 번호가 된다.
다음 단계는 이 경로를 그대로 사용하는 두 글리프 가시성 PoC다.

## 실행 기준선

| 항목 | 값 |
| --- | --- |
| DuckStation | `0.1-11636-g7111f4c12` |
| SCM timestamp | `2026-08-04T00:10:50+10:00` |
| EXE SHA-256 | `98F0CE45C21F947CD6EB3741A83409589EAF0D42A5A1BB66CB28B807CEAD83B3` |
| 설정 SHA-256 | `02037CC124559E370BDF7DEA165806C272EE97B835FCD61BFA732567219E9C9D` |
| BIOS | SCPH-5500, v3.0 09-09-96 J |
| BIOS SHA-256 | `9C0421858E217805F4ABE18698AFEA8D5AA36FF0727EB8484944E00EB5E7EADB` |
| GDB | `127.0.0.1:2345`, 연결 시 `S02`, 실행 중 중단 `S05` |
| Renderer | Vulkan, runtime adapter AMD Radeon RX 9070 XT |
| 원본 | 프로젝트 BIN/CUE SHA-256과 에뮬레이터 ROM 사본이 각각 일치 |

설정에는 9× internal resolution, 8× MSAA, PGXP, MMPXEnhanced filter와 두 개의
post-processing shader가 켜져 있다. configured adapter는 RX 7800 XT지만 실행
로그에서는 찾지 못해 첫 장치인 RX 9070 XT를 선택했다. 향후 픽셀 QA 기준은
별도의 clean software/native 프로필로 분리해야 한다.

## 글꼴 구조

| 항목 | 값 |
| --- | --- |
| source | `HAGECHO.BIN`, block 0 |
| TIM catalog | `#005` |
| container offset | `0x34A98` |
| TIM size | 33,056 bytes |
| image | 256×256 4bpp, VRAM word size 64×256 |
| grid hypothesis | 16 columns × 16 rows, 16×16 pixel cell |
| occupied cells | `0x00`–`0xFE` 255개 |
| empty cell | `0xFF` 1개 |
| CLUT | 16×8 words, 8 palettes, 128 colors |

셀 순서는 숫자, Latin, 히라가나, 가타카나와 기호의 규칙적인 배열이다.
decoder 출력 byte는 셀 index와 같다. builder는 하위 nibble로 U, 상위 nibble로
V를 만든다. 다만 저장 문자열에는 셀 번호가 아니라 Shift-JIS 2바이트가 들어간다.

## 저장→RAM→VRAM 관측

### RAM 스냅샷

DuckStation GDB 서버로 실행 3초, 8초, 15초의 2MB RAM을 읽었다. 세 시점 모두
TIM 전체 33,056 bytes, image payload 32,768 bytes, CLUT payload 256 bytes의
완전 일치는 0건이었다. 이는 업로드 자체를 기각하지 않으며, 임시 적재 버퍼가
전송 뒤 재사용된다는 가설과 양립한다.

### CPU→VRAM 업로드

20초 실행에서 146개 CPU→VRAM dump를 얻었다. 121개 TIM의 image와 CLUT를
RGB555로 정규화해 비교한 결과 block 0 TIM 9개의 image/CLUT가 런타임 dump와
연결됐다.

`#005`의 확정 대응:

| part | upload ID | 크기(word) | RGB555 판정 | TIM destination |
| --- | ---: | ---: | --- | ---: |
| image | 139 | 64×256 | 모든 word 일치 | `(960,256)` |
| CLUT | 140 | 16×8 | 모든 word 일치 | `(288,496)` |

DuckStation PNG dump는 STP/bit 15를 제거하므로 비교에서도 bit 15만 마스킹했다.
15비트 RGB 값과 배열은 전부 일치한다. destination은 TIM header 값이며,
ReleaseLTCG의 GPU debug log에는 저수준 copy 문장이 남지 않아 GPU 명령 좌표를
별도로 재관측하지는 못했다.

### 실제 texture 소비

자동 진행 20초 texture dump에서는 `#005`가 잡히지 않았지만, 수동으로 Options에
진입한 `HITL-OPTIONS-001` 세션에서는 다음 증거를 얻었다.

- Options 화면 스크린샷 1개
- CPU→VRAM dump 385개와 texture dump 544개
- `#005` image upload ID 139와 330의 RGB555 전량 일치
- texture hash `87256C8FB824543F`, palette 0–3 full sample의 65,536 pixel 전량 일치
- atlas 집계 sample 영역 `(16,176)`–`(243,255)`, 즉 셀 행 11–15와 열 1–15 교차

마지막 영역은 여러 draw의 사용 범위를 합친 DuckStation dump이므로 개별 글자의
정확한 UV는 아니다. 그러나 Options 화면과 같은 세션에서 `#005`를 실제
샘플링했다는 판정에는 충분하므로 `FONT-CONSUME-001`은 PASS다.

## 문자열 코드 가설

16×16 셀 순서에서 예상한 바이트로 다음 앵커를 검색했다.

- `オプション` → `AA D6 B7 E6 F0`
- `ノーマル` → `CD FB DD EA`
- `トレーニング` → `C7 EB FB CA F0 B0`
- `OPTIONS` → `1F 20 24 19 1F 1E 23`

이 1바이트 연속열의 완전 일치가 0건이었던 이유는 저장 문자열이 Shift-JIS
2바이트이기 때문이다. 실제 `オプション` 저장 후보는 `83 49 83 76 83 56 83 87
83 93`, decoder 출력 셀은 `AC D9 B9 E9 F5`다. 정적 재검색에서는 알려진 앵커
6건을 확보했고, `プッシュスタートボタン`은 HAGECHO `0x1A064`에 있다.

## 관측 판정

| ID | 판정 | 증거 | 다음 행동 |
| --- | --- | --- | --- |
| `ENV-001` | PASS | 버전·BIOS·설정·원본 해시 고정 | clean 픽셀 QA 프로필은 PoC 직전에 분리 |
| `FONT-UPLOAD-001` | PASS | upload 139 image, 140 CLUT RGB555 전량 일치 | 완료 |
| `RAM-001` | NEGATIVE | 3/8/15초 exact payload 0건 | 임시 버퍼 write/read breakpoint 후보 |
| `FONT-CODE-001` | PASS | `83 58`(ス)→cell `BB`→UV `B0/B0`→`POLY_FT4` | 두 글리프 가시성 PoC |
| `FONT-CONSUME-001` | PASS | Options 화면, upload 139/330, palette 0–3 texture 완전 일치 | 완료 |
| `FONT-VISIBILITY-001` | PASS | `D9/C5`→`한/글`, 타이틀과 Mode Select 실기 캡처 | 코드 배정·텍스트 모집단 조사 |
| `FONT-MOTION-001` | 재사용 확인 | builder 6회, ±256 순환, 양방향 16px 진행 | 한국어 prompt 연속 영상 QA |

## 재현 도구와 증거

- `workspace/scripts/analyze_font_atlas.py`
- `workspace/scripts/scan_font_strings.py`
- `workspace/scripts/capture_duckstation_ram.py`
- `workspace/scripts/capture_vram_uploads.py`
- `workspace/scripts/match_vram_uploads.py`
- `workspace/scripts/capture_gpu_log.py`
- `workspace/scripts/capture_texture_dumps.py`
- `workspace/scripts/capture_options_session.py`
- `workspace/scripts/analyze_options_capture.py`
- `workspace/scripts/capture_ui_ram_stages.py`
- `workspace/scripts/analyze_ui_ram_diff.py`
- `workspace/scripts/analyze_font_primitives.py`
- `workspace/scripts/analyze_all_cell_primitives.py`
- `workspace/scripts/capture_options_transition.py`
- `workspace/scripts/trace_font_primitive_writes.py`
- `workspace/scripts/extract_font_encoding.py`
- `workspace/scripts/analyze_font_writer.py`
- `work/analysis/font_candidate_005.json`
- `work/analysis/duckstation_ram_trace.json`
- `work/analysis/vram_tim_matches.json`
- `work/analysis/gpu_upload_coordinates.json`
- `work/analysis/options_session_001.json`
- `work/analysis/options_session_001_font_consume.json`
- `work/analysis/font_writer_analysis.json`
- `workspace/config/font_encoding.json`

초기 임시 실행의 `H:\Duckstation\settings.ini`는 SHA-256
`09D2A47A...F5629D`였다. 시간 초과된 감시 세션 종료 뒤 DuckStation이 설정을
재직렬화해 현재 기준선은 `02037CC1...9C9D`다. 렌더러·PGXP·dump·GDB 핵심값을
재검증했고 원본 BIN/CUE는 변하지 않았다. texture capture가 만든
`H:\Duckstation\textures\SLPM-86272`에는 파일 없이 빈 하위 폴더만 남아 있다.

## 다음 게이트

가시성 PoC와 raw-sector EDC/ECC 빌드는
[font-visibility-poc.md](font-visibility-poc.md)에서 PASS로 종료했다. 다음 단계는
원본 코드 사용 전수와 decoder 예약 영역을 조사해 충돌 없는 한글 코드 배정을
결정하고, 실제 소비자 기준의 텍스트 모집단 추출 경계를 세우는 것이다.
