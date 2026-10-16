# Rakugaki Showtime 선행 한글화 조사와 PS1 전략

- 조사일: 2026-08-04
- 대상: 일본판 PlayStation `SLPM-86272`
- 개발사: Treasure
- 유통사: Enix
- 판정 상태: 전략 수립 완료, 구현 및 런타임 검증 전

## 1. 결론

Treasure가 개발한 게임의 공개 한국어 패치 이력은 있다.

| 게임 | 플랫폼 | 한국어 패치 기록 | 이번 프로젝트로의 전이 |
| --- | --- | --- | --- |
| Advance Guardian Heroes | GBA | Juen, 2004-11-11 | 개발사 선례만 인정. 하드웨어와 엔진이 달라 구조 재사용 불가 |
| Sin and Punishment: Successor of the Earth | N64 | adek, 2015-12-14 | 개발사 선례만 인정. 하드웨어와 엔진이 달라 구조 재사용 불가 |

Treasure의 공식 제품 목록에는 두 작품과 Rakugaki Showtime이 모두 등재되어
있다. 한국어 패치 기록은 각각
[한글로게임 GBA 목록](https://www.hangulogame.com/platform/gba/)과
[N64 목록](https://www.hangulogame.com/platform/n64/)에서 교차 확인했다.
개발작 범위는 [Treasure 공식 제품 목록](https://www.treasure-inc.co.jp/products1.html)을
기준으로 삼았다.

반면 공개 [PlayStation 한국어 패치 목록](https://www.hangulogame.com/platform/ps/)과
제목·개발사 검색에서는 Rakugaki Showtime 또는 Treasure의 PS1 작품용 한국어
패치를 찾지 못했다. 이는 조사한 공개 자료 범위의 결론이며, 비공개·소실된 작업이
절대 없었다는 뜻은 아니다.

같은 개발사·같은 플랫폼의 외국어 현지화 선례로는 Treasure의 PS1판
Silhouette Mirage를 Working Designs가 북미에 현지화한 사례가 있다
([GameSpot 당시 보도](https://www.gamespot.com/articles/playstation-gets-silhouette-mirage/1100-2462933/)).
그러나 별도 게임이므로 텍스트 포맷, 폰트, 아카이브, 훅 주소는 후보 비교 자료일
뿐 Rakugaki Showtime의 사양으로 이식하지 않는다.

따라서 이 프로젝트는 **선행 한국어 패치의 기술을 포팅하지 않고, 대상 디스크에서
저장→적재→렌더 경로를 새로 확정하는 PS1 표준 전략**으로 진행한다.

## 2. 현재 확인된 대상 구조

### 2.1 입력 기준선

| 항목 | 확인값 |
| --- | --- |
| BIN 크기 | 591,880,800 bytes |
| BIN SHA-256 | `99DC6C7F748C4882D6F197A0A0A2EB943AFFC25BB4271DEFF9BB9BF55859AA54` |
| BIN SHA-1 | `B648451D4EE6BFA29F56467551D3EC3240E2EEBE` |
| BIN MD5 | `F162A25833BD0FC92E1B0C6DA88CDB7A` |
| PVD volume ID | 빈 문자열 |
| 트랙 | 단일 `MODE2/2352` 데이터 트랙 |
| 실행 파일 | `SLPM_862.72`, 81,920 bytes |
| 실행 파일 SHA-256 | `52B2FC1FB7AF2AE2791B94AB0ED835573DACB8D2E24E0C94F1C995EEB959F4C5` |
| EXE load address | `0x80190000` |
| EXE payload size | `0x13800` |

제품 코드는 실행 파일명과 `SYSTEM.CNF`의 boot 경로에서 확인했다. 외부 자료의
제품 코드·단일 데이터 트랙 정보도
[PSX DataCenter 항목](https://psxdatacenter.com/games/J/R/SLPM-86272.html)과 일치한다.
PVD volume ID는 이 교체 덤프에서 비어 있으므로 제품 코드 근거로 사용하지 않는다.

### 2.2 ISO 파일 지도

| 경로 | LBA | 크기 | 현재 해석 |
| --- | ---: | ---: | --- |
| `SLPM_862.72` | 23 | 81,920 | PS-X EXE |
| `ARA.BIN` | 63 | 888,832 | 자산 컨테이너 후보, 미확정 |
| `DO.BIN` | 497 | 652 | `HAGECHO.BIN` 경계표로 확인 |
| `HAGECHO.BIN` | 498 | 13,039,616 | 162개 블록 컨테이너 후보 |
| `SYSTEM.CNF` | 6,865 | 68 | 정상 판독, `SLPM_862.72` boot 선언 |
| `CDXA/` | 6,866 | 2,048 | 정상 판독, XA 파일 6개 |
| `MOVIE/*.STR` | 155,636 이후 | 7개 STR 영상 |

`DO.BIN`은 163개의 little-endian 32비트 값으로 구성되고, 값이 `0`에서
`0xC6F800`까지 단조 증가한다. 마지막 값 `0xC6F800`은 `HAGECHO.BIN`의
파일 크기와 정확히 같으므로 **162개 블록의 시작점과 종점 테이블**이라는 판정은
강하다. 다만 각 블록의 내부 포맷과 소비자는 아직 미확정이다.

### 2.3 교체 덤프 무결성 판정

이전 BIN SHA-256
`1003B0D10744A4F303EE3968886412FBDB54453C3E685255571EFC7E5D592A4B`에서는
LBA 6,855 이후에 표준 raw-sector sync가 없는 구간이 있었고 `HAGECHO.BIN`,
`SYSTEM.CNF`, `CDXA`를 정상적으로 읽을 수 없었다.

2026-08-04에 교체한 현재 BIN에서는 이 문제가 재현되지 않는다.

| 검사 | 결과 |
| --- | --- |
| 전체 sector 수 | 251,650 |
| raw sync 일치 | 251,650/251,650 |
| MODE2 mode byte | 251,650/251,650 |
| BCD MSF 주소 | 251,650/251,650 |
| duplicated subheader | 251,650/251,650 |
| Form 1 / Form 2 | 138,025 / 113,625 |
| LBA 0–6,866 Form 1 EDC/ECC | 6,863/6,863 일치 |
| ISO directory traversal | `SYSTEM.CNF`, `CDXA` 6개 XA, MOVIE 7개 STR까지 성공 |

`HAGECHO.BIN` 전체 logical SHA-256은
`616C313D02F136133BAAE7BDC4F32FEF04902D7F877B9DBB94264B5C56BD4E43`이다.
따라서 이전의 “특수/보호 섹터” 가설은 현재 기준 입력에서는 기각한다. 정확한
공개 보존 DB 해시 일치는 검색으로 확인하지 못했다. 2026-08-04 사용자가 교체
BIN/CUE의 clean boot 성공을 확인했으며, 에뮬레이터 버전·설정은 미기록이다.

### 2.4 텍스트·그래픽 가설

- 실행 파일에서 대표 메뉴 용어의 평문 CP932 문자열은 확인되지 않았다.
- `ARA.BIN`과 `HAGECHO.BIN`은 무작위 바이트가 CP932처럼 보이는 거짓양성이
  많아 문자열 검색만으로 텍스트라고 판정할 수 없다.
- 엄격한 TIM 구조 검사 결과 유효 표준 TIM 121개가 모두 `HAGECHO.BIN`에서
  확인됐다. `ARA.BIN`의 단순 magic hit는 유효 TIM으로 판정되지 않았다.
- block 0의 offset `0x34A98`에는 숫자·Latin·일본어 글리프가 격자로 배치된
  256×256 4bpp TIM이 있다. 동적 글꼴 후보지만 런타임 소비는 미확정이다.
- 공개된 메뉴 번역 자료에는 Normal/Versus/Training/Options, 상세 옵션,
  Save/Load, Staff/Credits 등이 확인된다
  ([RetroAchievements 게임 토픽](https://retroachievements.org/forums/topic/28939)).

따라서 1차 가설은 **메뉴 글자의 상당 부분이 TIM 또는 게임 고유 그래픽 자산이고,
일부 상태/수치만 동적 렌더링될 가능성**이다. 화면 캡처와 VRAM/자산 대응 전에는
확정하지 않는다.

## 3. 작업 전략과 게이트

### Gate 0 — 원본·매체 기준선

목표는 지원 원본을 식별하고 원본의 Form 1/Form 2·XA·LBA 표현을 보존하면서
다시 쓸 수 있는 경계를 확정하는 것이다.

1. 현재 BIN/CUE가 DuckStation에서 원본으로 부팅되는지 확인한다. **통과:**
   2026-08-04 사용자 clean boot 확인.
2. 가능하면 신뢰할 수 있는 덤프 기준과 트랙 크기·해시를 대조한다.
3. 무변경 출력은 원본 BIN과 바이트 동일해야 한다.
4. ISO 재빌드가 필요해질 경우 Form 1/Form 2, XA interleave와 LBA를 보존한다.

현재 판정: **부분 통과**. 원본 부팅과 지원 해시는 확정됐고, 핵심 logical file
추출 및 `DO.BIN`/`HAGECHO.BIN` 162블록 분할→재결합은 바이트 동일하게 통과했다.
수정 raw BIN을 만드는 sector Expected Write/EDC/ECC 빌더는 아직 구현 전이다.
부팅 확인의 에뮬레이터 버전·설정도 후속 기록한다.

현재 기본 빌드 후보는 **원본 raw BIN 복사본에 등록된 sector/user-data만 쓰는
in-place Expected Write 방식**이다. 새 덤프에서 특수 섹터 위험은 사라졌지만,
파일 성장이나 재배치가 필요하다고 확인되기 전에는 ISO 재패킹을 선택하지 않는다.

### Gate 1 — 화면·텍스트 모집단

원본 화면을 다음 상태별로 캡처하고 각 라벨을 안정 ID로 등록한다.

1. 부팅, 제작사 로고, 타이틀
2. Normal/Versus/Training/Options 진입 화면
3. 캐릭터·스테이지 선택, 로딩, 전투 HUD와 pause
4. 승패·랭크·continue·game over·ending 분기
5. Options의 기본/선택/ON/OFF 상태, key config
6. Save/Load와 메모리 카드 오류·확인 메시지
7. Staff/Credits, 해금 Special·Movie Test
8. STR 영상 안에 구운 일본어 또는 자막 존재 여부

공식 소개에서 게임 모드와 스토리 범위는 확인되지만
([Square Enix 공식 페이지](https://www.jp.square-enix.com/game/detail/rakugakishowtime/)),
실제 번역 분모는 대상 리비전의 모든 접근 가능한 화면으로 다시 센다.

각 항목은 `dynamic_text`, `baked_graphics`, `numeric/symbol`, `no_text`,
`unknown`으로 분류한다. `unknown`을 제외 처리하지 않는다.

### Gate 2 — 자산과 소비 경로

가장 대표적인 메뉴 라벨 하나를 증거 앵커로 고른다.

1. 원본 화면의 픽셀과 VRAM 위치를 고정한다.
2. GPU upload/LoadImage 계열 호출 또는 텍스처 덤프로 RAM 공급원을 찾는다.
3. 공급원을 `ARA.BIN`, `HAGECHO.BIN` 블록, EXE 내장 데이터 중 하나와 연결한다.
4. 압축·변환이 있으면 저장→해제→RAM→VRAM→primitive 소비를 연결한다.
5. 같은 자산을 다른 라벨·배경·선택 상태가 공유하는지 확인한다.

파일명, TIM 시그니처, 정적 문자열 hit만으로 소비를 확정하지 않는다.

### Gate 3 — 가시성 PoC

Gate 2 결과에 따라 하나만 먼저 수행한다.

#### A. 그래픽 텍스트 경로

- 원본과 같은 픽셀 크기·bit depth·CLUT·배열로 짧은 메뉴 라벨 하나를 한글화한다.
- `DO.BIN`/`HAGECHO.BIN`을 건드리면 해당 블록 경계와 다음 블록을 보존한다.
- 기본/선택 상태, 메뉴 진입·이탈·재진입에서 배경·외곽선·클리어를 확인한다.

#### B. 동적 글리프 경로

- 실제 코드→글리프 선택 루틴과 글리프 공급 자산을 확정한다.
- 미사용이라고 검증된 코드 2개 이상에 `한글` 글리프를 넣는다.
- 대표 문자열 하나가 기존 저장·적재·렌더 경로로 표시되게 한다.
- 주변 원문, 숫자·기호, 진입·이탈에 회귀가 없어야 한다.

통과는 해당 렌더 경로 하나만 승인한다. 다른 메뉴, HUD, 영상 자막까지
일반화하지 않는다.

### Gate 4 — 추출·재삽입

- `DO.BIN` 162블록을 안정 ID와 원본 offset/size로 카탈로그화한다.
- `ARA.BIN`은 실제 reader 또는 화면 대응으로 엔트리 경계를 확정한다.
- 원문, 원시 토큰, 포인터/인덱스, 그래픽 bounds와 팔레트를 보호 필드로 둔다.
- 수정 없는 추출→재삽입은 원본 바이트와 동일해야 한다.
- 미해석 필드, 알 수 없는 토큰, 중복/내부 진입 참조는 가역 보존한다.
- 번역은 `workspace/text`의 단일 승인 필드에서만 빌드가 읽는다.

### Gate 5 — 폰트·인코딩 설계

동적 경로가 확인될 때만 활성화한다.

- 완성형/조합형을 먼저 고르지 않고 코드 공간, 저장 글리프 상한, RAM/VRAM
  활성 상한을 각각 측정한다.
- 실제 번역 코퍼스의 고유 글리프 수를 공급 예산과 비교한다.
- 원본 외자·기호·제어값과 겹치지 않는 명시 매핑을 만든다.
- 인코더, 폰트 생성기, 디코더, 검증기가 같은 매핑 파일을 소비한다.
- 미할당 문자나 예산 초과는 빌드 실패로 처리한다.
- 셀 크기와 폰트는 실제 배경에서 받침·복합 모음의 판독성으로 결정한다.

이 게임이 UI 중심의 작은 코퍼스로 확인되면 필요한 완성형 음절만 공급하는
정적 매핑이 첫 후보가 된다. 이는 현재 가설이며 전량 화면 조사 전에 확정하지
않는다.

## 4. 빌드 전략

주 빌드는 다음 단일 경로를 목표로 한다.

```text
검증된 original BIN
  → 필요한 원본 자산을 매번 추출
  → 번역/그래픽/폰트 입력 검증
  → Expected Write 계획 생성 및 원본 바이트 확인
  → raw BIN 복사본에 승인된 변경만 적용
  → 변경 섹터의 form 및 EDC/ECC 재계산
  → 전체 diff/파일 경계/비표준 섹터 보존 감사
  → DuckStation 런타임 QA
  → xdelta 배포물과 적용 결과 해시 검증
```

불변식:

- 원본 BIN/CUE와 `extracted` 기준선은 수정하지 않는다.
- 이전 패치 이미지를 다음 빌드 입력으로 쓰지 않는다.
- Form 1/Form 2, XA interleave와 원본 LBA를 근거 없이 바꾸지 않는다.
- 파일 성장 전에는 제자리 교체를 우선 검토하되, 데이터 절단으로 맞추지 않는다.
- 파일 성장 시 모든 ISO LBA/size, 게임 내부 offset/size, 컨테이너 참조를 확인한
  뒤에만 재배치한다.
- 정적 빌드 성공은 런타임 성공이 아니다.

## 5. QA 범위

### 자동 게이트

- 원본 크기·SHA-256·PVD·실행 파일 기대값
- `DO.BIN` 엔트리 수 163, 단조 증가, 종점 `HAGECHO.BIN` 크기 일치
- 원본 무수정 라운드트립
- 등록되지 않은 diff 0건
- 수정 sector form/EDC/ECC 유효성
- 번역 미할당 문자 0건, 글리프·레이아웃 예산 초과 0건
- 패치 적용 결과와 빌드 목표 BIN의 SHA-256 일치

### 런타임 게이트

- 원본과 패치본의 clean boot
- 1P Normal 전체 분기와 continue/game over/ending
- Versus 2–4P, Training, Options, key config
- pause, save/load, 메모리 카드 없음·오류 상태
- 선택/비선택/ON/OFF 및 해금 상태
- Movie/Staff/Credits와 화면 전환·재진입
- 장시간 플레이와 전투/영상 전환 뒤 글리프·텍스처 잔상·덮어쓰기 없음

최종 후보는 DuckStation의 고정 버전에서 먼저 검증하고, raw-sector 변경이 남으면
다른 에뮬레이터 또는 실기/ODE에서 독립 확인한 뒤 배포 완료로 판정한다.

## 6. Go / No-Go 판정

| 판정 | 조건 | 다음 행동 |
| --- | --- | --- |
| GO-A | 그래픽 라벨 PoC가 제자리 자산 교체로 통과 | 그래픽 카탈로그와 화면별 배치 확장 |
| GO-B | 동적 글리프 PoC와 코드/VRAM 예산이 통과 | 명시 매핑·폰트·텍스트 파이프라인 확장 |
| CONDITIONAL | 표시 성공, 매체 라운드트립 또는 자산 예산 미확정 | 실패한 게이트만 더 작게 분리 |
| NO-GO | 비표준 섹터를 보존하는 재현 빌드가 없거나 주 렌더 경로에 한글 도달 불가 | 번역·전량 그래픽 제작을 중단하고 매체/렌더 설계 재검토 |

## 7. 바로 수행할 다음 작업

1. 부팅에 사용한 에뮬레이터 버전·설정 기록
2. 원본 화면 카탈로그 촬영
3. 대표 Options 라벨과 block 0 `#005` 글꼴의 VRAM→RAM→디스크 역추적
4. 문자 코드→글리프 셀과 실제 CLUT 선택 확인
5. 확인된 한 경로로 가시성 PoC 및 raw-sector Expected Write 빌더 구현
