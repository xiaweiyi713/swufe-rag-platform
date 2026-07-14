"""桩(mock)/真(real)实现的切换层 —— 模块D的核心设计。

server.py 只依赖本文件的 Retriever / Generator 抽象基类,通过 config.yaml 的
`provider: mock|real` 切换实现,目标是集成日只改配置不改代码。

方法签名严格等于主 README 契约2 / 契约3,不得擅自变更;
返回字段的具体化约定见主 README「模块D对契约的具体化约定」D-1~D-6。

real 模式对 B/C 的导入路径约定(与 retrieval/README.md、generation/README.md 一致):
    模块B: from retrieval.retriever import retrieve
    模块C: from generation.cite import answer
若 B/C 的实际入口不同,只需修改本文件底部 RealRetriever / RealGenerator 两个薄适配层。
"""
from abc import ABC, abstractmethod


class Retriever(ABC):
    """契约2:检索接口。"""

    @abstractmethod
    def retrieve(self, query: str, top_k: int = 5,
                 college: str = None, cohort: str = None) -> list:
        """按学院/年级/现行过滤后融合排序,返回 list[dict]。

        每个 dict = 契约1全部字段 + score: float(0~1,约定 D-1)。
        过滤规则(契约2): college∈{全校/校级, 用户学院} 且 cohort∈{"不限", 用户年级}
        且 status=="现行",过滤必须在排序前。
        """


class Generator(ABC):
    """契约3:生成接口。"""

    @abstractmethod
    def answer(self, query: str, chunks: list) -> dict:
        """返回 {"answer_md": str, "citations": list, "refused": bool}(契约3)。"""


def get_providers(config: dict) -> tuple:
    """按 config['provider'] 返回 (Retriever, Generator) 实例。"""
    mode = config.get("provider", "mock")
    if mode == "mock":
        # 函数体内延迟导入,避免 app <-> mock 循环依赖
        from mock.mock_provider import MockGenerator, MockRetriever
        return MockRetriever(config), MockGenerator(config)
    if mode == "real":
        return RealRetriever(config), RealGenerator(config)
    raise ValueError(f"config.yaml 的 provider 只能是 mock 或 real,当前为: {mode!r}")


class RealRetriever(Retriever):
    """模块B的薄适配层。B 未就位时实例化即报错,给出明确指引。"""

    def __init__(self, config: dict):
        self.config = config
        try:
            from retrieval.retriever import retrieve as _retrieve
        except ImportError as e:
            raise RuntimeError(
                "provider=real 但模块B未就位:无法导入 retrieval.retriever.retrieve。"
                "请确认模块B已并入仓库,或将 config.yaml 改回 provider: mock。"
            ) from e
        self._retrieve = _retrieve

    def retrieve(self, query: str, top_k: int = 5,
                 college: str = None, cohort: str = None) -> list:
        return self._retrieve(query, top_k=top_k, college=college, cohort=cohort)


class RealGenerator(Generator):
    """模块C的薄适配层。C 未就位时实例化即报错,给出明确指引。"""

    def __init__(self, config: dict):
        self.config = config
        try:
            from generation.cite import answer as _answer
        except ImportError as e:
            raise RuntimeError(
                "provider=real 但模块C未就位:无法导入 generation.cite.answer。"
                "请确认模块C已并入仓库,或将 config.yaml 改回 provider: mock。"
            ) from e
        self._answer = _answer

    def answer(self, query: str, chunks: list, college: str = None, cohort: str = None) -> dict:
        # 契约3签名不含用户身份;server 传入的 college/cohort 在此丢弃,
        # 待模块C确认主 README 待对齐项 D-7 后再决定是否透传
        return self._answer(query, chunks)
