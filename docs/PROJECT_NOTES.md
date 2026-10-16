# Rakugaki Showtime 일본판 한글화

- 플랫폼: Sony PlayStation
- 제품 코드: `SLPM-86272` (`SLPM_862.72`)
- 현재 단계: 번역 324/324·감사 blocking 0, 2뱅크 ABI runtime PASS·최종 bank 상주 조사 중
- 기준 BIN: `original/Rakugaki Showtime (Japan).bin`
- BIN 크기: `591,880,800 bytes`
- BIN SHA-256: `99DC6C7F748C4882D6F197A0A0A2EB943AFFC25BB4271DEFF9BB9BF55859AA54`
- 기준 CUE: `original/Rakugaki Showtime (Japan).cue`
- CUE SHA-256: `4C6D97218E38FB592E9C426BF2FA086A2110005A82AB062352387594E0AF3F97`

원본은 `original`에서 수정하지 않는다. 원본에서 다시 만들 수 있는 추출물은
`extracted`, 재생성 가능한 조사·빌드 산출물은 `work`와 `build`, 자체 작성한
도구·설정·번역 자료는 `workspace`, 배포 가능한 패치만 `dist`에 둔다.

현재 CUE의 내부 참조와 실제 BIN 파일명은 일치한다. 전체 251,650개 raw sector의
sync·MODE2·MSF·중복 subheader 정적 검사를 통과했다.
2026-08-04 사용자가 교체 BIN/CUE의 실제 부팅 성공을 확인했다. DuckStation
버전·BIOS·핵심 설정과 원본 해시는 `workspace/config/runtime_baseline.json`에 기록했다.

핵심 파일 5개를 해시 검증하며 추출했고, `DO.BIN`이 나누는 162개
`HAGECHO.BIN` 블록의 무변경 분할→재결합이 바이트 단위로 통과했다. 유효한 표준
TIM 121개를 판독·렌더링했으며, 1차 동적 글꼴 후보는 block 0의 256×256 4bpp
아틀라스(`#005`, HAGECHO offset `0x34A98`)다. 자세한 결과는
[docs/asset-survey.md](docs/asset-survey.md)에 기록했다.

DuckStation `0.1-11636-g7111f4c12`에서 `#005` image와 8개 CLUT가 실제
CPU→VRAM 업로드 및 Options texture sampling과 RGB555 단위로 일치함을 확인했다.
`FONT-CODE-001`에서는 HAGECHO의 NUL 종단 Shift-JIS 문자열이 runtime
`0x800430DC` decoder를 거쳐 1바이트 셀이 되고, `0x80042F28` builder가
16×16 atlas UV를 만드는 경로까지 확인했다. 실행 근거와 다음 PoC 게이트는
[docs/runtime-font-trace.md](docs/runtime-font-trace.md)를 본다.

`FONT-VISIBILITY-001`에서는 `D9`, `C5` 셀을 임시 `한`, `글`로 교체하고 LBA
615–617의 MODE2 Form 1 EDC/ECC를 재계산했다. 새 부팅의 타이틀과 Mode Select에서
실제 표시를 확인했다. 빌드·런타임 판정은
[docs/font-visibility-poc.md](docs/font-visibility-poc.md)를 본다.

타이틀 테두리의 움직이는 글자는 bitmap과 독립된 기존 `POLY_FT4` 위치 controller가
담당한다. 완성형 1코드→1셀 한글은 별도 애니메이션 구현 없이 이 동작을 상속할 수
있다. 반복 간격과 구현 조건은
[docs/font-motion-assessment.md](docs/font-motion-assessment.md)를 본다.

