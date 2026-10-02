"""生成语义缓存阈值校准集 ``eval/datasets/cache_pairs_v1.jsonl``（PLAN S10）。

每条是一对问题 (q1, q2) 加标签：
- ``same``（category=paraphrase）：同一个问题的不同说法——**可以**互相复用缓存答案；
- ``different``（category=hard_negative）：**只差一个关键要素**的问题——A 类 vs C 类、管理费 vs 托管费、
  一季度 vs 二季度、持有 7 天 vs 30 天、两只易混淆的基金……**不能**复用，复用就是答错；
- ``different``（category=unrelated）：毫不相干的两个问题（参考基线，不是主要指标）。

来源（provenance）诚实标注：
- ``template_generated``：由本脚本用「意图模板 × 真实基金池 × 槽位取值」确定性生成（固定随机种子）。
  模板的措辞由执行者（Claude）手写，**不是真实用户的提问分布**——这是局限，见 docs/LIMITATIONS.md；
- ``hand_written_claude``：执行者手写的自由说法（更口语、结构不拘一格），数量少。
**没有人工审核**；``same`` / ``different`` 的标签由构造方式保证（同槽位取值 = same，改一个槽位 = different）。

dev / test 按**意图**划分（同一个意图的所有对只在一边），所以 test 上的措辞和槽位都是选阈值时没见过的。
阈值只在 dev 上选；test 只跑一次（见 ai-service/src/fund_ai/eval/cache_calibration.py）。

用法：``python scripts/build_cache_pairs.py``（重写数据集文件；数据集一旦用于 test 就冻结，改动要升版本号）。
"""

from __future__ import annotations

import hashlib
import json
import random
import re
from collections import Counter
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "eval" / "datasets" / "cache_pairs_v1.jsonl"
SEED = 20261002

# ---------------------------------------------------------------- 基金与易混淆对

_SUFFIX = re.compile(r"(指数增强发起式|指数增强|发起式|混合|股票|指数)$")


def load_funds() -> list[dict]:
    u = yaml.safe_load((ROOT / "data" / "universe.yaml").read_text(encoding="utf-8"))
    out = []
    for f in u["funds"]:
        name = f["name"]
        out.append(
            {"code": f["code"], "name": name, "short": _SUFFIX.sub("", name), "theme": f["theme"]}
        )
    return out


# 名字相近、同一家公司或同一主题的基金对：fund 维度的难负例从这里选
CONFUSABLE = [
    ("中欧医疗健康混合", "易方达医疗保健行业混合"),
    ("易方达医疗保健行业混合", "广发医疗保健股票"),
    ("广发医疗保健股票", "南方医药保健混合"),
    ("易方达医疗保健行业混合", "易方达信息产业混合"),
    ("汇添富创新医药混合", "汇添富中证芯片产业指数增强发起式"),
    ("富国中证医药主题指数增强", "富国创新科技混合"),
    ("天弘中证医药100", "国泰国证医药卫生行业指数"),
    ("银华集成电路混合", "博时半导体主题混合"),
    ("永赢科技驱动混合", "华安科技动力混合"),
    ("大成中证360互联网+大数据100指数", "西部利得中证人工智能主题指数增强"),
    ("中银创新医疗混合", "工银前沿医疗股票"),
    ("汇添富创新医药混合", "工银前沿医疗股票"),
]

# ---------------------------------------------------------------- 槽位取值（规范值 → 可互换的说法）

PERIOD = {
    "2025年末": ["2025年末", "2025年年底", "2025年12月31日"],
    "2026年一季度末": ["2026年一季度末", "2026年第1季度末", "2026年3月31日"],
    "2026年二季度末": ["2026年二季度末", "2026年第2季度末", "2026年6月30日"],
}
DOC = {
    "2025年年报": ["2025年年报", "2025年年度报告"],
    "2026年一季报": ["2026年一季报", "2026年第1季度报告"],
    "2026年二季报": ["2026年二季报", "2026年第2季度报告"],
}
YEAR = {"2023年": ["2023年"], "2024年": ["2024年"], "2025年": ["2025年"]}
WINDOW = {
    "近1年": ["近1年", "近一年", "过去一年"],
    "近3年": ["近3年", "近三年", "过去三年"],
    "近6个月": ["近6个月", "近半年"],
    "成立以来": ["成立以来", "自成立起"],
}

