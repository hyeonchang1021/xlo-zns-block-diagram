# XLO-ZNS 시스템 블록도와 논리 회로도

XLO-ZNS(Encryption-Aware Zone Placement for Zoned Namespace SSDs) 논문의 시스템 구조를 설명하는 정적 페이지입니다.

- [시스템 블록도](https://hyeonchang1021.github.io/xlo-zns-block-diagram/): 블록이나 흐름 이름을 누르면 역할, 동작, 식, 핵심 수치가 나옵니다.
- [논리 회로도](https://hyeonchang1021.github.io/xlo-zns-block-diagram/logic/): 배치별 결정 경로를 레지스터, 비교기, 게이트로 옮긴 계층형 회로도와, 이를 Verilog로 구현해 시뮬레이션·합성한 결과입니다.
- [`rtl/`](rtl/): Verilog 구현(`xlo_place.v`), 테스트벤치, 검사기. 실행 방법은 `rtl/README.txt`에 있습니다.

알아 둘 점

- 논리 회로도와 RTL은 개념 설계입니다. 논문은 이 회로를 구현하거나 측정하지 않았습니다.
- 블록도의 수치는 모두 논문의 시뮬레이터 값이며, 실제 장치의 절대 지연으로 인용할 수 없습니다.
- 빌드 과정이 없는 정적 페이지입니다. `main`에 푸시하면 GitHub Pages가 그대로 배포합니다.
