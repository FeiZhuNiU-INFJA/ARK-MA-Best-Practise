"""case_paths.py —— 冻结层：case 工作目录的标准布局解析（永不随客户变）。

一个 case = 一次「拿某客户某批轨迹做 MA 重放对比实验」的完整工作目录，放在项目根 `ma-cases/<case>/` 下。
本模块把「布局约定」钉死成一处，供所有脚本（extract / build / gen_file_mocks / run）共用，
避免各脚本各写一套路径拼接、日后漂移。

标准布局：

    ma-cases/<case>/
    ├── trajectories/          # 原始轨迹（客户给的 *.json），只读输入
    ├── shared/                # 两种 mock 模式共享的抽取产物（extract/build/gen_file_mocks 的输出）
    │   ├── system_prompt.txt / system_sections.md
    │   ├── skill_bodies.json / replay_map.json / queries.json / coverage.json / index.json
    │   ├── skills/<code>/SKILL.md …          # 依据轨迹可见内容重建的业务 skill（两模式都用）
    │   ├── mocks-skill/…                     # 文件静态 mock 数据（仅 files 模式会挂）
    │   └── mock_system_appendix.md           # 离线取数附录（仅 files 模式拼进 system）
    ├── files-mode/<traj>/     # 静态文件模式：每条轨迹一子目录，放该轨迹的 MA 实跑 artifact
    │   └── rep<i>.json / run.json  # 每次并发重复明细 + 聚合（含 fail_ratio 与成功均值）
    ├── custom-mode/<traj>/    # custom tool 模式：每条轨迹一子目录（结构同上）
    └── reports/               # 对比报告（comparison-files.md / comparison-custom.md）

设计要点：
  * `shared/` 是「抽取产物」——它是 extract/build/gen_file_mocks 的 --out-dir，两模式共享同一份。
  * 每条轨迹的实跑结果按 <模式>/<轨迹stem>/ 分开放，互不覆盖（这正是之前 ma_runs 被覆盖的教训）。
  * 创建 agent/session/skills 的脚本**不在 case 里**——它们是冻结层 scripts/ 的一部分，
    每个 case 只产出数据，不复制脚本。

自包含：只用标准库 pathlib。
"""
from __future__ import annotations

from pathlib import Path


class CasePaths:
    """把一个 case 根目录解析成各标准子目录。传入 <项目>/ma-cases/<case>。"""

    def __init__(self, case_dir: Path | str):
        self.root = Path(case_dir).resolve()

    # ---- 标准子目录 ----
    @property
    def trajectories(self) -> Path:
        """原始轨迹目录（客户给的 *.json）。"""
        return self.root / "trajectories"

    @property
    def shared(self) -> Path:
        """两模式共享的抽取产物目录（= extract/build/gen_file_mocks 的 out-dir）。"""
        return self.root / "shared"

    @property
    def reports(self) -> Path:
        """对比报告目录。"""
        return self.root / "reports"

    def mode_dir(self, mock_mode: str) -> Path:
        """某 mock 模式的根目录：files-mode/ 或 custom-mode/。"""
        return self.root / f"{mock_mode}-mode"

    def run_dir(self, mock_mode: str, trajectory: str) -> Path:
        """某模式下某条轨迹的实跑目录：<mode>-mode/<轨迹stem>/。"""
        stem = Path(trajectory).stem if trajectory else "query"
        return self.mode_dir(mock_mode) / stem

    def ensure(self) -> "CasePaths":
        """建好标准骨架目录（幂等）。"""
        for d in (self.trajectories, self.shared, self.reports):
            d.mkdir(parents=True, exist_ok=True)
        return self

    def __repr__(self) -> str:
        return f"CasePaths({self.root})"


def add_case_args(ap) -> None:
    """给 argparse 挂上互斥的 --case-dir / --out-dir（各脚本共用同一套口径）。

    - --case-dir <ma-cases/xxx>：走标准布局，抽取产物落 <case>/shared。
    - --out-dir <dir>          ：直接指定输出目录（旧口径，向后兼容）。
    二选一，都不给时报错。
    """
    ap.add_argument("--case-dir", help="case 工作目录（项目根 ma-cases/<case>）；产物落其 shared/")
    ap.add_argument("--out-dir", help="直接指定抽取产物目录（与 --case-dir 二选一）")


def resolve_shared_dir(args) -> Path:
    """把 --case-dir / --out-dir 解析成「抽取产物目录」（extract/build/gen 的读写根）。"""
    if getattr(args, "case_dir", None):
        cp = CasePaths(args.case_dir).ensure()
        return cp.shared
    if getattr(args, "out_dir", None):
        return Path(args.out_dir)
    raise SystemExit("需要 --case-dir 或 --out-dir 之一。")