1차 텍스트 모집단 조사에서 범용 문자열 renderer 호출 37곳과 패턴 후보 324개를
찾았다. HAGECHO 직접 호출 18개와 ARA 직접 호출 19개의 형태는 모두 분류했고,
정적 원본 좌표 96개를 확인했다. 이어 단일 문자열 wrapper 호출자 16곳을 모두
분류한 뒤 남았던 런타임 생산자 2곳도 정적 테이블까지 역추적했다. wrapper 16곳은
모두 source가 해결됐고, 생산자 테이블의 새 고유 좌표 43개를 더해 누계 208개가
됐다. 이어 포인터 배열 renderer 호출자 27곳도 모두 해결해 41개 좌표를 추가했고
누계는 249개다. 전달 소비자 분모 43곳은 `unknown=0`으로 닫혔지만, 한 글자·별도
reader 누락 감사에서 사망 문구 24개, 크레딧 50개, 저작권 문구 1개를 추가했다.
확인된 decoder·atlas builder 경로의 unknown은 0이고 정적 원문 누계는 324개다.
다만 그래픽에 구운 텍스트는 별도 번역 범위이며, 도달 가능한 atlas 셀이 모두
점유되어 직접 코드 재사용은 여전히 불가능하다.
decoder가 도달 가능한 254개 atlas 셀은 모두 점유되어 안전한 한글 배정 칸은
아직 없다. 후보 수를 번역 대상 수로 간주하지 않는 이유와 다음 분류 게이트는
[docs/text-population.md](docs/text-population.md)를 본다.

`HANGUL-STRATEGY-001`에서는 글자당 2바이트 한국어 코드를 공용 decoder에서 기존
8비트 셀로 재매핑하고, 번역 corpus에 필요한 완성형 글리프로 255셀 atlas를 다시
구성하는 방식을 1순위로 선택했다. 번역·보존 기호의 고유 글리프가 255개를 넘으면
빌드를 실패시키고 정적 2뱅크 atlas hook PoC로 전환한다. 기존 atlas는 VRAM
`(960,256)`에서 우·하단 경계까지 차므로 제자리 확대할 수 없다. 판정과 구현 게이트는
[docs/hangul-strategy.md](docs/hangul-strategy.md)를 본다.

`TEXT-SSOT-001`에서는 확인된 동적 원문 324개를 UTF-8 BOM CSV로 내보냈다. 물리
좌표 기준 `ARA.BIN` 125개, `HAGECHO.BIN` 199개이며, 번역자는
`workspace/text/rakugaki_text_ja_ko.csv`의 `translation`, `status`, `notes`만 편집한다.
완료본은 불변 원문 31칸의 스프레드시트 자동 서식을 manifest에서 복구했고 번역문은
전부 보존했다. 교정본 재저장 때 재발한 같은 자동 서식 변환도 두 번째 백업 후 원문
필드만 복원했다. 전체 요구 글리프는 445개, 그중 완성형 한글은 379개다. 단일 255셀
atlas는 탈락했고 510셀 용량의 정적 2뱅크 PoC를 선택했다. `TXT-HAG-22634C` 교정을
확인해 번역 감사 blocking은 0이며, 비차단 사람 검토 후보 32행이 남아 있다. SSOT 계약과
census·감사 결과는
[docs/text-ssot-and-glyph-census.md](docs/text-ssot-and-glyph-census.md)를 본다.

`FONT-TWO-BANK-POC-001`에서는 decoder 반환값의 bit 8을 bank로 사용하고 공용
primitive builder가 bank별 tpage를 고르도록 20바이트 제자리 패치를 만들었다. 새 부팅
GDB 추적에서 `한`의 `0x1DE`가 `U=E0`, `V=D0`, `tpage=1E` primitive로 이어져 ABI는
runtime PASS했다. PoC용 bank는 splash 페이지를 임시 공유하므로 최종 상주 설계로
승인하지 않았다. 정적 TIM 121개와 겹치지 않는 `(704,256)` 페이지의 런타임 점유 감사가
다음 게이트다. 빌드·추적·제한은
[docs/two-bank-font-poc.md](docs/two-bank-font-poc.md)를 본다.

현재 상태와 다음 작업은 [docs/HANDOFF_2026-08-04.md](docs/HANDOFF_2026-08-04.md),
선행 사례 조사와 기술 게이트는
[docs/precedent-and-strategy.md](docs/precedent-and-strategy.md)를 본다.

## 폴더 역할

```text
backups/        수동 편집 전 복구용 사본
build/          최종 이미지 조립 등 재생성 가능한 빌드 출력
dist/           배포용 차분 패치와 적용 안내
docs/           조사 결론, 판정, QA, 인계 기록
extracted/      원본에서 재생성 가능한 추출물
original/       사용자가 제공한 원본 이미지 (수정 금지)
work/           분석·캡처·임시 중간 산출물
workspace/
  config/       지원 원본 기준선과 프로젝트 설정
  scripts/      추출·삽입·검증·빌드 도구
  tests/        자동 검증
  text/         원문·번역 자료의 단일 소유 위치
```
