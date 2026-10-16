# 문자 코드→글리프 셀 추적

## 관측 요청

`HITL-FONT-CODE-001`은 동일 실행에서 타이틀 메뉴와 Options 화면의 2MB RAM을
각각 캡처해 화면 전환 차분을 만든다. 두 캡처 모두 화면이 보이는 상태에서 `F10`
스크린샷을 트리거로 사용하므로 RAM과 사람이 확인할 수 있는 화면을 같은 상태에
귀속한다.

| 단계 | 사용자 조작 | 캡처 |
| --- | --- | --- |
| `title` | 타이틀 메뉴가 보이면 `F10` | 화면, CPU 레지스터, 2MB RAM |
| `options` | Options에 들어가 `F10` | 화면, CPU 레지스터, 2MB RAM |

분석은 `オプション`, `セレクト`, `サウンド` 등 화면에서 확인되는 앵커의 후보
코드열, 16비트 확장형, 전환 중 새로 생긴 RAM 구간을 구분한다. exact hit가 없으면
압축·인덱스·primitive 생성 테이블 가설을 유지하며 저장 문자열 확정으로 승격하지
않는다.

## 판정

`FONT-CODE-001`: **PASS**

정지 RAM의 단순 문자열 검색에서는 화면 앵커의 1바이트·16비트·UV 연속열이
발견되지 않았다. 그러나 쓰기 감시점으로 글꼴 primitive가 생성되는 순간을 잡아
다음 소비 사슬을 확인했다.

| 단계 | 런타임 주소 | 저장 위치/규칙 |
| --- | --- | --- |
| 2바이트 문자열 reader | `0x80144D44` | HAGECHO `0x19064`, lead→trail 순서 |
| Shift-JIS→셀 decoder | `0x800430DC` | ARA `0x6D0DC` |
| `POLY_FT4` builder | `0x80042F28` | ARA `0x6CF28` |
| U 계산/쓰기 | `0x80042F8C`–`0x80042F94` | `(cell & 0x0F) << 4` |
| V 계산/쓰기 | `0x80042F98`–`0x80042F9C` | `cell & 0xF0` |

reader는 `(lead << 8) | trail`로 Shift-JIS 코드를 만들고 포인터를 2바이트씩
증가시킨다. 다음 lead byte가 `0x00`이면 종료한다. decoder가 `0xFF`를 반환하면
해당 글리프는 생성하지 않는다.

### 런타임 표본

HAGECHO block 0의 `0x1A064`에는 `プッシュスタートボタン`이 저장돼 있다.
감시점 표본에서 reader 포인터 `0x80145D4C`는 HAGECHO `0x1A06C`의 `83 58`
(`ス`)를 가리켰다. decoder 출력은 `0xBB`였고 builder는 U/V에 `B0/B0`을
썼다. 따라서 이 표본에서 저장 코드→셀→UV→primitive가 전부 연결된다.

`workspace/config/font_encoding.json`은 decoder의 표준 범위와 특수 jump table을
복원한 SSOT다. 유효 Shift-JIS 입력은 257개다. 셀 `F6`, `F7`, `F8`은 각각 두
입력이 alias하고, atlas의 `0x30` 셀은 이 decoder로 도달하지 않는다. 그러므로
“255개 셀 모두 1:1 사용” 가설은 기각한다.

### 기각한 감시 결과

`font_write_trace_001`은 사용자의 입력을 기다리는 동안 시간 제한으로 끝나 분석
JSON이 생성되지 않은 불완전 세션이다. 증거 판정에는 사용하지 않는다.

`font_write_trace_002`의 `0x80046894`는 전환 중 같은 primitive 메모리를 재사용한
일반 메뉴 quad 작성자였다. 글꼴 코드 입력을 전달하지 않으므로 글꼴 소비자로
승격하지 않는다. `font_write_trace_003`에서 잡은 `0x80144DDC`는 선택된 셀의 UV
정점을 뒤집는 후처리이며 decoder가 아니다.

## 증거

- `work/analysis/ui_ram_session_001.json`
- `work/analysis/ui_ram_session_001_diff.json`
- `work/analysis/ui_ram_session_001_font_primitives.json`
- `work/analysis/font_write_trace_003.json`
- `work/analysis/font_writer_analysis.json`
- `workspace/config/font_encoding.json`
- `work/analysis/font_string_anchor_hits.json`

다음 단계는 검증된 title prompt 경로에서 기존 셀 두 개를 임시 한글 글리프로
바꾸는 가시성 PoC다. 이 변경은 원본 raw BIN이 아니라 Expected Write와 Form 1
EDC/ECC 재계산을 거친 별도 build에만 적용한다.
