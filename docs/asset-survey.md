# Rakugaki Showtime 자산 조사 — 2026-08-04

## 결론

교체 원본에 고정된 재현 가능한 판독·추출 경로를 만들었다. 전체 251,650개
MODE2/2352 sector를 다시 검사했고, ISO 파일 18개를 순회했다. 핵심 파일 5개는
`workspace/config/source_manifest.json`의 크기와 SHA-256에 모두 일치했다.

`DO.BIN`의 163개 offset으로 `HAGECHO.BIN`을 162개 블록으로 분할한 뒤 다시
결합한 결과, `DO.BIN`과 `HAGECHO.BIN` 모두 원본 logical file bytes와 완전히
같았다. 이 검사는 컨테이너 라운드트립이며, 수정된 raw BIN을 재작성하는 빌더와
EDC/ECC 재계산 검증은 아직 구현하지 않았다.

## 자동 조사 결과

| 항목 | 결과 |
| --- | ---: |
| 전체 raw sector 판독 | 251,650/251,650 |
| Form 1 / Form 2 | 138,025 / 113,625 |
| ISO 파일 수 | 18 |
| 핵심 파일 해시 일치 | 5/5 |
| `HAGECHO.BIN` 블록 | 162 |
| 분할→재결합 | `DO.BIN`, `HAGECHO.BIN` 모두 PASS |
| 유효 표준 TIM | 121 |
| TIM 위치 | 모두 `HAGECHO.BIN` |
| TIM 연락시트 | 4장 |

유효 TIM 판정은 magic만 세지 않고, flags, CLUT/image block 길이, width words,
height 및 payload 길이가 서로 맞는 경우만 인정한다. 121개 중 자주 나타나는
형식은 768×256 4bpp 30개, 256×240 8bpp 13개다.

## 글꼴과 타이틀 UI 후보

| 카탈로그 ID | 블록 | HAGECHO offset | 형식 | 크기 | 시각 판정 |
| --- | ---: | ---: | --- | ---: | --- |
| `#003` | 0 | `0x31D58` | TIM 4bpp | 256×44 | 타이틀 메뉴 장식/라벨 후보 |
| `#004` | 0 | `0x33578` | TIM 4bpp | 256×38 | 일본어 타이틀 메뉴 장식 |
| `#005` | 0 | `0x34A98` | TIM 4bpp | 256×256 | 숫자·Latin·히라가나·가타카나·한자 글리프 아틀라스 |
| `#007` | 0 | `0x4BDD8` | TIM 4bpp | 84×9 | 작은 일본어 라벨 후보 |
| `#008` | 0 | `0x4BF94` | TIM 4bpp | 120×9 | 저작권 표기 |

`#005`의 TIM 전체 크기는 33,056 bytes, SHA-256은
`C4249F66CDDC5EF8E7306953E07A3BAD36D1D62AF1FA859B19BE2257494A51A8`이다.
글리프가 규칙적인 격자로 배치되어 있으며, 후속 런타임 조사에서 image와 8개
CLUT가 실제 CPU→VRAM 업로드와 RGB555 단위로 일치함을 확인했다. 문자 코드→셀
선택 루틴과 `OPTIONS` 화면 primitive 소비는 아직 확인하지 않았다. 자세한 근거는
[runtime-font-trace.md](runtime-font-trace.md)를 본다.

현재 PNG 렌더러는 여러 CLUT가 들어 있는 TIM에서 첫 팔레트만 사용한다. 따라서
연락시트의 일부 768×256 아틀라스가 어둡거나 부자연스럽게 보이는 것은 원본
손상 판정이 아니다. 소비 시점의 CLUT 선택은 런타임 추적 대상이다.

## 재현 명령

프로젝트 루트에서 실행한다.

```powershell
python workspace/scripts/survey_assets.py
python workspace/scripts/verify_container_roundtrip.py
python workspace/scripts/render_tim_catalog.py
python -m unittest discover -s workspace/tests -v
```

주요 산출물:

- `work/analysis/disc_manifest.json`
- `work/analysis/hagecho_blocks.json`
- `work/analysis/tim_inventory.json`
- `work/analysis/tim_render_catalog.json`
- `work/analysis/container_roundtrip.json`
- `work/graphics/tim_contact_sheets/contact_sheet_00.png`–`03.png`

`extracted`와 `work`는 재생성 가능하다. 조사 기준선과 다음 표적은
`workspace/config/source_manifest.json` 및 `asset_targets.json`이 소유한다.

## 다음 런타임 게이트

1. DuckStation 버전·렌더러·PGXP 등 부팅 환경을 기록한다.
2. 타이틀 또는 Options 화면에서 글자 texture page/CLUT와 VRAM 좌표를 캡처한다.
3. `#005` TIM의 RAM 적재 주소와 GPU upload 호출을 잡는다.
4. 문자 코드 하나가 어느 글리프 셀을 고르는지 확인한다.
5. 원본 셀 두 개를 임시 `한`, `글`로 바꾸는 최소 PoC의 Expected Write를 설계한다.

런타임 소비가 확인되기 전에는 `#005`를 실제 폰트 사양으로 확정하거나 원본
`HAGECHO.BIN`을 수정하지 않는다.