# intent: split, forms（{F}/{G} = 基金，其余 {X} = 槽位）, slots: 槽位名 → {规范值: [说法...]}
INTENTS: dict[str, dict] = {
    "fee_rate": {
        "split": "dev",
        "forms": [
            "根据最新招募说明书，{F}的{K}年费率是多少？",
            "{F}每年收多少{K}？",
            "请问{F}的{K}是按什么比例计提的，年费率多少？",
            "查一下{F}的{K}率",
            "{F}{K}率是多少呢",
        ],
        "slots": {"K": {"管理费": ["管理费"], "托管费": ["托管费"], "销售服务费": ["销售服务费"]}},
    },
    "redemption_days": {
        "split": "dev",
        "forms": [
            "持有{N}赎回{F}，赎回费率是多少？",
            "{F}持有满{N}再赎回，要交多少赎回费？",
            "{F}赎回费：持有期限{N}对应的费率是多少",
            "{F}份额持有{N}卖出，赎回费按多少收？",
        ],
        "slots": {
            "N": {
                "7天": ["7天"],
                "30天": ["30天"],
                "90天": ["90天"],
                "180天": ["180天"],
                "365天": ["365天", "1年"],
            }
        },
    },
    "manager_now": {
        "split": "dev",
        "forms": [
            "{F}的{M}是谁？",
            "请问{F}{M}叫什么名字",
            "{F}的{M}是哪位",
            "想知道{F}目前的{M}",
        ],
        "slots": {
            "M": {
                "现任基金经理": ["现任基金经理", "在任基金经理"],
                "前任基金经理": ["前任基金经理", "离任的基金经理"],
            }
        },
    },
    "annual_return": {
        "split": "dev",
        "forms": [
            "{F}{Y}的年度收益率是多少？",
            "{F}在{Y}涨了多少（全年收益）？",
            "{Y}全年，{F}的净值增长率是多少",
            "{F}{Y}全年业绩怎么样，收益率多少",
        ],
        "slots": {"Y": YEAR},
    },
    "max_drawdown": {
        "split": "dev",
        "forms": [
            "{F}{W}的最大回撤是多少",
            "{F}{W}最大回撤多大？",
            "统计{W}内{F}的最大回撤",
            "{W}期间，{F}最大回撤是多少",
        ],
        "slots": {"W": WINDOW},
    },
    "contract_clause": {
        "split": "dev",
        "forms": [
            "{F}的基金合同里，{X}是怎么规定的？",
            "按基金合同，{F}的{X}具体是什么",
            "请说明{F}基金合同中关于{X}的条款",
        ],
        "slots": {
            "X": {
                "合同终止情形": ["合同终止情形", "基金合同终止的情形"],
                "持有人大会召开条件": ["持有人大会召开条件", "基金份额持有人大会的召开条件"],
                "巨额赎回处理方式": ["巨额赎回处理方式", "巨额赎回的处理方式"],
                "收益分配原则": ["收益分配原则", "分红的原则"],
            }
        },
    },
    "establish": {
        "split": "dev",
        "forms": [
            "{F}的{E}是什么？",
            "请问{F}的{E}",
            "查询{F}{E}",
        ],
        "slots": {
            "E": {
                "成立日期": ["成立日期", "成立日"],
                "基金托管人": ["基金托管人", "托管银行"],
                "基金类型": ["基金类型", "产品类型"],
                "基金管理人": ["基金管理人", "管理公司"],
            }
        },
    },
    "min_purchase": {
        "split": "dev",
        "forms": [
            "{F}的{Q}是多少？",
            "{F}{Q}有什么限制",
            "我想问下{F}的{Q}",
        ],
        "slots": {
            "Q": {
                "最低申购金额": ["最低申购金额", "起购金额"],
                "最低赎回份额": ["最低赎回份额"],
                "定投起点金额": ["定投起点金额", "定投起点"],
                "最低持有份额": ["最低持有份额", "最低保留份额"],
            }
        },
    },
    # ---- test
    "share_class_fee": {
        "split": "test",
        "forms": [
            "{F}{C}份额的{K}是多少？",
            "我买{F}的{C}，{K}是多少",
            "{F}的{C}{K}怎么收",
            "想了解{F}{C}的{K}",
        ],
        "slots": {
            "C": {"A类": ["A类"], "C类": ["C类"]},
            "K": {
                "申购费率": ["申购费率"],
                "赎回费率": ["赎回费率"],
                "销售服务费率": ["销售服务费率"],
            },
        },
    },
    "holdings": {
        "split": "test",
        "forms": [
            "{F}在{P}的{R}重仓股是什么？",
            "{P}，{F}的{R}持仓股票是哪只",
            "查询{F}{P}前十大持仓里的{R}股票",
        ],
        "slots": {
            "P": PERIOD,
            "R": {
                "第一大": ["第一大", "最大"],
                "第二大": ["第二大"],
                "第三大": ["第三大"],
                "第五大": ["第五大"],
            },
        },
    },
    "manager_period": {
        "split": "test",
        "forms": [
            "{F}在{P}的基金经理是谁？",
            "{P}时{F}由哪位基金经理管理",
            "{P}，{F}的基金经理叫什么",
        ],
        "slots": {"P": PERIOD},
    },
    "scale": {
        "split": "test",
        "forms": [
            "{F}{P}的基金资产净值是多少？",
            "{F}在{P}时规模多大",
            "{P}，{F}的净资产是多少",
        ],
        "slots": {"P": PERIOD},
    },
    "window_return": {
        "split": "test",
        "forms": [
            "{F}{W}的收益率是多少？",
            "{F}{W}涨幅多少",
            "{W}，{F}的区间收益是多少",
        ],
        "slots": {"W": WINDOW},
    },
    "contract_ratio": {
        "split": "test",
        "forms": [
            "根据基金合同，{F}投资{A}的比例{T}是多少？",
            "{F}的基金合同对{A}仓位的{T}怎么规定？",
            "{F}合同规定{A}占基金资产的比例{T}是多少",
        ],
        "slots": {
            "A": {"股票": ["股票"], "港股通标的股票": ["港股通标的股票", "港股"]},
            "T": {"下限": ["下限", "最低"], "上限": ["上限", "最高"]},
        },
    },
    "benchmark": {
        "split": "test",
        "forms": [
            "{F}的{Z}是什么？",
            "请介绍{F}的{Z}",
            "{F}{Z}具体怎么写的",
        ],
        "slots": {
            "Z": {
                "业绩比较基准": ["业绩比较基准"],
                "投资目标": ["投资目标"],
                "投资范围": ["投资范围"],
                "风险收益特征": ["风险收益特征"],
            }
        },
    },
    "dividend": {
        "split": "test",
        "forms": [
            "{F}在{Y}的分红记录是什么？",
            "{F}{Y}的分红情况",
            "{F}在{Y}分红了吗，分了多少",
        ],
        "slots": {"Y": YEAR},
    },
    "report_commentary": {
        "split": "test",
        "forms": [
            "{D}里，{F}的基金经理对{T}怎么说？",
            "{F}{D}中关于{T}的内容",
            "总结{F}在{D}中对{T}的看法",
        ],
        "slots": {
            "D": DOC,
            "T": {
                "后市展望": ["后市展望", "市场展望"],
                "业绩归因": ["业绩归因"],
                "仓位调整": ["仓位调整", "仓位变化"],
            },
        },
    },
    "compare": {
        "split": "test",
        "forms": [
            "对比{F}和{G}的{K}",
            "{F}与{G}的{K}分别是多少？",
            "{F}和{G}的{K}各是多少，做个对比",
        ],
        "slots": {
            "K": {
                "管理费率": ["管理费率", "管理费"],
                "托管费率": ["托管费率", "托管费"],
                "规模": ["规模", "基金规模"],
                "最大回撤": ["最大回撤"],
            }
        },
        "two_funds": True,
    },
}

# 每个意图生成的对数
N_POS, N_NEG, N_UNREL_PER_INTENT = 8, 12, 4

# ---------------------------------------------------------------- 手写的自由说法

HAND_POS = [
    ("易方达医疗保健行业混合每年收多少管理费", "易方达医疗保健行业混合的管理费率是多少"),
    ("广发医疗保健股票现在是哪位基金经理在管", "广发医疗保健股票的现任基金经理是谁"),
    ("银华集成电路混合二季度末规模多大", "银华集成电路混合2026年第二季度末的基金资产净值是多少"),
    ("中欧医疗健康混合的赎回费怎么收", "中欧医疗健康混合赎回费率是多少，持有多久有优惠"),
    (
        "永赢科技驱动混合一季度十大重仓股有哪些",
        "2026年一季报里永赢科技驱动混合的前十大持仓股票是什么",
    ),
    ("汇添富创新医药混合近一年涨了多少", "汇添富创新医药混合近1年的收益率"),
    ("南方医药保健混合是哪天成立的", "南方医药保健混合的成立日期"),
    ("华安科技动力混合最低买多少钱", "华安科技动力混合的最低申购金额是多少"),
    (
        "博时半导体主题混合合同里对股票仓位有什么要求",
        "博时半导体主题混合基金合同规定的股票投资比例范围",
    ),
    ("工银前沿医疗股票业绩比较基准是啥", "工银前沿医疗股票的业绩比较基准是什么"),
    ("天弘中证医药100的托管费是多少", "天弘中证医药100托管费年费率多少"),
    ("国泰国证医药卫生行业指数跟踪的是什么指数", "国泰国证医药卫生行业指数的标的指数是哪个"),
    ("富国创新科技混合2025年分红了吗", "富国创新科技混合在2025年有没有进行现金分红"),
    (
        "东财中证通信技术主题指数的C类份额收不收销售服务费",
        "东财中证通信技术主题指数C类销售服务费率是多少",
    ),
    (
        "大成中证360互联网+大数据100指数最大回撤多少",
        "大成中证360互联网+大数据100指数的最大回撤是多少",
    ),
    (
        "西部利得中证人工智能主题指数增强的基金托管人是谁",
        "西部利得中证人工智能主题指数增强由哪家银行托管",
    ),
    ("中银创新医疗混合近3年表现怎么样，涨了多少", "中银创新医疗混合近三年的收益率是多少"),
    (
        "请对比一下易方达医疗保健行业混合和广发医疗保健股票的管理费",
        "广发医疗保健股票和易方达医疗保健行业混合，两者的管理费率分别是多少",
    ),
    ("002692 的季报里基金经理怎么看后市", "富国创新科技混合二季报中基金经理对后市的展望"),
    (
        "汇添富中证芯片产业指数增强发起式是什么类型的基金",
        "汇添富中证芯片产业指数增强发起式属于哪类基金产品",
    ),
]
HAND_NEG = [
    (
        "易方达医疗保健行业混合的管理费率是多少",
        "易方达医疗保健行业混合的托管费率是多少",
        "fee_kind",
    ),
    ("易方达医疗保健行业混合的管理费率是多少", "易方达信息产业混合的管理费率是多少", "fund"),
    ("银华集成电路混合二季度末规模多大", "银华集成电路混合一季度末规模多大", "period"),
    ("中欧医疗健康混合A类的赎回费怎么收", "中欧医疗健康混合C类的赎回费怎么收", "share_class"),
    ("永赢科技驱动混合2025年的收益率", "永赢科技驱动混合2024年的收益率", "year"),
    ("广发医疗保健股票现任基金经理是谁", "广发医疗保健股票前任基金经理是谁", "manager_role"),
    ("汇添富创新医药混合近一年涨了多少", "汇添富创新医药混合近三年涨了多少", "window"),
    ("南方医药保健混合的最低申购金额", "南方医药保健混合的最低赎回份额", "metric"),
    ("华安科技动力混合的股票仓位下限", "华安科技动力混合的股票仓位上限", "bound"),
    ("工银前沿医疗股票持有7天赎回费率", "工银前沿医疗股票持有30天赎回费率", "number"),
    ("富国创新科技混合2025年分红了吗", "富国创新科技混合2024年分红了吗", "year"),
    ("博时半导体主题混合的业绩比较基准", "博时半导体主题混合的投资范围", "metric"),
    ("国泰国证医药卫生行业指数的管理费率", "天弘中证医药100的管理费率", "fund"),
    (
        "大成中证360互联网+大数据100指数2026年二季度末第一大重仓股",
        "大成中证360互联网+大数据100指数2026年二季度末第二大重仓股",
        "rank",
    ),
    (
        "中银创新医疗混合2025年年报里基金经理的后市展望",
        "中银创新医疗混合2026年二季报里基金经理的后市展望",
        "doc",
    ),
    (
        "东财中证通信技术主题指数C类的销售服务费率",
        "东财中证通信技术主题指数A类的申购费率",
        "share_class",
    ),
    ("汇添富创新医药混合的成立日期", "汇添富创新医药混合的基金托管人", "metric"),
    ("易方达医疗保健行业混合近一年最大回撤", "易方达医疗保健行业混合成立以来最大回撤", "window"),
    (
        "西部利得中证人工智能主题指数增强的基金合同终止条件",
        "西部利得中证人工智能主题指数增强的基金份额持有人大会召开条件",
        "clause",
    ),
    (
        "对比易方达医疗保健行业混合和广发医疗保健股票的管理费",
        "对比易方达医疗保健行业混合和南方医药保健混合的管理费",
        "fund",
    ),
]


# ---------------------------------------------------------------- 生成


class Builder:
    def __init__(self) -> None:
        self.rng = random.Random(SEED)
        self.funds = load_funds()
        self.by_name = {f["name"]: f for f in self.funds}
        self.partners: dict[str, list[str]] = {}
        for a, b in CONFUSABLE:
            assert a in self.by_name and b in self.by_name, (a, b)
            self.partners.setdefault(a, []).append(b)
            self.partners.setdefault(b, []).append(a)
        self.pairs: list[dict] = []
        self.seen: set[frozenset[str]] = set()

    # -- 基金说法：全名 / 去掉类型后缀的简称 / 代码
    def fund_surface(self, fund: dict, kind: str) -> str:
        return {"name": fund["name"], "short": fund["short"], "code": fund["code"]}[kind]

    def fund_kind(self) -> str:
        return self.rng.choices(["name", "short", "code"], weights=[0.6, 0.3, 0.1])[0]

    def render(
        self,
        form: str,
        funds: dict[str, dict],
        kinds: dict[str, str],
        vals: dict[str, str],
        surf_idx: dict[str, int],
        table: dict[str, dict[str, list[str]]],
    ) -> str:
        """把基金和槽位代入模板；槽位取规范值的第 surf_idx 个说法（超出则取模）。"""
        text = form
        for key, f in funds.items():
            text = text.replace("{" + key + "}", self.fund_surface(f, kinds[key]))
        for key, val in vals.items():
            options = table[key][val]
            text = text.replace("{" + key + "}", options[surf_idx.get(key, 0) % len(options)])
        # 基金代码（6 位）后面紧跟数字槽位（「008919」+「2025年」）会粘成一串 10 位数字，没有人会这样问：补一个空格
        return re.sub(r"(?<!\d)(\d{6})(?=\d)", r"\1 ", text)

    def add(
        self,
        split: str,
        label: str,
        category: str,
        subtype: str,
        intent: str,
        q1: str,
        q2: str,
        provenance: str,
    ) -> bool:
        key = frozenset((q1, q2))
        if q1 == q2 or key in self.seen:
            return False
        self.seen.add(key)
        self.pairs.append(
            {
                "split": split,
                "label": label,
                "category": category,
                "subtype": subtype,
                "intent": intent,
                "q1": q1,
                "q2": q2,
                "provenance": provenance,
            }
        )
        return True

    def pick_funds(self, two: bool) -> dict[str, dict]:
        f = self.rng.choice(self.funds)
        if not two:
            return {"F": f}
        g = self.rng.choice([x for x in self.funds if x["name"] != f["name"]])
        return {"F": f, "G": g}

    def other_fund(self, f: dict, exclude: set[str]) -> dict:
        cands = [self.by_name[n] for n in self.partners.get(f["name"], []) if n not in exclude]
        if not cands or self.rng.random() < 0.2:  # 偶尔用同主题的随机基金
            cands = [
                x
                for x in self.funds
                if x["theme"] == f["theme"] and x["name"] not in exclude and x["name"] != f["name"]
            ]
        return self.rng.choice(cands)

    def build_intent(self, name: str, spec: dict) -> None:
        split, forms, slots = spec["split"], spec["forms"], spec["slots"]
        two = spec.get("two_funds", False)
        slot_names = list(slots)
        n_ok = {"pos": 0, "neg": 0}
        guard = 0
        # ---- 同义改写对：同一组取值，两种不同的措辞
        while n_ok["pos"] < N_POS and guard < 2000:
            guard += 1
            funds = self.pick_funds(two)
            vals = {s: self.rng.choice(list(slots[s])) for s in slot_names}
            i, j = self.rng.sample(range(len(forms)), 2)
            kinds1 = {k: self.fund_kind() for k in funds}
            fund_alias = self.rng.random() < 0.15
            kinds2 = {k: (self.fund_kind() if fund_alias else kinds1[k]) for k in funds}
            if two and self.rng.random() < 0.5:  # 对比类：交换两只基金的顺序也是同一个问题
                funds2 = {"F": funds["G"], "G": funds["F"]}
                kinds2 = {"F": kinds1["G"], "G": kinds1["F"]}
                subtype = "swap_order"
            else:
                funds2 = funds
                subtype = "fund_alias" if fund_alias and kinds1 != kinds2 else "wording"
            s1 = {s: self.rng.randrange(3) for s in slot_names}
            s2 = {s: self.rng.randrange(3) for s in slot_names}
            q1 = self.render(forms[i], funds, kinds1, vals, s1, slots)
            q2 = self.render(forms[j], funds2, kinds2, vals, s2, slots)
            if self.add(split, "same", "paraphrase", subtype, name, q1, q2, "template_generated"):
                n_ok["pos"] += 1
        # ---- 难负例：只改一个关键要素（基金 / 某个槽位），措辞相同或不同各占一半
        guard = 0
        while n_ok["neg"] < N_NEG and guard < 4000:
            guard += 1
            funds = self.pick_funds(two)
            vals = {s: self.rng.choice(list(slots[s])) for s in slot_names}
            kinds = {k: self.fund_kind() for k in funds}
            change = self.rng.choice(["fund", *slot_names])
            funds2, vals2 = dict(funds), dict(vals)
            if change == "fund":
                which = self.rng.choice(list(funds))
                funds2[which] = self.other_fund(funds[which], {f["name"] for f in funds.values()})
                subtype = "fund"
            else:
                if len(slots[change]) < 2:
                    continue
                vals2[change] = self.rng.choice([v for v in slots[change] if v != vals[change]])
                subtype = f"slot:{change}"
            i = self.rng.randrange(len(forms))
            same_wording = self.rng.random() < 0.5
            j = i if same_wording else self.rng.choice([k for k in range(len(forms)) if k != i])
            s1 = {s: self.rng.randrange(3) for s in slot_names}
            q1 = self.render(forms[i], funds, kinds, vals, s1, slots)
            q2 = self.render(forms[j], funds2, kinds, vals2, s1, slots)
            if self.add(
                split, "different", "hard_negative", subtype, name, q1, q2, "template_generated"
            ):
                n_ok["neg"] += 1
        assert n_ok["pos"] == N_POS and n_ok["neg"] == N_NEG, (name, n_ok)

    def build_unrelated(self) -> None:
        """同一 split 内、不同意图的问题随机配对（参考基线）。"""
        for split in ("dev", "test"):
            names = [n for n, s in INTENTS.items() if s["split"] == split]
            want = N_UNREL_PER_INTENT * len(names)
            guard = 0
            made = 0
            while made < want and guard < 5000:
                guard += 1
                a, b = self.rng.sample(names, 2)
                qs = []
                for n in (a, b):
                    spec = INTENTS[n]
                    funds = self.pick_funds(spec.get("two_funds", False))
                    vals = {s: self.rng.choice(list(spec["slots"][s])) for s in spec["slots"]}
                    kinds = {k: self.fund_kind() for k in funds}
                    qs.append(
                        self.render(
                            self.rng.choice(spec["forms"]),
                            funds,
                            kinds,
                            vals,
                            {s: 0 for s in vals},
                            spec["slots"],
                        )
                    )
                if self.add(
                    split,
                    "different",
                    "unrelated",
                    "cross_intent",
                    f"{a}|{b}",
                    qs[0],
                    qs[1],
                    "template_generated",
                ):
                    made += 1

    def build_hand(self) -> None:
        for i, (q1, q2) in enumerate(HAND_POS):
            split = "dev" if i % 5 in (0, 2) else "test"  # 8 : 12
            self.add(
                split, "same", "paraphrase", "hand_written", "free", q1, q2, "hand_written_claude"
            )
        for i, (q1, q2, sub) in enumerate(HAND_NEG):
            split = "dev" if i % 5 in (1, 3) else "test"
            self.add(
                split,
                "different",
                "hard_negative",
                f"hand:{sub}",
                "free",
                q1,
                q2,
                "hand_written_claude",
            )


def main() -> None:
    b = Builder()
    for name, spec in INTENTS.items():
        b.build_intent(name, spec)
    b.build_unrelated()
    b.build_hand()
    pairs = b.pairs
    for n, p in enumerate(pairs, 1):
        p["id"] = f"cp-{n:04d}"
        p["version"] = "v1"
    keys = (
        "id",
        "version",
        "split",
        "label",
        "category",
        "subtype",
        "intent",
        "q1",
        "q2",
        "provenance",
    )
    lines = [json.dumps({k: p[k] for k in keys}, ensure_ascii=False) for p in pairs]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    sha = hashlib.sha256(OUT.read_bytes()).hexdigest()
    c = Counter((p["split"], p["category"]) for p in pairs)
    print(f"wrote {OUT.relative_to(ROOT)}  n={len(pairs)}  sha256={sha}")
    for k in sorted(c):
        print(f"  {k[0]:5s} {k[1]:14s} {c[k]}")


if __name__ == "__main__":
    main()
